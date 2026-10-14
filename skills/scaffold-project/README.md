# Scaffold Project

`scaffold-project` is an implicitly selectable project creation skill. It turns
an approved architecture into a path-owned scaffold plan, gathers exact files
from specialist skills, and applies the validated bytes through one guarded
executor.

## Core Model

```text
approved design or app-stack decision
  -> logical capabilities
  -> physical materialization units
  -> runtime units
  -> one owner per path
  -> candidate-set manifests and validation binding
  -> private digest-bound bundle
  -> task-authorized guarded apply
  -> Python dependency setup and readiness
  -> audit-only alignment
```

The skill supports greenfield projects and additive brownfield
standardization. It does not select technologies, implement product features,
or participate in Agentic SDLC.

## Safety Boundary

- Task intent selects planning or execution; creation includes Python readiness.
- Candidate production writes only private candidates. The static executor
  applies authored files; Python bootstrap owns generated dependency outputs.
- The private execution bundle must be a real `0700` directory outside the
  target and every Git worktree.
- The executor never deletes a path or blindly overwrites a file.
- Known conflicts block every write; mid-apply drift stops with a private
  recovery journal.
- Target roots and approved architecture sources remain identity- and
  digest-bound through apply, and an architecture source cannot also be an
  operation target.
- External JSON handoffs, plans, and candidate manifests reject duplicate
  object keys and non-standard numeric literals before validation.
- Every candidate set binds its specialist, unit, profile, normalized input,
  exact files, and validation requirements into the approved digest.
- Every required app-stack component is represented with its approved status
  and exact technology object; required materialized components bind their
  canonical technology name and language to every assigned unit, every runtime
  binds to one capability, and external-service technology is compared
  directly.
- Python core and frontend selections bind to closed producer inputs; other
  non-frontend selections fail closed.
- Required web UI profiles and declared frontend capability selections are
  additionally rechecked against normalized frontend candidate inputs.
- An absent target root is opened under a private staging name and atomically
  published without replacement while that descriptor remains authoritative.
- README and Makefile merge marker identities must be unique in the target.
- Git initialization, network generators,
  provisioning, deployment, publication, and live skill installation remain
  separate actions. Python dependency installation belongs to post-apply
  bootstrap within the creation task.

## Files

- `SKILL.md`: orchestration and safety contract.
- `agents/openai.yaml`: implicit invocation policy.
- `references/`: plan/candidate schemas, routing, layout, question, and safety
  contracts.
- `scripts/scaffold_project.py`: deterministic finalizer, validator, status,
  and apply executor.
- `scripts/test_scaffold_project.py`: disposable unit and race fixtures.
  Directory-replacement fixtures retain the displaced directory and assert a
  distinct device/inode identity, so Linux inode reuse cannot invalidate the
  test setup.
- `scripts/test_frontend_scaffold_e2e.py`: offline app-stack to frontend apply
  contract.
- `evals/trigger-prompts.csv`: positive, negative, and overlap trigger cases.
- `evals/process-cases.md`: supplemental workflow and output-quality cases.

## Choosing the workflow

Use [creation routing](references/creation-routing.md) for natural-language
examples, Python-only ownership and maintenance boundaries. A completed static
apply is distinct from Python readiness; bootstrap failures preserve the
applied scaffold and report the unfinished checks.
