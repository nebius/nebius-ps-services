# AI/ML Python Practices

Load this reference when the project trains, evaluates, or serves ML models (including LLM features).

## Project Structure

```text
src/<package_name>/ml/
├── datasets.py
├── features.py
├── train.py
├── evaluate.py
├── infer.py
└── registry.py
```

Keep training and inference paths separate.

## Dependency Strategy

- Keep base runtime lightweight.
- Use consumer extras for optional ML capabilities:
  - `[project.optional-dependencies].ml`
  - `[project.optional-dependencies].gpu`
- Avoid forcing heavy ML packages for CLI-only/API-only users.

## Reproducibility

- Set and document random seeds.
- Version datasets and feature transformations.
- Persist model metadata (data snapshot, hyperparameters, metrics, code version).
- Tie model artifacts to Git SHA and semantic version.

## Evaluation and Quality Gates

- Define task-specific offline metrics before deployment.
- Add regression tests for preprocessing and inference schema.
- Validate model outputs against policy/safety constraints.
- Track model drift and trigger re-training based on explicit thresholds.

## Serving Best Practices

- Preload models on startup and expose readiness only after warm-up.
- Bound request payload size and inference latency.
- Add batch controls and concurrency limits.
- Use structured model error responses; avoid leaking internals.

## LLM-Specific Security Notes

- Treat prompts and model outputs as untrusted input.
- Add prompt-injection defenses and tool-call allowlists.
- Keep retrieval sources scoped and audited.
- Strip secrets and sensitive fields before prompt construction.
