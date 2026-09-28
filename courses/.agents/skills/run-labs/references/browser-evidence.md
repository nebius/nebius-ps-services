# Native browser evidence

Required tools are headless Playwright and the installed Grafana/Nsight viewers.
Use `scripts/capture_browser.cjs` with the host's existing Playwright package
and prepared browser channel. Follow [evidence-runner.md](evidence-runner.md)
for its private configuration and persistent session protocol.
Do not install a new browser framework. Set bounded locator timeouts so an
absent control does not reset the host's browser session.

## Grafana

Import the selected course's `reference/grafana/<lab>.json` with:

```text
nebius-cxcli grafana import DASHBOARD --config PRIVATE_CONFIG \
  --target EXACT_TARGET --overwrite
```

Resolve data-source mappings with the installed CLI's supported
`--datasource-map SOURCE=UID` when needed. Never guess the target spelling.
Verify that each requested dashboard's UID, course identity and destination
folder match the authorized campaign. Preserve those identities, private
service routes and unrelated dashboards. The campaign request already
authorizes these updates: provisioned or foreign-owner metadata alone is not
a blocker and does not require another confirmation. Use `--overwrite` and
retain the installed CLI's editability, ownership and concurrency checks.
An actual CLI rejection requires resolving its specific supported update path
within existing authority; never bypass it by changing identity or disabling
management checks. Do not use `--attach` to mutate another catalog.

File provisioning may later replace an API update; record that limitation and
verify the effective dashboard definition before capturing evidence. Update
the provisioning source only when that change is within the requested scope.
See [Grafana provisioning](https://grafana.com/docs/grafana/latest/administration/provisioning/)
for the upstream persistence behavior. An unchanged import is reusable.

Use the course publisher with a stable `workspace_id` and explicit current
publication generation. Preserve publication state across fresh workspaces;
query and reconcile the current selection before overwriting it. Select
unprofiled originals and preserve the course's comparison rules. An internal
trial campaign uses its validated child pair/aggregate, not arbitrary newest
JSONs. Capture each required comparison with stable names. Browser time range,
course/lab/profile/workspace and generation must match the selected runs.

Check all displayed lab numeric values against the original JSON/metric scales,
not just page loading. Require no query errors or unexpected No data panels.
GPU telemetry is contextual unless allocation and run windows prove attribution.
Save the resulting comparison PNG and numerical check receipt.

## Nsight Systems and Compute

Stage only selected reports under a task-owned folder in the accepted reports
submount. Verify SHA256 after copy, and give the viewer account read/traverse
permissions on these copies only. Resolve producer and viewer paths from the
installation receipt; do not assume an entire jail PVC is the reports mount.

These viewers are streamed native desktop canvases over WebRTC, not ordinary
DOM applications. Forward HTTP **and TURN**, log in using the existing Secret
in memory, and use Playwright keyboard/mouse to open the exact report. Confirm
the filename/window title and selected operation. Allow bounded rendering waits.
Open one large report at a time; close the previous task-owned report first.
Do not silently increase viewer memory or kill unrelated sessions.

Use [service and profiler recovery](tool-recovery.md) for retained
memory, stuck native tools, failed streams or expired browser sessions, both
between completed reviews and after failed capture attempts.

For Systems show the relevant GPU timeline/NVTX and kernel/event detail, with
worker/variant identity. Inspect content for every expected rank even when the
GUI screenshot samples a declared representative rank. For Compute show kernel
identity, launch geometry and selected Speed of Light/occupancy/memory metrics.
Compare UI numbers with CLI-derived values using declared rounding tolerance.
Record report warnings and limitations; a screenshot does not prove lossless
capture or synchronized cross-host timestamps. A vendor unit-display defect
must be recorded explicitly, never hidden by relabeling a metric.

For Advanced Lab 29, inspect the actual UCX put submission/completion and its
payload size on the initiating process, or the target's final validation copy
after notification polling. Bind the selected role to vendor output; it can
change file ranks between runs. CUDA kernels are not required for this GPU DMA
workload. Preserve native warnings and the separate unprofiled comparison.

For Fundamentals Lab 03, inspect the host-copy timeline under `lab_workload`
and representative copy detail for each baseline/candidate configuration.
Compare direction, pageable/pinned memory kind, byte size, stream and timestamps
with the independently verified native records. Require the complete four-mode
copy/synchronization proof and final readback described in
[evidence.md](evidence.md#fundamentals-lab-03-host-copy-proof), even if incidental
kernels appear. Use `matching_host_copy`; no Compute kernel view is applicable.

Keep one browser process and isolated in-memory contexts for the three services.
Use locator readiness for Grafana and bounded render waits for native canvases,
without a fixed delay after every key. Inspect the current layout before each
new navigation batch. Events View can nest overlapping kernels: a count of
ArrowDown presses is not proof of event identity. Bind the displayed filename,
selected kernel/event, units and geometry to the independently checked report.

For Grafana, omit redundant overview images when inspected metric tables carry
all values. Crop the real inspector to its header and fully visible rows at
native scale; retain pagination for tables that overflow. Each image receives
only the numeric checks for its visible cases. Zero-metric qualifications still
retain an actual dashboard image and their rendered control checks. Never
compose a collage, alter DOM values or claim hidden rows were photographed.

Inspect every PNG with the host image tool before writing its hash-bound visual
review. Never photograph a login form or save browser auth/storage/trace files.

## Service and browser recovery

Follow [service and profiler recovery](tool-recovery.md) for Grafana, both Nsight
viewers/streamers, repeated native-tool restarts and browser reconnection. Restore
exited loopback forwards first when that is the fault; restart the affected
identified deployment/service when connection recovery is insufficient. These
actions reuse campaign authorization without a fixed count or repeated approval.

Verify a visible response to navigation after reconnecting, then the exact
original report or dashboard content under the gates above. Preserve
configuration, persistent storage, saved evidence and unrelated work. Resume
capture from the evidence checkpoint; a viewer or Grafana outage does not require
repeating completed GPU jobs. For a failed `nsys`/`ncu` capture itself, use the
separate profiling-runtime and failed-unit procedure in the recovery guide.
