# Execution protocol

The agent runs `scripts/run_labs.py`; its JSON `next_action` is a durable work
instruction. Continue until status is complete or a real blocker is identified.
Do not return to the user merely because the next stage requires agent tools.
The state controller is intentionally separate from native GUI reasoning.

Every execution stage submits the same explicit per-lab `.sbatch` file shown to
students. Normal jobs run the lab directly; `.nsys.sbatch` and `.ncu.sbatch` place
the native NVIDIA command at the GPU process. The selected job owns its profiler
flags and filters. `--workload small|large` selects problem size; it does not enable
profiling. The skill accepts `--workload both` to schedule both sizes.

Fresh plans use `execution_contract: native-jobs/v2`. Status and cancellation can
inspect earlier campaigns, but execution refuses an older plan: retain its evidence
and create a new campaign. This version requires managed-runtime selections and
verified prepared-root bindings. Do not translate old plans or replay saved argv.

Submission prepares private scheduler directories before calling `sbatch` with
an explicit working directory and log paths. Each fresh job exclusively creates
`results/LAB/jobs/JOB_ID/{results,profiles,logs,artifacts}`. Requeue is disabled;
retries require a new job ID. Collection admits only those producing job IDs and
their exact scheduler logs, verifies checksums, and preserves all required originals.
After successful publication, cleanup removes only those verified job directories
and logs. It never deletes a whole results tree or historical evidence.

Existing result JSON fields and metric labels named `profile` remain unchanged;
they describe workload size in the stored evidence. The old CLI `--profile` and
`COURSE_WORKLOAD_PROFILE` environment variable are unsupported.

Optimization Lab 15's Compute recipe selects `uniform_tail_probe` in
`tail_measure`: the first measured grid, excluding input initialization,
warmup, compilation and validation launches. Keep the other grids and concurrent
GEMM task sets in Systems evidence; one Compute capture does not cover them.

Optimization Lab 19 selects `consume_batch` for its first batch GEMM. Lab 20
selects `produce_output` with the kernel filter `.*(gemm|nvjet).*` to exclude
input fill. Kernel expressions match full demangled names without simplification,
including template arguments when a library uses a generic function name.
Both skip weight initialization and capture a warmup GEMM with
default arguments. Keep these diagnostics separate from clean whole-loop
timings and use all declared Systems variants to assess transfer overlap.

Inference Labs 09, 17 and 23 and Training Labs 02, 07, 14, 21, 22 and 30
select their named model/training operation plus a matrix-kernel filter. Retain
the guide's first-invocation boundary: generation, baseline and BF16 diagnostics
do not cover every decoding path, backward pass or precision mode. Training
Lab 06 instead selects its objective range without a matrix filter because its
work is reduction and elementwise math. Exclude setup kernels and verify the
actual native range, kernel and launch before accepting any capture. Keep
all Systems configurations and clean correctness checks.

Inference Lab 10 uses `--in-process` for both Systems variants and Compute;
clean timing keeps the default engine process mode. Only its Compute stage
requests `SBATCH_MEM_PER_NODE=262144` (256 GiB) for replay host memory. Verify
the prepared worker can satisfy it and independently check the actual allocation.
Its Compute selector combines `vllm_generate` with `.*(gemm|gemv|nvjet).*`
to skip staged request-buffer writes and capture a measured matrix launch.
Verify its actual kernel and role against Systems; one matrix capture does not
cover every layer, generation phase or engine operation.
Recipes may declare an `environment` mapping on an executable stage; currently
only `SBATCH_MEM_PER_NODE` is accepted, as a positive decimal MiB string.
The frozen stage value overrides prepared defaults only for that stage.
Unknown keys, zero, units, placeholders and malformed values fail before jobs.
Use this mapping instead of shell prefixes or global resource changes.

Inference Lab 16 queries live Hub metadata even when model files are cached.
Its managed runtime enables metadata access for that lab; verify the activated
environment instead of supplying inherited offline overrides. Do not change
unrelated offline labs or download model weights as part of running the audit.

Interrupted claim acquisition leaves the campaign initializing. Resume recovers
all selected claims under the control lock before any sync or job action. A
claim held by another campaign blocks resumed effects; cancel only the affected
campaign's owned work or wait for the other campaign to finish.

Internal helper (not another user-facing skill):

```text
python3 <skill>/scripts/stage.py CAMPAIGN sync
python3 <skill>/scripts/stage.py CAMPAIGN record --receipt PRIVATE_JSON
python3 <skill>/scripts/stage.py CAMPAIGN bind --receipt PRIVATE_BINDINGS_JSON
python3 <skill>/scripts/stage.py CAMPAIGN advance
python3 <skill>/scripts/stage.py CAMPAIGN collect
```

`sync` runs the inspected repository `sync-labs.sh --sync-only`, verifies every
frozen source hash remotely, and creates isolated per-lab/profile workspaces
with the actual course slug as the leaf. It binds the existing prepared catalog
and runs read-only native activation checks for all selected launchers, recording
runtime fingerprints. A failure gives the owning preparation command and cannot
complete preflight. Record independent hardware/monitoring observations after sync. `advance` submits or polls exactly the
next execution/profile/reference stage. The helper persists intent first and
uses a remote lock and durable job receipt. A lost response reconciles the same
name, owner and job. If accounting has no unique row, stop rather than resubmit.
Do not change Slurm admission/resource flags behind the frozen plan.

Job queries retry SSH transport failures (exit status 255 or timeout) for at
most three total attempts, each with a 60-second timeout. Other exit statuses,
malformed responses and application errors fail immediately. Submission,
cancellation, cleanup and reconciliation each make one transport attempt;
inspect durable intent and receipts before repeating an uncertain operation.

`bind` resolves only declared `${VARIABLE}` placeholders before a stage starts.
For Labs 30/31 in Advanced Communication, find the dependency's exact final
weights, verify its hash and profile, and bind the same artifact to both variants
and diagnostics. Never select an older similarly named reference. Retain the
binding proof alongside originals.

Recipes preserve existing launchers that own repeated independent processes,
alternating variant order, server lifecycle and correctness probes. Do not
replace them with one client process. Repetitions outside those launchers use
separate jobs and alternate variant order. Internal baseline/candidate kernels
are checked inside their supplied executable; do not invent source extensions.
Qualification and CPU-modeling results are not performance speedups.

Optimization Labs 09, 16 and 19 require three independent jobs for each
configured variant in each profile. The runner reverses variant order for the
middle repetition and retains every result. Lab 09 also contains three rounds
inside each process; those rounds do not replace the independent jobs. Keep
native diagnostics separate from these unprofiled repetitions.

CUDA Lab 12 includes the guide's separate memcheck dependency before its three
fresh unprofiled trials. Verify the exact sanitizer job and zero-error original
output with the same executable, image and profile. Keep that diagnostic output
outside acceptance results. Independently recompute the original aggregate
medians, ratio and decision; match all three child records and their shared
observed GPU identity. Use the evidence runner's child result indices to capture
every child's metrics without manufacturing an aggregate result JSON. The
capstone leaves application integration pending until separately demonstrated.

Inference Labs 11, 15, 20, 33 and 34 use fixed server workloads in the supplied
launchers. Their small and large labels currently have identical effective
workload settings. Report this limit and record the actual request counts and
other parameters; do not claim a size comparison or silently invent presets.

Inference Lab 30 runs two fresh equivalent jobs for each protocol: OpenAI and
its repeat, then Triton and its repeat. Publish each protocol's pair separately;
never compare different protocols or select one original for both slots. Each
job owns a loopback server and one bounded request. Both profile labels use the
same qualification workload. Keep all four originals; these probes do not
establish a latency distribution, semantic quality or an engine speedup.

Inference Lab 35 keeps two fresh unprofiled runs for each fixed configuration:
CUDA, CPU and CPU with a one-token budget. Publish each equivalent pair
separately and retain all six originals. Capture Systems only for the CUDA
configuration, as the guide requires; CPU runs have no GPU profiling. Both
profile labels use the same fixed bigram exercise and make no performance claim.

Inference Lab 32 and Training Lab 31 each run the complete three-child
capstone launcher twice independently. Retain all six clean originals and
both validated aggregates. Pair child indices 0, 1 and 2 across the two groups,
checking matching seeds 17, 18 and 19 and their variant orders. Publish each pair
separately; do not combine the two aggregates or reuse a child in both slots.
These multi-result recipes keep `comparisons` empty; index-aware evidence
adapters bind the three child pairs. Systems and Compute each remain separate
single-process diagnostics.

Training Lab 32 keeps two CUDA and two CPU originals, published as separate
same-device pairs. Capture Systems only for CUDA; CPU mode has no GPU evidence
and there is no Compute stage. Both profiles use the same scalar exercise.

Inference Lab 36 retains six deterministic policy records and publishes five
one-control comparisons: no-tier to storage, then storage to TTL, bandwidth,
restart and revision variants. Both profiles use the same request trace.
Computed foreground and write-service costs are model outputs, not measured
I/O or serving latency; no native profiling is required.

After all jobs finish, use course `inspect_results.py`, publisher comparison
validation, capstone/engine aggregate checks and native report-content tools.
Identify exact original files by producing job and source, not newest filename.
Native Systems checks include CUDA activity, expected kernel/NVTX, all declared
ranks and warnings. Compute checks include the selected kernel, geometry and
numeric counters. A local Compute companion is explicitly diagnostic and is
never claimed as collective/server replay. Instrumented timings cannot replace
unprofiled acceptance measurements.

Fundamentals Lab 03 uses host-copy activity and its `lab_workload` range without
requiring CUDA kernels. Verify all four ordered groups of five warmup plus
twenty measured copies, successful synchronization and the final payload
readback through [host-copy proof](evidence.md#fundamentals-lab-03-host-copy-proof).
Both profiles use explicit 64/128 MiB baseline/candidate commands. These are
different payload sizes; each measured copy synchronizes, so do not claim
copy/compute overlap or a same-work speedup. The equality check covers the final
destination only. Preserve both Systems configuration views; no Compute capture
is declared.

Record `run-labs-verification/v1` with frozen `source_sha256` and `jobs` mapping
exact job numbers to `{ "passed": true, "checks": [...] }`. Checks must name
actual independent observations; attach detailed private proof files. Then
`collect` copies only the dispatched jobs' `results/LAB/jobs/JOB_ID/` trees and
their `results/LAB/logs/JOB_ID.out` and `.err` scheduler logs, with remote SHA256
and size inventory. Historical files and other jobs remain in the workspace.
Do not proceed if local and remote bytes differ.

The browser stage is described separately. Export is another `advance`: it
revalidates originals and screenshots, creates sanitized student files, then
replaces public and private current sets under a journal and lock. Keep control
state for resume; result artifacts use stable paths, not timestamped copies.
After confirmed publication, remove only this unit's task-owned remote staging
results/reports after confirming its jobs are terminal and its exact ownership
marker still matches. Preserve historical unmanaged archives. If cleanup fails,
report it and resume cleanup; do not claim duplicate-free completion.
The controller saves publication before cleanup and marks export complete only
after remote and local staging cleanup succeed. Resume after that checkpoint
retries cleanup without rebuilding or republishing the committed result set.

Cancellation checks each exact owned Slurm identity before scancel. Wait for
terminal state before releasing claims; never cancel by broad user, partition,
job-name prefix or cluster-wide selection. An uncertain intent must reconcile
before cancellation completes.

`BOOT_FAIL` and `DEADLINE` are terminal failures. The controller preserves their
accounting evidence, marks the stage and unit failed, and continues other
eligible units. Once no work remains, it marks the campaign failed and releases
its claims. Cancellation does not send `scancel` for these already-terminal
jobs. Resume uses this handling for existing campaigns without rewriting their
records or automatically resubmitting failed units.

## Failed profiling unit recovery

Recover an existing `nsys`/`ncu` runtime through
[service and profiler recovery](tool-recovery.md). A failed diagnostic-tool stage
is not permission to abandon the selected evidence or ask again for the same
recovery authority. It is also not an in-place retry: the controller marks a
terminal failed stage's unit failed, and the same dispatch identity returns the
old job receipt. `resume` continues other eligible work but does not retry that
failed unit. Never edit campaign state, erase a receipt or manually resubmit its
frozen job name to simulate a retry.

After a proven tool/environment failure is repaired:

1. Preserve the old campaign, exact failure/job identity, originals, partial
   reports and diagnostics privately. Prove its affected jobs terminal and
   report writers quiescent; ambiguous dispatch/accounting must reconcile first.
   Do not reclassify an application/correctness failure as a profiler failure or
   retry it until it passes.
2. Finish remaining unaffected units through the controller and normal campaign
   finalization, which releases its claims. If owned stuck work must be stopped,
   use the existing cancellation path and inspect its whole selected scope first;
   do not cancel unrelated or still-useful units just to release a claim. Confirm
   claim release before replacement and honor claims held by another campaign.
3. Start a **fresh campaign for only the affected lab/profile** with the existing
   public interface, without another approval within the accepted recovery scope:

   ```text
   python3 <skill>/scripts/run_labs.py run --lab COURSE:LAB \
     --workload small|large --environment PRIVATE_JSON
   ```

   Replace `small|large` with that one failed profile, not both. Reuse the accepted
   prepared target and verify the intended source/recipe inputs; do not silently
   absorb drift as recovery. Run the full prescribed unit, including prerequisites
   and fresh unprofiled trials, with new campaign/job identities. This intentional
   rerun of the failed unit is not same-campaign resume; do not rerun unaffected
   completed units or substitute old successes into its verification receipt.
4. Record the private link between old failure and replacement campaign plus the
   diagnosed cause and recovery observations. Complete all correctness, native
   content, browser, collection and publication gates. Preserve prior published
   results until the replacement succeeds. Further diagnosed tool failures may
   use this path again without a fixed count; inspect each failure and change an
   ineffective recovery before rerunning.

If only the viewer, Grafana or connection failed, recover and resume evidence
from retained originals instead; no fresh GPU campaign is needed.
