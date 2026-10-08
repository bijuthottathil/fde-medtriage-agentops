"""Offline eval pipeline: golden dataset -> agent -> metrics -> gate -> MLflow.

Usage: python -m eval.run_eval --prompt-version v2 [--mlflow]
Exit code 1 if the release gate fails (CI blocks the deploy).
"""
import argparse
import csv
import json
import os
import statistics
import sys
from collections import Counter

import yaml

from app import config
from app.agent import RANK, TriageAgent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt-version", default=None)
    ap.add_argument("--data", default="data/patient_messages.csv")
    ap.add_argument("--out", default="eval_report.json")
    ap.add_argument("--mlflow", action="store_true")
    args = ap.parse_args()

    agent = TriageAgent(prompt_version=args.prompt_version)
    rows = list(csv.DictReader(open(args.data)))
    results, lat, costs, escalations = [], [], [], 0
    for r in rows:
        out = agent.run(r["message"], r["patient_id"])
        t = out["trace"]
        lat.append(t["latency_ms"]); costs.append(t["cost_usd"])
        escalations += len(t["models_used"]) > 1
        results.append({"id": r["id"], "expected": r["expected_label"], "got": out["label"]})

    def recall(labels):
        rel = [x for x in results if x["expected"] in labels]
        hit = [x for x in rel if RANK[x["got"]] >= min(RANK[l] for l in labels)]
        return len(hit) / len(rel) if rel else 1.0

    metrics = {
        "accuracy": sum(x["expected"] == x["got"] for x in results) / len(results),
        "emergency_recall": recall(["EMERGENCY"]),
        "urgent_or_higher_recall": recall(["EMERGENCY", "URGENT"]),
        "under_triage_count": sum(RANK[x["got"]] < RANK[x["expected"]] for x in results),
        "avg_cost_usd": statistics.mean(costs),
        "p95_latency_ms": sorted(lat)[int(0.95 * (len(lat) - 1))],
        "escalation_rate": escalations / len(results),
    }
    th = yaml.safe_load(open("eval/thresholds.yaml"))
    gate = {
        "emergency_recall": metrics["emergency_recall"] >= th["emergency_recall_min"],
        "urgent_or_higher_recall": metrics["urgent_or_higher_recall"] >= th["urgent_or_higher_recall_min"],
        "accuracy": metrics["accuracy"] >= th["accuracy_min"],
        "avg_cost_usd": metrics["avg_cost_usd"] <= th["avg_cost_usd_max"],
        "p95_latency_ms": metrics["p95_latency_ms"] <= th["p95_latency_ms_max"],
    }
    passed = all(gate.values())
    misses = [x for x in results if x["expected"] != x["got"]]
    report = {"prompt_version": agent.prompt_version, "provider": agent.provider,
              "app_version": config.APP_VERSION, "n": len(results), "metrics": metrics,
              "gate": gate, "passed": passed, "misses": misses,
              "confusion": Counter(f'{x["expected"]}->{x["got"]}' for x in misses)}
    json.dump(report, open(args.out, "w"), indent=2)

    if args.mlflow:  # Experiment tracking + model registry
        import mlflow
        mlflow.set_tracking_uri(os.getenv("MLFLOW_TRACKING_URI", "http://mlflow.mlops:5000"))
        mlflow.set_experiment("medtriage-agent")
        with mlflow.start_run(run_name=f"prompt-{agent.prompt_version}-{config.APP_VERSION[:7]}"):
            mlflow.log_params({"prompt_version": agent.prompt_version, "provider": agent.provider,
                               "small_model": config.SMALL_MODEL, "large_model": config.LARGE_MODEL,
                               "escalate_below": config.ESCALATE_BELOW_CONFIDENCE,
                               "dataset_dvc_md5": os.getenv("DATASET_MD5", "local"),
                               "git_sha": config.APP_VERSION})
            mlflow.log_metrics({k: float(v) for k, v in metrics.items()})
            mlflow.log_artifact(args.out)
            mlflow.log_artifact(f"prompts/triage/{agent.prompt_version}.yaml")
            mlflow.set_tag("gate_passed", str(passed))

    print(json.dumps({"prompt": agent.prompt_version, **metrics, "PASSED": passed}, indent=2))
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
