#!/usr/bin/env bash
set -euo pipefail

echo "===> Loading environment from ./environment.sh"
# shellcheck source=/dev/null
source ./environment.sh

echo "Exported variables:"
echo "NEBIUS_TENANT_ID: ${NEBIUS_TENANT_ID:-<not set>}"
echo "NEBIUS_PROJECT_ID: ${NEBIUS_PROJECT_ID:-<not set>}"
echo "NEBIUS_REGION: ${NEBIUS_REGION:-<not set>}"
echo "NEBIUS_VPC_SUBNET_ID: ${NEBIUS_VPC_SUBNET_ID:-<not set>}"

echo "===> Setting Nebius parent-id to project: $NEBIUS_PROJECT_ID"
nebius config set parent-id "$NEBIUS_PROJECT_ID"

SA_NAME="terraform-sa"

# Try to create the service account
set +e
CREATE_JSON=$(nebius iam service-account create \
  --name "$SA_NAME" \
  --format json)
RC=$?
set -e

if [[ $RC -eq 0 ]]; then
  # Successfully created → parse ID
  NB_SA_ID=$(printf '%s' "$CREATE_JSON" | jq -r '.metadata.id')
else
  # Creation failed → assume it already exists and fetch by name
  echo "Service account exists → resolving ID with get-by-name..."
  NB_SA_ID=$(nebius iam service-account get-by-name \
      --name "$SA_NAME" \
      --parent-id "$NEBIUS_PROJECT_ID" \
      --format json | jq -r '.metadata.id')
fi

# Validate
if [[ -z "$NB_SA_ID" || "$NB_SA_ID" == "null" ]]; then
  echo "ERROR: could not get service account ID"
  exit 1
fi

echo "Service account ID: $NB_SA_ID"
export NB_SA_ID

NB_ADMINS_GROUP_ID=$(nebius iam group get-by-name \
  --name admins --parent-id "$NEBIUS_TENANT_ID" --format json \
  | jq -r '.metadata.id')
export NB_ADMINS_GROUP_ID

nebius iam group-membership create \
  --parent-id "$NB_ADMINS_GROUP_ID" \
  --member-id "$NB_SA_ID" || true

echo "===> Generating auth keys (will overwrite existing local files if present)..."
mkdir -p ~/.nebius/authkey
export NB_AUTHKEY_PRIVATE_PATH=~/.nebius/authkey/private.pem
export NB_AUTHKEY_PUBLIC_PATH=~/.nebius/authkey/public.pem

openssl genrsa -out "$NB_AUTHKEY_PRIVATE_PATH" 4096
openssl rsa -in "$NB_AUTHKEY_PRIVATE_PATH" \
  -outform PEM -pubout -out "$NB_AUTHKEY_PUBLIC_PATH"

echo "===> Uploading public key to Nebius..."
NB_AUTHKEY_PUBLIC_ID=$(nebius iam auth-public-key create \
  --account-service-account-id "$NB_SA_ID" \
  --data "$(cat "$NB_AUTHKEY_PUBLIC_PATH")" \
  --format json | jq -r '.metadata.id')
export NB_AUTHKEY_PUBLIC_ID

echo "Public key ID: $NB_AUTHKEY_PUBLIC_ID"

echo "===> Running terraform (directory: ./terraform)"
terraform -chdir=terraform init
terraform -chdir=terraform validate
terraform -chdir=terraform apply

echo "✔ Bootstrap complete: Nebius SA + authkey + terraform applied."
