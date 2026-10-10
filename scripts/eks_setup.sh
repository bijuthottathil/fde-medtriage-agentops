#!/usr/bin/env bash
# One-time AWS bootstrap for MedTriage on EKS Auto Mode (installation.md, level 3, steps 2-6).
# Creates billable resources. Review each step before running in a real account.
#   GH_REPO=<github-user>/fde-medtriage-agentops scripts/eks_setup.sh
# Tear down with scripts/eks_teardown.sh.
set -euo pipefail
cd "$(dirname "$0")/.."

: "${GH_REPO:?set GH_REPO=<github-user>/<repo>}"
export AWS_REGION="${AWS_REGION:-us-east-1}" CLUSTER="${CLUSTER:-medtriage-eks}"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
BUCKET="medtriage-$ACCOUNT_ID"
WORK=$(mktemp -d)
echo "Account $ACCOUNT_ID, region $AWS_REGION, cluster $CLUSTER, bucket $BUCKET, repo $GH_REPO"

# --- Step 2. Cluster (~20 min). Auto Mode = managed Karpenter, EBS CSI and ALB controller.
eksctl get cluster --name "$CLUSTER" --region "$AWS_REGION" >/dev/null 2>&1 || \
  eksctl create cluster --name "$CLUSTER" --region "$AWS_REGION" \
    --zones "${AWS_REGION}a,${AWS_REGION}b" --enable-auto-mode --with-oidc

# --- Step 3. Platform pieces.
kubectl create namespace argo-rollouts --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -n argo-rollouts -f https://github.com/argoproj/argo-rollouts/releases/download/v1.7.2/install.yaml
kubectl -n argo-rollouts rollout status deploy/argo-rollouts --timeout=300s

helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null 2>&1 || true
helm upgrade --install prometheus prometheus-community/prometheus -n monitoring --create-namespace \
  --set alertmanager.enabled=false --set prometheus-pushgateway.enabled=false \
  --set server.global.scrape_interval=15s --set server.persistentVolume.enabled=false

# The HPA needs metrics-server; Auto Mode doesn't include it.
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/download/v0.7.2/components.yaml

# Default StorageClass (Auto Mode has none) for MLflow's volume, and the internal ALB class.
kubectl apply -f - <<'EOF'
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: auto-ebs-sc
  annotations: {storageclass.kubernetes.io/is-default-class: "true"}
provisioner: ebs.csi.eks.amazonaws.com
volumeBindingMode: WaitForFirstConsumer
parameters: {type: gp3, encrypted: "true"}
---
apiVersion: eks.amazonaws.com/v1
kind: IngressClassParams
metadata: {name: alb}
spec: {scheme: internal}
---
apiVersion: networking.k8s.io/v1
kind: IngressClass
metadata: {name: alb}
spec:
  controller: eks.amazonaws.com/alb
  parameters: {apiGroup: eks.amazonaws.com, kind: IngressClassParams, name: alb}
EOF

# --- Step 4. S3 bucket (MLflow artifacts) and ECR repository.
aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null || \
  aws s3api create-bucket --bucket "$BUCKET" --region "$AWS_REGION"
aws s3api put-public-access-block --bucket "$BUCKET" --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws ecr describe-repositories --repository-names medtriage --region "$AWS_REGION" >/dev/null 2>&1 || \
  aws ecr create-repository --repository-name medtriage --region "$AWS_REGION" \
    --image-scanning-configuration scanOnPush=true

# --- Step 5. Pod IAM roles (IRSA, no static keys).
cat > "$WORK/bedrock.json" <<'EOF'
{"Version":"2012-10-17","Statement":[{"Effect":"Allow",
 "Action":["bedrock:InvokeModel","bedrock:Converse"],"Resource":"*"}]}
EOF
cat > "$WORK/mlflow-s3.json" <<EOF
{"Version":"2012-10-17","Statement":[
 {"Effect":"Allow","Action":["s3:ListBucket"],"Resource":"arn:aws:s3:::$BUCKET"},
 {"Effect":"Allow","Action":["s3:GetObject","s3:PutObject","s3:DeleteObject"],
  "Resource":"arn:aws:s3:::$BUCKET/mlflow-artifacts/*"}]}
EOF
for P in medtriage-bedrock-invoke:bedrock.json medtriage-mlflow-s3:mlflow-s3.json; do
  NAME=${P%%:*} FILE=${P#*:}
  aws iam get-policy --policy-arn "arn:aws:iam::$ACCOUNT_ID:policy/$NAME" >/dev/null 2>&1 || \
    aws iam create-policy --policy-name "$NAME" --policy-document "file://$WORK/$FILE" >/dev/null
done
eksctl create iamserviceaccount --cluster "$CLUSTER" --region "$AWS_REGION" \
  --namespace medtriage --name medtriage --role-name medtriage-bedrock-invoke --role-only \
  --attach-policy-arn "arn:aws:iam::$ACCOUNT_ID:policy/medtriage-bedrock-invoke" --approve
eksctl create iamserviceaccount --cluster "$CLUSTER" --region "$AWS_REGION" \
  --namespace mlops --name mlflow --role-name medtriage-mlflow-s3 --role-only \
  --attach-policy-arn "arn:aws:iam::$ACCOUNT_ID:policy/medtriage-mlflow-s3" --approve

# --- Step 6. GitHub Actions deploy role (OIDC). The CD job uses `environment: prod`.
OIDC_ARN="arn:aws:iam::$ACCOUNT_ID:oidc-provider/token.actions.githubusercontent.com"
aws iam get-open-id-connect-provider --open-id-connect-provider-arn "$OIDC_ARN" >/dev/null 2>&1 || \
  aws iam create-open-id-connect-provider --url https://token.actions.githubusercontent.com \
    --client-id-list sts.amazonaws.com >/dev/null
cat > "$WORK/gh-trust.json" <<EOF
{"Version":"2012-10-17","Statement":[{"Effect":"Allow",
 "Principal":{"Federated":"$OIDC_ARN"},
 "Action":"sts:AssumeRoleWithWebIdentity",
 "Condition":{"StringEquals":{"token.actions.githubusercontent.com:aud":"sts.amazonaws.com",
   "token.actions.githubusercontent.com:sub":"repo:$GH_REPO:environment:prod"}}}]}
EOF
aws iam get-role --role-name medtriage-gh-deploy >/dev/null 2>&1 || \
  aws iam create-role --role-name medtriage-gh-deploy --assume-role-policy-document "file://$WORK/gh-trust.json" >/dev/null
aws iam attach-role-policy --role-name medtriage-gh-deploy \
  --policy-arn arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryPowerUser
aws iam put-role-policy --role-name medtriage-gh-deploy --policy-name eks-describe \
  --policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":"eks:DescribeCluster","Resource":"*"}]}'
aws eks create-access-entry --cluster-name "$CLUSTER" --region "$AWS_REGION" \
  --principal-arn "arn:aws:iam::$ACCOUNT_ID:role/medtriage-gh-deploy" >/dev/null 2>&1 || true
# Demo scope: cluster admin. For a real customer, scope to the medtriage/mlops namespaces.
aws eks associate-access-policy --cluster-name "$CLUSTER" --region "$AWS_REGION" \
  --principal-arn "arn:aws:iam::$ACCOUNT_ID:role/medtriage-gh-deploy" \
  --policy-arn arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy \
  --access-scope type=cluster >/dev/null
gh secret set AWS_DEPLOY_ROLE_ARN --repo "$GH_REPO" --body "arn:aws:iam::$ACCOUNT_ID:role/medtriage-gh-deploy"

rm -rf "$WORK"
echo "Bootstrap done. Next: gh variable set DEPLOY_ENABLED --repo $GH_REPO --body true, then push to main."
