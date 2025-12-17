#!/bin/bash

# Set the following environment variables:
export NEBIUS_TENANT_ID='tenant-e00h6h46ywdm9fnqaf'
export NEBIUS_PROJECT_ID='project-e00kmt05pr00d4dnxgz81d'
export NEBIUS_REGION='eu-north1'

# IAM token
unset NEBIUS_IAM_TOKEN
export NEBIUS_IAM_TOKEN=$(nebius iam get-access-token)

# VPC subnet
NEBIUS_VPC_SUBNET_ID=$(nebius vpc subnet list \
  --parent-id "${NEBIUS_PROJECT_ID}" \
  --format json \
  | jq -r '.items[0].metadata.id')
export NEBIUS_VPC_SUBNET_ID

# Terraform variables
export TF_VAR_iam_token="${NEBIUS_IAM_TOKEN}"
export TF_VAR_tenant_id="${NEBIUS_TENANT_ID}"
export TF_VAR_parent_id="${NEBIUS_PROJECT_ID}"
export TF_VAR_region="${NEBIUS_REGION}"
export TF_VAR_subnet_id="${NEBIUS_VPC_SUBNET_ID}"

# Exported variables
echo "Exported variables:"
echo "NEBIUS_TENANT_ID: ${NEBIUS_TENANT_ID}"
echo "NEBIUS_PROJECT_ID: ${NEBIUS_PROJECT_ID}"
echo "NEBIUS_REGION: ${NEBIUS_REGION}"
echo "NEBIUS_VPC_SUBNET_ID: ${NEBIUS_VPC_SUBNET_ID}"
