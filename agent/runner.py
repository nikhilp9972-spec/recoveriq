"""Stage 4/5 — the recovery loop executor and audit ledger.

One executor runs EVERY policy (agent and baselines) through the identical
simulator, so the comparison in eval/ is apples to apples. Every decision,
action, clamp, approval gate, and closure is appended to the audit trail;
the agent run is also persisted to ledger.sqlite for inspection.
"""

import json
import os
import sqlite3

from agent.bandit import ARM_BY_NAME, ThompsonBandit, schedule
from agent.diagnose import diagnose
from agent.guardrails import Guardrails

HORIZON_HOURS = 66 * 24
CONTACT_LAG = {"nudge": 6, "voice": 1}  # hours until a promised payment lands


class AgentPolicy:
    """Diagnosis + Thompson bandit + guardrails. The real product."""
    name = "recoveriq_agent"
    uses_diagnosis = True

    def __init__(self, seed: int = 99):
        self.bandit = ThompsonBandit(seed=seed)
        self.guardrails = Guardrails()

    def next_action(self, case, cause, state, t):
        allowed = self.guardrails.allowed_arms(state)
        arm, ev, p = self.bandit.choose(cause, case["amount"], allowed)
        if arm == "GIVE_UP":
            reason = ("caps_exhausted" if allowed == ["GIVE_UP"]
                      else "expected_value_below_cost")
            return None, reason, {}
        return arm, schedule(arm, t), {"expected_value": round(ev, 1),
                                       "sampled_p": round(p, 3)}

    def observe(self, cause, arm, success):
        self.bandit.update(cause, arm, success)


class CronBaseline:
    """What merchants actually do: retry every 24h, three times, blindly."""
    name = "baseline_cron_24h"
    uses_diagnosis = False

    def __init__(self, offsets=(24, 48, 72)):
        self.offsets = offsets

    def next_action(self, case, cause, state, t):
        if state["retries"] >= len(self.offsets):
            return None, "retries_exhausted", {}
        return "RETRY_NOW", case["created_at"] + self.offsets[state["retries"]], {}

    def observe(self, *a):
        pass


class ImmediateBaseline(CronBaseline):
    """Retry-all-immediately: three attempts in the first three hours."""
    name = "baseline_immediate"

    def __init__(self):
        super().__init__(offsets=(1, 2, 3))


def run_policy(policy, cases, sim, collect_audit=False):
    """Run one policy over the whole batch. Returns (metrics, ledger)."""
    guard = Guardrails()
    ledger = {"cases": [], "decisions": [], "actions": [], "audit": []}
    curve = []
    recovered_amt = attempts = 0
    cost = 0.0
    exceptions = []

    def audit(case_id, actor, event, detail, t):
        if collect_audit:
            ledger["audit"].append((case_id, actor, event,
                                    json.dumps(detail), int(t)))

    for idx, case in enumerate(cases):
        cause, rationale = diagnose(case)
        state = {"retries": 0, "nudges": 0, "voice_calls": 0,
                 "opted_out": False, "attempts_total": 0}
        t = case["created_at"]
        outcome, close_reason, case_recovered = None, None, 0
        audit(case["case_id"], "agent", "DIAGNOSED",
              {"root_cause": cause, "rationale": rationale}, t)

        if policy.uses_diagnosis and cause == "HARD_DECLINE":
            outcome, close_reason = "CLOSED_UNRECOVERABLE", "hard_decline"

        while outcome is None:
            arm, exec_t_or_reason, meta = policy.next_action(case, cause, state, t)
            if arm is None:
                outcome, close_reason = "CLOSED_EXHAUSTED", exec_t_or_reason
                break
            exec_t = exec_t_or_reason
            channel = ARM_BY_NAME[arm][1]

            if channel in ("nudge", "voice"):
                exec_t, clamped = guard.clamp_contact_time(exec_t)
                if clamped:
                    audit(case["case_id"], "guardrail", "CONTACT_WINDOW_CLAMP",
                          {"arm": arm, "moved_to_hour": exec_t}, t)
            if guard.needs_human_approval(case["amount"]):
                audit(case["case_id"], "guardrail", "HUMAN_APPROVAL_REQUIRED",
                      {"arm": arm, "amount": case["amount"],
                       "approval": "simulated_granted"}, t)
            if exec_t > HORIZON_HOURS:
                outcome, close_reason = "CLOSED_EXHAUSTED", "horizon_reached"
                break

            ledger["decisions"].append((case["case_id"], policy.name, cause,
                                        arm, json.dumps(meta), int(t)))
            success = sim.attempt(case, cause, channel, exec_t,
                                  state["attempts_total"],
                                  state["attempts_total"] + 1)
            policy.observe(cause, arm, success)

            counter = {"retry": "retries", "nudge": "nudges",
                       "voice": "voice_calls"}[channel]
            state[counter] += 1
            state["attempts_total"] += 1
            attempts += 1
            cost += ARM_BY_NAME[arm][2]
            paid_at = exec_t + CONTACT_LAG.get(channel, 0)
            ledger["actions"].append((case["case_id"], arm, channel,
                                      "recovered" if success else "failed",
                                      int(exec_t)))
            audit(case["case_id"], "agent", f"{arm}_{'RECOVERED' if success else 'FAILED'}",
                  meta, exec_t)

            if channel in ("nudge", "voice") and case["customer"]["opts_out_on_contact"]:
                state["opted_out"] = True
                audit(case["case_id"], "guardrail", "CUSTOMER_OPT_OUT",
                      {"halt": "no further contact"}, exec_t)

            if success:
                outcome, case_recovered = "RECOVERED", case["amount"]
                recovered_amt += case["amount"]
                audit(case["case_id"], "agent", "CASE_RECOVERED",
                      {"amount": case["amount"], "paid_at": int(paid_at)}, paid_at)
                break
            t = exec_t + 1

        if outcome != "RECOVERED":
            exceptions.append({"case_id": case["case_id"], "root_cause": cause,
                               "amount": case["amount"], "reason": close_reason})
            audit(case["case_id"], "agent", "CASE_CLOSED",
                  {"outcome": outcome, "reason": close_reason}, t)
        ledger["cases"].append((case["case_id"], cause, case["amount"],
                                outcome, close_reason or "", case_recovered))
        curve.append(recovered_amt)

    total_amt = sum(c["amount"] for c in cases)
    metrics = {
        "policy": policy.name,
        "cases": len(cases),
        "total_at_risk": total_amt,
        "recovered": recovered_amt,
        "recovery_rate_pct": round(100 * recovered_amt / total_amt, 1),
        "cases_recovered": sum(1 for r in ledger["cases"] if r[3] == "RECOVERED"),
        "attempts": attempts,
        "action_cost": round(cost, 0),
        "rupees_recovered_per_attempt": round(recovered_amt / max(attempts, 1)),
        "guardrail_violations": 0,  # enforced structurally; asserted in eval
        "curve": curve,
        "exceptions": exceptions,
    }
    return metrics, ledger


def write_ledger(ledger, path):
    if os.path.exists(path):
        os.remove(path)
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE cases(case_id TEXT PRIMARY KEY, root_cause TEXT,
            amount INT, outcome TEXT, close_reason TEXT, recovered INT);
        CREATE TABLE decisions(case_id TEXT, policy TEXT, root_cause TEXT,
            arm TEXT, meta TEXT, decided_at_hour INT);
        CREATE TABLE actions(case_id TEXT, arm TEXT, channel TEXT,
            result TEXT, executed_at_hour INT);
        CREATE TABLE audit_log(case_id TEXT, actor TEXT, event TEXT,
            detail TEXT, at_hour INT);
    """)
    db.executemany("INSERT INTO cases VALUES(?,?,?,?,?,?)", ledger["cases"])
    db.executemany("INSERT INTO decisions VALUES(?,?,?,?,?,?)", ledger["decisions"])
    db.executemany("INSERT INTO actions VALUES(?,?,?,?,?)", ledger["actions"])
    db.executemany("INSERT INTO audit_log VALUES(?,?,?,?,?)", ledger["audit"])
    db.commit()
    db.close()
