# AMD GPU Decision Guide

## Applicability

Use for implicated AMD accelerator allocation, initialization, execution or
collective failures. Keep application, Linux, storage and network owners in view;
do not translate NVIDIA tools or configuration into an assumed AMD equivalent.

## Version Fingerprint

Pin GPU model/architecture, firmware, kernel driver, OS/kernel, ROCm runtime and
libraries, framework/image, device permissions, virtualization/partitioning,
scheduler allocation and topology. Consult the matrix for the observed release.
Firmware, driver and userspace form a coordinated stack; unsupported or undocumented
combinations remain gaps. Verify available tool syntax against that exact version.

## Adaptive Decision Guide

<!-- markdownlint-disable MD013 -->

| Symptom | Competing explanations | Cheapest distinguishing evidence | Interpretation | Next branch |
| --- | --- | --- | --- | --- |
| Device absent or initialization fails | allocation/permissions / incompatible stack / application setup | actual assigned device and process identity, exact versions versus support matrix, bounded error | management visibility does not establish application execution; mismatch is a candidate cause requiring a mechanism | locate first allocation/runtime failure; compare supported control |
| OOM or poor throughput | input/allocator / host cgroup / GPU memory / transfer starvation | workload shape, allocation trend, host/cgroup and device evidence for the same window | host pressure differs from device memory exhaustion | isolate input, transfer and kernel phases |
| Collective failure | rank/placement / network/fabric / runtime / device | rank timeline, allocated topology and current counter deltas | collective error alone does not establish GPU failure | compare affected rank boundary with network/runtime evidence |

<!-- markdownlint-enable MD013 -->

### Diagnostic Effects

Workers inspect existing bounded evidence only. Benchmarks, profilers, stress,
resets, driver changes and scheduler changes remain coordinator-owned actions with
authority, impact, duration, redaction and restoration checks. No universal tuning
or driver-upgrade prescription follows from a symptom.

### Verification

Replay the original workload and relevant control on the exact supported stack,
allocation and topology. Verify the demonstrated boundary independently; report
what remains untested. A support-matrix match alone is not a verified repair.

## Official Sources

- [ROCm compatibility matrix](https://rocm.docs.amd.com/en/latest/compatibility/compatibility-matrix.html)
