# mysterybox-bridge

`mysterybox-bridge` is a standalone webhook integration that syncs Nebius
MysteryBox values into Kubernetes `Secret` objects through
[External Secrets Operator (ESO)](https://external-secrets.io/latest/).

Python requirement: `3.12+`.

## Usage Modes

1. Standalone in any Kubernetes project that uses ESO.
2. Integrated via [`services/nebius-cxcli`](../nebius-cxcli/) (Flux manifests rendered automatically).

## High-Level Flow

1. ESO reconciles an `ExternalSecret`.
2. ESO calls the webhook URL from a `ClusterSecretStore`.
3. `mysterybox-bridge` receives `(secret, key, version)` and authenticates to Nebius API.
4. Bridge resolves secret name/ID and reads the payload entry from MysteryBox.
5. Bridge returns `{ "value": "..." }`; ESO writes/updates the target Kubernetes Secret.

## Standalone Use (Manual Workflow)

When you do not use `nebius-cxcli`, you install ESO and create all required
resources manually.

### 1. Install ESO via Helm

Use the official ESO Helm installation flow:
[Install with Helm](https://external-secrets.io/latest/introduction/getting-started/#installing-with-helm)
using chart repo [`https://charts.external-secrets.io`](https://charts.external-secrets.io).

```bash
helm repo add external-secrets https://charts.external-secrets.io
helm repo update

kubectl create namespace external-secrets --dry-run=client -o yaml | kubectl apply -f -

helm upgrade --install external-secrets external-secrets/external-secrets \
  --namespace external-secrets
```

### 2. Deploy mysterybox-bridge webhook chart

```bash
helm upgrade --install mysterybox-webhook ./charts/mysterybox-webhook \
  --namespace external-secrets \
  --create-namespace \
  --set image.repository=quay.io/nebius/mysterybox-bridge \
  --set image.tag=<tag>
```

### 3. Create required Secrets (manual, no automation)

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: nebius-mysterybox-auth
  namespace: external-secrets
type: Opaque
stringData:
  NEBIUS_PROJECT_ID: "<project-id>"
  NEBIUS_SA_ID: "<service-account-id>"
  NEBIUS_AUTH_PUBLIC_KEY_ID: "<auth-public-key-id>"
  NEBIUS_AUTH_PRIVATE_KEY_PEM: |
    -----BEGIN PRIVATE KEY-----
    ...
    -----END PRIVATE KEY-----
```

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: mysterybox-bridge-webhook-auth
  namespace: external-secrets
  labels:
    external-secrets.io/type: webhook
type: Opaque
stringData:
  token: "<shared-webhook-token>"
```

### 4. Create ClusterSecretStore CR

```yaml
apiVersion: external-secrets.io/v1
kind: ClusterSecretStore
metadata:
  name: nebius-mysterybox
spec:
  provider:
    webhook:
      url: >-
        http://mysterybox-webhook.external-secrets.svc.cluster.local:8080
        /v1/secret?secret={{ .remoteRef.key }}&key={{ .remoteRef.property }}
        &version={{ .remoteRef.version }}
      method: GET
      result:
        jsonPath: "$.value"
      headers:
        X-MBX-Request: "{{ .bridgeAuth.token }}"
      secrets:
        - name: bridgeAuth
          secretRef:
            name: mysterybox-bridge-webhook-auth
            namespace: external-secrets
```

### 5. Create ExternalSecret CR

```yaml
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: app-runtime-secrets
  namespace: default
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: nebius-mysterybox
    kind: ClusterSecretStore
  target:
    name: app-runtime-secrets
    creationPolicy: Owner
    deletionPolicy: Retain
  data:
    - secretKey: API_KEY
      remoteRef:
        # MysteryBox secret name or mbsec-* id.
        key: my-app-runtime
        property: API_KEY
```

### 6. Verify

```bash
kubectl -n external-secrets get pods
kubectl -n external-secrets logs deploy/mysterybox-webhook --tail=100
kubectl -n default get externalsecret app-runtime-secrets
kubectl -n default get secret app-runtime-secrets -o yaml
```

See:

- `charts/mysterybox-webhook/README.md`
- `charts/jwt-minter/README.md`
- `docs/design.md`
- [external-secrets.io/latest](https://external-secrets.io/latest/)

## Optional Chart Features

- TLS is optional (`tls.enabled=false` by default).
- If you enable `tls.certManager.enabled=true`, cert-manager must be installed
  in the cluster (CRDs + controller).
- `ServiceMonitor` is optional (`monitoring.serviceMonitor.enabled=false` by default)
  and requires Prometheus Operator CRDs.
- Optional `NetworkPolicy`, `PodDisruptionBudget`, and configurable HPA are available.
- When using `nebius-cxcli`, required platform apps are installed end-to-end via
  automation based on your enabled configuration.

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

## Versioning Policy

`mysterybox-bridge` uses related but independent version layers:

- **Application version** (Python package):
  - SemVer from Git tags via `setuptools-scm`.
  - Tag format: `mysterybox-bridge-vMAJOR.MINOR.PATCH`.
- **Container image version** (runtime artifact):
  - Release tag publishes immutable image tags:
    - `sha-<shortsha>`
    - `<MAJOR.MINOR.PATCH>`
    - `<MAJOR.MINOR.PATCH>-g<shortsha>`
  - PR and `main` validation build the image in CI but do not publish registry tags.
- **Helm chart version** (deployment packaging):
  - `Chart.yaml.version` is chart SemVer and changes when chart packaging changes.
  - `Chart.yaml.appVersion` tracks the default app/image SemVer.

Recommended production deployment strategy:

- Pin images by digest (`image.digest`) instead of mutable tags.
- Treat chart version and app version as separate release lifecycles.

## Image Workflows

- CI workflow: `.github/workflows/mysterybox-bridge-webhook-ci.yml`
  - validates Python checks on PRs, `main`, and manual runs
  - performs a local Docker build smoke test without pushing
- Publish workflow: `.github/workflows/mysterybox-bridge-image.yml`
  - triggers only from pushed `mysterybox-bridge-v*` tags
- Important:
  - pushing `mysterybox-bridge-vX.Y.Z` triggers image publish immediately
  - a later `main` push is not required for tag-triggered release images
- Registry target: `quay.io/nebius/mysterybox-bridge`
- Required GitHub secret: `QUAY_MYSTERYBOX` (recommended format: `username:token`)
- Optional GitHub variable: `QUAY_MYSTERYBOX_USERNAME` (used when secret contains token only)

## Release Helper Script

Use `publish-image.sh` to standardize changelog prep and release tagging.

```bash
cd services/mysterybox-bridge

# Update CHANGELOG.md for release and commit it (pushes current branch by default)
./publish-image.sh --prep 0.1.0

# Create and push release tag (main-only by default)
./publish-image.sh --publish 0.1.0
```

Notes:

- `--publish` defaults to clean, up-to-date `main` for safety.
- `--prep` now also requires a clean worktree before it rewrites `CHANGELOG.md`.
- `--allow-non-main` exists as an explicit override when you intentionally
  want to release outside `main`, but the tagged commit must still already be
  in `origin/main` history or the workflow will reject it.
- `--prep` is for working branches before PR merge.
- `--publish` pushes the release tag immediately, which triggers
  image publish from the tagged commit.
- `--publish` refuses to tag if `CHANGELOG.md` does not already contain the
  matching `mysterybox-bridge-vX.Y.Z` release heading.
- Recommended governance:
  1. run `--prep` on a branch
  2. merge PR to `main`
  3. run `--publish` on clean synced `main`
- Tag format is `mysterybox-bridge-vMAJOR.MINOR.PATCH`.
- Changelog is tracked in `CHANGELOG.md` with `## [Unreleased]` at the top.

## Chart CI (PR)

- Workflow: `.github/workflows/mysterybox-bridge-charts-ci.yml`
  (`mysterybox-bridge-chart-lint-and-tests`)
- Trigger: PRs that touch `services/mysterybox-bridge/charts/**`
- Checks:
  - `ct lint` (chart-testing)
  - chart rendering snapshot tests (`all_tests.py`)

### Run chart checks locally

```bash
# Lint charts
helm lint services/mysterybox-bridge/charts/mysterybox-webhook
helm lint services/mysterybox-bridge/charts/jwt-minter

# Verify chart rendering snapshots
python3 services/mysterybox-bridge/charts/tests/all_tests.py

# Refresh snapshots after intentional rendering changes
python3 services/mysterybox-bridge/charts/tests/all_tests.py --update
```

`all_tests.py` validates rendered manifests for these scenarios:

- `mysterybox-webhook` default values.
- `mysterybox-webhook` with `hpa.enabled=true`.
- `mysterybox-webhook` with `tls.enabled=true` and cert-manager mode.
- `mysterybox-webhook` with `monitoring.serviceMonitor.enabled=true`.
- `jwt-minter` with `enabled=true`.

`--update` re-renders those scenarios and overwrites files in
`services/mysterybox-bridge/charts/tests/snapshots/`. Use it only when chart output
changes are intentional, then review and commit the updated snapshots.

### Common CI failures (and fixes)

- `ct lint` fails with `chart version not ok. Needs a version bump!`:
  - You changed chart files but did not bump chart packaging version.
  - Fix: bump `services/mysterybox-bridge/charts/mysterybox-webhook/Chart.yaml` `version`
    (for example `0.2.0` -> `0.2.1`) and commit.
- `render-snapshot-tests` fails with `mismatch: .../snapshots/...yaml`:
  - Rendered chart output changed (often after version/template/value changes) but
    snapshots were not refreshed.
  - Fix:

```bash
python3 services/mysterybox-bridge/charts/tests/all_tests.py --update
python3 services/mysterybox-bridge/charts/tests/all_tests.py
```

Then review and commit updated files under
`services/mysterybox-bridge/charts/tests/snapshots/`.

## Webhook Local Dev

```bash
cd services/mysterybox-bridge/webhook
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -m ruff check src tests
python -m pytest -q
```

## Project Structure

```text
services/mysterybox-bridge/
  CHANGELOG.md                               # Keep-a-Changelog release history.
  README.md                                  # Top-level overview and standalone workflow.
  publish-image.sh                           # Changelog prep and release-tag helper.
  docs/
    design.md                                # Detailed architecture and design decisions.
  webhook/
    Dockerfile                               # Webhook container image build.
    Makefile                                 # Local install/lint/test/build helpers.
    pyproject.toml                           # Python package metadata and tool config.
    README.md                                # Webhook-only local development notes.
    src/
      mysterybox_bridge/
        __init__.py                          # Package init and version export.
        __main__.py                          # CLI entrypoint (`python -m mysterybox_bridge`).
        app.py                               # Flask routes (`/healthz`, `/readyz`, `/v1/secret`).
        config.py                            # Environment settings and validation.
        iam.py                               # Nebius SDK auth initialization (token or SA key).
        cache.py                             # Thread-safe in-memory TTL cache.
        mysterybox_client.py                 # Secret-id resolution and payload read logic.
    tests/
      test_app.py                            # Webhook HTTP contract tests.
      test_iam.py                            # SDK provider auth path tests.
      test_config.py                         # Env parsing/validation tests.
      test_mysterybox_client.py              # Payload parsing behavior tests.
  charts/
    ct.yaml                                  # Chart-testing configuration.
    mysterybox-webhook/
      Chart.yaml                             # Chart metadata/version.
      values.yaml                            # Default deployment values.
      values.schema.json                     # Values schema validation.
      README.md                              # Install and configuration guide.
      templates/
        _helpers.tpl                         # Shared template helpers.
        serviceaccount.yaml                  # ServiceAccount resource.
        service.yaml                         # ClusterIP Service for webhook endpoint.
        deployment.yaml                      # Webhook Deployment (gunicorn/env/probes/security).
        certificate.yaml                     # Optional cert-manager Issuer/Certificate.
        servicemonitor.yaml                  # Optional Prometheus ServiceMonitor.
        pdb.yaml                             # Optional PodDisruptionBudget.
        networkpolicy.yaml                   # Optional ingress NetworkPolicy.
        hpa.yaml                             # Optional HorizontalPodAutoscaler.
        NOTES.txt                            # Post-install notes.
    jwt-minter/
      Chart.yaml                             # Optional chart metadata/version.
      values.yaml                            # Optional chart defaults.
      values.schema.json                     # Optional chart schema validation.
      README.md                              # Optional chart usage and scope.
      templates/
        _helpers.tpl                         # Shared template helpers.
        serviceaccount.yaml                  # ServiceAccount for CronJob.
        role.yaml                            # Role for Secret writes.
        rolebinding.yaml                     # RoleBinding for ServiceAccount.
        cronjob.yaml                         # Optional CronJob scaffold.
        NOTES.txt                            # Post-install notes.
    tests/
      all_tests.py                           # Chart render snapshot test runner.
      snapshots/
        jwt-minter-enabled.yaml              # Snapshot: jwt-minter enabled render.
        mysterybox-webhook-default.yaml      # Snapshot: mysterybox-webhook default render.
        mysterybox-webhook-hpa.yaml          # Snapshot: webhook with HPA enabled.
        mysterybox-webhook-servicemonitor.yaml # Snapshot: webhook with ServiceMonitor enabled.
        mysterybox-webhook-tls-certmanager.yaml # Snapshot: webhook with TLS cert-manager mode.
```
