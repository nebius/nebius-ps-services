# mysterybox-bridge Design

## 1. Purpose

`mysterybox-bridge` provides a standalone webhook service that lets
[External Secrets Operator (ESO)](https://external-secrets.io/latest/)
sync Nebius MysteryBox values into native Kubernetes `Secret` objects.

It is intentionally reusable:

- Directly by any Kubernetes/ESO project.
- Indirectly via `services/nebius-cxcli` Flux rendering.

## 2. Architecture

## 2.1 Components

- **ESO** (`ExternalSecret`, `ClusterSecretStore`): triggers secret sync.
- **Webhook bridge service** (`mysterybox-bridge`): receives ESO webhook calls.
- **Nebius SDK client** (inside bridge): authenticates and reads MysteryBox.
- **MysteryBox**: source of secret payload entries.

## 2.2 Runtime Auth Modes

Bridge supports two auth paths to Nebius API:

1. **Service account mode** (preferred for CI/K8s):
   - `NEBIUS_SA_ID`
   - `NEBIUS_AUTH_PUBLIC_KEY_ID`
   - `NEBIUS_AUTH_PRIVATE_KEY_PEM` (or `NEBIUS_AUTH_PRIVATE_KEY_FILE`)
2. **Token mode** (local/manual convenience):
   - `NEBIUS_IAM_TOKEN`

Request-level protection between ESO and bridge is optional and supported with:

- `MYSTERYBOX_WEBHOOK_AUTH_HEADER`
- `MYSTERYBOX_WEBHOOK_AUTH_TOKEN`

## 2.3 Request Flow

1. ESO evaluates an `ExternalSecret`.
2. ESO calls webhook URL (from `ClusterSecretStore`), typically:
   - `/v1/secret?secret=<name-or-id>&key=<payload-key>&version=<optional-version-id>`
3. Bridge authenticates request (optional header check).
4. Bridge resolves secret reference:
   - if `mbsec-*`, use as ID
   - otherwise resolve name -> ID with `SecretService.GetByName`
5. Bridge reads key value from MysteryBox:
   - `PayloadService.GetByKey`
6. Bridge returns JSON:
   - `{ "value": "..." }`
7. ESO writes/updates Kubernetes target secret.

## 3. Operational Workflow

## 3.1 Standalone Workflow (No nebius-cxcli)

1. Install ESO in cluster.
2. Publish bridge image via release tag workflow (`quay.io/nebius/mysterybox-bridge:<tag>`).
3. Deploy Helm chart `charts/mysterybox-webhook`.
4. Create bridge auth secret(s) in cluster namespace.
5. Create `ClusterSecretStore` using webhook provider.
6. Create `ExternalSecret` resources for app namespaces.

## 3.2 nebius-cxcli Workflow

1. Operator edits customer `config.yaml`.
2. `nebius-cxcli render` generates:
   - ESO HelmRelease
   - bridge Deployment/Service
   - MysteryBox ClusterSecretStore
   - ExternalSecret manifests
3. `nebius-cxcli flux bootstrap` seeds runtime Kubernetes auth secrets.
4. Flux reconciles manifests; ESO starts sync.

## 3.3 Release and Image Publish Workflow

Use `services/mysterybox-bridge/publish-image.sh` with two modes:

1. `--prep X.Y.Z` on a working branch:
   - rolls `CHANGELOG.md` `Unreleased` entries into
     `mysterybox-bridge-vX.Y.Z`
   - commits and pushes changelog updates for PR review
2. merge the PR to `main`
3. `--publish X.Y.Z` on clean synced `main`:
   - creates and pushes tag `mysterybox-bridge-vX.Y.Z`
   - tag push triggers `.github/workflows/mysterybox-bridge-image.yml`
   - workflow builds and pushes release image tags

## 4. Security Boundaries

- Secret values are **not** stored in Git.
- Bridge credentials are passed via Kubernetes Secret env vars.
- Optional ESO->bridge header token prevents unauthenticated webhook use.
- Bridge process only exposes read endpoints (`/v1/secret`, health/metrics).

## 5. Project Structure and File Roles

```text
services/mysterybox-bridge/
  CHANGELOG.md
  README.md
  .gitignore
  publish-image.sh
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
      Chart.yaml
      values.yaml
      values.schema.json
      README.md
      templates/
        _helpers.tpl
        serviceaccount.yaml
        service.yaml
        deployment.yaml
        certificate.yaml
        servicemonitor.yaml
        pdb.yaml
        networkpolicy.yaml
        hpa.yaml
        NOTES.txt
    jwt-minter/
      Chart.yaml
      values.yaml
      values.schema.json
      README.md
      templates/
        _helpers.tpl
        serviceaccount.yaml
        role.yaml
        rolebinding.yaml
        cronjob.yaml
        NOTES.txt
```

## 5.1 Top-level Files

- `CHANGELOG.md`: release history with `Unreleased` staging section.
- `README.md`: quick-start and usage modes (standalone and via `nebius-cxcli`).
- `.gitignore`: local/dev/build/cache exclusions for this project.
- `publish-image.sh`: release helper with two modes (`--prep`, `--publish`).
- `docs/design.md`: architecture and workflow contract.

## 5.2 `webhook/` Files

- `Dockerfile`: runtime container image build for bridge service.
- `Makefile`: local automation targets (`install`, `lint`, `test`, `build`).
- `pyproject.toml`: project metadata, package config, lint/test config.
- `README.md`: local developer commands for the webhook package.
- `src/mysterybox_bridge/app.py`: Flask app, endpoints, request validation, metrics wiring.
- `src/mysterybox_bridge/config.py`: environment contract and strict settings validation.
- `src/mysterybox_bridge/iam.py`: Nebius SDK initialization (token or service-account auth).
- `src/mysterybox_bridge/cache.py`: lightweight TTL cache for name->secret-id mapping.
- `src/mysterybox_bridge/mysterybox_client.py`: SDK calls for secret lookup and payload retrieval.
- `src/mysterybox_bridge/__main__.py`: module entrypoint (`python -m mysterybox_bridge`).

### Tests

- `tests/test_app.py`: webhook API behavior and auth checks.
- `tests/test_iam.py`: SDK provider auth mode selection behavior.

## 5.3 `charts/mysterybox-webhook/` Files

- `Chart.yaml`: chart metadata/version.
- `values.yaml`: deploy-time defaults (image, secrets, service, auth, resources).
- `values.schema.json`: value validation for safer installs.
- `README.md`: chart install/usage and ClusterSecretStore example.
- `templates/_helpers.tpl`: Helm naming helpers.
- `templates/serviceaccount.yaml`: bridge service account.
- `templates/service.yaml`: bridge cluster service endpoint.
- `templates/deployment.yaml`: bridge pod runtime spec.
- `templates/certificate.yaml`: optional cert-manager Issuer/Certificate for server TLS.
- `templates/servicemonitor.yaml`: optional Prometheus Operator `ServiceMonitor`.
- `templates/pdb.yaml`: optional PodDisruptionBudget for HA rollout safety.
- `templates/networkpolicy.yaml`: optional ingress restrictions to webhook pods.
- `templates/hpa.yaml`: optional horizontal autoscaling.
- `templates/NOTES.txt`: post-install reminders and verification hints.

## 5.4 `charts/jwt-minter/` Files (Optional Scaffold)

- `Chart.yaml`: chart metadata/version.
- `values.yaml`: disabled-by-default scaffold values.
- `values.schema.json`: validation for schedule, secrets, and job settings.
- `README.md`: states optional nature and non-default path.
- `templates/_helpers.tpl`: Helm naming helpers.
- `templates/serviceaccount.yaml`: service account for job.
- `templates/role.yaml`: namespaced Secret write permissions.
- `templates/rolebinding.yaml`: binds job service account to Secret-write role.
- `templates/cronjob.yaml`: configurable scaffold job schedule and runtime defaults.
- `templates/NOTES.txt`: install-time scaffold reminder.

## 6. Design Decisions

- **Webhook provider path** was selected because ESO has no native MysteryBox provider.
- **Standalone service** avoids coupling bridge lifecycle to `nebius-cxcli` release cadence.
- **Helm chart included** so teams can deploy independently from CLI-generated Flux.
- **`jwt-minter` included as optional scaffold** for stricter auth models without blocking
  the default working path.

## 7. Known Scope

- `jwt-minter` chart is scaffold-level, not mandatory in current integration.
- Bridge currently returns single-value JSON contract expected by webhook provider usage.
- Full mutual TLS is not currently provided by ESO webhook provider; chart supports
  optional server-side TLS and optional request header auth.

## 7.1 Optional Helm Capabilities

- `mysterybox-webhook`:
  - optional server TLS (`tls.enabled`)
  - optional cert-manager self-signed certificate automation (`tls.certManager.enabled`)
  - optional ServiceMonitor (`monitoring.serviceMonitor.enabled`)
  - optional NetworkPolicy and PodDisruptionBudget
- `jwt-minter`:
  - optional CronJob scaffold with Role/RoleBinding to write output Secret

All optional features are disabled by default.

## 8. Quick Start

## 8.1 Prerequisites

- A Kubernetes cluster with cluster-admin access.
- A Nebius MysteryBox secret already created (name or `mbsec-*` id).
- A Nebius service account with read permission to MysteryBox payloads.
- Bridge image published, for example `quay.io/nebius/mysterybox-bridge:<tag>`.

## 8.2 Install ESO

```bash
helm repo add external-secrets https://charts.external-secrets.io
helm repo update

kubectl create namespace external-secrets --dry-run=client -o yaml | kubectl apply -f -

helm upgrade --install external-secrets external-secrets/external-secrets \
  --namespace external-secrets
```

## 8.3 Create Bridge Auth Secrets

```bash
kubectl -n external-secrets create secret generic nebius-mysterybox-auth \
  --from-literal=NEBIUS_SA_ID='<serviceaccount-id>' \
  --from-literal=NEBIUS_AUTH_PUBLIC_KEY_ID='<auth-public-key-id>' \
  --from-file=NEBIUS_AUTH_PRIVATE_KEY_PEM='<path-to-private-key.pem>' \
  --from-literal=NEBIUS_PROJECT_ID='<project-id>'

kubectl -n external-secrets create secret generic mysterybox-bridge-webhook-auth \
  --from-literal=token='<shared-webhook-token>'
```

## 8.4 Deploy Bridge Chart

```bash
helm upgrade --install mysterybox-webhook ./charts/mysterybox-webhook \
  --namespace external-secrets \
  --set image.repository=quay.io/nebius/mysterybox-bridge \
  --set image.tag=<tag> \
  --set authSecret.name=nebius-mysterybox-auth \
  --set requestAuth.enabled=true \
  --set requestAuth.headerName=X-MBX-Request \
  --set requestAuth.tokenSecretName=mysterybox-bridge-webhook-auth \
  --set requestAuth.tokenSecretKey=token
```

## 8.5 Create ClusterSecretStore

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

```bash
kubectl apply -f clustersecretstore-nebius-mysterybox.yaml
```

## 8.6 Create ExternalSecret

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
        # Use MysteryBox secret name or mbsec-* id.
        key: my-app-runtime
        property: API_KEY
```

```bash
kubectl apply -f externalsecret-app-runtime-secrets.yaml
```

## 8.7 Verify

```bash
kubectl -n external-secrets get pods
kubectl -n external-secrets logs deploy/mysterybox-webhook --tail=100
kubectl -n default get externalsecret app-runtime-secrets
kubectl -n default get secret app-runtime-secrets -o yaml
```

If the target secret is not created, inspect:

- `ExternalSecret` status conditions.
- Bridge logs for auth or MysteryBox lookup errors.
- Service account scope/permissions and project id mismatch.

## 8.8 Validation and CI

- PR chart CI workflow:
  - `.github/workflows/mysterybox-bridge-charts-ci.yml`
  - runs `ct lint` and chart render snapshot tests

- Local validation:

```bash
helm lint services/mysterybox-bridge/charts/mysterybox-webhook
helm lint services/mysterybox-bridge/charts/jwt-minter
python3 services/mysterybox-bridge/charts/tests/all_tests.py
```

- Update snapshots after intentional chart rendering changes:

```bash
python3 services/mysterybox-bridge/charts/tests/all_tests.py --update
```
