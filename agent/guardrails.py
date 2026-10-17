"""Policy guardrails — the bandit proposes, this module disposes.

Hard limits live in policy.json, outside the learner, so a regulation change
is a config edit, not a retrain. Every intervention is checked here and every
adjustment (window clamp, approval gate, cap exhaustion) is written to the
audit trail.
"""

import json
import os

_POLICY_PATH = os.path.join(os.path.dirname(__file__), "..", "policy.json")
with open(_POLICY_PATH) as f:
    POLICY = json.load(f)


class Guardrails:
    def __init__(self, policy: dict = POLICY):
        self.p = policy

    def allowed_arms(self, state: dict) -> list:
        """Filter the arm menu by caps and consent. state tracks per-case counters."""
        allowed = ["GIVE_UP"]
        if state["retries"] < self.p["max_debit_representations"]:
            allowed += ["RETRY_NOW", "RETRY_NEXT_MORNING", "RETRY_SALARY_DAY"]
        if not state["opted_out"]:
            if state["nudges"] < self.p["max_nudges"]:
                allowed.append("NUDGE_LINK")
            if state["voice_calls"] < self.p["max_voice_calls"]:
                allowed.append("VOICE_CALL")
        return allowed

    def clamp_contact_time(self, t: int) -> tuple:
        """Contact only inside the permitted window. Returns (t', clamped?)."""
        lo, hi = self.p["contact_window_hours"]
        hod = t % 24
        if lo <= hod <= hi:
            return t, False
        if hod < lo:
            return (t // 24) * 24 + lo, True
        return (t // 24 + 1) * 24 + lo, True

    def needs_human_approval(self, amount: float) -> bool:
        return amount > self.p["human_approval_amount_ceiling"]
