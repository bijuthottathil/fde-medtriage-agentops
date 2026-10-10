#!/usr/bin/env bash
# Interactive triage tester: type a patient message, see the agent's decision.
#   scripts/ask.sh                       # talks to http://localhost:8080 (make mk-ui / port-forward)
#   URL=http://localhost:8000 scripts/ask.sh
# Each new non-emergency message costs ~$0.001 when the app runs on Bedrock; repeats are cached.
set -uo pipefail
URL="${URL:-http://localhost:8080}"

if ! curl -s -m 5 "$URL/healthz" >/dev/null; then
  echo "Can't reach $URL. Start the tunnel first, e.g.:"
  echo "  kubectl -n medtriage port-forward svc/medtriage-canary 8080:80"
  exit 1
fi
echo "Connected: $(curl -s "$URL/healthz")"
echo "Patients: P1001 67 CHF | P1002 34 healthy | P1003 72 AFib | P1004 55 HTN | P1005 8 asthma"
echo "          P1006 45 COPD | P1007 61 T1 diabetes | P1008 29 depression | P1009 40 | P1010 51"
echo "Press Enter on an empty message to quit."

PID="P1003"
while true; do
  echo
  read -r -p "Patient ID [$PID]: " in && [[ -n "$in" ]] && PID="$in"
  read -r -p "Message: " MSG
  [[ -z "$MSG" ]] && break
  BODY=$(jq -cn --arg p "$PID" --arg m "$MSG" '{patient_id: $p, message: $m}')
  curl -s -m 60 "$URL/triage" -H 'content-type: application/json' -d "$BODY" | jq -r '
    "  label:      \(.label)",
    "  queue:      \(.queue)",
    "  confidence: \(.confidence)",
    "  models:     \((.trace.models_used // []) | if length == 0 then "none (red-flag rule or error)" else join(" -> ") end)",
    "  cost:       $\(.trace.cost_usd // 0)   latency: \(.trace.latency_ms // "-") ms",
    "  rationale:  \(.rationale)"'
done
