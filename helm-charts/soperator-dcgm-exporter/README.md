# soperator-dcgm-exporter

Companion chart that deploys NVIDIA DCGM Exporter with the Soperator
`DCGM_HPC_JOB_MAPPING_DIR` path so metrics can include Slurm job mapping labels.

## When To Use

Use the default NVIDIA GPU Operator DCGM exporter for node and GPU-level
telemetry. Enable this chart only when per-job Slurm/DCGM labels are required.
Running both exporters can duplicate DCGM metrics unless they are scraped into
separate jobs.

## Requirements

- GPU worker nodes must expose the configured `dcgmHpcJobMappingDir`.
- The chart schedules on nodes matching `daemonSet.nodeSelector`.
- `ServiceMonitor` rendering is disabled by default. Enable it only when the
  Prometheus Operator CRDs are installed.

## Install

```bash
helm upgrade --install soperator-dcgm-exporter . --namespace soperator
```
