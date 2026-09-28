# Service and profiler recovery

Use this procedure whenever an existing Grafana service, Nsight Systems/Compute
viewer or streamer, `nsys`/`ncu` runtime, browser session or owned forward stops
working during an authorized lab run or resume. Full recovery of these identified
components is included in the campaign: **no fixed recovery count and no further
approval for actions already within target authority**. Bound each operation and
readiness wait, inspect its result, then choose the next useful action. An error
from one helper or an earlier restart does not exhaust recovery authority.

## Connections, browsers and services

1. Preserve original reports, hashes, failed-attempt evidence and completed
   checkpoints. Inspect the actual connection, service and browser failure.
   Resolve installed owners from accepted target/connection receipts; every
   Kubernetes call uses explicit kubeconfig, context and namespace. Keep
   credentials in their existing store or process memory and diagnostics private.
2. Restore the exact-target, loopback-only connections for **Systems, Compute and
   Grafana** as needed, using [local port-forward recovery](environment.md#local-port-forward-recovery).
   Keep both installed HTTP/TURN mappings for each Nsight viewer; three service
   connections need not mean three TCP port mappings or three processes. Reuse
   healthy owned forwards, recreate missing/exited ones, and verify listeners,
   process lifetime after launch returns and the actual service response.
3. Reconnect the affected browser context using the
   [evidence runner](evidence-runner.md). Inspect the visible response to a
   harmless navigation: open the intended dashboard or report, change its view
   and confirm the displayed content responds. A successful HTTP request, a
   mounted page or an unchanged canvas alone does not prove a working session.
   Keep authentication in memory; do not capture a login form or save browser
   storage, HAR or traces. Reconcile uncertain publication/helper effects first.
4. When connection/session recovery is insufficient, restart the affected
   authorized component. For an installed Nsight Streamer that supports it,
   **File > Exit** restarts the native tool in the task-owned session and can
   release retained report memory. Preserve saved evidence before maintenance
   between reports; retain healthy browser contexts. If that cannot restore the
   tool, restart the exact identified Nsight deployment/service through its
   installed controller or service manager. A stopped or unresponsive Grafana
   service may likewise be started or restarted through its installed owner.
   Reuse existing action authority; do not request approval again merely because
   a remote restart is now needed or has already been tried.
5. Before replacing a process/pod, verify originals are durable outside its
   disposable storage and that the restart preserves configuration and persistent
   data. For Grafana preserve database/storage, plugins, dashboard and datasource
   identities, provisioning, secrets and private routing; for Nsight preserve
   report mounts, originals and existing settings. Restart the current configured
   workload, without upgrade, reinstall, config reset or deletion of PVCs/data.
   Confirm unrelated active work will not be interrupted. A shared service is
   not owned merely because the campaign uses it; remote mutations require a
   confirmed non-production target or existing exact live-action authorization.
6. Observe rollout/service readiness and relevant resource pressure. Restore any
   forwards invalidated by a replaced pod, reconnect the browser and repeat the
   visible navigation check. For Nsight, reopen the same original report and
   verify its hash, operation selection and expected contents. For Grafana,
   recheck the exact dashboard, datasource, profile, time window, jobs, publication
   generation and displayed values against retained originals. A healthy service
   alone is insufficient; repeat affected captures and actual visual review.
   Resume the evidence checkpoint without resubmitting completed GPU work.

If recovery still fails, use its new observations to diagnose the next scoped
action. Do not blindly repeat an ineffective restart. No namespace-wide/node
restart, unrelated service interruption, resource-limit change, new installation,
IAM expansion or public exposure is included. Respect actual tool/access denials;
report the concrete unresolved restriction only when no useful permitted recovery
remains. Existing authority, not a numeric retry cap, determines the boundary.

Match installed versions and topology to the vendor mechanisms:
[NVIDIA Streamer restart](https://docs.nvidia.com/nsight-operator/NsightStreamer/index.html#streamer-troubleshooting),
[Kubernetes workload restart](https://kubernetes.io/docs/reference/kubectl/generated/kubectl_rollout/kubectl_rollout_restart/),
[Grafana service start/restart](https://grafana.com/docs/grafana/latest/setup-grafana/start-restart-grafana/)
and [Grafana persistent data](https://grafana.com/docs/grafana/latest/administration/back-up-grafana/).

## Profiling runtimes: nsys and ncu

`nsys` and `ncu` are profiling tools with sessions/processes, not generic services
to restart by name. Their recovery is included, independently of viewer health.

1. Inspect the exact job's exit state and original stdout/stderr, installed tool
   version/path, execution image/node, environment and output directory. Separate
   an actual application/correctness failure from a tool, transport, storage or
   session failure. Do not call every nonzero profiler exit an environment error.
2. Reconcile saved submission intent and exact owned job/session/process identity
   before any stop or relaunch. Preserve partial reports and diagnostics. Use the
   installed tool's supported scoped stop/shutdown for an owned stuck session,
   or the existing exact-job cancellation path; wait for terminal state and prove
   writers quiescent. Never use broad `pkill`, cancel foreign jobs or remove a
   lock merely because its file exists. Nsight Compute deliberately retains its
   serialization lock file after profiling. Do not stop unrelated monitoring or
   profilers to free counters.
3. Restore the existing prepared runtime, executable selection, session and
   authorized working environment without changing the frozen lab recipe. Reuse
   existing configured credentials and permissions. Do not reinstall/upgrade the
   tools, relax hardware-counter/security permissions, reset GPUs or reboot nodes.
   A missing installation or actual permission/resource conflict needs its owning
   workflow; it does not justify weakening capture or correctness requirements.
4. Recheck the failed prerequisite and resume the controller's next eligible
   action. If its diagnostic stage is terminally failed, follow
   [failed profiling unit recovery](execution.md#failed-profiling-unit-recovery).
   A version/help response is only preliminary readiness: recovery must ultimately
   produce a valid required capture through the unchanged recipe. Verify the
   original report is readable, has the expected activity/counters and belongs to
   the new exact producing job. Retain warnings and perform native browser review.
   Instrumented timing never replaces unprofiled acceptance measurements.

Use installed help and the matching official
[Systems CLI session controls](https://docs.nvidia.com/nsight-systems/UserGuide/),
[Compute CLI](https://docs.nvidia.com/nsight-compute/NsightComputeCli/) and
[Compute error/serialization guidance](https://docs.nvidia.com/nsight-compute/ProfilingGuide/)
before choosing version-specific commands. A restored environment is recovery
evidence, not proof of a profiler or course-source defect being fixed.
