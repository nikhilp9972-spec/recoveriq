"""Reproducible evaluation: agent vs. baselines on the frozen batch.

    make evaluate   (or: python3 -m eval.run_eval)

Same batch, same seeded simulator for every policy. Prints the comparison
table, writes eval/results.json (metrics + learning curves + what the bandit
learned) and ledger.sqlite (full audit trail for the agent run).
"""

import json
import os

from data.generator import generate
from simulator.engine import PaymentSimulator
from agent.runner import (AgentPolicy, CronBaseline, ImmediateBaseline,
                          run_policy, write_ledger)

ROOT = os.path.join(os.path.dirname(__file__), "..")
SIM_SEED = 7


def main():
    batch_path = os.path.join(ROOT, "data", "failed_payments.jsonl")
    if os.path.exists(batch_path):
        with open(batch_path) as f:
            cases = [json.loads(line) for line in f]
    else:
        cases = generate()
    cases.sort(key=lambda c: c["created_at"])  # agent learns in arrival order

    results = {}
    agent = AgentPolicy()
    for policy in (ImmediateBaseline(), CronBaseline(), agent):
        metrics, ledger = run_policy(policy, cases, PaymentSimulator(SIM_SEED),
                                     collect_audit=isinstance(policy, AgentPolicy))
        assert metrics["guardrail_violations"] == 0
        results[policy.name] = metrics
        if isinstance(policy, AgentPolicy):
            write_ledger(ledger, os.path.join(ROOT, "ledger.sqlite"))

    results["paisa_wapas_agent"]["bandit_posterior_means"] = agent.bandit.snapshot()

    cols = ["recovered", "recovery_rate_pct", "cases_recovered", "attempts",
            "action_cost", "rupees_recovered_per_attempt"]
    print(f"\n{'policy':<24}" + "".join(f"{c:>28}" for c in cols))
    for name, m in results.items():
        print(f"{name:<24}" + "".join(f"{m[c]:>28,}" for c in cols))

    base = results["baseline_cron_24h"]["recovered"]
    ours = results["paisa_wapas_agent"]["recovered"]
    print(f"\nagent vs cron baseline: +{100 * (ours - base) / base:.0f}% rupees recovered")
    exc = results["paisa_wapas_agent"]["exceptions"]
    print(f"agent exceptions (honest unrecovered list): {len(exc)} cases")
    reasons = {}
    for e in exc:
        reasons[e["reason"]] = reasons.get(e["reason"], 0) + 1
    for r, n in sorted(reasons.items(), key=lambda kv: -kv[1]):
        print(f"  {r}: {n}")

    out = os.path.join(os.path.dirname(__file__), "results.json")
    with open(out, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nwrote {out} and ledger.sqlite")


if __name__ == "__main__":
    main()
