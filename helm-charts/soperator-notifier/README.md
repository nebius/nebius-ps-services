# soperator-notifier Helm chart

Companion chart for Soperator Slack job notifications.

The chart renders VictoriaMetrics Operator resources:

- `VMAlertmanagerConfig`
- `VMAlertmanager`
- `VMRule`
- `VMAlert`

It does not create the Slack webhook Secret. The webhook URL must exist in a
Kubernetes Secret referenced by `slack.existingSecret` and
`slack.existingSecretKey`. This keeps Slack webhook URLs out of Helm values,
Flux manifests, Git repositories, and generated customer artifacts.

Slack references:

- [Sending messages using incoming webhooks](https://docs.slack.dev/messaging/sending-messages-using-incoming-webhooks/)
- [Installing with OAuth](https://docs.slack.dev/authentication/installing-with-oauth/)
- [conversations.create](https://docs.slack.dev/reference/methods/conversations.create/)

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

For cxcli-managed deployments, select the `soperator-notifier` app. cxcli
creates or reuses the runtime Secret during `deploy`.

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
