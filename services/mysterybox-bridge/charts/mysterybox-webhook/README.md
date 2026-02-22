# mysterybox-webhook chart

Deploys the `mysterybox-bridge` webhook service used by
[External Secrets Operator](https://external-secrets.io/latest/) webhook provider.

## Production Defaults

- Avoids `latest` image tag by default.
- Supports digest pinning (`image.digest`).
- `livenessProbe`: `GET /healthz`
- `readinessProbe`: `GET /readyz`
- Pod/container security contexts enabled.
- Optional `PodDisruptionBudget` and `NetworkPolicy`.

## TLS (maintenance-friendly option)

The chart supports optional server-side TLS for webhook traffic.

- Default: disabled.
- Recommended low-maintenance mode: enable `tls.enabled=true` and
  `tls.certManager.enabled=true` to auto-issue/rotate self-signed certs via cert-manager.
- Dependency for cert automation: cert-manager CRDs installed in the cluster.

Example:

```bash
helm upgrade --install mysterybox-webhook ./charts/mysterybox-webhook \
  --namespace external-secrets \
  --set tls.enabled=true \
  --set tls.certManager.enabled=true
```

Note on mTLS:

- ESO webhook provider supports server TLS verification (for example with `caBundle`/`caProvider`),
  but does not natively provide client certificate authentication for full mutual TLS.
- Keep request header auth enabled (`requestAuth.*`) and optionally combine with `NetworkPolicy`.

## ServiceMonitor (optional)

`monitoring.serviceMonitor.enabled=false` by default.
When enabled, this chart renders a `ServiceMonitor` and requires Prometheus Operator CRDs.

Example:

```bash
helm upgrade --install mysterybox-webhook ./charts/mysterybox-webhook \
  --namespace external-secrets \
  --set monitoring.serviceMonitor.enabled=true
```

## Required Kubernetes Secret

Create a Secret (default `nebius-mysterybox-auth`) in the release namespace with:

- `NEBIUS_SA_ID`
- `NEBIUS_AUTH_PUBLIC_KEY_ID`
- `NEBIUS_AUTH_PRIVATE_KEY_PEM`
- `NEBIUS_PROJECT_ID`

Optional when using token auth instead of SA:

- `NEBIUS_IAM_TOKEN`

## Install

```bash
helm upgrade --install mysterybox-webhook ./charts/mysterybox-webhook \
  --namespace external-secrets \
  --create-namespace
```

## Useful Values

- `image.repository`, `image.tag`, `image.digest`
- `global.imagePullSecrets`, `imagePullSecrets`
- `authSecret.name`, `authSecret.optional`
- `requestAuth.*`
- `tls.*` (including cert-manager automation)
- `monitoring.serviceMonitor.*`
- `pdb.enabled`
- `networkPolicy.enabled`
- `hpa.*`

## Example ClusterSecretStore

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

Note: verify your ESO CRD fields against installed version:

```bash
kubectl api-resources | grep -i secretstore
kubectl explain clustersecretstore.spec.provider.webhook
```
