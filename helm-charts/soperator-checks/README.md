# soperator-checks

Companion chart that deploys the Soperator checks controller used by
`ActiveCheck` resources.

## Requirements

- Install this chart after the main Soperator chart and before
  `soperator-activechecks`.
- `ServiceMonitor` rendering is disabled by default. Enable
  `serviceMonitor.enabled=true` only when the Prometheus Operator CRDs are
  installed.

## Install

```bash
helm upgrade --install soperator-checks . --namespace soperator
```
