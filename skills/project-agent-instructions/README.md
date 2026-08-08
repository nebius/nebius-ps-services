# Project Agent Instructions

`project-agent-instructions` is an explicit-only, coordinator-owned support
skill for Task Implementer and Agentic SDLC. After the owning workflow validates
the current requirements and design, the skill decides whether the exact
selected project needs a concise `AGENTS.md` that should apply in every future
agent session. `not-needed` is a valid result; specifications do not
automatically require a project instruction file.

## What It Does

- Replays the workflow owner's specification receipt before trusting project
  requirements, design, or traceability.
- Evaluates only durable, project-specific, actionable rules supported by
  tracked repository evidence.
- Compares those rules with the effective inherited instruction chain without
  copying personal global instructions into repository content.
- Preserves human-owned or edited instruction files byte-for-byte.
- Deterministically renders, owns, recovers, and verifies a generated
  selected-project `AGENTS.md` through guarded transitions.

## Coordinator Flow

```text
Task Implementer or Agentic SDLC
              |
              v
validated requirements/design receipt
              |
              v
inspect context -> decide -> apply safely -> verify
              |
              v
continue, block, or restart in a fresh Codex session
```

This is an internal workflow boundary. It has no standalone public workflow
action, and its helper commands and flags are not a user-facing interface. Use
`$project-agent-instructions --help` or `$project-agent-instructions -h` only
for report-only help; Help performs no inspection, mutation, or private-state
write.

When repository instruction bytes are created, refreshed, or retired, the
coordinator stops and starts a fresh Codex session before planning, contract
lock, auto-steering, or worker dispatch. The fresh session replays validation
and explicitly reads the active instruction file.

## Decision And Ownership Model

The selected project may be a subproject rather than the Git root. Project
root discovery follows the effective Codex configuration, while the target is
always the selected-project root `AGENTS.md`.

Possible results are:

- `created`: a missing project file was needed and created.
- `refreshed`: an intact owned file was regenerated from current evidence.
- `adopted`: an intact v2 file received explicit exact-digest ownership.
- `retired`: an intact owned file was removed with explicit exact-digest
  approval.
- `existing-sufficient`: active human-owned instructions already cover the
  durable project contract.
- `not-needed`: no meaningful durable project-specific rule remains.
- structured blocker: ownership, discovery, safety, recovery, specification,
  or concurrency proof is incomplete.

The selected-project `AGENTS.md` is committed project truth. Manifests,
decisions, ownership receipts, runtime declarations, approvals, and final state
remain private outside Git. Adoption and retirement require exact target
approval; edited generated files become human-owned immediately. Recovery
artifacts block every transition until resolved instead of being removed or
bypassed automatically.

## Boundaries

- Do not invoke this skill directly outside Task Implementer or Agentic SDLC.
- Do not generate generic advice, temporary task rules, prompts, architecture
  prose, handoffs, troubleshooting history, or repeatable procedures.
- Do not create `AGENTS.override.md` or recursively generate instruction files.
- Do not write or delete the selected-project target outside the guarded
  helper.
- Do not commit private receipts, decisions, ownership evidence, or workflow
  state.
- Do not copy personal global instructions, secrets, endpoints, environment
  values, or absolute home paths into generated content.
- Do not add a legacy compatibility path for v1-generated files.

The authoritative workflow, failure codes, size limits, state schemas,
transition rules, recovery algorithm, and private helper interface remain in
[`SKILL.md`](SKILL.md) and
[`references/decision-contract.md`](references/decision-contract.md).

## Files

- [`SKILL.md`](SKILL.md): runtime workflow and safety contract.
- [`agents/openai.yaml`](agents/openai.yaml): UI metadata and explicit-only
  invocation policy.
- [`references/decision-contract.md`](references/decision-contract.md): v2
  receipts, discovery, rendering, ownership, recovery, and verification.
- [`evals/trigger-prompts.md`](evals/trigger-prompts.md): should-trigger and
  should-not-trigger examples.
- [`scripts/project_agent_instructions.py`](scripts/project_agent_instructions.py):
  private coordinator helper entry point.
- [`scripts/project_agent_instructions_lib/`](scripts/project_agent_instructions_lib/):
  contracts, discovery, private state, target I/O, and workflow transitions.
- [`scripts/test_project_agent_instructions.py`](scripts/test_project_agent_instructions.py):
  focused unit and disposable-Git coverage.

## Source Validation

From the repository `skills/` directory:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 \
  project-agent-instructions/scripts/test_project_agent_instructions.py
python3 align-skill/scripts/validate-skill-structure.py \
  --profile stateful-workflow project-agent-instructions
markdownlint project-agent-instructions/README.md
```

These checks validate the repository source. They do not install the skill or
prove that an active Codex session loaded it. Installation remains a separate,
intentional action through the repository's canonical `install-skills.sh`
workflow.
