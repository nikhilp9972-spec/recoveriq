"""Razorpay test-mode integration (live path — Day 8-9 of the build plan).

Batch evaluation uses the simulator; this module is the real-world path that
proves platform integration in the demo:

  * create_payment_link(): a real test-mode Payment Link for nudges
  * webhook payloads (payment.failed / payment_link.expired) are normalised
    by to_recovery_case() into the same RecoveryCase shape the batch path uses

Set RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET (test-mode keys from the
dashboard). Uses raw HTTPS so there is no SDK dependency.
"""

import base64
import json
import os
import urllib.request

BASE = "https://api.razorpay.com/v1"


def _auth_header() -> str:
    kid = os.environ["RAZORPAY_KEY_ID"]
    secret = os.environ["RAZORPAY_KEY_SECRET"]
    token = base64.b64encode(f"{kid}:{secret}".encode()).decode()
    return f"Basic {token}"


def enabled() -> bool:
    return bool(os.environ.get("RAZORPAY_KEY_ID"))


def create_payment_link(case: dict, message: str) -> dict:
    """Real test-mode Payment Link; the URL goes into the nudge."""
    body = json.dumps({
        "amount": case["amount"] * 100,  # paise
        "currency": "INR",
        "description": f"Payment recovery — {case['merchant_name']}",
        "reference_id": case["case_id"],
        "notes": {"root_cause": case.get("root_cause", ""),
                  "agent": "paisa-wapas"},
    }).encode()
    req = urllib.request.Request(
        f"{BASE}/payment_links", data=body, method="POST",
        headers={"Authorization": _auth_header(),
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)


def to_recovery_case(webhook_payload: dict) -> dict:
    """Normalise a payment.failed webhook into the batch-path case shape."""
    p = webhook_payload["payload"]["payment"]["entity"]
    return {
        "case_id": p["id"],
        "source": "live",
        "merchant_id": "live_merchant",
        "merchant_name": "Live (test-mode) merchant",
        "customer_id": p.get("email") or p.get("contact") or "unknown",
        "amount": p["amount"] // 100,
        "bank": p.get("bank") or "UNKNOWN",
        "failure_code": p.get("error_code") or "U28_TECHNICAL_DECLINE",
        "created_at": 0,
        "customer": {"responsiveness": 0.5, "opts_out_on_contact": False},
    }
