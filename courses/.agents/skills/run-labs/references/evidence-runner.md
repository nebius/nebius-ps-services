# Reusable evidence runner

Use this internal protocol after Slurm execution reaches independent
verification. The public skill actions remain `run`, `status`, `resume` and
`cancel`. Keep one allocation at a time. Collection, hashing and
evidence replacement retain their existing owners and behavior. The exporter
uses the shared course archive assembler for the combined dashboards-and-results
ZIP; see [public export](evidence.md).

## Private inputs and ownership

Create an owner-only manifest outside Git with schema
`run-labs-evidence-runner/v1`. Bind `campaign` (absolute directory),
`campaign_id`, frozen `source_sha256`, exact `unit` (`course:lab`) and `profile`.
Set `checked` to the family verifier's independent JSON, `verification_receipt`
to its controller receipt, and record observed `hardware` and native-report
`limitations`. The checked JSON contains every job (`stage`, `job`, `results`
with original relative `path`) and the complete `native` report list accepted
by the existing evidence validator. No family correctness check is generalized
away; inspect the prepared verifier's effects and contract before binding it.

An adapter has absolute-executable `argv`, `pins` mapping each reviewed script
and supporting input path to SHA256, JSON `outputs` paths, and bounded
`timeout_seconds` (default 300, maximum 1800). Include every executable helper
and imported private dependency in its pins. No shell command interpolation.
`verify` is an adapter; `viewer_aliases` is a list of adapters that stage and
hash-check task-owned native report copies. Logs and intent receipts remain
private under the campaign's `evidence-runner/course/lab-profile` directory.

Each `publications` entry has `intent`, `confirmed`, exact paired `stages`, and
an inspected `adapter` for the existing course publisher. A confirmed receipt
is reused. An intent without confirmation stops for independent reconciliation;
never delete an intent to force retry. Confirmed receipts bind lab, profile,
stage pair, selected originals and positive publication generation.

Each `grafana` entry has the same `confirmed` and `stages`, `folder_uid`, a
stable `prefix`, new absolute `observation` JSON and `review` JSON paths.
Every publication needs at least one matching capture. The runner captures all
views of one published comparison before publishing the next comparison, since
the next generation replaces the dashboard's current values.
Optional `result_indices` chooses baseline/candidate child indices, both zero
by default. Configure exact `datasource_map` UID substitutions. The runner
derives dashboard, metrics, result values, job identities and absolute window
from frozen course sources and collected originals. For zero-metric labs,
provide a public-safe `overview_clip` containing the complete generation and
both correctness rows. Use finite `x`, `y`, `width` and `height` values inside
the fixed 1920 by 1080 viewport, with at least 640 by 360 pixels. The runner
rejects missing or invalid crops for declared zero-metric labs before adapters
or publication. After scrolling, the browser waits for current control values
to render fully inside that crop; earlier off-screen checks are insufficient.
For other display units than seconds,
unitless, percent or IEC bytes, supply `display_scales[unit]` mapping each
expected rendered suffix to its conversion factor; never guess a conversion.

`native_reviews` lists the explicit per-configuration visual review files.
Each binds lab, profile, stage, job, producer, screenshot basename and SHA256;
requires `visual_review`, `public_view_review`, `numeric_match` and the applicable
kernel/NVTX, peer-copy or NIXL-transfer match from the native content validator.
Fundamentals Lab 03 requires `matching_host_copy`, including when incidental
kernels exist. Its complete transfer proof and both configuration views remain
mandatory; a generic kernel/NVTX approval cannot replace copy inspection.
For Advanced Lab 29 use `matching_nixl_transfer` and select the representative
initiator by its observed vendor role; process-file rank is not a stable role.
Compute also requires `launch_geometry_match`
and `sol_occupancy_memory_match`. Retain the independently compared native
values and warning limitations. Grafana reviews bind lab, profile, generation,
both visual booleans, and an exact basename-to-SHA256 `screenshot_sha256` map.
The runner never writes either form of visual approval.

## Persistent browser

Set manifest `node` to the prepared Node executable and `browser_config` to a
separate owner-only JSON. Do not change the campaign's frozen environment file.
Browser configuration contains:

- `playwright_module`: absolute path of the prepared Playwright package.
- `channel`: prepared `chrome` or `chromium`, or omit for prepared Chromium.
- `socket`: new absolute Unix socket path under a private directory, under
  100 bytes. No TCP listener or CDP endpoint is opened.
- `output_root`: existing owner-only directory for new PNG/JSON outputs.
- `services`: `grafana`, `systems` and `compute`, each with its own loopback
  `url`. If needed, `credentials` has an inspected absolute-executable `argv`
  returning existing credential JSON, dot paths `username`/`password`, and
  optional `base64: true`. Values remain only in process memory.

Start `node <skill>/scripts/capture_browser.cjs serve PRIVATE_CONFIG` once.
Send owner-private requests using `capture_browser.cjs request PRIVATE_CONFIG
PRIVATE_REQUEST`. Contexts and pages stay open between requests and are isolated
by service. Configuration and browser source hashes must match the worker.
The worker serializes requests and rejects a concurrent operation. Do not
persist browser storage, auth screenshots, HAR or traces. Cross-origin HTTP
requests are blocked; retain the prepared HTTP and TURN forwards.

Native requests select `service`, `operation` (`open-report`, `inspect-report`,
`close-report`), exact `report` (`viewer_path`, `sha256`, `job`, `producer`), new
absolute `output`, public-safe `clip`, and bounded `actions`. Actions are typed
`click` (`x`, `y`, optional `button`/`double`), `key` (`key`), `text` (`text`),
`locator` (`selector`) or `wait` (`ms`, at most 10000). Use coordinates from the
actual inspected layout. Opening must type the exact staged path; close only
the previously identified owned report. Native actions declare intent, not
successful selection: inspect the returned PNG before subsequent navigation
and final approval. After partial navigation, inspect the current state instead
of replaying an open request or assuming row ordinals are flat.

Choose the target event from independent native proof before navigating. In
Systems Events View, use a selective Name search, or a Description search for
the event's displayed duration or start time. Check the actual match count,
name, time, stream and launch geometry; equal durations alone do not identify
an event. Zoom to the verified selection and inspect its enclosing NVTX range.
Hierarchical rows make SQLite row counts unsuitable for blind paging.
NVIDIA's [guide](https://docs.nvidia.com/nsight-systems/UserGuide/#events-view)
covers search and timeline correlation; zoom changes visible events.

A streamed viewer may still be processing a completed input batch. Wait at
meaningful navigation boundaries and inspect the rendered state before the next
batch. Do not add a fixed delay to every key or equate an RPC response with a
completed GUI transition. Inspect file-dialog completion popups before
dismissing them: Escape without a popup can close the entire dialog. Check
typed search text and the loaded report identity before continuing.

Browser reuse does not require retaining native report memory indefinitely.
When measured retention requires it, follow the owned native-tool maintenance
sequence in [browser-evidence.md](browser-evidence.md) between completed
reviews. Bind the close request to the reviewed report, inspect the returned
desktop and verify memory release before the next open request. Retain the
same browser worker and its isolated service contexts.

In Compute, inspect the selected report's Details page. When section prose
pushes required metrics below the viewport, use View > Hide Section
Descriptions to bring launch geometry, Speed Of Light, memory and occupancy
values together. Keep warnings and rule output visible, and verify every
required value remains readable before accepting the capture. This changes
only the report presentation; see NVIDIA's
[report controls](https://docs.nvidia.com/nsight-compute/NsightCompute/index.html#header).
If a keyboard shortcut produces no visible transition, inspect the current
state and use the viewer's File > Open File menu through `inspect-report`
with the same reserved report identity.

The runner builds Grafana requests and records compact observations. Use the
worker's `status` operation to inspect service reuse and `stop` to close only
this worker's browser. Session expiry, lost responses and viewer failures stop
the current helper request without automatic replay. The agent then follows
[service and profiler recovery](tool-recovery.md)
and may recover repeatedly without a fixed count. Reconcile uncertain effects
before replay; prove an old worker is quiescent before removing its exact stale
socket. Never adopt another worker or remove unrelated processes or sockets.
An exited local Grafana or Nsight forward follows
[local port-forward recovery](environment.md#local-port-forward-recovery): restore
the connection, verify it survives the launch call, and resume this evidence
stage. Do not treat the transport exit alone as missing restart permission.

## Execute and review

1. Run `python3 <skill>/scripts/evidence_runner.py status PRIVATE_MANIFEST`.
   This is read-only and reports the current exact unit gate.
2. Run its `prepare` operation. It verifies frozen inputs, runs pinned adapters,
   records verification, collects originals and stages viewer aliases. It then
   publishes/reuses each comparison and captures its Grafana views before
   advancing to the next comparison. It stops at
   `visual-review`; it never starts another Slurm job.
3. Use the persistent native viewer to inspect each required configuration.
   Inspect every Grafana and native PNG. Write explicit hash-bound reviews.
4. Run `finish`. It assembles the reviewed evidence, revalidates every original,
   metric and native-report requirement, then delegates record/export to the
   existing controller. Interrupted export resumes through that controller.

Adapter intent is written before effects. Failed or uncertain invocations
require independent reconciliation; neither a timeout nor an absent Slurm
accounting row authorizes replay. Completed adapter outputs are hash-checked on
resume. A runner/manifest revision change requires reconciliation at the
evidence boundary. Course inputs and recipes remain frozen; changing only the
skill does not require rerunning completed GPU work.

Use [Playwright contexts](https://playwright.dev/docs/api/class-browsercontext)
for isolated sessions and [actionability](https://playwright.dev/docs/actionability)
for DOM readiness. The native canvas still needs visual inspection.
[Node IPC](https://nodejs.org/api/net.html#ipc-support) provides the private
Unix socket; its path length is bounded for macOS as well as Linux.
