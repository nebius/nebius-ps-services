# Isolated systemd and networkd regressions

Run `python3 tests/systemd/run.py` from the service directory. This explicitly
builds and starts a disposable Ubuntu 24.04 container with systemd as PID 1,
then removes its uniquely named container and image. Docker and a Linux kernel
with XFRM interface support (`CONFIG_XFRM_INTERFACE`) are required. Some Docker
Desktop kernels lack that support; use a disposable Ubuntu VM or Linux runner
for the complete lane. Missing XFRM support fails fixture setup, rather than
skipping route-retirement coverage.

This development fixture requires root and privileged mode to run the actual
service manager and configure its own networking. Run it only in Docker Desktop's
Linux VM or on a disposable Linux runner. It has private PID, network, and cgroup
namespaces, no published ports, no host system bus or credential mounts, and a
read-only source, tests, and project metadata mounts. Runtime state lives in its disposable filesystem;
`/run`, `/run/lock`, and `/tmp` use tmpfs. It is not a deployment image.

The tests exercise real job replies, failed reloads, a dead caller with a surviving
job, lost acknowledgements, delayed stops, native Netplan convergence, effective
unit dependencies, interrupted migration handoff, and old-agent startup admission.
Local command tests also verify descendant settlement after leader exit and timeout
using the real process journal and Linux process groups.
Route retirement uses real XFRM interfaces in private network namespaces to verify
whole-tunnel removal and positional interface reuse. It preserves connected,
FRR-protocol and foreign-table routes, checks durable absence history and exercises
the existing bounded command journal.
The same runner includes complete ordinary SSH transport tests. They execute the
actual `sudo`/Python command with its full streamed source and JSON request,
including a request larger than Linux's single-argument limit, and reproduce the
old argument-size failure. These read-only transport cases also run in a disposable
container without XFRM support; they do not substitute for the full XFRM lane.
An explicit environment marker and container/PID 1 checks prevent accidental host
execution. Ordinary integration runs skip this dedicated lane; its own CI job
requires the prerequisites and all tests to pass.

The image base is pinned; Ubuntu package updates are resolved during each build.
Local validation can use native ARM64 Ubuntu. The `ubuntu-24.04` CI job
provides the AMD64 runner lane. These tests do not prove a live gateway upgrade,
VPN connectivity, cloud fencing, or HA failover.
