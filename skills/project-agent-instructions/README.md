# Project Agent Instructions

`project-agent-instructions` is an explicit-only mutation skill that may be
routed by `maintain-project-specs`. After the shared owner validates
the current requirements and design, the skill decides whether the exact
selected project needs a concise `AGENTS.md` that should apply in every future
agent session. `not-needed` is a valid result; specifications do not
automatically require a project instruction file.

## What It Does

- Replays the shared owner's specification receipt before trusting project
  requirements, design, or traceability.
- Evaluates only durable, project-specific, actionable rules supported by
  tracked repository evidence.
- Compares those rules with the effective inherited instruction chain without
  copying personal global instructions into repository content.
- Turns canonical existing-user no-break intent into a stable public-interface
  compatibility contract without requiring magic keywords, unless active
  selected-project instructions already provide it.
- Preserves every human-authored prefix byte while owning only one generated
  tail region; managed-region edits fail closed.
- Deterministically renders exact current-turn rules without repository
  mutation, rejects target-inapplicable decisions before publication, and
  serializes atomic private-rules revision after revalidating exact owned state
  and rules predecessors at the final replacement boundary. Only
  `RENDER_STATE_PUBLICATION_INCOMPLETE` is rerunnable after an interrupted
  matching-state I/O write; its equal-bytes retry re-syncs the private parent
  directory when needed, while generic unsafe targets remain terminal. The
  workflow then owns, recovers, and verifies selected-project `AGENTS.md`
  through a guarded terminal-seal transition.

## Coordinator Flow

```text
explicit user request or maintain-project-specs route
                         |
                         v
      canonical requirements/design receipt
                         |
                         v
inspect context -> decide -> render -> implement -> apply safely -> verify
                         |
                         v
finish this mutation workflow or report its own blocker
```

Task Implementer and Agentic SDLC do not invoke, wait for, or seal this
workflow; they read only the instruction chain already effective in their
session and treat project-instruction lifecycle status as advisory. Helper
commands and flags are not a user-facing interface. Use
`$project-agent-instructions --help` or
`$project-agent-instructions -h` only for report-only help; Help performs no
inspection, mutation, or private-state write.

When repository instruction bytes are created, attached, refreshed, or
retired, this workflow recommends a fresh Codex session before relying on the
new rules. That recommendation never halts Task Implementer or Agentic SDLC.

## Decision And Ownership Model

The selected project may be a subproject rather than the Git root. For
nonempty effective markers, discovery uses the nearest matching directory from
the selected project through the enclosing Git root and scans down from there;
an empty marker list uses only the selected directory. The target is always the
selected-project root `AGENTS.md`.

When the canonical specs say existing users depend on behavior that future
changes must not break, the default protected surface is supported public APIs
and import paths, CLI behavior, configuration schemas/defaults, persisted
formats, and upgrade paths. Breaking one requires explicit approval,
deprecation or migration planning, and regression coverage. Private internals
remain free to use one canonical implementation path.

Possible results are:

- `created`: a missing project file was needed and created.
- `attached`: needed rules were appended as a managed tail while all existing
  human bytes were preserved.
- `refreshed`: an intact owned file was regenerated from current evidence.
- `adopted`: an intact v3 region received explicit exact-digest ownership.
- `retired`: an intact owned region was removed with explicit exact-digest
  approval; any human prefix remains byte-identical.
- `existing-sufficient`: active human-owned instructions already cover the
  durable project contract.
- `not-needed`: no meaningful durable project-specific rule remains; no file is
  created or changed, and a missing target remains absent.
- structured blocker: ownership, discovery, safety, recovery, specification,
  or concurrency proof is incomplete.

The selected-project `AGENTS.md` is committed project truth. Manifests,
decisions, ownership receipts, runtime declarations, approvals, and final state
remain private outside Git. A new session automatically continues ownership
from a locked, monotonic workspace-private registry entry that binds the exact
project, scope, target path, whole-target digest, source-state digest, and
active receipt. If the subject has no entry, the helper may bootstrap it once
only when all relevant safe sealed history unanimously proves that exact
active authority; retirement, mismatch, damaged evidence, ambiguity, or no
proof publishes a durable blocked subject generation. Removing that evidence
does not retry bootstrap; only exact-digest adoption may supersede the block.
The registry root itself appears atomically with a complete generation-zero
registry. When one exact completed apply is awaiting registry publication, the
helper records a pending generation bound to its receipt and state digest;
observers cannot import it, while the exact interrupted writer can recover it
to active. The helper imports the verified receipt into the current private
bundle before apply. Instruction discovery, marker presence, session IDs,
timestamps, and filesystem order never confer rewrite authority. Unproven
adoption and every retirement require exact target approval. Marker-only drift
may continue only from a receipt already in the current session whose project,
target, and managed-body bindings are unchanged. Human prefix edits remain
outside automation ownership; managed-body edits transfer the region out of
automation ownership. Recovery artifacts block every transition until
resolved instead of being removed or bypassed automatically.

Lifecycle-owned inspection is one uncomposed canonical command. It declares
the active Codex home explicitly and uses absolute receipt, runtime,
private-root, and manifest-output paths from the exact current-session bundle;
environment fallback or relative output is not valid lifecycle evidence.
Inspection reports `ownership_continuity` as `current`, `carried-forward`,
`unproven`, or `not-applicable`; callers must not copy ownership files between
sessions themselves.
The same canonical registry validator supplies a private closed retention
disposition to project-spec maintenance. `pending`, absent-registry legacy
evidence, missing final manifest/decision evidence, and malformed or mismatched
state remain protected; only matching active, retired, or blocked registry
generation plus canonical registry digest snapshots release historical
ownership evidence. Public inspect, render,
apply, and verify commands hold the stable
workspace/session maintenance locks before render or ownership locks.
Matching `PreToolUse` guards run before the command and may report a rejection
before the assistant can explain or retry it. `Stop` evaluates turn-final
lifecycle state after the assistant response; these event positions are not a
workflow command ordering mechanism.

## Boundaries

- Do not infer mutation authority from ordinary project work or from Task
  Implementer, Agentic SDLC, or hook observations.
- Do not generate generic advice, temporary task rules, prompts, architecture
  prose, handoffs, troubleshooting history, or repeatable procedures.
- Do not create `AGENTS.override.md` or recursively generate instruction files.
- Do not write or delete the selected-project target outside the guarded
  helper.
- Do not commit private receipts, decisions, ownership evidence, or workflow
  state.
- Do not copy personal global instructions, secrets, endpoints, environment
  values, or absolute home paths into generated content.
- Do not add a legacy compatibility path for v1 or v2 generated files.

The authoritative workflow, failure codes, size limits, state schemas,
transition rules, recovery algorithm, and private helper interface remain in
[`SKILL.md`](SKILL.md) and
[`references/decision-contract.md`](references/decision-contract.md).

## Files

- [`SKILL.md`](SKILL.md): runtime workflow and safety contract.
- [`agents/openai.yaml`](agents/openai.yaml): UI metadata and explicit-only
  invocation policy.
- [`references/decision-contract.md`](references/decision-contract.md): v3
  receipts, discovery, rendering, ownership, recovery, and verification.
- [`evals/trigger-prompts.csv`](evals/trigger-prompts.csv): should-trigger and
  should-not-trigger examples.
- [`evals/process-cases.md`](evals/process-cases.md): supplemental coordinator
  and decision-outcome cases.
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
