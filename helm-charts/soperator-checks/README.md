# soperator-checks

Companion chart that deploys the Soperator checks controller used by
`ActiveCheck` resources and Soperator node maintenance/degraded status
reconciliation.

## When To Use

Use this chart when the cluster needs to reconcile Soperator `ActiveCheck`
resources or intentionally use the Soperator checks controller's node
maintenance/degraded-condition automation. It installs the controller, not the
check objects themselves and not the NodeConfigurator rebooter. The main
`soperator` umbrella chart enables it with `soperator-checks.enabled=true`;
cxcli production profiles keep it disabled by default and enable it
automatically only when Soperator ActiveChecks are explicitly enabled. Operators
who want Soperator-managed node maintenance without ActiveChecks must opt in
with explicit values.

For advanced production-maintenance automation, pair this chart with the parent
chart's `rebooter.enabled=true` value and keep the intents separate:
`NebiusMaintenanceScheduled=True` is graceful maintenance drain and node handoff,
while `SlurmNodeReboot=True` is the actual Soperator host reboot path after
drain. The maintenance path writes `SlurmNodeDrain=True`, not
`SlurmNodeReboot=True`. Do not treat `NebiusMaintenanceScheduled=True` as a
direct reboot signal.

## Requirements

- Install this chart after the main Soperator chart and before
  `soperator-activechecks`.
- `ServiceMonitor` rendering is disabled by default. Enable
  `serviceMonitor.enabled=true` only when the Prometheus Operator CRDs are
  installed.

## Scheduling

Use `checks.affinity`, `checks.nodeSelector`, and `checks.tolerations` to place
the checks controller on dedicated Soperator system nodes. cxcli sets these
through the parent Soperator profile when role mapping is enabled.

## Install

```bash
helm upgrade --install soperator-checks . --namespace soperator
```
