#!/usr/bin/env bash
# Local equivalent of cd.yml: build image:TAG inside minikube and start a canary.
#   scripts/minikube_release.sh v2        # healthy release -> promotes to 100%
#   scripts/minikube_release.sh bad --bad # canary errors -> analysis fails -> auto rollback
set -euo pipefail
cd "$(dirname "$0")/.."
TAG="${1:-$(date +%H%M%S)}"
NS=medtriage

minikube image build -t "medtriage:$TAG" --build-opt=build-arg=GIT_SHA="$TAG" .
if [[ "${2:-}" == "--bad" ]]; then
  # Simulate a broken config release: unknown LLM provider -> every LLM call raises.
  kubectl -n $NS patch rollout medtriage --type=json -p "[
    {\"op\":\"replace\",\"path\":\"/spec/template/spec/containers/0/image\",\"value\":\"medtriage:$TAG\"},
    {\"op\":\"add\",\"path\":\"/spec/template/spec/containers/0/env\",\"value\":[{\"name\":\"LLM_PROVIDER\",\"value\":\"broken\"}]}]"
else
  kubectl -n $NS patch rollout medtriage --type=json -p '[{"op":"remove","path":"/spec/template/spec/containers/0/env"}]' 2>/dev/null || true
  kubectl argo rollouts set image medtriage app="medtriage:$TAG" -n $NS
fi
echo "Watch: kubectl argo rollouts get rollout medtriage -n $NS --watch"
