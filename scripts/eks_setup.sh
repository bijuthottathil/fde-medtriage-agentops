#!/usr/bin/env bash
# One-time cluster bootstrap. Review each step before running in a real account.
set -euo pipefail
CLUSTER=medtriage-eks
REGION=us-east-1

# 1. Cluster (Auto Mode gives managed Karpenter + ALB controller; drop the flag to self-manage)
eksctl create cluster --name $CLUSTER --region $REGION --enable-auto-mode

# 2. ECR repo for images
aws ecr create-repository --repository-name medtriage --region $REGION \
  --image-scanning-configuration scanOnPush=true || true

# 3. Argo Rollouts (canary + automated rollback)
kubectl create namespace argo-rollouts || true
kubectl apply -n argo-rollouts -f https://github.com/argoproj/argo-rollouts/releases/latest/download/install.yaml

# 4. Prometheus (feeds canary analysis + cost/latency dashboards)
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm upgrade --install prometheus prometheus-community/prometheus -n monitoring --create-namespace

# 5. MLflow tracking server
kubectl apply -f k8s/40-mlflow.yaml

# 6. App
kubectl apply -f k8s/00-namespace-config.yaml -f k8s/20-analysis.yaml -f k8s/10-rollout.yaml -f k8s/30-autoscaling.yaml
echo "Done. Next: create IAM role medtriage-bedrock-invoke (IRSA) and the GitHub OIDC deploy role."
