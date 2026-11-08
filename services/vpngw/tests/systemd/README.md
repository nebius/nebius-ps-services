# Isolated systemd and networkd regressions

Run `python3 tests/systemd/run.py` from the service directory. This explicitly
builds and starts a disposable Ubuntu 24.04 container with systemd as PID 1,
then removes its uniquely named container and image. Docker and a Linux kernel
with XFRM interface support (`CONFIG_XFRM_INTERFACE`) are required. Some Docker
Desktop kernels lack that support; use a disposable Ubuntu VM or Linux runner
for the complete lane. Missing XFRM support fails fixture setup, rather than
skipping route-retirement coverage.

The preceding 104-case native suite took nearly 15 minutes on the qualification VM,
excluding image construction. The runner bounds image construction at ten
minutes and test execution at twenty minutes to include the additional Static
activation/crash regressions. CI reserves thirty-five minutes for
both phases, startup and cleanup; individual product recovery deadlines remain
unchanged.

Run `python3 tests/systemd/run.py --ssh-bootstrap-only` to test SSH bootstrap on
kernels without XFRM support. This selection executes the generated cloud-init
SSH command with real OpenSSH and systemd from an absent `/run/sshd`. It checks
socket and service activation, root-owned `0755` directory permissions, an SSH
banner on port 22, and failure before activation or continuation on invalid
configuration. The default invocation includes these cases and the full network
lane. The focused selection does not validate ESP4 reboot or cloud-init as a whole.

This development fixture requires root and privileged mode to run the actual
service manager and configure its own networking. Run it only in Docker Desktop's
Linux VM or on a disposable Linux runner. It has private PID, network, and cgroup
namespaces, no published ports, no host system bus or credential mounts, and a
read-only source, tests, and project metadata mounts. Runtime state lives in its disposable filesystem;
`/run`, `/run/lock`, and `/tmp` use tmpfs. It is not a deployment image.

The tests exercise real job replies, failed reloads, a dead caller with a surviving
job, lost acknowledgements, delayed stops, native Netplan convergence, effective
unit dependencies, interrupted migration handoff, and old-agent startup admission.
They also exercise an immediately dispatched timer job that observes the pending
operation guard, and require bounded settlement without completing that operation.
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

The image base and FRR 10.5.5 packages, including matching `frr-pythontools`,
are pinned; other Ubuntu package updates are resolved during each build.
The default lane also runs real UFW host-policy tests serially, last in the
disposable container. Its saved rules and POSIX lock are shared with product
reconciliation inside the router namespace. Tests cover pre-device permits,
kernel-only INPUT/OUTPUT loss, saved deny ordering, native match normalization,
no-op repeat, invalid dependencies, public BGP denial, and preservation of
management/peer rules, forwarding state, unrelated chains and MSS handling.
Veth links model tunnel host interfaces; these UFW tests do not prove ESP or
IPsec. Missing UFW/network prerequisites fail the opted-in fixture.

The modernization lane also checks package resolution with absent system APT
indexes and a host update-hook trap. Native forwarding tests send real packets
through the destination guard after route withdrawal and guard reinstallation,
including protected default routes, prefix overlap and later customer drops.
These namespace tests do not establish cloud route ownership or live VM fencing.

Separate session tests run two actual strongSwan daemons in private network and
mount namespaces, using ephemeral credentials and real ESP/XFRM policies. They
require ESP4 kernel support, including a loaded module when automatic loading is
disabled. The runner does not change host module policy; prepare ESP4 on the
disposable Linux host before starting this lane. The namespace daemon writes
level-one logs to its captured stdout because its private `/run` does not contain
the system journal socket. The fixture installs the independently hash-pinned
VICI client for the system Python used by the observation subprocess, and loads
the product-generated retransmission settings. Initial negotiation tests measure
the five-retransmission sequence and continued native initiation separately from
DPD detection. The session tests cover IKEv2 overlapping negotiation, IKEv1 startup,
encrypted TCP/179,
stable session identities and peer-close recovery, including the prior release's
session settings on one peer. TCP/179 payload exchange does not prove BGP prefix
exchange, and prior-policy coverage does not qualify a full release upgrade.

The HA tunnel tests also run actual FRR BGP sessions and the production tunnel
controller, native effects and observers. They verify whole-connection Static
switching and cold alternatives, BGP import/export overrides and failed-target
release, bounded recovery, repeated reconciliation, and bidirectional packets.
Mixed-mode cases check that switching a Static connection preserves an unrelated
BGP session, its routes and traffic under both owner bindings. Production systemd
filesystem and capability restrictions cover XFRM preparation and FRR policy writes.
Cloud ownership is a frozen fixture input; these tests do not prove Compute
fencing, allocation transfer or peer-provider return routing.

BGP restart cases persist an operation at activate, verify or release, or hold a
selected peer down during restart, then run
the production forwarding guard, passive `Agent.reload`, VM activation and tunnel
verification against real SA/XFRM and FRR evidence. Both local-export settings are
covered. The failed-peer cases retain FRR's stale route and require the canonical
passive policy refresh before active forwarding. Namespace service dispatch, LAN
setup, host firewall and cloud ownership
are fixture boundaries; routing policy, forwarding and operation results are not
substituted. The composed lane separately covers failed and unknown selected
paths, expired requests, failback, checkpointed activation retries, crashes before
and after passive refresh, and mixed cold-Static/BGP readiness with unknown policy.

Local validation can use native ARM64 Ubuntu. The `ubuntu-24.04` CI job
provides the AMD64 runner lane. The fixture proves isolated encrypted packet paths;
live gateway upgrades, cloud fencing and VM failover require separate live trials.

## Authentic historical package upgrades

`run.py --historical-package-upgrades --candidate-wheel /path/to/candidate.whl
--report /path/to/matrix.json` uses `Dockerfile.packages` and the four pinned
source-release wheels in `historical-releases.json`. The default platform is
`linux/amd64`; select the VM architecture explicitly when it differs. This lane
does not require XFRM support and does not establish IPsec or BGP sessions.
The source release runs on a recorded environment rebuilt from current package
repositories; this does not reproduce its original release-date dependency set.

Each source release has ordinary and HA package trials for Static and BGP.
Four additional latest-release cases terminate the package process immediately
after real native unpack or product-wheel installation. Independent observations
require stopped writers, dead process groups, retained startup inhibition and
the exact HA lock and forwarding fence where applicable. Retry uses the same
manifest, staged closure and runtime transaction; it performs no new resolution.
Checkpoint conversion repeats must preserve the converted checkpoint bytes.

After every successful upgrade or interrupted-install recovery, the same container
repeats the real package preparation with its saved request. It performs no new
resolution or staging. Independent observations compare native and Python payloads,
dependencies, service assets, permissions, ownership and symlink targets, together
with the runtime operation, manifest, configuration, startup inhibition and HA
lock/checkpoint. Both observations and the repeated execution receipt are retained.
Reinstalling identical packages is permitted: execution journals, process identities
and guard installation times may change. Generated bytecode and its RECORD rows,
file timestamps/inodes and execution logs are excluded from payload convergence.
These exclusions never permit payload, recovery authority or safety-policy drift.
This check proves package convergence; mutation-free full apply remains a live-VM
requirement. Fixture hashes bind each report separately from the candidate wheel.

The base image contains systemd, fixture prerequisites and the legacy native
daemon. It removes the minimal Ubuntu image's documentation exclusions before
installing packages so the complete declared payload can be checked; missing
package files fail observation. Python checks retain every installed distribution
root and shadow payload while recording effective dependency precedence separately.
`Dockerfile.predecessor` caches each authentic source installation in a
separate image, without candidate dependencies or scenario configuration.
Each case starts a fresh container by immutable image ID and rechecks source
wheel files, Python/native package inventory, OS and architecture before fixture
setup. Docker's content cache reuses unchanged installation layers; use
`--release-wheels` to reuse downloaded source wheels, which are always rehashed.
These disposable images run as root for package/systemd tests and are not
published. Source repositories are contacted when building an uncached baseline;
reused layers retain their recorded dependency versions until explicitly rebuilt.
The predecessor's own installer and emitted service assets establish the source
checkpoint. The candidate controller runs
from a separate virtual environment; installed files are checked using system
Python. These are package-stage and roll-forward checks, not full `apply`, HA
activation, VM reboot, traffic interruption or mutation-free full-apply proof.
The report preserves every case, artifact digests and exact-owned cleanup result.
