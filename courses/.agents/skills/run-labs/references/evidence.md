# Evidence and replacement contract

Private current originals:
`PRIVATE_ROOT/raw/COURSE/LAB/small|large/`.
Public current files:
`COURSE/reference/lab-results/LAB/small|large/`.
Control receipts remain under `PRIVATE_ROOT/campaigns/ID`; they are not student
artifacts. A staging set belongs to exactly one campaign and lab/profile.

Public files are `summary.csv`, `manifest.json`, `result-VARIANT-trialN.json`,
`grafana-COMPARISON.png`, `nsight-systems-VARIANT-WORKER-timeline.png` and
`nsight-compute-KERNEL.png` where applicable. Use stable meaningful tokens.
No README, interpretation report or timestamp suffix. The external
`reference/COURSE-lab-results.zip` combines `grafana-dashboards/`,
`small/LAB/` and `large/LAB/` without nested ZIPs. It includes environment
readiness and retains exact dashboard, manifest and artifact bytes. The course
page links this archive and the shared lab setup guide after generation. Original
lab scripts are delivered through `sync-labs.sh`; no lab-kit ZIP is generated. The shared
`tools/course_archives.py` assembler validates identities, ownership, symlinks
and checksums before replacing the archive under the existing course lock.
A packaging failure preserves the previous archive; the evidence transaction
and publication checkpoint retain their existing recovery behavior.

`inventory.json` uses `run-labs-inventory/v1` and `files` entries with relative
`path`, `size` and `sha256`. Collection first records remote originals. Generate
native CLI proofs in the owned remote results before collection.
The collector budgets transfer time from the remote inventory's total bytes at
5 MiB/s, with a 30-minute minimum and four-hour maximum. This sizes the deadline;
rsync uses the available transfer speed. A failed copy keeps completed files for
the next collection attempt. All original files and independent checksum checks
remain required before collection can complete.
Browser PNGs are local staged artifacts, separately hash-bound in the evidence
receipt; do not add them to or rewrite the immutable remote inventory. Capture
proof is independent of report provenance, not a replacement for it.

Record the browser stage with `run-labs-evidence/v1`:

The receipt contains these fields:

- `schema`: `run-labs-evidence/v1`; exact `lab` and `profile`.
- `hardware`: only `gpu_model` (observed NVIDIA model) and integer `gpu_count`.
- `results`: every execute stage ID maps to its exact original JSON paths in
  trial order. Capstones require three counterbalanced results; engine campaigns
  require six pairwise-validated results and distinct client process windows.
- `native_reports`: every expected capture producer has `stage`, `tool`, `job`,
  stable `producer`, relative `path`, `sha256`, observed `kernels` and `nvtx`,
  relative native CLI `proof`, `proof_sha256`, `native_cli_verified: true`, and
  `warnings` array. All reports and proof bytes must be in the remote inventory.
  Fundamentals Lab 03 requires `host_copy` proof even with incidental kernels;
  its copy-only reports may have empty `kernels`. See the host-copy schema below.
  Advanced Lab 06 may record an empty `nvtx` list: its pinned vendor does not
  promise annotations. Its CE capture additionally requires `peer_copies`,
  covering all 56 distinct directed GPU pairs with integer `source`,
  `destination`, positive `events` and `bytes` equal to the event count times
  the profile's buffer size. Derive these counters from native peer-copy
  events, excluding host initialization transfers, and retain the query proof.
  Its SM capture must contain the vendor's `stridingMemcpyKernel`. Inspect the
  measured copy activity in the native GUI; initialization kernels alone are
  insufficient.
  Advanced Lab 29 may record empty `kernels`: UCX GPU transfers need no CUDA
  kernel. Bind each report to its observed NIXL role, independently of file rank,
  and require both initiator and target per configuration. Its `nixl_transfer`
  proof records positive integer `cuda_api_events`, `osrt_events` and
  `registration_events`, `buffer_bytes: 1073741824` and `buffer_device: 0`.
  The initiator must show 550 `write_ranges`, `put_submissions` and
  `put_completions`, with `put_bytes` equal to 550 times the profile block size
  (4096 or 4194304 bytes). Derive these from NIXL write ranges and matching
  UCP put submission/completion events, including their payload sizes.
  The target requires positive `notification_polls` and `notification_end_ns`,
  followed by one `validation_copy` with profile-sized `bytes`, `device: 0`,
  `direction: "device-to-host"` and increasing integer `start_ns`/`end_ns` after
  the final notification poll. Retain native CUDA buffer preparation, vendor
  role/registration and write/notification annotations, both successful vendor
  processes and consistency checks. Initialization alone cannot pass. Keep the
  timing comparison in the unprofiled originals; these traces are diagnostic.
  Advanced Lab 33's KV candidate may leave one replica idle. Only that replica
  may have empty `kernels`, with a `routing_idle` proof using schema
  `run-labs-routing-idle/v1`, `router: "kv"`, the exact `job`, `producer` and
  distinct `peer_producer`. Require integer zero `selected_requests`,
  `completed_requests`, `kernel_events` and `inference_nvtx_events`;
  `session_requests`, `peer_selected_requests` and `peer_completed_requests`
  must each be 69 (64 measured, four warmup and one readiness request).
  Require `gpu_devices` 0 through 7 and eight `profiler_starts`, each with a
  distinct positive `pid` and positive `timestamp_ns` from native API events.
  Require one through eight `profiler_stops` with distinct matching PIDs and
  timestamps after all starts, plus true `capture_start_acknowledged` and
  `capture_stop_acknowledged` from the owned service control acknowledgements.
  Capture termination can omit a worker stop event; do not invent one.
  Independently bind those PIDs to the owned TP
  workers, retain warnings and prove zero assignment from complete router and
  worker logs. An absent lazily created kernel table alone proves nothing.
  The same configuration must include the active peer's normal kernel/NVTX
  proof and `routing_activity` with matching integer `selected_requests` and
  `completed_requests`. An idle report cannot replace that peer's evidence.
  Other producers retain their kernel and NVTX requirements.
- `screenshots`: each entry has stable `name`, local staged `path`, `sha256`,
  `tool`, `browser: "playwright-headless"`, `visually_reviewed: true`, and
  `public_view_reviewed: true`. Use a restricted/cropped UI view that excludes
  private report paths, hostnames, credentials and unrelated tabs.
- Grafana screenshots also have the exact `dashboard_uid`, `profile`, integer
  `generation`, and `numeric_checks`. Each check identifies `stage`, zero-based
  `result_index`, metric `metric`, positional `case`, `unit`, and the rendered
  value converted to `observed_base_units`. The union must cover every declared
  metric of every selected result, within 0.5 percent rounding tolerance.
  When the observability recipe declares no available quantitative metrics,
  use an explicit empty `numeric_checks` list. Still inspect the rendered
  generation, selected-result correctness and job identity; all result,
  dashboard and native evidence checks remain required.
- Native screenshots also bind `report`, `report_sha256` and `content_checks`
  (`matching_job`, `matching_kernel_or_nvtx`, `native_cli_verified`, all true).
  For Advanced Lab 06's CE capture, use `matching_peer_copy` in place of
  `matching_kernel_or_nvtx`, after inspecting its actual peer-copy activity.
  For Advanced Lab 29 use `matching_nixl_transfer` after checking the actual
  UCX transfer or target validation activity against the role-specific proof.
  For Fundamentals Lab 03 use `matching_host_copy` after inspecting the actual
  copy timeline and detail against the independently verified transfers.
  A Lab 33 idle-replica screenshot uses `matching_routing_idle` if included,
  but does not satisfy the configuration's representative-view requirement:
  inspect and capture the active peer's measured inference.
  Every capture configuration needs a representative screenshot, while content
  proof covers all producers.
- Optional `limitations` uses public codes: `incomplete-events`,
  `separate-clock-domains`, `representative-gui-rank`, `diagnostic-companion`,
  `vendor-display-unit`, `unlocked-gpu-clocks`, `cpu-scheduling-unavailable`.
  Full diagnostics remain private.

## Fundamentals Lab 03 host-copy proof

`host_copy` uses schema `run-labs-host-copy/v1`, exact `job` and `producer`
(`rank-0`), `buffer_bytes`, `device: 0`, and the observed `context_id`,
`stream_id`, `global_pid` and `global_tid`. Bind all API/copy activity to this
single process/thread/context/stream. `workload` contains `name: "lab_workload"`
and positive integer `start_ns`/`end_ns` from the enclosing NVTX interval.
The reviewed commands explicitly select 64 MiB baseline and 128 MiB candidate
for both profile labels; do not infer payload size from the profile name.

Require four ordered `modes`: `pageable_blocking`, `pageable_nonblocking`,
`pinned_blocking`, `pinned_nonblocking`. Each row has its `mode`, `host_memory`
(`Pageable` or `Pinned`), `direction: "host-to-device"`, and integer
`submissions`, `completions` and `correlated_copies`, all 25 (five warmup plus
twenty measured). `bytes` equals 25 times the effective buffer size; `segments`
counts native activity rows and may exceed the logical-copy count. Derive
these values from successful copy APIs and complete correlated activity,
checking every segment's direction, memory kinds, bytes and device identity.
Use the report's enum labels, not enum numbers from a different API schema.

Each mode records `first_submission_ns`, `last_submission_end_ns`,
`first_copy_start_ns` and `last_copy_end_ns`. All bounds lie inside the workload,
and each complete group precedes the next group's submission. Verify
`device_synchronizations: 21`: one after all warmups and one after every
measured copy. `stream_synchronizations` is 25 for blocking modes and zero for
nonblocking modes. Derive successful synchronization counts from their exact
API positions and verify completion before the next dependent operation.

`validation_copy` uses the same fields except `mode`, with
`direction: "device-to-host"`, `host_memory: "Pageable"`, one correlated
submission/completion, one payload of `bytes`, zero device synchronizations
and one stream synchronization. It follows all four groups. The source's
equality assertion checks only this final destination; do not claim independent
per-mode equality or copy/compute overlap. Preserve original warnings, query
proof hashes and both configuration views. Unprofiled results own timings.

## Shared validation and replacement

Use the actual complete stage map and applicable Compute screenshot. Each named
result must pass the existing course validator and exact profile/producing-job
checks. The exporter independently repeats every declared pair check and metric
extraction. Native proof must include every report/rank; screenshot selection
may be representative only when that scope is explicit. Keep software versions,
effective workload settings, result/report/job associations and limitations in
private proof, and sanitized public provenance without endpoints or credentials.

Public result JSON is a projection with its own schema/hash; it is not claimed
to be byte-identical to the raw original. Arbitrary strings and infrastructure
fields are omitted. CSV uses declared metric names, scaling and units. Keep
original bytes privately for independent inspection.

Replacement copies only owned files, preserves unknown files, and refuses a
name collision with an unrelated file. The ownership manifest records exactly
what may be replaced. A journal rolls back interrupted pre-commit updates and
finishes cleanup after commit. Do not hand-delete journal/backup files to force
progress. A failed new campaign leaves the previous complete result visible.
