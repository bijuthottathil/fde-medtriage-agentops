#!/usr/bin/env bash
# Delete everything scripts/eks_setup.sh created, to stop the charges.
#   GH_REPO=<github-user>/fde-medtriage-agentops scripts/eks_teardown.sh
# Deletes the S3 bucket INCLUDING all MLflow artifacts in it.
set -uo pipefail     # no -e: keep going if something is already gone

: "${GH_REPO:?set GH_REPO=<github-user>/<repo>}"
export AWS_REGION="${AWS_REGION:-us-east-1}" CLUSTER="${CLUSTER:-medtriage-eks}"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

gh variable set DEPLOY_ENABLED --repo "$GH_REPO" --body false
# Delete the Ingress first so the ALB it created is removed with it.
kubectl delete ingress medtriage -n medtriage --ignore-not-found --wait=true
# Delete MLflow's volume claim (after its pod) so the EBS volume is deleted, not orphaned:
# deleting the cluster alone leaves dynamically provisioned volumes behind.
kubectl delete deploy mlflow -n mlops --ignore-not-found --wait=true
kubectl delete pvc mlflow-data -n mlops --ignore-not-found --wait=true
eksctl delete cluster --name "$CLUSTER" --region "$AWS_REGION" --wait
aws ecr delete-repository --repository-name medtriage --force --region "$AWS_REGION"
aws s3 rb "s3://medtriage-$ACCOUNT_ID" --force

aws iam detach-role-policy --role-name medtriage-gh-deploy \
  --policy-arn arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryPowerUser
aws iam delete-role-policy --role-name medtriage-gh-deploy --policy-name eks-describe
aws iam delete-role --role-name medtriage-gh-deploy
for R in medtriage-bedrock-invoke medtriage-mlflow-s3; do
  # eksctl usually removes these with the cluster; clean up if it didn't.
  aws iam detach-role-policy --role-name "$R" --policy-arn "arn:aws:iam::$ACCOUNT_ID:policy/$R" 2>/dev/null
  aws iam delete-role --role-name "$R" 2>/dev/null
  aws iam delete-policy --policy-arn "arn:aws:iam::$ACCOUNT_ID:policy/$R"
done
gh secret delete AWS_DEPLOY_ROLE_ARN --repo "$GH_REPO"
# The GitHub OIDC provider is left in place: other repos may use it. Remove it with
#   aws iam delete-open-id-connect-provider --open-id-connect-provider-arn \
#     arn:aws:iam::$ACCOUNT_ID:oidc-provider/token.actions.githubusercontent.com

echo "Teardown done. Check: eksctl get cluster --region $AWS_REGION ; and Cost Explorer tomorrow."
