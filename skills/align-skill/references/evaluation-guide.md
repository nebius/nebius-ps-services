# Skill Evaluation Guide

Read this reference when `align-skill` creates, migrates, validates, runs, or
reports evaluations for a target skill. Keep evaluation effort proportional to
the changed behavior.

Source basis:

- [OpenAI Build skills](https://learn.chatgpt.com/docs/build-skills)
- [Agent Skills evaluation guidance](https://agentskills.io/skill-creation/evaluating-skills)
- [Optimizing skill descriptions](https://agentskills.io/skill-creation/optimizing-descriptions)

## Required Trigger Contract

Every authorized writable target that completes alignment must contain:

```text
evals/trigger-prompts.csv
```

Use the exact schema:

```csv
id,should_trigger,prompt
example-positive-01,true,"A realistic user request for this skill"
example-negative-01,false,"A realistic near-miss owned by another skill"
```

Requirements:

- Use unique, non-empty IDs and prompts.
- Use only lowercase `true` and `false` labels.
- Include at least three cases for each label.
- Keep the CSV as a target-owned regular file. Do not accept a symlinked
  `evals/` directory or CSV, or a resolved path outside the skill.
- Report invalid or duplicate cases by row number without copying raw IDs or
  prompts into terminal, CI, or report output.
- Make positive cases resemble real user intent, inputs, and phrasing.
- Make negative cases plausible near-misses at adjacent routing boundaries, not
  unrelated trivia.
- Expand toward roughly twenty cases when a broad or ambiguous description
  needs more routing evidence; do not inflate a narrow skill mechanically.

For a writable aligned target, migrate unique useful cases from
`evals/trigger-prompts.md` into the CSV and remove the old trigger authority.
Do not keep Markdown and CSV trigger catalogs in parallel. Preserve distinct
quality rubrics or manual runtime instructions under clearly different names.

For a report-only, remote, or unauthorized target, do not create or migrate
files. Report `EVALS_MISSING` or the observed coverage and keep the outcome
partial.

## Quality Oracle Selection

Choose the cheapest oracle that tests the behavior actually changed:

| Change | Required evidence definition |
| --- | --- |
| Description or invocation only | Canonical trigger CSV; fresh runtime routing when a runnable surface exists. |
| Deterministic script, parser, schema, or template | Canonical trigger CSV plus focused deterministic tests. |
| Material instruction workflow or generated output | Canonical trigger CSV plus `evals/evals.json` and baseline comparison when a clean runner exists. |
| Documentation with no runtime contract effect | Update existing cases only when their assumptions changed. |

Do not add an empty test script or model benchmark to satisfy a folder shape.
Use deterministic code for mechanical assertions and model or human judgment
only for qualities that cannot be checked reliably in code.

## Output-Quality Cases

For material workflow or output changes, add at least two realistic cases to
`evals/evals.json` unless deterministic target tests fully verify the intended
outcome:

```json
{
  "skill_name": "example-skill",
  "evals": [
    {
      "id": 1,
      "prompt": "A realistic user request",
      "expected_output": "An observable description of success",
      "files": [],
      "assertions": [
        "A concrete, verifiable result"
      ]
    }
  ]
}
```

Use observable assertions. Avoid vague claims such as "the output is good" and
brittle wording checks that reject equally correct phrasing. Include an edge or
boundary case when it exercises a likely failure.

## Baselines And Clean Runs

Before editing an existing writable target, snapshot its current working bytes
outside the repository in a task-owned temporary directory. Use that snapshot
as the previous-version baseline when the worktree is dirty; do not substitute
`HEAD` when it excludes accepted user changes.

Run output-quality cases in clean contexts with the same prompt and input files:

1. Current aligned skill.
2. Captured previous version, or no skill when no meaningful prior version
   exists.

Keep generated outputs, timings, grades, and benchmark workspaces outside the
reusable skill directory unless the repository explicitly owns stable fixtures.
Record concrete evidence for every assertion. Compare quality, time, and token
cost when the runner exposes those values, but do not manufacture unavailable
metrics.

## Evidence States

Use these exact meanings:

- `STATIC_PASS`: the eval definitions, structure, and applicable deterministic
  static checks passed. This does not prove model routing.
- `RUNTIME_PASS`: a fresh target surface loaded or declined the skill as
  expected for the tested trigger cases.
- `QUALITY_PASS`: output assertions passed against the selected baseline.
- `NOT_RUN`: the applicable lane was intentionally not executed; state why.
- `UNAVAILABLE`: no safe runner, baseline, required access, or fresh surface was
  available; state which dependency was missing.
- `FAIL`: an executed applicable check failed; do not claim completion.

After trigger or invocation changes, run fresh runtime cases when the relevant
surface is available. After material workflow or output changes, compare
quality when a clean runner and baseline are available. Higher evidence tiers
may remain unavailable, but they must remain visible and must never be inferred
from static files.

## Efficient Validation Order

1. Validate the canonical CSV and target structure.
2. Run changed deterministic target tests.
3. Run fresh trigger cases only when invocation behavior changed or routing is
   otherwise material.
4. Run output-quality comparisons only for material instruction/output changes
   with a clean runner and useful baseline.
5. Broaden the suite when failures, ambiguity, or real usage expose a gap.

This order keeps ordinary alignment fast while preserving truthful evidence
for changes that need more than static inspection.
