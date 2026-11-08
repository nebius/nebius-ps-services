# strongSwan modernization and recovery

Normal `apply` owns this upgrade for Ubuntu 24.04 SingleVM and VM-HA gateways,
with Static or BGP routing. Customer YAML, credentials, VM/disk/IP identities,
active/passive topology and unrelated routes remain authoritative. The runtime
uses Ubuntu's `charon-systemd` package and `strongswan.service`, with the Python
VICI client pinned independently to `6.0.3`.

## Apply and resume

Use the original configuration path and its lifecycle sidecars. Pin the exact
release wheel when scheduling an upgrade:

```bash
export VPNGW_AGENT_WHEEL=/path/to/nebius_vpngw-VERSION-py3-none-any.whl
nebius-vpngw apply -c gateway.config.yaml --dry-run
nebius-vpngw apply -c gateway.config.yaml --approve-disruption DIGEST
```

The existing interactive `apply` approval is also supported. Preparation resolves
and hashes the exact product, dependency and OS package closure while traffic
flows. Package resolution refreshes authenticated indexes in a temporary workspace;
it does not rewrite system APT indexes, run update hooks or install packages
during planning. Staging downloads only the approved versions and verifies their
hashes. A version removed from its repository after approval fails before
disruption. Fully staged or inhibited resume uses the retained archives offline.
HA stages both members before either is disrupted. No broad distribution
upgrade, autoremove, automatic reboot or legacy rollback is part of this runtime
transaction. An unchanged YAML file does not bypass native-runtime verification.

For absent HA members, the approval binds SSH key-generation intent, deployment
identity, exact targets and any retained trust predecessor. Dry-run planning does
not publish generated keys, and its temporary key bytes do not change the next
invocation's approval. Existing identities remain bound to their exact trust
receipts; changed targets or trust authority require a fresh review.
After publication, read-only enrollment verification loads the exact private keys
from the same default directory and checks their pins without generating keys or
rewriting trust.

An unstarted HA member has no controller state directory yet. After startup
inhibition and writer quiescence, guarded package preparation initializes the
canonical lock storage through the mTLS store owner before acquiring the HA
writer locks. Unsafe directories or existing startup evidence are rejected.
This preparation creates no certificate, credential, controller checkpoint or
activation marker; those transitions retain their existing lifecycle owners.

For a retained pair, package preparation overlaps only after both exact apply
locks, controller quiescence and stopped-writer fences have been verified. Each
member has an independent transport; shared SSH-agent authentication is serialized.
Both preparations settle before activation or a failure returns. Configuration
activation and owner/standby release retain their existing guarded order.
Repeated staging still rehashes every guest archive and wheel and publishes the
complete current manifest, but does not retransmit identical wheel payloads.

The existing operation owners persist startup inhibition, quiesce management
writers and services, install the staged package closure, and publish canonical
units. Inhibition covers package starts, old and new daemon paths, route
maintenance and existing control services. The daemon unit performs no automatic
configuration loading or reload; configuration remains product-owned. Initial HA
service readiness proves guard/bootstrap readiness without waiting for VICI.
During an exact apply inhibition, tunnel processing yields under the rearm lock
before snapshot collection; the ordinary controller then retires prior-generation
proof and materializes the passive runtime through its existing guarded path.
This avoids recursively acquiring the same writer lock during startup.
HA generation identity includes the resolved region, so an inherited region and
the equivalent explicit region used by nested status inspection agree.
Activation checks passive materialization once per second under a 60-second
monotonic deadline that includes observation time. It retains the same readiness
predicate and rejects observations that complete after the deadline.

After an interruption, rerun the same command with the same artifact and
configuration. The approved operation rolls forward from its durable journal,
including partially unpacked/configured dpkg state. Keep the original approval
and artifact; do not remove inhibition files, start individual writers, edit
journals, or downgrade one HA member to complete an interrupted operation.
Unrelated unfinished package work is rejected before a new transaction begins.
For an already failed HA release, `apply` may plan a corrective artifact only
when both earlier package operations completed and both retained members have
exact locks, current-boot fences and unchanged ownership, credentials and routes.
This creates a new reviewed transaction, archives the failed transaction and
completed guest journals, reserves new locks, and repeats canonical guarded
activation. It does not rewind the failed release. Interrupted corrective work
resumes its own exact artifact and approval; unfinished package work cannot
switch artifacts. Recovery results remain separate from normal-upgrade timing.
Retained strongSwan aliases resolve to the primary systemd unit before a stop is
journaled. A lost stop acknowledgement requires fresh job and process settlement
proof before resume; it never establishes that systemd cancelled the stop.
Before activation, a journaled verification binds the exact approved cloud
snapshot and rejects changes between observations.
If the failed release retained an owner-adoption declaration, the corrective
cycle archives it before replacement. Every field must match except the exact
approved predecessor operation ID; current lock, fence and quiescence proof
remain mandatory.

Existing operator masks inhibit planning and remain intact; enabled/disabled
startup policy and the pre-existing package-start policy are preserved.

HA activation remains guarded: prepare the passive member first, prove reciprocal
current-version heartbeats, and independently verify the exact owner's release.
HA activation uses those required predicates directly; advisory ping/BGP polling
and fixed diagnostic sleeps do not extend the fenced interval. Use `status` for
operational diagnostics after activation.
Every promotion still requires the former Compute owner **Stopped** and the shared
allocation confirmed on the candidate. Maintenance inhibition and the sole
standby Compute-start owner remain unchanged. There is no mixed-version runtime
fallback.

Repeated upgrades retain completed checkpoint-conversion receipts as audit
history. A new release converts a supported predecessor checkpoint under the
current apply lock and boot fence, then archives the earlier receipt. Replaying
an older checkpoint for the same artifact remains an error. Coordinated artifact
recovery also preserves completed standby-replacement receipts from earlier
configuration generations; they grant no current authority.

## Recovery ownership

strongSwan handles IKE retransmission, DPD, rekeying and eligible reconnection.
Eligible persistent connections use `dpd_action=restart`, `keyingtries=0` and
`start_action=trap|start`; `close_action=trap` and `unique=no` remain. Cold Static
standbys, unselected Static paths and suspended connections have no initiation
or trap authority. Switching removes that authority, terminates exact SAs and
verifies teardown before selecting another path.

Retransmission uses a one-second timeout, base 1.5, five retransmissions and 10%
jitter, with no additional cap. Its calculated maximum is about 20.8 seconds;
native timing still requires qualification. DPD intervals and IKE/CHILD rekey
settings are preserved. `dpd_timeout` applies to IKEv1, not IKEv2. MOBIKE does not
provide inter-VM recovery. See the upstream [configuration semantics](https://docs.strongswan.org/docs/5.9/swanctl/swanctlConf.html)
and [retransmission calculation](https://docs.strongswan.org/docs/5.9/config/retransmission.html).

Gateway decisions distinguish ownership/guard safety failures, confirmed local
daemon/kernel defects, confirmed peer/path or required-coverage loss, and unknown
evidence. Missing SAs, route withdrawal, DPD expiry and authentication rejection
alone do not identify a failed VM. Ordinary monitoring admits a bounded repair
only for a positively failed component or complete evidence that currently
authorized connection configuration or its credential identifiers are missing
from an active daemon. The bounded VICI inventory reads connection names and
shared-key identifiers on one session; it never reads secret values. A fast
systemd restart can lose loaded configuration without leaving a failed unit.
The existing recovery owner reloads only the exact current private swanctl file,
under writer and ownership checks, using its existing bounded attempt. Unknown
inventories, changed policy, pending tunnel selections and cold or suspended
connections cannot authorize loading. A reload does not restart a healthy daemon
or FRR. Tunnel selection defers a confirmed local defect to component recovery.
Policy changes load credentials before connections on the same VICI session,
using upstream `swanctl --load-all --noprompt`, so a concurrent daemon restart
cannot populate connection definitions without their credentials. See the
[upstream loader order](https://github.com/strongswan/strongswan/blob/5.9.13/src/swanctl/commands/load_all.c)
and [VICI identifier query](https://github.com/strongswan/strongswan/blob/5.9.13/src/libcharon/plugins/vici/README.md#get-shared). HA monitoring only observes; the controller admits its own component
repair. Ordinary component repair requires its health monitor to be enabled.
The production controller also consumes the independent retained-route proof:
daemon health loss does not invalidate unchanged published route authority.
Missing or changed cloud route evidence still blocks this local recovery path.
Deferred repair uses that same retained authority. If its local defect disappears
before dispatch, the controller retires the mutation intent and retains the
consumed attempt and deadline. Readiness and route health sampled across a daemon
restart can disagree; this is unknown native evidence, not lost route ownership.
Keep protected forwarding while withholding configuration-writer readiness.
Before a Static path change, remove its connection's initiation and trap
authority, terminate exact daemon SAs, and verify kernel teardown. A daemon crash
can leave kernel SAs and policies that VICI no longer knows about. Cleanup binds
each object to the configured XFRM interface ID, actual interface, local address
and remote peer, then deletes exact SA or policy identities. It never flushes
the kernel IPsec tables or infers ownership from broad traffic selectors.
Authority changes, ambiguous objects and incomplete readback prevent completion;
the existing bounded operation resumes or restores its previous selection.
Fenced candidate activation and cold standby materialization use this same exact
cleanup, including when the persisted selection has not changed. Candidate
preparation preserves selected paths; cold materialization removes all inactive
Static objects. Admission checks, including cloud observations, and native
commands share one 30-second cleanup budget. Failure leaves forwarding fenced;
this cleanup bound does not renew a path-recovery episode.
See [XFRM interface identity](https://docs.strongswan.org/docs/5.9/features/routeBasedVpn.html)
and the [iproute2 6.1 command contract](https://github.com/iproute2/iproute2/blob/v6.1.0/man/man8/ip-xfrm.8).
Retained forwarding during the first healthy recovery observation does not grant
configuration-writer readiness. Controller status and the agent share the same
admitted states; reconciliation waits for the required second healthy observation.
The same bounded component owner can reload a warm BGP standby after proving
the peer still owns the allocation and the local member remains fenced. Its
persisted attempt explicitly binds passive mode, survives controller restart,
and cannot reactivate cold Static connections. Failed passive repair consumes
the attempt and keeps the standby unready; it does not trigger VM transfer or
restart FRR. Checkpoint v7 records repair mode, with deployment-only conversion
from supported v1-v6 records through normal `apply`; heartbeat v6 is unchanged.

Confirmed required-coverage loss starts one persisted 30-second episode. New
failures, retries and controller restarts cannot renew it. Static alternatives
retain the ten-second first-candidate and five-second subsequent-candidate
schedule, with restoration using the remaining budget. Interrupted restoration
remains pending until authority is verified; it does not grant a new candidate
window. Recovery confirmation requires two healthy observations, and episode
reset requires 60 seconds of continuous health.

At expiry a healthy owner stays in service unless fresh candidate evidence proves
coverage for every required destination or another existing transfer condition
applies. A Static cold standby cannot supply tunnel coverage evidence. Healthy
connections continue forwarding while native retry continues for unavailable
paths. A failed redundant path with complete alternative coverage remains a
path-level event. Optional proactive refresh waits for complete health and is
serialized after recovery.

## Evidence and forwarding protection

Services and CLI probes share the upstream VICI adapter. Complete snapshots must
identify established IKE, installed ESP CHILD and both exact XFRM IDs; overlapping
rekeys are supported. Partial frames, disconnects and timeouts produce unknown
evidence. Every operation has a bounded, separate session. Client timeout does
not establish cancellation of a daemon mutation. The streamed probe includes its
dependency, so older gateways can be inspected without installing it first.

VICI grants control, not just read access. The Unix socket is root-owned mode
`0600` for existing authorized service identities; see the upstream
[VICI security model](https://docs.strongswan.org/docs/5.9/plugins/vici.html).

Heartbeat v6 separates local service health, authority and destination coverage.
A fresh owner with degraded paths is present; stale or missing authority retains
the existing fail-closed checks. Candidate evidence is rechecked before transfer.

The HA dataplane owns a persistent nftables forwarding guard. Configured VPN
destination intent and the admitted BGP-prefix ledger survive withdrawals and
restarts. Protected destinations may leave only through configured XFRM
interfaces; the guard adds drops and grants no acceptance. More-specific local
or VPN ownership wins; ambiguous equal-prefix ownership fails closed. Local/VPC
destinations and host IKE/BGP control traffic retain their existing behavior.
Unknown destination ownership or unverified protection retains the global fence.
Admission canonicalizes kernel route prefixes, including suffix-free IPv4 host
routes, before matching exact configured XFRM ownership.
Identity verification normalizes the hexadecimal XFRM IDs emitted by `iproute2`
before comparing them with the configured IDs; malformed or mismatched identities
remain blocking.

## Verification and release gate

Run `status`, `vm-ha --dry-run`, and the existing `failover`/`failback` commands
through their public interfaces. CLI success, healthy service status and local
tests are distinct from workload delivery. Inspect logs using
`journalctl -u strongswan.service` and native sessions with `swanctl --list-sas`.

Local tests and the disposable Ubuntu systemd fixture are separate evidence. The
native fixture covers real package replacement, interrupted unpack resume,
inhibition, loader/socket policy and nftables readback. It does not substitute
for authentic previous-release artifacts or live workload measurements.

Package planning admits only the exact released ordinary/HA agent ordering
overrides, the current canonical overrides, or an absent override. Both package
paths replace known strongSwan-starter ordering with charon-systemd ordering;
unknown administrator content remains blocked. The historical container lane
uses this same product admission check before package mutation.

Release qualification uses the four most recent stable releases (currently
0.6.0–0.6.3) for authentic package-history trials in Ubuntu 24.04 containers.
Each source wheel installs its own dependencies and emits its own saved state
before the candidate's package owners run. The controller interpreter is
isolated from the gateway interpreter. Cache source installations in separate
images without candidate dependencies or scenario state, then start a fresh
container by immutable image ID for every case. Reverify installed source files
and the recorded package closure before fixture setup. Record selected artifact hashes,
installed files and metadata, dependency closure, service assets and checkpoint
decoding. Ordinary package-component and HA guarded-package preparation results
have separate boundaries; neither establishes full `apply` success.

Live upgrades start from the latest published stable release (currently 0.6.3)
in SingleVM and VM-HA, Static/BGP, plus fresh installs in all four modes.
Recovery, maintenance,
interrupted apply/reboot, stale evidence, cold-tunnel authority and plaintext
escape cases must pass. Freeze the final artifact and independently measure
bidirectional workload traffic throughout all four upgrade modes. Normal upgrades
must interrupt traffic for at most 120 seconds, preserve resource identities and
produce a mutation-free second apply. Already-failed recovery is reported
separately. These final-artifact live gates remain unqualified until measured;
earlier release measurements do not qualify this implementation. A container
package pass cannot replace any live gate. This split supersedes earlier plans
to run every historical predecessor on live VMs.

The preceding prepared-policy source passed 4,417 local tests; native-only tests are skipped on
macOS. Its isolated Ubuntu 24.04 run passed all 100 native cases, including
real package replacement, interrupted package work, daemon restart, loader
admission, rekey/identity observations and protected forwarding. Later credential
inventory, controller-authority and kernel-teardown changes have their own
qualification evidence below.

The development BGP runtime candidate passed a normal update from an already
modernized gateway. Complete bidirectional workload windows measured maximum
reply gaps of 103.4 seconds on the frozen prepared-policy candidate. All seven managed resource identities were retained;
a second apply preserved installed-file hashes, service invocation identities and
lifecycle state. This proves that update path, not an authentic Starter upgrade
or the complete release matrix.

The first authentic retained HA predecessor Static upgrade failed promotion and
exceeded the 120-second interruption gate. Candidate preparation had written the
selected owner configuration while forwarding remained fenced; the new loader
check incorrectly demanded cold-standby configuration during observation. The
repair recognizes the exact prepared file for native health collection while
keeping standby reload admission strict. Source regressions and native replay passed, and the existing guarded `apply`
transaction restored the failed pair. Both members matched the frozen artifact,
resource identities were retained, and a second apply was mutation-free. The
failed upgrade remains failed evidence, independently of subsequent recovery.

BGP public VM failover and failback passed, with independent cloud observations proving
the former owner Stopped before allocation transfer. The complete round-trip
traffic window had a maximum reply gap below 77.4 seconds. Repeating failback on
the current owner submitted no request and preserved sessions and services.
Public tunnel failover and failback passed with verified selection changes and
maximum reply gaps below 0.41 seconds in overlapping complete observation windows.

BGP owner and warm-standby daemon-crash trials restored configuration and SAs in about
four seconds. Writer or standby readiness followed within eight seconds, with no
VM transfer and no recreated table-220 rule. The serving owner's sessions were
unchanged during standby repair. Workload reply gaps remained below 0.42 seconds.
This independently verifies the shared writer-readiness, passive-loader and
`routing_table=0` corrections. See the
[upstream kernel-netlink implementation](https://github.com/strongswan/strongswan/blob/5.9.13/src/libcharon/plugins/kernel_netlink/kernel_netlink_net.c).

A timed peer-path outage exercised actual DPD and native retries without forced
SA termination. The healthy redundant path kept forwarding. A shared-outage
trial retained the owner when the candidate remained unusable, preserved one
exhausted coverage episode through native recovery, and cleared it only after
continuous health. Independent packet capture observed no protected plaintext
while forwarding was enabled and route lookup selected the underlay; positive
unrelated outgoing packets confirmed capture activity. Deliberate fault duration
and recovery gaps are separate from the normal-upgrade limit.

Earlier failed development upgrades and recovery trials remain failed evidence.
Their repairs included checkpoint conversion, exact XFRM ID parsing, guarded
corrective activation, route-authority composition and configuration-writer
readiness. Recovery of an already-failed pair does not qualify normal upgrade
interruption.

Maintenance testing found two CLI convergence defects: accepted recovery was
incorrectly required to have enabled policy already, and a resumed client tried
to clean a completed Compute recovery before its policy transaction committed.
The repaired frozen CLI completed that interrupted transaction without restarting
the serving owner. A fresh disable, stopped-standby hold and enable replay also
passed. All managed identities and the owner's sessions and service instances
remained unchanged, and a complete 3,000-packet window in each direction measured
maximum reply gaps below 0.41 seconds.

A completely passing normal Static upgrade and the complete
ordinary/predecessor/fresh-install matrix remain outstanding. Already-failed
Static recovery is separate evidence.
No release-wide brief-interruption claim is made.

A later Static owner-daemon crash exposed a selector/component recovery race:
connection definitions could be restored without shared credentials, hiding the
local defect from the previous connection-only inventory. One trial transferred
the VM; a clean replay retained the owner but failed workload recovery. Both
remain failed evidence. The complete-configuration inventory, ordered loader and
subsequent retained-authority repairs passed 4,445 local tests and 102 native
cases on their frozen candidate. Three owner-daemon crash trials in each routing
mode recovered without VM transfer; the BGP warm standby also recovered without
disturbing the owner's sessions. A normal BGP update measured maximum gaps below
102.9 seconds, retained all identities and produced a mutation-free second apply.
Static recovery through `apply` restored traffic about 241 seconds after starting
from an already-failed baseline; its second apply was also mutation-free.
BGP VM failover/failback passed independent stopped-former-owner checks and a
complete traffic window with maximum gaps below 78 seconds. Same-owner failback
submitted no request and preserved sessions.

The third Static crash selected its backup successfully but left orphan kernel
SAs and policies on the unselected path. That teardown criterion remains failed
evidence. A native counterfactual reproduces it after an actual daemon SIGKILL.
The first native cleanup replay rejected statistics-formatted request IDs before
mutation. After matching that format to upstream iproute2, the shared parser now
accepts only agreeing decimal/hex identities and rejects ambiguous templates.
That frozen repair passed 4,483 local tests, typing on 117 source files, and all
104 native cases. Its normal Static update measured maximum bidirectional gaps
below 107.4 seconds and preserved resource identities, but left nine policies on
an unselected path. The full teardown criterion therefore failed.
A new native regression reproduces the missing activation cleanup without a
selector switch. The activation repair passes all six direct/NAT crash cases,
including retained-selection activation and a policies-only orphan checkpoint.
Review also added cumulative bounds for cloud admission checks. The frozen
activation repair passed 4,490 local tests and all 108 native cases in 932.87
seconds. Live qualification remains pending. Subsequent desired Security Group
attachment changes require a new combined artifact and their own qualification;
these results do not qualify that newer artifact.
Static public VM failback passed independently, including stopped-former-owner
proof and complete traffic windows with maximum reply gaps of 82.2 seconds.

A later combined candidate passed all 108 native cases and live activation cleanup.
Its `apply` repair removed the inactive policies, preserved seven resource
identities and passed a mutation-free second apply. Repair traffic gaps were
164.94 and 164.89 seconds; this already-failed recovery does not pass or replace
the normal-upgrade interruption gate. Three daemon crashes recovered locally.
A separate daemon-loss seed followed by peer loss triggered automatic path
selection and removed every former-path SA and policy. Independent cloud
observations confirmed unchanged ownership; complete workload captures measured
maximum reply gaps of 57.81 and 57.76 seconds. Restoring the peer did not reactivate
the unselected path, and temporary fault controls were removed.
Newer command-result and equivalent-failback changes still require a new frozen
artifact and qualification. Security Group attachment and the remaining authentic
SingleVM upgrade matrix are also open gates.
