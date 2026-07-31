# Scaffold Project

`scaffold-project` is an explicit-only repository composition skill. It turns
an approved architecture into a path-owned scaffold plan, gathers exact files
from specialist skills, and applies the approved bytes through one guarded
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
  -> explicit guarded apply
  -> audit-only alignment
```

The skill supports greenfield projects and additive brownfield
standardization. It does not select technologies, implement product features,
or participate in Agentic SDLC.

## Safety Boundary

- Planning is the default.
- Specialists write private candidates, never the target.
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
- Non-frontend capability selections fail closed until their specialist owns a
  closed normalized-input binding.
- Required web UI profiles and declared frontend capability selections are
  additionally rechecked against normalized frontend candidate inputs.
- An absent target root is opened under a private staging name and atomically
  published without replacement while that descriptor remains authoritative.
- README and Makefile merge marker identities must be unique in the target.
- Git initialization, dependency installation, network generators,
  provisioning, deployment, publication, and live skill installation remain
  separate explicit actions.

## Files

- `SKILL.md`: orchestration and safety contract.
- `agents/openai.yaml`: explicit-only invocation policy.
- `references/`: plan/candidate schemas, routing, layout, question, and safety
  contracts.
- `scripts/scaffold_project.py`: deterministic finalizer, validator, status,
  and apply executor.
- `scripts/test_scaffold_project.py`: disposable unit and race fixtures.
- `scripts/test_frontend_scaffold_e2e.py`: offline app-stack to frontend apply
  contract.
- `evals/trigger-prompts.md`: positive, negative, overlap, and quality cases.
