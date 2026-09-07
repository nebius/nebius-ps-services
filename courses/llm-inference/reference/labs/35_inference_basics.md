# Lab 35: Trace fixed-parameter autoregressive inference

This exercise shows how repeated predictions create a token sequence while the model's parameters stay unchanged. It uses an explicit four-token vocabulary and a fixed table of next-token scores, so every decision can be checked by hand. You will inspect logits, probabilities, token feedback and stopping without downloading a language model or launching a server. This is a bigram teaching model, not a transformer, attention implementation or KV-cache benchmark.

## Before you start

**Theory preparation:** Read Lesson 1 for tokens, embedding-table logits, softmax, greedy selection, stopping, evaluation/inference modes and the unchanged-parameter check. Start with the fixed CPU example before loading an external model.

Use the inference mechanics PyTorch environment. CPU mode is the default; CUDA mode requires an explicitly selected full H100. No tokenizer package, external dataset, remote code or model weights are fetched. Read the opening definitions of inference, token, logit, greedy decoding and end-of-sequence. The supplied vocabulary is already tokenized: a real application must additionally format and tokenize text. Shared profile and seed metadata do not change this fixed example.

## Concepts and code path

`run_experiment` constructs a four-by-four embedding table, overwrites every score with a declared constant, and snapshots the parameters. Each row represents scores for the next token given one current token. Starting with I, the forward call selects its row; softmax converts scores into probabilities; argmax chooses a highest-probability token. That token becomes the next input unless it is the end marker. `main` validates the output budget, selects the device and writes the standard private result.

The model is in evaluation mode and the loop uses inference mode. Evaluation mode changes the behavior of certain modules such as dropout; it does not itself disable gradient tracking. Inference mode avoids graph recording here, and explicit equality checks prove this program did not change its table. No backward pass or optimizer is present. Per-step transfers to CPU make the trace easy to read and intentionally unsuitable for performance benchmarking.

## Practice

Run the default sequence and then a shorter output budget. CUDA is an optional alternative for observing the same mechanics on H100.

```bash
umask 077
python3 labs/35_inference_basics.py --device cpu
python3 labs/35_inference_basics.py --device cpu --max-new-tokens 1
sbatch slurm/single_gpu.sbatch labs/35_inference_basics.py --device cuda
```

## Check your results

The default `generated_tokens` are like, GPUs and the end marker, with `stop_reason` eos. The one-token budget yields only like and stops with reason length. The recorded sequence includes the end marker for teaching visibility; a user-facing renderer would normally omit it. Each score row contains a 4 and three -2 values. The winning probability is exp(4)/(exp(4) + 3 × exp(-2)), approximately 0.9926; the other probabilities sum to the remainder. Require `parameters_unchanged` true and `gradients_created` false. The program checks the exact expected token sequence, not meaningful language quality.

## Investigate the behavior

Follow `steps`: which row was read, which token was chosen, and which input the next forward receives. Explain why changing the output budget changes work without improving a kernel. As a source-edit extension, change one score and predict the resulting sequence; keep the original run as the reference. How would sampling differ from greedy choice? Why does this table fail to model attention over a long prompt? Distinguish those missing mechanisms from the feedback loop it does demonstrate.

## If something goes wrong

A zero or negative budget is rejected before PyTorch loads. A missing import requires the mechanics environment. CUDA selection fails clearly on a non-H100 device; do not silently reinterpret a CPU result as a GPU run. If edited scores change outputs, revise the declared expected behavior and its test together rather than disabling state checks. Real-model nondeterminism and tokenizer mismatch are outside this fixed-table experiment.

## Takeaways and next step

Inference changes request state while using fixed learned parameters. Autoregression feeds an earlier prediction into a later forward call; stopping bounds that loop. The next lesson expands this model into prompt prefill, transformer attention and KV-cached decode, where the first output is chosen from prefill's final-position logits. This lab measures none of those phases, TTFT or serving throughput.
