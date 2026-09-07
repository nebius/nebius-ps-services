# Lab 25: Pack variable-length examples without crossing boundaries

Padding reserves positions that may contribute no useful training target, while packing can place several short examples into one fixed-capacity row. This lab constructs a packing plan and a boundary-safe causal mask. You will account for useful tokens and prove that independent examples cannot attend across their boundaries before attempting a packed-model performance comparison.

## Before you start

**Theory preparation:** Read Lesson 2 for first-fit bin packing, capacity, per-example spans and block-diagonal causal masks. Run the planning/mask check there; Lesson 4 adds learning-objective interpretation without turning this script into a packed-transformer training benchmark.

Use one H100 and review shifted labels versus attention masks. The supplied lengths must fit the selected `--capacity`; the minimum accepted capacity is 32 and every individual example must fit.

Larger H100 batches amplify padding waste and make aligned packed shapes attractive, but sequence length also changes activation memory and attention work quadratically or through backend-specific algorithms.

## Concepts and code path

The program assigns example lengths to bins, compares padded and packed token capacity, and constructs dense causal attention masks that isolate examples. It checks adjacent boundaries and causality. This is a planning/mask mechanics lab: it does not run packed transformer training, construct a complete packed-label objective, or measure a variable-length attention kernel.

## Practice

Given examples with 5 and 7 valid tokens padded separately to length 16, only 12 of 32 slots are useful. Change to one packed length-16 sequence with a segment boundary and four padding slots. Expected observation: useful density rises from 37.5 to 75 percent, while a mask test must prove that tokens in the second segment cannot attend to the first when the recipe requires independence.

Run Lab 25's packing-plan and causal-mask checks now; it does not execute transformer training or a loss/backward comparison. Inspect Lab 13's token and label construction and compare ignored versus trained tokens with the packing calculation above. Defer Lab 13's loss and gradient checks until Lesson 4, when the model and backward semantics are established.

Run two valid capacity choices and inspect the resulting bin membership. Increasing capacity can change both packing efficiency and dense attention work, so it is not automatically an optimization.

```bash
umask 077
sbatch slurm/single_gpu.sbatch labs/25_sequence_packing.py --profile smoke --capacity 256
sbatch slurm/single_gpu.sbatch labs/25_sequence_packing.py --profile smoke --capacity 512
```

## Check your results

Inspect `lengths`, `bins`, `valid_tokens`, `padded_tokens`, `packed_capacity_tokens`, and both efficiency ratios. Require every boundary and causal-mask check. These gates prove mask structure, not equivalence of model loss or throughput.

Retain token IDs, labels, ignored positions, boundary representation, valid-token count, and loss.

Higher token utilization matters only when semantics and loss masking remain correct.

## Investigate the behavior

Trace the last token of one example and first token of the next in a packed row. Which attention entries must be blocked? Explain why higher occupancy of token slots need not reduce dense quadratic attention work proportionally.

Packing raises useful-token density but complicates masks, positions, document boundaries, and reproducibility. Bucketing reduces padding but can skew order or batch composition unless sampling is designed deliberately.

## If something goes wrong

Capacity rejection means the fixture cannot fit as declared; do not truncate silently. An attention-mask entry that permits cross-example attention invalidates independence even if labels are masked correctly. Check segment identity and causal ordering separately.

Concatenating examples without boundary handling trains unintended cross-example dependencies.

## Takeaways and next step

Packing changes data layout and requires explicit semantic boundaries. Extend with positions, shifted labels, and a model-forward/loss reference comparison before measuring packed training or claiming a tokens/s improvement.

Validate every target and boundary before using packing as a throughput optimization.

Hand-compute labels and masks for two packed three-token examples.
