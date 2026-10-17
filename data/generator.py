"""Synthetic failed-payment generator.

Produces the frozen evaluation batch: failed recurring debits (UPI Autopay /
e-mandate), abandoned checkouts, and overdue payment links for Indian
merchants. Distributions are grounded in public sources cited in
data/SOURCES.md — bank mix, failure-mode mix, and the salary-day balance
cycle are the load-bearing assumptions.

Usage:  python3 -m data.generator          (writes data/failed_payments.jsonl)
"""

import json
import os
import numpy as np

SEED = 42
N_CASES = 1000

# Absolute timeline in hours. Months are 30 days; salary credits land on
# day-of-month 1 (absolute days 0, 30, 60). Cases arrive across days 10-40
# so every case sees at least one salary day before the day-66 horizon.
CREATED_DAY_RANGE = (10, 40)
HORIZON_HOURS = 66 * 24

BANKS = ["HDFC", "ICICI", "SBI", "AXIS", "KOTAK", "PNB"]
BANK_WEIGHTS = [0.22, 0.18, 0.28, 0.12, 0.10, 0.10]

# (failure_code, source_type, weight) — mix approximates published UPI/mandate
# decline patterns: business declines (mostly insufficient balance) dominate,
# technical declines and bank downtime follow. See data/SOURCES.md.
FAILURE_MIX = [
    ("U30_DEBIT_FAILED_FUNDS", "mandate", 0.40),
    ("U68_BANK_UNAVAILABLE", "mandate", 0.12),
    ("U16_LIMIT_EXCEEDED", "mandate", 0.08),
    ("U28_TECHNICAL_DECLINE", "mandate", 0.12),
    ("CHECKOUT_ABANDONED", "checkout", 0.15),
    ("MANDATE_PAUSED", "mandate", 0.05),
    ("MANDATE_REVOKED", "mandate", 0.03),
    ("CARD_BLOCKED", "mandate", 0.05),
]

# When data/real_distributions.json exists (written by data/scrape_sources.py
# from live NPCI statistics), it overrides the assumed bank mix and re-splits
# the mandate-decline weights by the published business:technical ratio.
_REAL = os.path.join(os.path.dirname(__file__), "real_distributions.json")
if os.path.exists(_REAL):
    with open(_REAL) as _f:
        _real = json.load(_f)
    BANK_WEIGHTS = [_real["bank_mix"][b] for b in BANKS]
    BANK_WEIGHTS = [w / sum(BANK_WEIGHTS) for w in BANK_WEIGHTS]
    FAILURE_MIX = [tuple(row) for row in _real["failure_mix"]]

MERCHANTS = [
    ("m_edtech", "EdTech subscriptions", (299, 4999)),
    ("m_ott", "OTT streaming", (99, 999)),
    ("m_fitness", "Gym & wellness", (499, 2999)),
    ("m_saas", "B2B SaaS invoices", (2000, 200000)),
    ("m_d2c", "D2C storefront", (250, 8000)),
]
MERCHANT_WEIGHTS = [0.25, 0.30, 0.15, 0.10, 0.20]


def generate(seed: int = SEED, n: int = N_CASES) -> list:
    rng = np.random.default_rng(seed)
    cases = []
    for i in range(n):
        code, source, _ = FAILURE_MIX[
            rng.choice(len(FAILURE_MIX), p=[w for _, _, w in FAILURE_MIX])
        ]
        m_id, m_name, (lo, hi) = MERCHANTS[
            rng.choice(len(MERCHANTS), p=MERCHANT_WEIGHTS)
        ]
        # log-uniform amounts: many small subscriptions, a long tail of big invoices
        amount = int(np.exp(rng.uniform(np.log(lo), np.log(hi))) // 10 * 10) + 9
        day = rng.integers(CREATED_DAY_RANGE[0], CREATED_DAY_RANGE[1])
        hour = int(rng.choice(24, p=_hour_profile()))
        cases.append({
            "case_id": f"case_{i:05d}",
            "source": source,
            "merchant_id": m_id,
            "merchant_name": m_name,
            "customer_id": f"cust_{rng.integers(0, 10**6):06d}",
            "amount": amount,
            "bank": BANKS[rng.choice(len(BANKS), p=BANK_WEIGHTS)],
            "failure_code": code,
            "created_at": int(day * 24 + hour),
            # CRM-style traits: the SIMULATOR uses these as ground truth;
            # the agent never reads them — it must learn behaviour from outcomes.
            "customer": {
                "responsiveness": round(float(rng.beta(2.0, 2.5)), 3),
                "opts_out_on_contact": bool(rng.random() < 0.03),
            },
        })
    return cases


def _hour_profile():
    # Debits and checkouts cluster in daytime; a small NACH-style early-morning
    # presentation spike at 6-8 AM.
    w = np.ones(24)
    w[6:9] = 3.0
    w[9:22] = 2.0
    w[1:5] = 0.3
    return w / w.sum()


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(__file__), "failed_payments.jsonl")
    cases = generate()
    with open(out, "w") as f:
        for c in cases:
            f.write(json.dumps(c) + "\n")
    total = sum(c["amount"] for c in cases)
    print(f"wrote {len(cases)} cases, Rs.{total:,} at risk -> {out}")
