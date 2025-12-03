# create a VPC network with a subnet using Nebius CLI
set -euo pipefail

# Required variables
NETWORK_NAME="reza-test-network"
NETWORK_CIDR="10.50.0.0/15"
POOL_NAME="${NETWORK_NAME}-pool"

# Check dependencies
command -v nebius >/dev/null 2>&1 || { echo >&2 "nebius CLI not found. Aborting."; exit 1; }
command -v jq >/dev/null 2>&1 || { echo >&2 "jq not found. Aborting."; exit 1; }

# Ensure required Nebius environment variables are set
if [[ -z "${NEBIUS_PROJECT_ID:-}" ]]; then
  echo "Error: NEBIUS_PROJECT_ID is not set. Export it (see service-account.sh)." >&2
  exit 1
fi

# Create the address pool
echo "Creating address pool '$POOL_NAME' with CIDR '$NETWORK_CIDR'..."
# build the JSON array expected by --cidrs flag, e.g. [{"cidr":"10.88.0.0/13"}]
CIDRS_JSON=$(jq -nc --arg cidr "$NETWORK_CIDR" '[{cidr:$cidr}]')
NB_POOL_ID=$(nebius vpc pool create \
  --name "$POOL_NAME" \
  --parent-id "$NEBIUS_PROJECT_ID" \
  --version ipv4 \
  --visibility private \
  --cidrs "$CIDRS_JSON" \
  --format json | jq -r ".metadata.id")

if [[ -z "$NB_POOL_ID" || "$NB_POOL_ID" == "null" ]]; then
  echo "Failed to create address pool."
  exit 1
fi

echo "Address pool created with ID: $NB_POOL_ID"

# Create the network
echo "Creating network '$NETWORK_NAME'..."
# build JSON array expected by --ipv-4-private-pools-pools, e.g. [{"id":"pool-id"}]
NETWORK_POOLS_JSON=$(jq -nc --arg id "$NB_POOL_ID" '[{id:$id}]')
NB_NETWORK_ID=$(nebius vpc network create \
  --name "$NETWORK_NAME" \
  --parent-id "$NEBIUS_PROJECT_ID" \
  --ipv-4-private-pools-pools "$NETWORK_POOLS_JSON" \
  --format json | jq -r ".metadata.id")

if [[ -z "$NB_NETWORK_ID" || "$NB_NETWORK_ID" == "null" ]]; then
  echo "Failed to create network."
  exit 1
fi

echo "Network created with ID: $NB_NETWORK_ID"

# Create a subnet in the network using the address pool
echo "Creating subnet in network using address pool..."
NB_SUBNET_ID=$(nebius vpc subnet create \
  --name "${NETWORK_NAME}-subnet" \
  --network-id "$NB_NETWORK_ID" \
  --ipv-4-private-pools-use-network-pools true \
  --parent-id "$NEBIUS_PROJECT_ID" \
  --format json | jq -r ".metadata.id")

if [[ -z "$NB_SUBNET_ID" || "$NB_SUBNET_ID" == "null" ]]; then
  echo "Failed to create subnet."
  exit 1
fi

echo "Subnet created with ID: $NB_SUBNET_ID in network: $NETWORK_NAME"