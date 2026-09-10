# Lab 18: Reduce padded prefill work with length buckets

A single batch rectangle can waste substantial work when one prompt is much longer than the others. This lab compares one padded prefill batch with two length buckets containing the same prompts. You will verify corresponding last-token logits and account for padding while considering the extra launches and smaller batch sizes introduced by bucketing.

## Before you start

**Theory preparation:** Read Lesson 5 for padding, attention masks, bucketing and the hand-worked position count. Read the implementation and per-prompt checks below before running.

Use one H100 and the audited immutable Hugging Face artifact in the mechanics environment. Review padding, attention masks, and the difference between a real prompt token and an allocated rectangle position.

## Concepts and code path

Inputs are right-padded, so each prompt's last real-position logits come from `length-1`; left-padded generation needs a different index rule.

The code tokenizes the prompt set, measures true lengths, builds a single padded batch, and separately groups prompts by length. Both paths execute prefill with the appropriate masks. Results are restored to corresponding prompts before comparing final-position logits. This is a prefill batching experiment, not continuous online admission or a complete generation service.

## Practice

Run the supplied prompt fixture before editing lengths or grouping policy. Keep model revision, prompt contents, dtype, and semantic masks identical between the compared paths.

```bash
umask 077
python labs/18_padding_bucketing.py --help
sbatch slurm/single_gpu.sbatch labs/18_padding_bucketing.py --profile smoke
```

## Check your results

Require `equivalent_last_token_logits`. Each prompt's compared logits, norm calculation and relative L2 error must be finite, with error no greater than `0.02`, before the maximum is reported. The checks below apply to each prompt; NaN in either an early or a later prompt must fail rather than disappear during aggregation. Inspect true prompt tokens, single-batch and bucketed rectangle tokens, padding counts, and both timing distributions. Fewer padded positions do not guarantee lower elapsed time when launch or batching efficiency changes.

## Investigate the behavior

Calculate the padding fraction before and after grouping. Which prompt determines each bucket's rectangle width? Explain the trade-off between finer buckets, extra launches, and smaller matrix batches.

## If something goes wrong

A last-token mismatch may indicate incorrect padding-side handling, mask alignment, or output restoration order. Check prompt identity and valid final positions before changing numerical tolerances.

## Takeaways and next step

Length grouping is useful only when it preserves semantics and improves performance at the chosen measurement boundary. A live-service extension must also include queue delay while waiting to form buckets; this offline prefill measurement excludes that delay.
