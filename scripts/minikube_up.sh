#!/usr/bin/env bash
# Local equivalent of eks_setup.sh: minikube + Argo Rollouts + Prometheus + MLflow + app.
set -euo pipefail
cd "$(dirname "$0")/.."

minikube status >/dev/null 2>&1 || minikube start --driver=docker --cpus=4 --memory=6144 --addons=metrics-server

kubectl create namespace argo-rollouts --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -n argo-rollouts -f https://github.com/argoproj/argo-rollouts/releases/download/v1.7.2/install.yaml
kubectl -n argo-rollouts rollout status deploy/argo-rollouts --timeout=180s

helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null 2>&1 || true
helm upgrade --install prometheus prometheus-community/prometheus -n monitoring --create-namespace \
  --set alertmanager.enabled=false --set prometheus-pushgateway.enabled=false \
  --set server.global.scrape_interval=15s --set server.persistentVolume.enabled=false

PHASE=$(kubectl -n medtriage get rollout medtriage -o jsonpath='{.status.phase}' 2>/dev/null || true)
if [[ "$PHASE" == "Progressing" || "$PHASE" == "Paused" ]]; then
  echo "A canary is in progress (phase=$PHASE). Re-applying now would change it mid-flight."
  echo "Wait for it to finish, or reset with: make mk-rollback"
  exit 1
fi

minikube image build -t medtriage:local --build-opt=build-arg=GIT_SHA=local .
# Re-running must not start a canary back to :local, so keep whatever image is already live.
CUR=$(kubectl -n medtriage get rollout medtriage -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null || true)
kubectl kustomize k8s-minikube | sed "s#image: medtriage:local\$#image: ${CUR:-medtriage:local}#" | kubectl apply -f -
kubectl argo rollouts status medtriage -n medtriage --timeout 12m
