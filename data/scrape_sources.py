"""Scrape the original public data that grounds the synthetic generator.

npci.org.in sits behind an F5 JavaScript challenge, so plain HTTP fails.
This module reaches NPCI's own JSON API (the one its React frontend calls,
endpoints recovered from the site's JS bundle) through the monid CLI's
context.dev scraping proxy, which handles the challenge server-side
(~$0.001 per request, billed to the monid workspace).

Fetched sources (all aggregate statistics — no customer data):
  - UPI bank-wise remitter performance (top-50 banks: volume share,
    approved %, business-decline %, technical-decline %)
  - UPI monthly product statistics (volume / value)
  - UPI daily statistics for one recent month (day-of-month volume cycle)
  - UPI downtime / incident list for one recent month

Derived output: data/real_distributions.json
  - bank_mix           -> generator BANK_WEIGHTS (renormalized over its banks)
  - bank_profiles      -> per-bank approved/bd/td rates (for citation)
  - decline_split      -> business vs technical share of declined debits
  - failure_mix        -> generator FAILURE_MIX weights, re-split so the
                          mandate-decline mass follows the real bd:td ratio
                          (with a 10% technical floor so downtime recovery
                          stays demonstrable — documented honesty tradeoff)

Raw API responses are cached in data/raw_sources/; delete a file or pass
--fresh to refetch.

Usage:  python3 -m data.scrape_sources [--fresh]
"""

import datetime
import json
import os
import subprocess
import sys
import tempfile

BASE = "https://www.npci.org.in"
RAW_DIR = os.path.join(os.path.dirname(__file__), "raw_sources")
OUT_PATH = os.path.join(os.path.dirname(__file__), "real_distributions.json")

# Generator bank code -> substring to match in NPCI's bank names.
BANK_MATCH = {
    "SBI": "state bank of india",
    "HDFC": "hdfc",
    "ICICI": "icici",
    "AXIS": "axis",
    "KOTAK": "kotak",
    "PNB": "punjab national",
}

# Mandate-decline codes in the generator, split by decline class.
BUSINESS_CODES = ["U30_DEBIT_FAILED_FUNDS", "U16_LIMIT_EXCEEDED"]
TECHNICAL_CODES = ["U28_TECHNICAL_DECLINE", "U68_BANK_UNAVAILABLE"]
TECHNICAL_FLOOR = 0.10  # min technical share of the decline mass

MONTHS_SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MONTHS_FULL = ["January", "February", "March", "April", "May", "June",
               "July", "August", "September", "October", "November", "December"]


def fetch_json(name: str, url: str, fresh: bool = False):
    """Fetch a JSON API response through the monid scraping proxy, cached."""
    os.makedirs(RAW_DIR, exist_ok=True)
    cache = os.path.join(RAW_DIR, f"{name}.json")
    if not fresh and os.path.exists(cache):
        with open(cache) as f:
            return json.load(f)
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        query = json.dumps({"url": url, "maxAgeMs": 0})
        proc = subprocess.run(
            ["monid", "run", "-p", "context.dev", "-e", "/web/scrape/html",
             "--query", query, "-w", "120", "-o", tmp_path],
            env={**os.environ, "NO_COLOR": "1"},
            capture_output=True, text=True, timeout=180,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"monid run failed for {url}: {proc.stderr.strip()[:300]}")
        with open(tmp_path) as f:
            wrapper = json.load(f)
        try:
            payload = json.loads(wrapper["html"])
        except (json.JSONDecodeError, KeyError):
            raise RuntimeError(f"non-JSON response for {url}: "
                               f"{str(wrapper.get('html'))[:120]}")
        if payload.get("status") != 200:
            raise RuntimeError(f"NPCI API error for {url}: {str(payload)[:200]}")
        with open(cache, "w") as f:
            json.dump(payload, f, indent=1)
        print(f"  fetched {name}")
        return payload
    finally:
        os.unlink(tmp_path)


def recent_months(n=5):
    """(short, full, year) for the last n complete months, newest first."""
    today = datetime.date.today().replace(day=1)
    out = []
    for _ in range(n):
        today = (today - datetime.timedelta(days=1)).replace(day=1)
        out.append((MONTHS_SHORT[today.month - 1], MONTHS_FULL[today.month - 1],
                    str(today.year)))
    return out


def try_months(name, url_fmt, fresh, months=None):
    """Fetch the newest month that has data; NPCI publishes ~1 month behind."""
    for short, full, year in months or recent_months():
        try:
            data = fetch_json(f"{name}_{short}{year}", url_fmt(short, full, year), fresh)
            return data, f"{short} {year}"
        except RuntimeError as e:
            print(f"  {name} {short} {year}: no data ({str(e)[:80]})")
    raise RuntimeError(f"no recent month with data for {name}")


def table_rows(payload):
    res = payload["data"]["results"]
    return res["tableDetail"] if isinstance(res, dict) else res


def pct(s):
    return float(str(s).replace("%", "").replace(",", ""))


def main():
    fresh = "--fresh" in sys.argv

    print("1/4 bank-wise remitter performance (top-50)")
    banks_raw, banks_month = try_months(
        "upi_top50_remitter",
        lambda s, f, y: (f"{BASE}/api/ecosystem-statistics/get-statistics"
                         f"?product_name=UPI&tab_name=top50-member&type_name=remitter"
                         f"&year={y}&month={s}&page_no=1&sort_by=asc&size=60"),
        fresh)
    bank_rows = table_rows(banks_raw)

    print("2/4 monthly product statistics")
    fy = datetime.date.today().year - (datetime.date.today().month < 4)
    monthly = []
    for yr in (f"{fy - 1}-{str(fy)[2:]}", f"{fy}-{str(fy + 1)[2:]}"):
        try:
            p = fetch_json(
                f"upi_monthly_{yr}",
                f"{BASE}/api/product-statistic/tab/detail?product_name=upi"
                f"&tab_name=upi-monthly-statistics&year_range={yr}"
                f"&excel_type=monthly&page_no=1&page_size=20", fresh)
            monthly += [{"month": r["month"],
                         "volume_mn": pct(r["total_volume"]),
                         "value_cr": pct(r["total_value"])} for r in table_rows(p)]
        except RuntimeError as e:
            print(f"  monthly {yr}: {str(e)[:80]}")

    print("3/4 daily statistics (salary-day cycle evidence)")
    daily = []
    try:
        p, daily_month = try_months(
            "upi_daily",
            lambda s, f, y: (f"{BASE}/api/product-statistic/tab/detail?product_name=upi"
                             f"&tab_name=upi-daily-statistics&excel_type=daily"
                             f"&month={f}&year={y}&page_no=1&page_size=40"),
            fresh)
        daily = [{"date": r.get("npci_day"),
                  "volume_mn": pct(r.get("upi_volume_mn") or 0),
                  "value_cr": pct(r.get("upi_value_crores") or 0)}
                 for r in table_rows(p) if r.get("npci_day") != "Total"]
    except RuntimeError as e:
        daily_month = None
        print(f"  daily stats unavailable: {str(e)[:80]}")

    print("4/4 downtime / incidents")
    downtime = []
    try:
        p, downtime_month = try_months(
            "upi_downtime",
            lambda s, f, y: (f"{BASE}/api/product-statistic/tab/detail?product_name=upi"
                             f"&tab_name=upi-downtime&excel_type=downtime"
                             f"&month={f}&year={y}&page_no=1&page_size=100"),
            fresh)
        downtime = table_rows(p)
    except RuntimeError as e:
        downtime_month = None
        print(f"  downtime unavailable: {str(e)[:80]}")

    # ---- derive distributions ----------------------------------------------
    profiles, volumes = {}, {}
    for row in bank_rows:
        name = row.get("upi_remitter_banks", "")
        for code, needle in BANK_MATCH.items():
            if needle in name.lower():
                profiles[code] = {
                    "npci_name": name,
                    "volume_mn": pct(row["total_volume_in_mn"]),
                    "approved_pct": pct(row["approved_percent"]),
                    "business_decline_pct": pct(row["bd_percent"]),
                    "technical_decline_pct": pct(row["td_percent"]),
                }
                volumes[code] = profiles[code]["volume_mn"]
    missing = set(BANK_MATCH) - set(profiles)
    if missing:
        print(f"  WARNING: banks not found in top-50: {missing}")
    total_vol = sum(volumes.values())
    bank_mix = {b: round(v / total_vol, 4) for b, v in volumes.items()}

    # Volume-weighted decline split across matched banks.
    bd = sum(p["business_decline_pct"] * p["volume_mn"] for p in profiles.values())
    td = sum(p["technical_decline_pct"] * p["volume_mn"] for p in profiles.values())
    tech_share = max(td / (bd + td), TECHNICAL_FLOOR)
    decline_split = {"business": round(1 - tech_share, 4),
                     "technical": round(tech_share, 4),
                     "raw_business": round(bd / (bd + td), 4),
                     "raw_technical": round(td / (bd + td), 4),
                     "technical_floor_applied": td / (bd + td) < TECHNICAL_FLOOR}

    # Re-split the generator's mandate-decline mass by the real ratio,
    # preserving each code's share within its class and all other weights.
    from data.generator import FAILURE_MIX
    biz_w = {c: w for c, _, w in FAILURE_MIX if c in BUSINESS_CODES}
    tec_w = {c: w for c, _, w in FAILURE_MIX if c in TECHNICAL_CODES}
    mass = sum(biz_w.values()) + sum(tec_w.values())
    failure_mix = []
    for code, source, w in FAILURE_MIX:
        if code in biz_w:
            w = mass * decline_split["business"] * biz_w[code] / sum(biz_w.values())
        elif code in tec_w:
            w = mass * decline_split["technical"] * tec_w[code] / sum(tec_w.values())
        failure_mix.append([code, source, round(w, 4)])

    out = {
        "fetched_at": datetime.date.today().isoformat(),
        "source": {
            "api": f"{BASE}/api/ecosystem-statistics/get-statistics (top50-member/remitter), "
                   f"{BASE}/api/product-statistic/tab/detail",
            "human_pages": [f"{BASE}/product/ecosystem-statistics/upi",
                            f"{BASE}/product/upi/product-statistics"],
            "bank_table_month": banks_month,
            "daily_month": daily_month,
            "downtime_month": downtime_month,
            "note": "Aggregate public statistics from NPCI; no customer data. "
                    "Technical-decline floor of 10% applied to keep the "
                    "downtime-recovery scenario demonstrable (raw split kept "
                    "alongside).",
        },
        "bank_mix": bank_mix,
        "bank_profiles": profiles,
        "decline_split": decline_split,
        "failure_mix": failure_mix,
        "monthly_stats": monthly,
        "daily_stats": daily,
        "downtime_incidents": downtime,
    }
    with open(OUT_PATH, "w") as f:
        json.dump(out, f, indent=1)
    print(f"\nwrote {OUT_PATH}")
    print(f"  bank_mix ({banks_month}): {bank_mix}")
    print(f"  decline_split: business={decline_split['business']} "
          f"technical={decline_split['technical']} "
          f"(raw {decline_split['raw_business']}/{decline_split['raw_technical']})")


if __name__ == "__main__":
    main()
