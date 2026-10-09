"""Record evidence that a canary release behaves as designed, on minikube.

Starts a release, then every 15s records: rollout step/weight, pods per version, and live
traffic + errors per version from Prometheus. Ends with every AnalysisRun measurement and the
final outcome, written to proof/canary-<tag>-<time>.md.

  python scripts/canary_proof.py --tag v7          # healthy: expect promotion to 100%
  python scripts/canary_proof.py --tag bad --bad   # broken: expect abort + rollback
"""
import argparse
import json
import subprocess
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

NS = "medtriage"
PROM_PORT = 9091


def kubectl_json(*args):
    return json.loads(subprocess.check_output(["kubectl", "-n", NS, *args, "-o", "json"]))


def prom(query):
    url = f"http://localhost:{PROM_PORT}/api/v1/query?" + urllib.parse.urlencode({"query": query})
    try:
        res = json.load(urllib.request.urlopen(url, timeout=5))["data"]["result"]
    except Exception:
        return {}
    return {r["metric"].get("app_version", "?"): float(r["value"][1]) for r in res}


def snapshot(t0):
    ro = kubectl_json("get", "rollout", "medtriage")
    st = ro["status"]
    steps = ro["spec"]["strategy"]["canary"]["steps"]
    idx = st.get("currentStepIndex", len(steps))
    weight = next((s["setWeight"] for s in reversed(steps[:idx + 1]) if "setWeight" in s), 0)
    pods = kubectl_json("get", "pods", "-l", "app=medtriage")["items"]
    per_ver = {}
    for p in pods:
        if p["metadata"].get("deletionTimestamp"):
            continue
        ver = p["spec"]["containers"][0]["image"].split(":")[-1]
        ready = all(c.get("ready") for c in p["status"].get("containerStatuses", []))
        per_ver[ver] = per_ver.get(ver, 0) + ready
    reqs = prom("sum by (app_version)(rate(triage_requests_total[1m]))")
    errs = prom("sum by (app_version)(rate(triage_errors_total[1m]))")
    total = sum(reqs.values()) + sum(errs.values()) or 1
    traffic = {v: round(100 * (reqs.get(v, 0) + errs.get(v, 0)) / total)
               for v in set(reqs) | set(errs) if reqs.get(v, 0) + errs.get(v, 0) > 0.01}
    return {
        "t": f"{int(time.time() - t0) // 60}:{int(time.time() - t0) % 60:02d}",
        "phase": st.get("phase", "?"),
        "step": f"{idx}/{len(steps)} ({weight}%)",
        "pods": ", ".join(f"{v}×{n}" for v, n in sorted(per_ver.items())),
        "traffic": ", ".join(f"{v} {pct}%" for v, pct in sorted(traffic.items())),
        "errors": ", ".join(f"{v} {e:.2f}/s" for v, e in sorted(errs.items()) if e > 0.001) or "none",
        "message": st.get("message", ""),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--bad", action="store_true")
    args = ap.parse_args()

    pf = subprocess.Popen(["kubectl", "-n", "monitoring", "port-forward", "svc/prometheus-server",
                           f"{PROM_PORT}:80"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        before = kubectl_json("get", "rollout", "medtriage")
        stable_before = before["spec"]["template"]["spec"]["containers"][0]["image"].split(":")[-1]
        started = datetime.now()
        subprocess.check_call(["scripts/minikube_release.sh", args.tag] + (["--bad"] if args.bad else []),
                              stdout=subprocess.DEVNULL)
        t0, rows = time.time(), []
        print(f"{'time':>5} {'phase':<12} {'step':<12} {'pods per version':<28} {'live traffic share':<26} errors")
        while True:
            s = snapshot(t0)
            rows.append(s)
            print(f"{s['t']:>5} {s['phase']:<12} {s['step']:<12} {s['pods']:<28} {s['traffic']:<26} {s['errors']}",
                  flush=True)
            if len(rows) > 2 and s["phase"] in ("Healthy", "Degraded"):
                break
            time.sleep(15)

        runs = [r for r in kubectl_json("get", "analysisrun")["items"]
                if datetime.fromisoformat(r["metadata"]["creationTimestamp"].replace("Z", "+00:00"))
                .astimezone().replace(tzinfo=None) >= started.replace(microsecond=0)]
        final = kubectl_json("get", "rollout", "medtriage")
        stable_after = final["spec"]["template"]["spec"]["containers"][0]["image"].split(":")[-1]
        live = sorted({p["spec"]["containers"][0]["image"].split(":")[-1]
                       for p in kubectl_json("get", "pods", "-l", "app=medtriage")["items"]
                       if not p["metadata"].get("deletionTimestamp")})
    finally:
        pf.terminate()

    phase = final["status"].get("phase")
    expected = "Degraded" if args.bad else "Healthy"
    out = [f"# Canary proof: `{args.tag}` ({'broken' if args.bad else 'healthy'} release)", "",
           f"- Started: {started:%Y-%m-%d %H:%M:%S}",
           f"- Stable before: `{stable_before}` · Released: `{args.tag}`",
           f"- Final phase: **{phase}** (expected {expected}) {'✅' if phase == expected else '❌'}",
           f"- Message: {final['status'].get('message', '') or '-'}",
           f"- Pods serving traffic after: {', '.join(f'`{v}`' for v in live)}", "",
           "## Timeline (every 15s)", "",
           "| t | phase | step | pods per version | live traffic share (Prometheus) | errors |",
           "|---|---|---|---|---|---|"]
    out += [f"| {r['t']} | {r['phase']} | {r['step']} | {r['pods']} | {r['traffic'] or '-'} | {r['errors']} |"
            for r in rows]
    out += ["", "## Analysis runs (canary pods only)", ""]
    for r in sorted(runs, key=lambda r: r["metadata"]["creationTimestamp"]):
        out.append(f"**{r['metadata']['name']}**: {r['status'].get('phase')}")
        for m in r["status"].get("metricResults", []):
            vals = ", ".join(x.get("value", "?").strip("[]")[:6] for x in m.get("measurements", []))
            out.append(f"- {m['name']}: {m['phase']} — measurements [{vals}]")
        out.append("")
    report = "\n".join(out)
    path = Path("proof") / f"canary-{args.tag}-{started:%H%M%S}.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text(report)
    print("\n" + report + f"\n\nSaved: {path}")


if __name__ == "__main__":
    main()
