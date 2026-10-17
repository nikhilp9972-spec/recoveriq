"""Stage 3 — the brain: a contextual Thompson-sampling bandit.

Per (root_cause, arm) cell we keep a Beta posterior over P(action recovers
the money). To decide, we sample p from each allowed arm's posterior and
pick the arm maximising sampled economic value:

    EV(arm) = p_sampled * amount - cost(arm)

GIVE_UP is always worth exactly 0, so the agent stops chasing a payment as
soon as no arm is worth its cost — a learned stopping rule on top of the
hard policy caps in guardrails.py. No ML framework, no LLM in the loop:
~80 lines of NumPy that can be replayed and explained line by line.
"""

import numpy as np

# (name, channel, cost in Rs.) — costs make the policy learn efficiency,
# not just raw recovery. Voice is deliberately expensive.
ARMS = [
    ("RETRY_NOW", "retry", 2.0),
    ("RETRY_NEXT_MORNING", "retry", 2.0),
    ("RETRY_SALARY_DAY", "retry", 2.0),
    ("NUDGE_LINK", "nudge", 1.0),
    ("VOICE_CALL", "voice", 15.0),
    ("GIVE_UP", "close", 0.0),
]
ARM_BY_NAME = {name: (name, ch, cost) for name, ch, cost in ARMS}


def next_morning_9am(t: int) -> int:
    cand = (t // 24) * 24 + 9
    if cand <= t + 6:
        cand += 24
    return cand


def next_salary_day_9am(t: int) -> int:
    day = t // 24
    dom = day % 30 + 1
    cand = (day + (31 - dom) % 30) * 24 + 9
    if cand <= t:
        cand += 30 * 24
    return cand


def schedule(arm_name: str, t: int) -> int:
    """When would this arm execute, if chosen at hour t?"""
    if arm_name == "RETRY_NOW":
        return t + 1
    if arm_name == "RETRY_NEXT_MORNING":
        return next_morning_9am(t)
    if arm_name == "RETRY_SALARY_DAY":
        return next_salary_day_9am(t)
    return t + 1  # nudge/voice go out promptly (guardrails clamp to window)


class ThompsonBandit:
    def __init__(self, seed: int = 99):
        self.rng = np.random.default_rng(seed)
        self.posteriors = {}  # (root_cause, arm_name) -> [alpha, beta]

    def _post(self, cause: str, arm: str) -> list:
        return self.posteriors.setdefault((cause, arm), [1.0, 1.0])

    def choose(self, cause: str, amount: float, allowed: list) -> tuple:
        """Return (arm_name, expected_value, sampled_p) for the best arm."""
        best = ("GIVE_UP", 0.0, 0.0)
        for name in allowed:
            if name == "GIVE_UP":
                continue
            _, _, cost = ARM_BY_NAME[name]
            a, b = self._post(cause, name)
            p = self.rng.beta(a, b)
            ev = p * amount - cost
            if ev > best[1]:
                best = (name, ev, p)
        return best  # stays GIVE_UP when nothing beats EV=0

    def update(self, cause: str, arm: str, success: bool):
        post = self._post(cause, arm)
        post[0 if success else 1] += 1.0

    def snapshot(self) -> dict:
        """Posterior means, for the dashboard's 'what the agent learned' view."""
        return {
            f"{cause}|{arm}": round(a / (a + b), 3)
            for (cause, arm), (a, b) in sorted(self.posteriors.items())
        }
