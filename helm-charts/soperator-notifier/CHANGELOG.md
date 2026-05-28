# Changelog

All notable changes to this chart are tracked here.

## [Unreleased]

- Clarified that the chart is optional and should be enabled only for Slurm job
  notifications to Slack with a runtime webhook Secret.
- Updated the Soperator Slack notifier chart for upstream release 3.0.4 and
  packaged it as a parent-chart child dependency.
- Documented the Slack App incoming-webhook setup and cxcli's two secret-safe
  webhook source paths: deploy-time Kubernetes Secret creation or existing
  Nebius MysteryBox Secret ID synced through ESO.
- Allowed parent-chart wrapper keys in the chart schema so strict parent lint
  can validate nested Soperator child-chart enablement.
- Added the initial Soperator Slack notifier child chart with
  VictoriaMetrics Alertmanager resources and an existing-Secret-only Slack
  webhook contract.
