#!/usr/bin/env bash
# Roll back the triage agent. Because image tag = git SHA and the prompt is baked into
# the image, rolling back the image rolls back code + prompt + model config together.
set -euo pipefail
NS=medtriage
REV="${1:-}"

kubectl argo rollouts abort medtriage -n "$NS" || true     # stop any in-flight canary
if [[ -n "$REV" ]]; then
  kubectl argo rollouts undo medtriage -n "$NS" --to-revision="$REV"
else
  kubectl argo rollouts undo medtriage -n "$NS"
fi
kubectl argo rollouts status medtriage -n "$NS" --timeout 10m
kubectl argo rollouts get rollout medtriage -n "$NS"
