"""Payment-outcome simulator: the hidden world the agent must learn.

Answers one question — "if you take action X on case Y at hour T, does the
money arrive?" — with probabilities the agent cannot see. Structure encodes
the real dynamics of Indian recurring payments (see data/SOURCES.md):

* Insufficient-balance debits succeed dramatically more on days 1-3 of the
  month (salary credit) and barely at all at month-end.
* Bank-downtime failures recover once the outage window passes; hammering
  immediately wastes a capped re-presentation.
* Per-day limit breaches reset at midnight.
* Abandoned checkouts respond to human contact (nudge/voice), not retries.
* NPCI-style maintenance hours (1-4 AM) halve success everywhere.

Every draw comes from a per-case seeded RNG, so a full evaluation replays
bit-for-bit: same seed, same batch, same numbers.
"""

import zlib
import numpy as np

BANK_RELIABILITY = {
    "HDFC": 0.98, "ICICI": 0.97, "AXIS": 0.95,
    "KOTAK": 0.96, "SBI": 0.90, "PNB": 0.88,
}

# Response propensity multipliers: P(pays) = responsiveness * factor
NUDGE_FACTOR = {
    "USER_DROPPED": 0.60, "MANDATE_PAUSED": 0.35,
    "INSUFFICIENT_FUNDS": 0.25, "LIMIT_EXCEEDED": 0.15,
    "BANK_DOWNTIME": 0.15, "TECHNICAL_DECLINE": 0.15,
}
VOICE_FACTOR = {
    "USER_DROPPED": 0.85, "MANDATE_PAUSED": 0.55,
    "INSUFFICIENT_FUNDS": 0.40, "LIMIT_EXCEEDED": 0.25,
    "BANK_DOWNTIME": 0.25, "TECHNICAL_DECLINE": 0.25,
}

ATTEMPT_DECAY = 0.92  # each prior failed attempt slightly sours the odds


def day_of_month(t_hours: int) -> int:
    return (t_hours // 24) % 30 + 1  # 1..30; salary credit on day 1


def hour_of_day(t_hours: int) -> int:
    return t_hours % 24


class PaymentSimulator:
    def __init__(self, seed: int = 7):
        self.seed = seed

    def _rng(self, case: dict, salt: str) -> np.random.Generator:
        key = f"{self.seed}:{case['case_id']}:{salt}"
        return np.random.default_rng(zlib.crc32(key.encode()))

    def _outage_hours(self, case: dict) -> int:
        return int(self._rng(case, "outage").integers(2, 12))

    def retry_success_prob(self, case: dict, root_cause: str, t: int,
                           attempts_prior: int) -> float:
        dom, hod = day_of_month(t), hour_of_day(t)
        if root_cause == "HARD_DECLINE":
            return 0.0
        if root_cause == "INSUFFICIENT_FUNDS":
            if dom in (1, 2, 3):
                p = 0.75
            elif dom >= 27:
                p = 0.05
            else:
                p = 0.08
            if 6 <= hod <= 11:
                p += 0.05
        elif root_cause == "BANK_DOWNTIME":
            in_outage = t < case["created_at"] + self._outage_hours(case)
            p = 0.05 if in_outage else 0.85
        elif root_cause == "LIMIT_EXCEEDED":
            p = 0.70 if t // 24 > case["created_at"] // 24 else 0.05
        elif root_cause == "TECHNICAL_DECLINE":
            p = 0.55
        elif root_cause in ("USER_DROPPED", "MANDATE_PAUSED"):
            p = 0.05 if root_cause == "USER_DROPPED" else 0.02
        else:
            p = 0.10
        if 1 <= hod <= 4:  # NPCI maintenance window
            p *= 0.5
        p *= BANK_RELIABILITY[case["bank"]]
        p *= ATTEMPT_DECAY ** attempts_prior
        return min(max(p, 0.0), 0.98)

    def contact_success_prob(self, case: dict, root_cause: str,
                             channel: str) -> float:
        factors = NUDGE_FACTOR if channel == "nudge" else VOICE_FACTOR
        return case["customer"]["responsiveness"] * factors.get(root_cause, 0.1)

    def attempt(self, case: dict, root_cause: str, channel: str, t: int,
                attempts_prior: int, attempt_no: int) -> bool:
        """Execute one action. channel: 'retry' | 'nudge' | 'voice'."""
        if channel == "retry":
            p = self.retry_success_prob(case, root_cause, t, attempts_prior)
        else:
            p = self.contact_success_prob(case, root_cause, channel)
        rng = self._rng(case, f"{channel}:{attempt_no}")
        return bool(rng.random() < p)
