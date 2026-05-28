# Changelog

## [Unreleased]

- Clarified that the chart installs the checks controller for ActiveChecks and
  node maintenance/degraded-condition reconciliation, can be enabled by the
  parent Soperator umbrella chart, and remains disabled by default in cxcli
  production profiles unless ActiveChecks or node-maintenance automation is
  explicitly enabled. The README now distinguishes
  `NebiusMaintenanceScheduled=True` as graceful maintenance drain/node handoff
  from `SlurmNodeReboot=True` as the actual host reboot signal.
- Added `checks.affinity`, `checks.nodeSelector`, and `checks.tolerations` so
  parent-chart and cxcli role mappings can place the checks controller on
  Soperator system nodes.
- Granted the checks controller read access to `PodTemplate` resources so
  ActiveChecks using `podTemplateNameRef` can be reconciled.
- Updated the pinned Soperator checks controller import for upstream release
  3.0.4 and packaged it as a parent-chart child dependency.
- Disabled `ServiceMonitor` by default and changed the manager pull policy to
  `IfNotPresent`.
