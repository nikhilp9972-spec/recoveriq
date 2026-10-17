"""Export eval results + audit ledger into dashboard/data.js.

Run after `make evaluate`:  python3 -m dashboard.export
The dashboard is a static page; this keeps it dependency-free and demo-safe
(no server, no network, works offline in the pitch video).
"""

import json
import os
import sqlite3

ROOT = os.path.join(os.path.dirname(__file__), "..")
ARM_ORDER = ["RETRY_NOW", "RETRY_NEXT_MORNING", "RETRY_SALARY_DAY",
             "NUDGE_LINK", "VOICE_CALL"]
CAUSE_ORDER = ["INSUFFICIENT_FUNDS", "BANK_DOWNTIME", "LIMIT_EXCEEDED",
               "TECHNICAL_DECLINE", "USER_DROPPED", "MANDATE_PAUSED"]


def main():
    with open(os.path.join(ROOT, "eval", "results.json")) as f:
        results = json.load(f)
    db = sqlite3.connect(os.path.join(ROOT, "ledger.sqlite"))
    db.row_factory = sqlite3.Row

    # --- policy comparison + downsampled cumulative-recovery curves
    policies = {}
    for name, m in results.items():
        policies[name] = {k: m[k] for k in
                          ("recovered", "recovery_rate_pct", "cases_recovered",
                           "attempts", "action_cost",
                           "rupees_recovered_per_attempt", "total_at_risk")}
        policies[name]["curve"] = m["curve"][::10] + [m["curve"][-1]]

    # --- what the bandit learned (posterior-mean matrix)
    snap = results["paisa_wapas_agent"]["bandit_posterior_means"]
    learned = [[snap.get(f"{c}|{a}") for a in ARM_ORDER] for c in CAUSE_ORDER]

    # --- guardrail + exception summaries from the audit log
    guardrails = dict(db.execute(
        "SELECT event, COUNT(*) FROM audit_log WHERE actor='guardrail' "
        "GROUP BY event").fetchall())
    exceptions = {}
    for e in results["paisa_wapas_agent"]["exceptions"]:
        exceptions[e["reason"]] = exceptions.get(e["reason"], 0) + 1

    # --- case explorer: a curated mix of stories, full audit timeline each
    ids = []
    for q in (
        "SELECT case_id FROM cases WHERE outcome='RECOVERED' ORDER BY amount DESC LIMIT 6",
        "SELECT c.case_id FROM cases c JOIN decisions d USING(case_id) "
        "  WHERE d.arm='RETRY_SALARY_DAY' AND c.outcome='RECOVERED' LIMIT 6",
        "SELECT DISTINCT case_id FROM audit_log WHERE event='CUSTOMER_OPT_OUT' LIMIT 3",
        "SELECT DISTINCT case_id FROM audit_log WHERE event='HUMAN_APPROVAL_REQUIRED' LIMIT 3",
        "SELECT DISTINCT case_id FROM audit_log WHERE event='CONTACT_WINDOW_CLAMP' LIMIT 3",
        "SELECT case_id FROM cases WHERE close_reason='hard_decline' LIMIT 3",
        "SELECT case_id FROM cases WHERE close_reason='caps_exhausted' ORDER BY amount DESC LIMIT 3",
        "SELECT case_id FROM cases WHERE outcome='RECOVERED' AND root_cause='USER_DROPPED' LIMIT 4",
    ):
        ids += [r[0] for r in db.execute(q)]
    seen, cases = set(), []
    for cid in ids:
        if cid in seen:
            continue
        seen.add(cid)
        row = db.execute("SELECT * FROM cases WHERE case_id=?", (cid,)).fetchone()
        events = [dict(r) for r in db.execute(
            "SELECT actor, event, detail, at_hour FROM audit_log "
            "WHERE case_id=? ORDER BY at_hour, rowid", (cid,))]
        for ev in events:
            ev["detail"] = json.loads(ev["detail"])
        cases.append({**dict(row), "events": events})
    cases.sort(key=lambda c: -c["amount"])

    data = {
        "batch": {"n": results["paisa_wapas_agent"]["cases"],
                  "at_risk": results["paisa_wapas_agent"]["total_at_risk"]},
        "policies": policies,
        "learned": {"causes": CAUSE_ORDER, "arms": ARM_ORDER, "matrix": learned},
        "guardrails": guardrails,
        "exceptions": exceptions,
        "cases": cases,
    }
    out = os.path.join(os.path.dirname(__file__), "data.js")
    with open(out, "w") as f:
        f.write("const DATA = " + json.dumps(data) + ";\n")
    print(f"wrote {out} ({len(cases)} explorer cases)")


if __name__ == "__main__":
    main()
