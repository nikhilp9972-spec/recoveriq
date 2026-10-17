"""Claude integration — deliberately a small, replaceable component.

The agent's decisions come from the bandit; Claude is used only where
language is the job:
  1. classifying long-tail failure codes that the deterministic map misses
  2. writing the plain-English rationale stored on each audit event
  3. drafting Hinglish nudge messages / voice scripts (template-constrained)

Offline by default so `make evaluate` needs no API key. Set ANTHROPIC_API_KEY
and USE_CLAUDE=1 to go live.
"""

import os

MODEL = "claude-sonnet-5"

NUDGE_TEMPLATE = (
    "Namaste {name}! {merchant} ki aapki payment (Rs.{amount}) complete nahi "
    "ho paayi. Koi baat nahi — yeh secure link se 2 minute mein ho jayegi: "
    "{payment_link}. Agar aap ye messages nahi chahte, reply STOP."
)


def enabled() -> bool:
    return bool(os.environ.get("USE_CLAUDE")) and bool(os.environ.get("ANTHROPIC_API_KEY"))


def classify_unknown_code(failure_code: str, raw_context: dict) -> str:
    """LLM fallback for codes outside diagnose.CODE_TO_CAUSE."""
    if not enabled():
        return "TECHNICAL_DECLINE"  # conservative offline default
    import anthropic
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=MODEL, max_tokens=50,
        messages=[{"role": "user", "content":
                   "Map this Indian payment failure code to exactly one of: "
                   "INSUFFICIENT_FUNDS, BANK_DOWNTIME, LIMIT_EXCEEDED, "
                   "TECHNICAL_DECLINE, USER_DROPPED, MANDATE_PAUSED, HARD_DECLINE. "
                   f"Code: {failure_code}. Context: {raw_context}. "
                   "Reply with only the label."}])
    label = msg.content[0].text.strip()
    valid = {"INSUFFICIENT_FUNDS", "BANK_DOWNTIME", "LIMIT_EXCEEDED",
             "TECHNICAL_DECLINE", "USER_DROPPED", "MANDATE_PAUSED", "HARD_DECLINE"}
    return label if label in valid else "TECHNICAL_DECLINE"


def draft_nudge(case: dict, payment_link: str) -> str:
    """Hinglish nudge. Template-constrained: the LLM personalises tone only;
    amounts, links, and the opt-out line are injected, never generated."""
    base = NUDGE_TEMPLATE.format(
        name=case["customer_id"][-4:], merchant=case["merchant_name"],
        amount=case["amount"], payment_link=payment_link)
    if not enabled():
        return base
    import anthropic
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=MODEL, max_tokens=200,
        messages=[{"role": "user", "content":
                   "Rewrite this Hinglish payment reminder to be warmer and "
                   "more natural. Keep the amount, the link placeholder, and "
                   "the STOP opt-out line EXACTLY as they are:\n" + base}])
    return msg.content[0].text.strip()
