# Project Agent Instructions Decision Contract

## Contents

- [Evidence gate](#evidence-gate)
- [Decision schema](#decision-schema)
- [Generated content](#generated-content)
- [Ownership and conflicts](#ownership-and-conflicts)
- [Helper commands](#helper-commands)

## Evidence gate

Create a project file only when at least one candidate instruction:

1. follows from current requirements, design, or repository evidence;
2. applies durably to future work in the selected project;
3. changes agent behavior beyond inherited instructions;
4. is actionable and can be checked; and
5. belongs in agent guidance rather than product specs, task state, detailed
   architecture documentation, or a reusable skill.

Requirements and design may establish boundaries, public contracts, generated
artifacts, security controls, rollout rules, or verification obligations.
Commands require separate proof from current project configuration, scripts,
task runners, or CI.

Both documents must belong exclusively to the declared workflow. The helper
requires the expected requirements and design markers in their corresponding
files and rejects every marker from the other workflow in either document.

The following alone do not justify a file:

- generic advice such as "write clean code";
- restating acceptance criteria;
- a generic instruction to read requirements and design;
- global security or Git rules that already apply;
- empty template sections; or
- temporary task decisions.

## Decision schema

Store the decision outside the Git worktree:

<!-- markdownlint-disable MD013 -->

```json
{
  "schema": "project-agent-instructions.decision.v1",
  "manifest_sha256": "<sha256 from inspect>",
  "disposition": "needed",
  "rationale": "Compact, public-safe reason for the decision.",
  "evidence": [
    {
      "path": "docs/design.md",
      "sha256": "<current file sha256>"
    }
  ],
  "body": "# Example Agent Instructions\n\n## Scope\n\nThese instructions apply to this directory and all descendants.\n\nProject root: `.`\n\n## Read before changing\n\n- Requirements: `docs/requirements.md`\n- Design: `docs/design.md`\n"
}
```

<!-- markdownlint-enable MD013 -->

`disposition` is exactly one of:

- `needed`: `body` is a complete generated body and evidence is non-empty;
- `not-needed`: target is absent, `body` is null, and no meaningful delta
  survives the evidence gate;
- `existing-sufficient`: an active human-owned project instruction file already
  supplies the needed contract and `body` is null.

Every evidence path is relative to the selected project and is digest-checked
by the helper. Untracked paths classified as ignored by
`git check-ignore --quiet` are rejected. Tracked files remain eligible even
when they match an ignore pattern because Git does not classify them as
ignored. Do not cite private state, symlinks, files outside the selected
project, or raw prompt snapshots.

## Generated content

The helper adds the provenance marker. `inspect` reports
`generated_body_max_bytes` as the smaller of 7 KiB and the remaining
`project_doc_max_bytes` capacity after ancestor project instruction bytes and
the generated marker. Global instructions and join separators do not consume
Codex's project-document counter. Only ancestor project files are charged
before the target; a selected-project target refresh replaces its prior bytes,
while an alternate active file blocks generation. The helper floors exhausted
capacity at zero.

Generate the complete body within that advertised limit. The helper rejects an
oversized body and never truncates it. The decision body must:

- be UTF-8 Markdown no larger than `generated_body_max_bytes`, with exactly one
  trailing newline;
- start with `# <Project Name> Agent Instructions`;
- contain `## Scope` and `## Read before changing`;
- state that scope applies to the directory and descendants;
- identify the project root relative to the Git root;
- cite `docs/requirements.md` and `docs/design.md`;
- omit secrets, private endpoints including private IPv6 literals, and absolute
  user-home paths;
- use only these optional second-level headings, in order:
  - `Project purpose`
  - `Architecture and boundaries`
  - `Development commands`
  - `Change requirements`
  - `Testing strategy`
  - `Security requirements`
  - `Operational requirements`
  - `Verification requirements`
  - `Context authority`
  - `Definition of done`
- omit empty sections, placeholders, `TODO`, and `TBD`.

Prefer terse bullets. Put detailed explanations in project documentation and
repeatable procedures in skills.

## Ownership and conflicts

The marker has this form:

```text
<!-- project-agent-instructions:generated-v1 body-sha256=<digest> -->
```

The digest covers all bytes after the marker and separating blank line. A
matching marker permits compare-before-replace refresh. Missing or mismatched
provenance makes the file human-owned.

Apply rules:

- missing target plus `needed` -> exclusive create;
- unchanged generated target plus changed `needed` body -> guarded refresh;
- unchanged generated target plus identical body -> no project write;
- missing target plus `not-needed` -> private state only;
- active human-owned target plus `existing-sufficient` -> private state only;
- unchanged provenance-owned generated content never qualifies for an
  `existing-sufficient` decision; use `needed` with the complete body so the
  helper can compare or refresh it;
- active human-owned target plus `needed` -> block without mutation;
- same-directory override or configured fallback -> treat it as the active
  human-owned file;
- stale generated content that is no longer needed -> preserve and report;
- every create, refresh, and no-write decision must satisfy its exact final
  target ownership, digest, active-source, and transition postconditions before
  private state is written or verified;
- any concurrent target or active-source change -> block;
- if refresh installation fails and no competing target appeared, restore the
  verified prior generated file when possible; preserve the recovery backup if
  restoration also fails;
- if a competing file appears at the final refresh boundary, preserve that file
  at `AGENTS.md` and retain the prior generated bytes in
  `.AGENTS.md.project-agent-instructions.backup` for explicit recovery.

## Helper commands

Use the installed skill path and caller-owned private paths:

```text
python3 scripts/project_agent_instructions.py inspect \
  --project-root <selected-project> \
  --spec-owner <task-implementer|agentic-sdlc> \
  --requirements docs/requirements.md \
  --design docs/design.md \
  --private-root <private-project-agent-instructions-dir> \
  --output <private-manifest.json>

python3 scripts/project_agent_instructions.py apply \
  --private-root <private-project-agent-instructions-dir> \
  --manifest <private-manifest.json> \
  --decision <private-decision.json> \
  --state <private-state.json>

python3 scripts/project_agent_instructions.py verify \
  --private-root <private-project-agent-instructions-dir> \
  --state <private-state.json>
```

The private root is a dedicated mode-`0700` directory outside every Git
worktree. The helper creates or validates its private ownership marker and
accepts no manifest, decision, or state path outside that root. Existing
workflow-owned JSON must retain its complete expected schema and mode `0600`;
both `apply` and `verify` recheck those modes and reject malformed evidence with
a structured blocker.
The helper uses Python 3.9-compatible annotations. Reading an existing Codex
`config.toml` requires the standard-library `tomllib` from Python 3.11+ or the
optional `tomli` package on older Python versions.

The helper prints a bounded JSON result. A nonzero exit with
`"status": "blocked"` is authoritative; do not bypass it with direct file
editing. A reported recovery backup blocks later refreshes until a human
compares both files, resolves the intended content, and removes the backup.
