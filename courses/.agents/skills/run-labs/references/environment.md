# Prepared environment

Read for a new run or missing prerequisite. Reuse the user's existing access
receipt, deployment config, login connection and accepted target identity.
The course root is a sibling set of catalogs, not a single course directory.
`sync-labs.sh --sync-only` uses existing SSH authentication and writes a private
connection receipt; no interactive login is needed for subsequent commands.

Create the following **outside Git**, mode 600 in a mode 700 directory. Fields
are examples, not defaults. There are no secret values in this schema:

```json
{
  "schema": "run-labs-environment/v1",
  "private_root": "/private/operator/lab-validation/run-labs",
  "target_id": "accepted-cluster-resource-id",
  "workspace_id": "course-reference",
  "ssh": {"target": "student@login-alias", "port": 22, "identity_file": "/private/operator/ssh-key"},
  "cxcli": "/qualified/bin/nebius-cxcli",
  "config": "/private/operator/deployment/config.yaml",
  "target": "exact-config-target-name",
  "kube_context": "accepted-context",
  "reports": {"producer_root": "/data/nsight-reports", "viewer_root": "/mnt/reports"},
  "variables": {"COURSE_PYTHON": "/qualified/bin/python3"}
}
```

Optional `course_variables` maps a course slug to its variable overrides;
`lab_variables` maps an exact `course:source-stem` to overrides. Precedence is
global, course, lab, then the recipe's profile variables. Use these for distinct
prepared Python, torchrun, model and container environments. The reserved
`${REMOTE_WORKSPACE}` expands after sync to the selected unit's remote directory;
use it for source-matched CUDA build directories required by the launchers.

Resolve missing per-course variables from accepted course environment files;
load credential-bearing environment content only into process memory, never
into `variables`, campaign state, logs or shell examples. Typical prerequisites
are qualified Python/torchrun, immutable container digests and runner, pinned
model paths/revisions, NCCL Tests/MPI, HCA pairs, and CUDA build directories.
CUDA binaries must match this source and the new `--profile small|large`
interface. A source build through the existing build launcher is allowed;
installing compilers, services or shared README setup is outside this skill.

Preflight independently checks cluster identity, login, Slurm, software versions,
GPU topology, existing Grafana, both native viewers and prepared assets for the
selected recipes. Record observed values privately, then supply the stage helper
with `run-labs-preflight/v1`, the campaign `environment_sha256`, and boolean
`checks` keys `cluster_identity`, `login`, `slurm`, `software`, `gpu_topology`,
`grafana`, `systems_viewer`, `compute_viewer`, `prepared_assets`.
A checkbox is a summary of observations, never a substitute for doing them.
For a prepared container runner that restricts working directories, verify after
sync that every selected workspace is admitted by its effective configuration.
Checking the runner's executable and image alone is insufficient. Register only
the exact owned campaign root through the prepared adapter's supported setup,
preserving unrelated configuration and recording before/after identity privately.
Keep this prerequisite binding separate from lab execution and finish it before
freezing prepared-asset proofs or submitting jobs. Never broaden the allowed root
to a home directory or disable the runner's ownership checks.
For an image with its own Python environment, preserve its declared interpreter
search path in the prepared runner and verify required imports through that
effective environment. Installed package metadata alone does not prove that a
backend can import it. Keep prerequisite probes separate from lab acceptance.
Keep writable module, lock and compiler caches separate from immutable model
assets. For example, Transformers' `HF_MODULES_CACHE` must resolve to a writable,
job-isolated directory when `HF_HOME` points into a read-only model mount.
Qualify the backend's actual cache operation through the effective runner;
directory existence alone is insufficient. Preserve model mounts, pinned assets
and offline settings. Package imports can initialize CUDA, so observe their
effects rather than labeling every import probe CPU-only.
Qualify the current prepared runtime independently from setup discovery. Reuse
existing private connection provenance and bind it to the explicit kubeconfig,
context, cluster identity, service UIDs, ready endpoints and local metrics
backend. Keep these proof references outside the frozen environment schema;
never add credentials or unsupported fields to that receipt.

For monitoring, verify exactly one healthy results scrape with label preservation,
the intended local VMAgent write destination and Grafana query backend, and fresh
GPU telemetry for the complete accepted inventory. Preserve an explicit
local-only routing requirement. Record the effective scrape configuration and
fresh runtime observations under the existing `grafana` preflight check. Actual
publication generation and readback remain mandatory in the evidence stage.
Use the course's readiness and `verify_monitoring.py` checks for the invariants
they cover; retain independent checks for the prepared runtime rather than
claiming an incompatible setup check passed. Do not reduce GPU inventory or
accept missing, ambiguous, stale or foreign runtime evidence.

The shared README's `course_setup.py` workflow owns new connection discovery and
the current `gpu-course-monitoring/v2` receipt. Do not rerun setup solely to resume
a prepared campaign, convert old receipts, or change the setup helper's ownership
checks. If new connections are needed, use that workflow and its explicit
`verify_monitoring.py` check with `--receipt PRIVATE_JSON`,
`--kubeconfig PRIVATE_KUBECONFIG` and `--context ACCEPTED_CONTEXT`. Missing runtime prerequisites still block
execution. Existing Grafana, Nsight viewer/streamer, `nsys`/`ncu` runtime and task-owned
forward recovery is part of the campaign under
[service and profiler recovery](tool-recovery.md);
apply it and recheck readiness before proceeding. Infrastructure installation
and recovery outside that scope belong to their owning workflow. Environment
recovery alone is not lab acceptance evidence.

Read the installed/source CLI contract for
`nebius-cxcli soperator profiling install --help` and its profiling documentation.
**Do not execute install.** Resolve the accepted release names, namespaces,
ClusterIP services, ready pods, auth Secret references and authoritative reports
PVC/submount. Verify the live objects match the accepted target. Installation
readiness alone does not verify reports or browser rendering.

Current cxcli contract exposes separate HTTP and TURN TCP ports for each viewer.
Forward both to `127.0.0.1` with exact context/namespace/service. Preserve TURN's
configured local port; HTTP may use a free loopback port. Reuse compatible
existing forwards after checking identity and health. Keep credentials in memory
and preserve unrelated connections. Reject unexpected public routes.

## Local port-forward recovery

An exited or missing local forward for the campaign's Grafana, Nsight Systems
or Nsight Compute connection is recoverable under its existing target/access
authorization. Recreate it as often as necessary without repeated approval or
a recovery-count limit, including after a prior session ended. A remote-service
restart is a separate action; do not require its non-production classification
or approval merely to restore an already-authorized local connection.

1. Reload the campaign's accepted connection identity and inspect the failed
   process/session and its bounded error output. Resolve the exact kubeconfig,
   context, namespace, service or SSH target and port mappings; never fall back
   to the default cluster or invent a new access scope.
2. Inspect existing local listeners. Reuse a healthy matching forward; otherwise
   start the missing one. A dead prior-session process need not have been created
   by the current agent. Verify identity before stopping a stale campaign-owned
   process, and never kill a foreign listener to reclaim a port. Use another
   free HTTP port and update the private browser endpoint when supported;
   preserve Nsight's configured TURN port and resolve any conflict explicitly.
3. Launch the forward with `127.0.0.1` binding in a persistent tool-managed
   session or supervised local process that survives the launch tool call.
   Retain its handle and any redacted diagnostics privately outside Git, with
   no credentials in argv or logs. Keep both installed Nsight HTTP/TURN mappings.
4. After the launch call returns, check that the process and listeners remain
   alive and verify the intended service through the connection. Use bounded
   readiness waits; a background PID or initial "Forwarding from" message alone
   is insufficient. Reconnect the browser and verify the exact dashboard/report,
   then continue the current evidence stage without replaying completed GPU jobs.

If the forward exits again, inspect the new error and recover the transport
within the same authority. Report the concrete unresolved cause, such as a
tool-policy denial, missing credential/access, unavailable target or conflicting
foreign listener, only when no permitted useful action remains. Do not report
the mere exit as a permission blocker or bypass actual tool/access controls.
Kubernetes explicitly notes that a forward ends when its selected pod terminates
and the command must be rerun; see the
[port-forward reference](https://kubernetes.io/docs/reference/kubectl/generated/kubectl_port-forward/).
