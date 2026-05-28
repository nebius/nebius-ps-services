# Changelog

## [Unreleased]

- Clarified that the chart is an optional Soperator jail backup schedule and
  that cxcli wires backup credentials only when backup is enabled.
- Updated the pinned Soperator K8up backup schedule import for upstream release
  3.0.4 and packaged it as a parent-chart child dependency.
- Removed the direct K8up CRD render guard because the parent Soperator chart
  now owns K8up as an optional dependency.
- Added an explicit `enabled` gate and fail-fast validation for required bucket
  and Secret reference fields.
