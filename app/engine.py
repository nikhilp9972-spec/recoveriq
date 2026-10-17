"""Live engine — the same agent, running as an operable product.

The batch evaluation proves the numbers; this runs the identical brain
(diagnose.py, bandit.py, guardrails.py, simulator) event-driven on a
simulated clock so an operator can:

  * start/pause the agent and set the clock speed
  * watch decisions and outcomes stream in live
  * approve or deny high-value actions from an inbox (bounded autonomy)
  * inject a failed payment by hand and watch it get handled
  * edit the compliance policy and see it enforced immediately

The bandit warm-starts by replaying the frozen training batch, then keeps
learning from live outcomes.
"""

import heapq
import itertools
import json
import os
import threading

from agent.bandit import ARM_BY_NAME, schedule
from agent.diagnose import CODE_TO_CAUSE, diagnose
from agent.guardrails import Guardrails
from agent.runner import AgentPolicy, run_policy
from data.generator import generate
from simulator.engine import PaymentSimulator

FEED_LIMIT = 250
ARM_LABEL = {
    "RETRY_NOW": "retry immediately",
    "RETRY_NEXT_MORNING": "retry next morning",
    "RETRY_SALARY_DAY": "wait for salary day, then retry",
    "NUDGE_LINK": "send Hinglish nudge + payment link",
    "VOICE_CALL": "place Hinglish voice call",
}


def fmt_when(t):
    t = int(t)
    return f"Day {t // 24} · {t % 24:02d}:00"


class LiveEngine:
    def __init__(self, n_incoming=150, incoming_seed=123):
        self.lock = threading.Lock()
        self.sim = PaymentSimulator(seed=11)
        self.guard = Guardrails()
        self.bandit = None  # set by warm_start()
        self.running = False
        self.speed = 6.0  # simulated hours per real second
        self.seq = itertools.count()
        self.events = []  # heap of (t, seq, kind, case_id, payload)
        self.feed = []
        self.cases = {}
        self.approvals = {}
        self.recovered = 0
        self.action_cost = 0.0
        self.attempts = 0

        incoming = generate(seed=incoming_seed, n=n_incoming)
        self.clock = float(min(c["created_at"] for c in incoming) - 1)
        for c in incoming:
            self._register(c, arrive_at=c["created_at"])

    # ---------- setup ----------
    def warm_start(self):
        """Replay the frozen training batch through the real agent, then
        keep its learned posteriors for live traffic."""
        policy = AgentPolicy()
        run_policy(policy, generate(seed=42, n=1000), PaymentSimulator(7))
        with self.lock:
            self.bandit = policy.bandit
            self._feed(self.clock, "agent", "win",
                       "Agent is ready — it has already learned from 1,000 past failed payments")

    def _register(self, case, arrive_at):
        self.cases[case["case_id"]] = {
            "case": case,
            "state": {"retries": 0, "nudges": 0, "voice_calls": 0,
                      "opted_out": False, "attempts_total": 0},
            "cause": None, "status": "incoming", "next": "awaiting arrival",
            "recovered": 0, "close_reason": None, "last_t": arrive_at,
        }
        self._push(arrive_at, "ARRIVE", case["case_id"], None)

    def _push(self, t, kind, cid, payload):
        heapq.heappush(self.events, (float(t), next(self.seq), kind, cid, payload))

    def _feed(self, t, actor, kind, text):
        self.feed.append({"t": int(t), "actor": actor, "kind": kind, "text": text})
        del self.feed[:-FEED_LIMIT]

    # ---------- clock ----------
    def tick(self, real_dt):
        with self.lock:
            if not self.running or self.bandit is None:
                return
            self.clock += self.speed * real_dt
            while self.events and self.events[0][0] <= self.clock:
                t, _, kind, cid, payload = heapq.heappop(self.events)
                if kind == "ARRIVE":
                    self._on_arrive(cid, t)
                elif kind == "ACT":
                    self._on_act(cid, t, payload)

    # ---------- agent loop ----------
    def _on_arrive(self, cid, t):
        cs = self.cases[cid]
        cause, rationale = diagnose(cs["case"])
        cs["cause"] = cause
        c = cs["case"]
        self._feed(t, "agent", "agent",
                   f"{cid} · New failed payment: ₹{c['amount']:,} from {c['merchant_name']}. "
                   f"Reason found — {cause.replace('_', ' ').lower()}. {rationale}")
        if cause == "HARD_DECLINE":
            self._close(cid, t, "hard_decline",
                        "the card/mandate is blocked, so retrying is pointless — closing it honestly")
            return
        self._decide(cid, t)

    def _decide(self, cid, t):
        cs = self.cases[cid]
        c = cs["case"]
        allowed = self.guard.allowed_arms(cs["state"])
        arm, ev, p = self.bandit.choose(cs["cause"], c["amount"], allowed)
        if arm == "GIVE_UP":
            if allowed == ["GIVE_UP"]:
                self._close(cid, t, "caps_exhausted",
                            "reached the legal retry limit — stopping, as the rules require")
            else:
                self._close(cid, t, "no_action_worth_its_cost",
                            "no remaining action is worth its cost — stopping instead of pestering")
            return
        exec_t = schedule(arm, int(t))
        if ARM_BY_NAME[arm][1] in ("nudge", "voice"):
            exec_t, clamped = self.guard.clamp_contact_time(exec_t)
            if clamped:
                self._feed(t, "guardrail", "guardrail",
                           f"{cid} · won't disturb the customer at night — moved to {fmt_when(exec_t)}")
        payload = {"arm": arm, "ev": round(ev), "p": round(p, 3)}
        if self.guard.needs_human_approval(c["amount"]):
            self.approvals[cid] = {**payload, "exec_t": exec_t,
                                   "amount": c["amount"],
                                   "merchant": c["merchant_name"],
                                   "action": ARM_LABEL[arm],
                                   "cause": cs["cause"]}
            cs["status"] = "waiting_approval"
            cs["next"] = f"needs your OK: {ARM_LABEL[arm]}"
            self._feed(t, "guardrail", "guardrail",
                       f"{cid} · ₹{c['amount']:,} is a big amount — asking a human before acting")
            return
        self._schedule_act(cid, exec_t, payload)

    def _schedule_act(self, cid, exec_t, payload):
        cs = self.cases[cid]
        cs["status"] = "scheduled"
        cs["next"] = f"{ARM_LABEL[payload['arm']]} at {fmt_when(exec_t)}"
        self._push(exec_t, "ACT", cid, payload)

    def _on_act(self, cid, t, payload):
        cs = self.cases[cid]
        c, arm = cs["case"], payload["arm"]
        channel = ARM_BY_NAME[arm][1]
        success = self.sim.attempt(c, cs["cause"], channel, int(t),
                                   cs["state"]["attempts_total"],
                                   cs["state"]["attempts_total"] + 1)
        self.bandit.update(cs["cause"], arm, success)
        counter = {"retry": "retries", "nudge": "nudges", "voice": "voice_calls"}[channel]
        cs["state"][counter] += 1
        cs["state"]["attempts_total"] += 1
        self.attempts += 1
        self.action_cost += ARM_BY_NAME[arm][2]
        cs["last_t"] = t
        if channel in ("nudge", "voice") and c["customer"]["opts_out_on_contact"]:
            if not cs["state"]["opted_out"]:
                cs["state"]["opted_out"] = True
                self._feed(t, "guardrail", "guardrail",
                           f"{cid} · customer said stop — the agent will never contact them again")
        if success:
            cs["status"], cs["next"] = "recovered", "—"
            cs["recovered"] = c["amount"]
            self.recovered += c["amount"]
            self._feed(t, "agent", "win",
                       f"{cid} · ₹{c['amount']:,} WON BACK — {ARM_LABEL[arm]} worked "
                       f"(agent was {round(payload['p'] * 100)}% confident)")
        else:
            self._feed(t, "agent", "agent",
                       f"{cid} · {ARM_LABEL[arm]} didn't work — trying a different approach")
            self._decide(cid, t + 1)

    def _close(self, cid, t, reason, note):
        cs = self.cases[cid]
        cs["status"], cs["close_reason"], cs["next"] = "closed", reason, "—"
        cs["last_t"] = t
        kind = "loss" if reason == "hard_decline" else "guardrail"
        self._feed(t, "agent", kind, f"{cid} · closed — {note}")

    # ---------- operator actions ----------
    def set_running(self, running):
        with self.lock:
            self.running = bool(running) and self.bandit is not None

    def set_speed(self, speed):
        with self.lock:
            self.speed = max(0.5, min(48.0, float(speed)))

    def resolve_approval(self, cid, approved):
        with self.lock:
            pending = self.approvals.pop(cid, None)
            if not pending:
                return False
            if approved:
                exec_t = pending["exec_t"]
                if exec_t <= self.clock:
                    # the intended slot passed while waiting — re-derive the
                    # arm's smart timing from now instead of firing at bad odds
                    exec_t = schedule(pending["arm"], int(self.clock) + 1)
                    if ARM_BY_NAME[pending["arm"]][1] in ("nudge", "voice"):
                        exec_t, _ = self.guard.clamp_contact_time(exec_t)
                self._feed(self.clock, "human", "win",
                           f"{cid} · operator approved: {pending['action']} "
                           f"→ {fmt_when(exec_t)}")
                self._schedule_act(cid, exec_t,
                                   {k: pending[k] for k in ("arm", "ev", "p")})
            else:
                self._feed(self.clock, "human", "loss",
                           f"{cid} · operator denied the action")
                self._close(cid, self.clock, "operator_declined",
                            "operator declined the proposed action")
            return True

    def inject_case(self, merchant, amount, bank, failure_code):
        with self.lock:
            cid = f"live_{len(self.cases):04d}"
            case = {
                "case_id": cid, "source": "operator",
                "merchant_id": "live", "merchant_name": merchant or "Live merchant",
                "customer_id": f"cust_live{len(self.cases):04d}",
                "amount": max(1, int(amount)),
                "bank": bank if bank in ("HDFC", "ICICI", "SBI", "AXIS", "KOTAK", "PNB") else "HDFC",
                "failure_code": failure_code if failure_code in CODE_TO_CAUSE else "U28_TECHNICAL_DECLINE",
                "created_at": int(self.clock) + 1,
                "customer": {"responsiveness": 0.5, "opts_out_on_contact": False},
            }
            self._register(case, arrive_at=self.clock + 0.01)
            self._feed(self.clock, "human", "human",
                       f"{cid} · operator injected a failed payment: ₹{case['amount']:,} at {case['bank']}")
            return cid

    def update_policy(self, updates):
        editable = {"max_debit_representations", "max_nudges", "max_voice_calls",
                    "human_approval_amount_ceiling"}
        with self.lock:
            for k, v in updates.items():
                if k in editable:
                    self.guard.p[k] = max(0, int(v))
                elif k == "contact_window_hours" and isinstance(v, list) and len(v) == 2:
                    lo, hi = int(v[0]), int(v[1])
                    if 0 <= lo < hi <= 23:
                        self.guard.p[k] = [lo, hi]
            path = os.path.join(os.path.dirname(__file__), "..", "policy.json")
            with open(path, "w") as f:
                json.dump(self.guard.p, f, indent=2)
            self._feed(self.clock, "human", "guardrail",
                       "policy.json updated by operator — new limits enforced immediately")

    # ---------- state for the UI ----------
    def snapshot(self):
        with self.lock:
            live = [cs for cs in self.cases.values() if cs["cause"] is not None]
            live.sort(key=lambda cs: -cs["last_t"])
            at_risk = sum(cs["case"]["amount"] for cs in live)
            active = sum(1 for cs in live if cs["status"] in
                         ("scheduled", "waiting_approval"))
            return {
                "ready": self.bandit is not None,
                "running": self.running,
                "clock": fmt_when(self.clock),
                "speed": self.speed,
                "counters": {
                    "at_risk": at_risk,
                    "recovered": self.recovered,
                    "seen": len(live),
                    "active": active,
                    "recovered_n": sum(1 for cs in live if cs["status"] == "recovered"),
                    "attempts": self.attempts,
                    "cost": round(self.action_cost),
                },
                "approvals": [{"case_id": k, **v} for k, v in self.approvals.items()],
                "feed": self.feed[::-1][:120],
                "cases": [{
                    "case_id": cs["case"]["case_id"],
                    "merchant": cs["case"]["merchant_name"],
                    "amount": cs["case"]["amount"],
                    "bank": cs["case"]["bank"],
                    "cause": cs["cause"],
                    "status": cs["status"],
                    "next": cs["next"],
                    "close_reason": cs["close_reason"],
                } for cs in live[:40]],
                "policy": dict(self.guard.p),
            }
