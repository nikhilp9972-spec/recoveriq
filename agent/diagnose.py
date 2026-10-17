"""Stage 2 — root-cause diagnosis.

Deterministic failure-code mapping does the heavy lifting; an LLM is only
worth invoking for long-tail/ambiguous codes (integrations/claude_client.py),
and every case gets a plain-English rationale for the audit trail.
Hard declines are closed immediately — knowing when NOT to spend a capped
retry is part of the product.
"""

from simulator.engine import day_of_month

CODE_TO_CAUSE = {
    "U30_DEBIT_FAILED_FUNDS": "INSUFFICIENT_FUNDS",
    "U68_BANK_UNAVAILABLE": "BANK_DOWNTIME",
    "U16_LIMIT_EXCEEDED": "LIMIT_EXCEEDED",
    "U28_TECHNICAL_DECLINE": "TECHNICAL_DECLINE",
    "CHECKOUT_ABANDONED": "USER_DROPPED",
    "MANDATE_PAUSED": "MANDATE_PAUSED",
    "MANDATE_REVOKED": "HARD_DECLINE",
    "CARD_BLOCKED": "HARD_DECLINE",
}

RATIONALE = {
    "INSUFFICIENT_FUNDS": "Debit bounced on day {dom} of the month — balance likely low before the salary credit lands on the 1st.",
    "BANK_DOWNTIME": "{bank} returned an unavailability code — outages of this class typically clear within hours.",
    "LIMIT_EXCEEDED": "Per-day debit limit breached at {bank}; the limit resets at midnight.",
    "TECHNICAL_DECLINE": "Transient technical decline at {bank}; an ordinary re-presentation usually clears it.",
    "USER_DROPPED": "Customer abandoned checkout — a silent retry cannot help; a human-visible nudge might.",
    "MANDATE_PAUSED": "Customer paused the mandate; only consented re-activation is appropriate.",
    "HARD_DECLINE": "Instrument revoked or blocked — unrecoverable by policy; closing without spending any retry.",
}


def diagnose(case: dict) -> tuple:
    """Return (root_cause, rationale)."""
    cause = CODE_TO_CAUSE.get(case["failure_code"], "TECHNICAL_DECLINE")
    text = RATIONALE[cause].format(
        dom=day_of_month(case["created_at"]), bank=case["bank"]
    )
    return cause, text
