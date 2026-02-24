#!/usr/bin/env bash
set -euo pipefail

# shellcheck source=/dev/null
source ./environment.sh

echo "===> Reading terraform outputs..."
REGISTRY_ID="$(terraform -chdir=terraform output -raw registry_id)"
export REGISTRY_ID

echo "===> Login into Nebius registry..."
nebius registry configure-helper

REGISTRY_PATH="$(echo "$REGISTRY_ID" | cut -d- -f2)"
export REGISTRY_PATH
IMAGE="cr.$NEBIUS_REGION.nebius.cloud/$REGISTRY_PATH/mda-dask:latest"

echo "===> Building Docker image..."
docker buildx build \
  --platform=linux/amd64 \
  -f docker/Dockerfile \
  -t mda-dask \
  --provenance=false \
  --sbom=false \
  .

echo "===> Tagging image as $IMAGE"
docker tag mda-dask "$IMAGE"

echo "===> Pushing image..."
docker push "$IMAGE"

echo "✔ Done: Image pushed to $IMAGE"
