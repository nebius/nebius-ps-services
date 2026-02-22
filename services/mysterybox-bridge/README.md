# mysterybox-bridge

`mysterybox-bridge` is a standalone webhook integration that syncs Nebius
MysteryBox values into Kubernetes `Secret` objects through
[External Secrets Operator (ESO)](https://external-secrets.io/latest/).

Python requirement: `3.12+`.

## Usage Modes

1. Standalone in any Kubernetes project that uses ESO.
2. Integrated via `services/nebius-cxcli` (Flux manifests rendered automatically).

## High-Level Flow

1. ESO reconciles an `ExternalSecret`.
2. ESO calls the webhook URL from a `ClusterSecretStore`.
3. `mysterybox-bridge` receives `(secret, key, version)` and authenticates to Nebius API.
4. Bridge resolves secret name/ID and reads the payload entry from MysteryBox.
5. Bridge returns `{ "value": "..." }`; ESO writes/updates the target Kubernetes Secret.

## Project Structure

```text
services/mysterybox-bridge/
  docs/
    design.md
  webhook/
    Dockerfile
    Makefile
    pyproject.toml
    README.md
    src/
      mysterybox_bridge/
        __init__.py
        __main__.py
        app.py
        config.py
        iam.py
        cache.py
        mysterybox_client.py
    tests/
      test_app.py
      test_iam.py
  charts/
    mysterybox-webhook/
    jwt-minter/
```

## Webhook Local Dev

```bash
cd services/mysterybox-bridge/webhook
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -m ruff check src tests
python -m pytest -q
```

## Standalone Use

1. Install ESO in your cluster.
2. Build and push bridge image (`quay.io/nebius/mysterybox-bridge:<tag>`).
3. Deploy `charts/mysterybox-webhook`.
4. Create a `ClusterSecretStore` webhook config pointing to `/v1/secret`.
5. Create `ExternalSecret` resources with MysteryBox secret reference + payload key.

See:

- `charts/mysterybox-webhook/README.md`
- `charts/jwt-minter/README.md`
- `docs/design.md`
- [external-secrets.io/latest](https://external-secrets.io/latest/)

## Optional Chart Features

- TLS for webhook service is optional (`tls.enabled=false` by default).
  - Maintenance-friendly option: `tls.certManager.enabled=true` to auto-rotate
    self-signed server certs.
- Prometheus `ServiceMonitor` is optional (`monitoring.serviceMonitor.enabled=false` by default).
  - Requires Prometheus Operator CRDs in the cluster.
- Optional `NetworkPolicy`, `PodDisruptionBudget`, and configurable HPA are available.

## nebius-cxcli Integration

`nebius-cxcli` renders and applies:

- ESO HelmRelease
- Bridge Deployment + Service
- MysteryBox `ClusterSecretStore`
- `ExternalSecret` resources from `config.yaml`

It also seeds required runtime Kubernetes Secrets used by bridge auth.

## Container Image Defaults

- Default image in `nebius-cxcli` schema/template:
  `quay.io/nebius/mysterybox-bridge:latest`

## Image CI Publish

- Workflow: `.github/workflows/mysterybox-bridge-image.yml`
- Trigger: push to `main` with changes under `services/mysterybox-bridge/webhook/**`
- Registry target: `quay.io/nebius/mysterybox-bridge`
- Required GitHub secret: `QUAY_MYSTERYBOX` (recommended format: `username:token`)
- Optional GitHub variable: `QUAY_MYSTERYBOX_USERNAME` (used when secret contains token only)

## Chart CI (PR)

- Workflow: `.github/workflows/mysterybox-bridge-charts-ci.yml`
- Trigger: PRs that touch `services/mysterybox-bridge/charts/**`
- Checks:
  - `ct lint` (chart-testing)
  - golden manifest snapshot tests

### Run chart checks locally

```bash
# Lint charts
helm lint services/mysterybox-bridge/charts/mysterybox-webhook
helm lint services/mysterybox-bridge/charts/jwt-minter

# Verify golden manifests
python3 services/mysterybox-bridge/charts/tests/golden_test.py

# Update golden manifests after intentional chart output changes
python3 services/mysterybox-bridge/charts/tests/golden_test.py --update
```
