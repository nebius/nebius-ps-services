# Lab 36: Model KV retention and restore decisions

A reusable prefix is valuable only if compatible cached state survives until the next request and restoring it is worthwhile. This CPU lab models those decisions explicitly. You will vary capacity, expiry, reuse intervals and transfer cost, observe deterministic LRU movement across device/host/storage tiers, and distinguish modeled savings from real serving latency. It performs no CUDA, network, engine or filesystem data-transfer workload.

## Before you start

Complete the [Lab Guide](../../../README.md#how-to-set-up-the-lab) before starting.

Python and the supplied standard-library helpers are sufficient; a GPU and serving dependencies are unnecessary. The profile label is retained for the course result format and does not activate H100 execution. All prefixes are inactive, equally sized and completely reusable inside this model. Active-request pinning and partial-prefix matching are outside its scope.

## Concepts and code path

`Policy` stores capacities in whole prefixes, bytes per prefix, idle TTL, prefill cost and transfer assumptions. `TierCache` owns three exclusive ordered maps. Access expires entries whose deadlines have arrived, finds the exact namespace/prefix key, compares restore cost with recomputation, then promotes the entry. Capacity pressure demotes the oldest entry; the last tier discards it. Every operation checks unique ownership and capacity bounds.

A TTL refresh happens on access, while demotion preserves the existing deadline. A restart clears device and host maps only. A model revision changes the namespace, so old entries cannot satisfy new requests even if numeric prefix IDs match. These names stand for the complete semantic identity; the toy program does not calculate real token hashes or validate stored tensors.

The arrival schedule is fixed independently of work cost. Foreground and write-service costs are accumulated separately. Transfer uses decimal GB/s, while `--prefix-mib` uses binary MiB. Storage transfer time uses a declared effective end-to-end restore rate; the model does not add separate storage-to-host and host-to-device transfer times. The exclusive model records each modeled demotion write, including writes that real systems might filter or overlap.

For a modeled 4-MiB reusable prefix, 2 GB/s of effective storage-to-device throughput and 0.2 ms startup imply 0.2 + 4,194,304 / 2,000,000,000 × 1,000 = 2.297152 ms to restore. Against an assumed 8-ms prefill, restoration has a 5.702848-ms foreground advantage before queueing and write costs. At 0.2 GB/s, restoration takes 21.17152 ms and recomputation is cheaper. These are declared model inputs, not measurements or claims that storage performs like DRAM.

Vary one of capacity, TTL, reuse gap or storage rate, then model a worker restart and a model-identity change separately. This is a CPU policy model, not a vLLM/Dynamo implementation or a serving benchmark.

## Practice

`labs/36_kv_tiering.py` models prefix retention, expiry, eviction, and restore costs across capacity-limited tiers. It checks exclusive ownership and capacity constraints, then records modeled results without storage access, CUDA, or a serving engine.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 \
  --chdir="$PWD" \
  --output="$PWD/results/36_kv_tiering/logs/%j.out" \
  --error="$PWD/results/36_kv_tiering/logs/%j.err" \
  slurm/cpu.sbatch \
  labs/36_kv_tiering.py --host-prefixes 0 --storage-prefixes 0
```

## Check your results

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Wait for this CPU job to reach `COMPLETED` with exit code `0:0`. Read its own logs and open the exact JSON path printed there; check the lab identity, policy inputs, Slurm job ID, and correctness fields.

```bash
export LAB_JOB_ID='<job number printed by sbatch>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/36_kv_tiering/logs/$LAB_JOB_ID.out"
cat "results/36_kv_tiering/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact JSON path printed by this run>'
cat "$RESULT_JSON"
```

The report labels `evidence_kind` as `deterministic_policy_model`. Follow each request's `found_tier`, decision, occupancy, expiry and capacity-eviction counts. `reuse_fraction` counts actual modeled reuse decisions, so an expensive tier hit that chooses recomputation is not a reuse. Review read/write bytes and both modeled time totals. Their sum is service-work accounting, not measured elapsed time: write work may overlap, and queueing is absent.

Compare the reported restore decision with the calculation in Concepts and code path: the default rate should favor restoration, while the slower-rate control should favor recomputation.

For Lab 36 retain each tier hit, reuse/recompute decision, expiry/eviction count, occupancy, read/write bytes, and separate modeled foreground and write-service totals. Fixed arrivals are not delayed by the model's costs, so these totals must not be labeled TTFT, QPS or goodput. The model omits active-request pinning, partial-prefix reuse, queueing, chunking and decode. Transfer the question to a qualified engine only with those effects and task-quality checks restored.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Reuse fraction | `reuse_fraction` | `none` |
| Modeled foreground total (seconds) | `modeled_foreground_total_ms` | `s` |
| Modeled write service total (seconds) | `modeled_write_service_total_ms` | `s` |
| No cache prefill total (seconds) | `no_cache_prefill_total_ms` | `s` |
| Read bytes | `read_bytes` | `bytes` |
| Write bytes | `write_bytes` | `bytes` |

Select successful baseline and candidate policy-model artifacts in the same profile that differ in only one declared input. Compare storage capacity against the no-retention baseline, then compare each TTL, transfer-rate, restart or identity control against the retained baseline. These slots describe policy configurations, not independent timing trials. Set the paths to the selected result files and review the current generation (use `0` for the first selection):

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it.

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 36_kv_tiering \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select the workspace and profile. Require **Correctness of selected results** to equal 1 and **Selected comparison generation** to match publication confirmation. Compare the selected artifact fields and experiment timestamps. This dashboard omits GPU telemetry because this recipe cannot attribute device activity to its result.

## Investigate the behavior

### Workload variations

Submit each CPU policy variant from this course directory. Each job writes a new private result. Begin with no slower retention, then change only storage capacity; compare TTL, rate, restart and identity separately against that retained baseline.

```bash
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 --chdir="$PWD" \
  --output="$PWD/results/36_kv_tiering/logs/%j.out" \
  --error="$PWD/results/36_kv_tiering/logs/%j.err" slurm/cpu.sbatch labs/36_kv_tiering.py --host-prefixes 0 --storage-prefixes 0
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 --chdir="$PWD" \
  --output="$PWD/results/36_kv_tiering/logs/%j.out" \
  --error="$PWD/results/36_kv_tiering/logs/%j.err" slurm/cpu.sbatch labs/36_kv_tiering.py --host-prefixes 0 --storage-prefixes 8
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 --chdir="$PWD" \
  --output="$PWD/results/36_kv_tiering/logs/%j.out" \
  --error="$PWD/results/36_kv_tiering/logs/%j.err" slurm/cpu.sbatch labs/36_kv_tiering.py --host-prefixes 0 --storage-prefixes 8 --ttl-ms 100
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 --chdir="$PWD" \
  --output="$PWD/results/36_kv_tiering/logs/%j.out" \
  --error="$PWD/results/36_kv_tiering/logs/%j.err" slurm/cpu.sbatch labs/36_kv_tiering.py --host-prefixes 0 --storage-prefixes 8 --storage-gbps 0.2
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 --chdir="$PWD" \
  --output="$PWD/results/36_kv_tiering/logs/%j.out" \
  --error="$PWD/results/36_kv_tiering/logs/%j.err" slurm/cpu.sbatch labs/36_kv_tiering.py --host-prefixes 0 --storage-prefixes 8 --restart-at 6
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 --chdir="$PWD" \
  --output="$PWD/results/36_kv_tiering/logs/%j.out" \
  --error="$PWD/results/36_kv_tiering/logs/%j.err" slurm/cpu.sbatch labs/36_kv_tiering.py --host-prefixes 0 --storage-prefixes 8 --revision-change-at 6
```

Use `--gap-ms` to vary reuse interval, keeping the cyclic prefix sequence unchanged. With six prefixes and a 50-ms gap, a prefix recurs every 300 ms. The supplied deterministic policy needs no repeated timing benchmark; independent runtime trials belong to a later real-engine experiment.

Keep the profile and all other policy inputs fixed for each comparison. Repeating an unchanged configuration checks reproducibility of the deterministic model; it does not measure timing variation.

Does TTL expire before the next reuse even when capacity is ample? Does increasing storage capacity help a low-reuse workload? Can a storage hit be rejected as too costly? After a modeled restart, only prefixes previously demoted to storage survive; exclusive tiers are not a persistent backup of every device entry. Explain why changing identity prevents reuse while a mere capacity change does not.

**Nsight Systems: not applicable.** This CPU retention model simulates cache placement without transferring GPU data or running inference. Inspect the measured or modeled fields in this lab's dashboard; retain the artifact and its stated scope.

## If something goes wrong

Invalid capacities, non-finite costs and decreasing arrival times fail explicitly. A duplicate key across exclusive tiers or capacity overflow is a state bug. Do not relabel the model's rates as measurements to reconcile a surprising outcome. A real cuFile read might use a staged compatibility path; API success and cache reuse alone do not prove GDS, safe persistent recovery or correct attention output.

Publication failure is separate from model execution failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown; compare the selected artifact fields with the dashboard queries before interpreting the policy comparison.

## Takeaways and next step

A useful cache policy balances valid reuse, capacity, transfer and write costs. State the model's assumptions before interpreting its result. For an optional qualified engine study, retain exact model/cache identity, verify cache counters and outputs, establish the actual direct or staged I/O path, and measure cold/warm/restart cohorts with TTFT, decode and queueing. No infrastructure installation or real GDS execution is supplied by this lab.
