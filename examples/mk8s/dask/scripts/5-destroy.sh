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
NB_SA_ID=$(nebius iam service-account get-by-name \
  --name "$SA_NAME" \
  --parent-id "$NEBIUS_PROJECT_ID" \
  --format json | jq -r '.metadata.id')

echo "Service account ID: $NB_SA_ID"
export NB_SA_ID

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

echo "===> Deleting images"
REGISTRY_ID="$(terraform -chdir=terraform output -raw registry_id)"
export REGISTRY_ID
nebius registry image list "{\"parent_id\":\"$REGISTRY_ID\"}" \
  --format json | jq -r '.items[].id' | while read -r id; do
    nebius registry image delete "{\"id\":\"$id\"}"
done

echo "===> Running terraform destroy (directory: ./terraform)"
terraform -chdir=terraform destroy

echo "✔ Destroy complete."
