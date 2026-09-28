# Lab 25: Pack variable-length examples without crossing boundaries

Padding reserves positions that may contribute no useful training target, while packing can place several short examples into one fixed-capacity row. This lab constructs a packing plan and a boundary-safe causal mask. You will account for useful tokens and prove that independent examples cannot attend across their boundaries before attempting a packed-model performance comparison.

## Before you start

Complete [environment setup](../../../README.md#how-to-set-up-the-lab) once. This lab uses the [assigned Grafana dashboard](../grafana/25_sequence_packing.json).

Use one H100 and review shifted labels versus attention masks. The supplied lengths must fit the selected `--capacity`; the minimum accepted capacity is 32 and every individual example must fit.

Larger H100 batches amplify padding waste and make aligned packed shapes attractive, but sequence length also changes activation memory and attention work quadratically or through backend-specific algorithms.

## Concepts and code path

The program assigns example lengths to bins, compares padded and packed token capacity, and constructs dense causal attention masks that isolate examples. It checks adjacent boundaries and causality. This is a planning/mask mechanics lab: it does not run packed transformer training, construct a complete packed-label objective, or measure a variable-length attention kernel.

Given examples with 5 and 7 valid tokens padded separately to length 16, only 12 of 32 slots are useful. Change to one packed length-16 sequence with a segment boundary and four padding slots. Expected observation: useful density rises from 37.5 to 75 percent, while a mask test must prove that tokens in the second segment cannot attend to the first when the recipe requires independence.

The supplied experiment checks packing plans and segment-local causal masks. It does not execute transformer training or compare losses and gradients. Count ignored and trained tokens separately before using a packing plan in a model.

## Practice

Run the experiment commands on the login node. Save the printed JSON paths; job submission alone is not a result.

Run two valid capacity choices and inspect the resulting bin membership. Increasing capacity can change both packing efficiency and dense attention work, so it is not automatically an optimization.

```bash
umask 077
python3 tools/submit_lab.py --lab 25_sequence_packing slurm/single_gpu.sbatch labs/25_sequence_packing.py --profile small --capacity 256
python3 tools/submit_lab.py --lab 25_sequence_packing slurm/single_gpu.sbatch labs/25_sequence_packing.py --profile small --capacity 512
```

Keep a fixed profile for a comparison. If both profiles appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

## Check your results

After the submitted job completes, inspect its state and measured results on the login node. The second command prints the exact JSON paths and numeric fields used by this dashboard. For a direct CPU run, use job `0`.

```bash
sacct -j "${LAB_JOB_ID:?submitted job number}" --format=JobID,State,ExitCode
"$COURSE_PUBLISH_PYTHON" tools/inspect_results.py --lab 25_sequence_packing --job "$LAB_JOB_ID"
```

Inspect `lengths`, `bins`, `valid_tokens`, `padded_tokens`, `packed_capacity_tokens`, and both efficiency ratios. Require every boundary and causal-mask check. These gates prove mask structure, not equivalence of model loss or throughput.

Retain the supplied lengths, bin membership, capacity counts, efficiencies and mask checks. For a later model-training extension, also retain token IDs, shifted labels, ignored positions, position IDs and loss/gradient comparisons; the supplied program does not produce those training records.

Higher token utilization matters only when semantics and loss masking remain correct.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Valid tokens | `valid_tokens` | `none` |
| Padded tokens | `padded_tokens` | `none` |
| Packed capacity tokens | `packed_capacity_tokens` | `none` |
| Padding efficiency | `padding_efficiency` | `none` |
| Packing efficiency | `packing_efficiency` | `none` |

Select two successful, equivalent, unprofiled runs in the same profile. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 25_sequence_packing \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

Trace the last token of one example and first token of the next in a packed row. Which attention entries must be blocked? Explain why higher occupancy of token slots need not reduce dense quadratic attention work proportionally.

Packing raises useful-token density but complicates masks, positions, document boundaries, and reproducibility. Bucketing reduces padding but can skew order or batch composition unless sampling is designed deliberately.

Capture a separate diagnostic run:

```bash
python3 tools/submit_lab.py --lab 25_sequence_packing --export=ALL,COURSE_PROFILE_TOOL=nsys slurm/single_gpu.sbatch labs/25_sequence_packing.py --profile small --capacity 256
```

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker/container; submission and result publication remain outside capture. Expand lab_workload and CUDA GPU rows. Locate zero-fill, triangular-mask construction and scalar-read synchronization. This lab validates a boundary-safe mask; it does not benchmark packed transformer training. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

Capacity rejection means the fixture cannot fit as declared; do not truncate silently. An attention-mask entry that permits cross-example attention invalidates independence even if labels are masked correctly. Check segment identity and causal ordering separately.

Concatenating examples without boundary handling trains unintended cross-example dependencies.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Packing changes data layout and requires explicit semantic boundaries. Extend with positions, shifted labels, and a model-forward/loss reference comparison before measuring packed training or claiming a tokens/s improvement.

Validate every target and boundary before using packing as a throughput optimization.

Hand-compute labels and masks for two packed three-token examples.
