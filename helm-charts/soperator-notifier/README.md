# soperator-notifier Helm chart

Child chart for Soperator Slack job notifications.

The chart renders VictoriaMetrics Operator resources:

- `VMAlertmanagerConfig`
- `VMAlertmanager`
- `VMRule`
- `VMAlert`

## When To Use

Use this chart only when Slurm job lifecycle notifications should be posted to
Slack. It is optional for Soperator itself and requires VictoriaMetrics
Operator resources plus a runtime Slack incoming-webhook Secret. The main
`soperator` umbrella chart enables it with `soperator-notifier.enabled=true`;
cxcli keeps it disabled by default until a webhook source is configured.

It does not create the Slack webhook Secret. The webhook URL must exist in a
Kubernetes Secret referenced by `slack.existingSecret` and
`slack.existingSecretKey`. This keeps Slack webhook URLs out of Helm values,
Flux manifests, Git repositories, and generated customer artifacts.

Slack setup:

- Create or reuse a Slack App for the workspace.
- Enable incoming webhooks for the app and add a webhook to the target channel,
  following Slack's
  [incoming webhook guide](https://docs.slack.dev/messaging/sending-messages-using-incoming-webhooks/).
- Store the returned webhook URL only in a runtime secret system. Do not commit
  it to this repository, Helm values, generated manifests, or public logs.
- Slack's connectivity check is a JSON `POST` to the webhook URL, for example:

```bash
curl -X POST -H 'Content-type: application/json' \
  --data '{"text":"Hello, World!"}' \
  '<slack-incoming-webhook-url>'
```

## Install

Create the runtime Secret first:

```bash
kubectl -n soperator create secret generic soperator-notifier-slack-webhook \
  --from-literal=url='<slack-incoming-webhook-url>'
```

Then install the chart:

```bash
helm install soperator-notifier helm-charts/soperator-notifier \
  --namespace soperator \
  --create-namespace
```

For cxcli-managed deployments, enable the parent `soperator` app with
`values.soperator-notifier.enabled: true`. cxcli supports two secret-safe
webhook sources:

- `slack.webhookSource: mysterybox`: store the webhook URL once in an existing
  Nebius MysteryBox Secret payload key such as `url`, set
  `slack.mysterybox.secretId` to the non-secret `mbsec-...` Secret ID, and let
  cxcli render an ExternalSecret. cxcli intentionally omits
  `remoteRef.version`, so ESO always reads the current primary MysteryBox
  version.
- `slack.webhookSource: deploy-time`: provide the webhook URL during `deploy`
  with `NEBIUS_CXCLI_SOPERATOR_SLACK_WEBHOOK_URL`, a target-specific
  `NEBIUS_CXCLI_SOPERATOR_SLACK_WEBHOOK_URL_<TARGET>`, an interactive hidden
  prompt, or a precreated Kubernetes Secret. cxcli writes only the runtime
  Kubernetes Secret.

## Requirements

- Soperator Slurm exporter metrics must be available in the configured
  `dataSourceUrl`.
- VictoriaMetrics Operator CRDs must be installed before this chart is applied.
- The Slack incoming webhook posts only to the channel selected when the
  webhook was created or authorized.

## Validation

```bash
helm lint --strict helm-charts/soperator-notifier
helm template soperator-notifier helm-charts/soperator-notifier \
  --namespace soperator >/tmp/soperator-notifier.yaml
```
