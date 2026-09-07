# Lab 16: Audit model, tokenizer, configuration, and license identity

A model name alone does not fully identify an inference workload. Weights, tokenizer, configuration, generation settings, and code-loading policy can all affect behavior. This lab inspects a pinned public model revision before inference, helping you establish reproducible artifact identity and identify compatibility or licensing questions before allocating time to performance experiments.

## Before you start

**Theory preparation:** Read Lesson 1 for artifact bundles, immutable revisions, tokenizers/templates, safetensors shards, configuration and remote-code authority. This metadata audit precedes model execution; matching metadata is not runtime or quality qualification.

Use one H100 and the approved mechanics environment with access to the selected public artifact. Review its license separately; reading license metadata is not legal approval. Never place credentials in commands or reports.

The artifact must include a supported dtype and architecture path for SM90. Heavyweight engines may transform or compile weights into engine-specific artifacts that need their own pinned identity and validation.

## Concepts and code path

The script resolves the immutable revision, inspects artifact listings, loads configuration/tokenizer metadata without remote custom code, and checks expected safe weight artifacts. It records model dimensions, head counts, positional settings, vocabulary sizes, chat-template presence, and generation metadata. It does not perform a model-quality evaluation or prove engine compatibility merely from configuration fields.

## Practice

Given identical prompt text tokenizing to 1,024 IDs under revision A and 1,088 under revision B, input-token count and ideal KV bytes increase by 6.25 percent before any engine change. Prefill work is not a single linear quantity: projections scale approximately with token count while dense attention has a quadratic sequence term (the squared ratio is about 1.1289). Real allocation rounds blocks and latency requires measurement. Change only the tokenizer revision while keeping weights named identically. Expected observation: output IDs, TTFT, and memory can change, so the artifact audit rejects the comparison until tokenizer, template, generation config, and revision are fixed.

Run Lab 35's fixed-model token loop, then run Lab 16 and build a manifest for the pinned public model revision.

Inspect the model/revision options and run the pinned default. Any replacement must use a matching immutable commit rather than a moving branch such as a default repository head.

```bash
umask 077
python labs/16_model_artifact_audit.py --help
sbatch slurm/single_gpu.sbatch labs/16_model_artifact_audit.py --profile smoke
```

## Check your results

Require matching pinned/resolved revision, expected artifacts, safetensors weights, and the standard-code loading policy. Inspect tokenizer/model vocabulary and KV-head metadata. License inspection and successful parsing do not imply permission for every intended use.

Retain public artifact IDs, immutable revisions, file hashes, tokenizer settings, license, and code-execution policy.

Artifact identity is part of correctness, security, capacity, and performance evidence.

## Investigate the behavior

Which fields affect memory capacity, attention layout, and tokenization? Explain why identical prompt text with a different tokenizer or chat template is not necessarily identical model input.

Immutable pinning reduces surprise but increases release and storage management. Remote code can enable model-specific behavior while expanding the security and compatibility surface. Prebuilt engines improve startup consistency only after their exact build/runtime contract is qualified.

## If something goes wrong

A revision mismatch or remote-code requirement blocks the declared artifact contract. If expected weight files are missing, investigate rather than guessing a loader format. Keep access errors private and never bypass the no-remote-code policy casually.

Loading mutable latest revisions makes later results impossible to reproduce.

## Takeaways and next step

Artifact identity is part of the experiment, not administrative trivia. Carry this manifest into generation and engine qualification, then confirm actual startup and request behavior before benchmarking the selected model.

Pin every artifact and review executable model code before loading model weights or executing that code.

List the artifacts that can change token IDs or kernel selection.
