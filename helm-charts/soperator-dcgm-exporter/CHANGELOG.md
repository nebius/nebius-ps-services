# Changelog

## [Unreleased]

- Documented `validateToolkit` for Nebius GPU-image deployments where the host
  NVIDIA runtime stack is already present and the GPU Operator toolkit
  validation file is not expected.
- Updated the pinned Soperator DCGM job-mapping exporter import for upstream
  release 3.0.4 and packaged it as a parent-chart child dependency.
- Disabled `ServiceMonitor` by default and moved the busybox init image into
  values.
