# Project Agent Instructions Decision Contract

## Contents

- [Prerequisite receipts](#prerequisite-receipts)
- [Discovery contract](#discovery-contract)
- [Decision schema](#decision-schema)
- [Deterministic rendering](#deterministic-rendering)
- [Ownership and transitions](#ownership-and-transitions)
- [Recovery and reload](#recovery-and-reload)
- [Helper commands](#helper-commands)

## Prerequisite receipts

The `maintain-project-specs` owner validates both complete spec files before inspection. The
mode-`0600` receipt uses schema
`maintain-project-specs.spec-validation.v4` and binds:

- owner, validator, and validator version;
- exact selected project, Git root, and project scope;
- relative requirements/design paths and full-file SHA-256 digests; and
- a deterministic traceability-validation digest.

Task Implementer and Agentic SDLC are authoring adapters. They invoke the same
shared validator after checking their workflow-specific refinement or phase
contracts. During every inspection and replay, the helper reruns the fixed
`maintain-project-specs` validator and requires its complete output to equal the supplied
receipt. Marker presence or a caller-named validator is never sufficient.

The receipt is atomically written by the owner coordinator in the caller's
private workflow directory outside Git. Any
path, digest, owner, validator, or scope mismatch is
`SPEC_VALIDATION_REQUIRED`.

## Discovery contract

`inspect` resolves the exact selected project and fingerprints the effective
Codex discovery inputs in this order:

1. user `config.toml`;
2. declared active profile from `$CODEX_HOME/PROFILE.config.toml`;
3. trusted project `.codex/config.toml` files from Git root through selected
   project; and
4. declared discovery-sensitive runtime overrides.

Only fallback filenames, project-document byte capacity, and project-root
markers affect this workflow. The runtime declaration is mandatory: `null`
explicitly selects the base configuration, while a non-null profile loads the
current Codex 0.134+ profile file and fails closed if its name or file is
invalid. For a nonempty marker list, walk upward from the selected project
through the enclosing Git root and use the nearest directory containing any
effective marker as the instruction discovery root. Do not inspect a marker or
instruction above the Git root, and fail closed when no marker matches. An
empty list disables parent traversal and makes the selected directory the
discovery root. In every case the receipt, evidence, target, and generated
`AGENTS.md` remain scoped to the exact selected project.

The manifest separates global instructions from ancestor project
instructions. Global instructions are conflict context only: their presence or
absence does not change generated project bytes. Ancestor project files count
toward capacity and redundancy. At the selected directory,
`AGENTS.override.md`, then `AGENTS.md`, then configured fallbacks determine the
active source.

Semantic compatibility intent is supplied by `maintain-project-specs`, not
inferred by a hook. A canonical statement that real users depend on existing
behavior and future code or interface changes must not break them is explicit
intent even without prescribed keywords. Unless active same-directory project
instructions already provide an equivalent contract, render the two default
`Change requirements` rules documented in `SKILL.md`. Personal global
instructions remain conflict context only and cannot suppress those rules.

Git-ignored target paths are rejected before generation. Ancestor project
instructions and human-owned active instruction sources must be tracked and
non-ignored. A newly generated non-ignored `AGENTS.md` may remain untracked only
until the owning workflow stages and commits its locked contract.

Inspection fails with `RECOVERY_REQUIRED` if either managed lock or backup
artifact exists. This applies before every disposition, including no-write
decisions.

## Decision schema

Store the exact decision as `<private-root>/decision.json` outside Git:

<!-- markdownlint-disable MD013 -->

```json
{
  "schema": "project-agent-instructions.decision.v3",
  "manifest_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "disposition": "needed",
  "rationale": "The project has one durable verification rule not supplied by ancestor project instructions.",
  "evidence": [
    {
      "path": "docs/design.md",
      "sha256": "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789",
      "locator": "## Cross-Cutting Validation Strategy"
    }
  ],
  "rules": [
    {
      "section": "Testing and verification",
      "instruction": "Run the service contract suite for every public behavior change.",
      "evidence": ["docs/design.md"]
    }
  ],
  "budget_exception": null,
  "ownership_approval": null
}
```

<!-- markdownlint-enable MD013 -->

`disposition` is exactly one of:

- `needed`: one to twelve structured rules; the helper renders Markdown;
- `not-needed`: no rules because no durable project delta remains; or
- `existing-sufficient`: no rules because an active human-owned project file
  already supplies the contract.

A missing target plus `not-needed` is a complete, verified no-file decision:
render emits empty rules, apply writes final private state only, and verify
confirms the target remains absent. Coordinators must report that outcome
explicitly instead of describing it as pending or failed `AGENTS.md` creation.

Every disposition needs non-empty tracked evidence. Each evidence record binds
a canonical project-relative path, full-file digest, and one exact single-line
locator present in the UTF-8 file. Git-ignored or untracked evidence is
rejected. Each generated rule references at least one evidence path.

`ownership_approval` is normally null. It is exactly
`{"action":"adopt","target_sha256":"..."}` for explicit adoption of an
unproven intact v3 region or
`{"action":"retire","target_sha256":"..."}` for explicit retirement.
Approvals are applicable only to the exact inspected digest. An exact active
receipt imported by inspection from the workspace-private registry, including
an entry bootstrapped from unanimous sealed history, is already proven
ownership and does not use an adoption approval.

## Deterministic rendering

The helper, not the decision author, renders the complete body. It always
includes scope, the selected Git-relative project root, context authority for
requirements/design, and a nested-refinement statement. It then sorts rules
under these optional sections:

1. Architecture and boundaries
2. Development commands
3. Change requirements
4. Testing and verification
5. Security and operations
6. Definition of done

Rule IDs are deterministic hashes of section, instruction, and sorted evidence
paths. IDs remain private decision metadata and are not printed in the project
file.

The preferred budget is eight rules and 2 KiB. Exceeding either needs a compact
`budget_exception`. The hard limits are twelve rules, 256 UTF-8 bytes per rule,
and 4 KiB for the body; effective Codex capacity may be smaller. Rendering
never truncates. Empty sections, duplicate rules, multiline or control-bearing bullets,
placeholders, URLs, IP literals, recognized secret forms, and absolute user
home paths are rejected.

Rendering also preflights the disposition against the fresh inspected target.
For example, `existing-sufficient` is valid only for active human-owned project
instructions and fails before private rules or state are published for a
managed target. A revised current-session decision may change existing private
rules only when the prior canonical render state exactly binds the project,
manifest, decision and rules paths and hashes the current mode-`0600`,
single-link rules bytes. Historical spec and decision digests remain generation
metadata so later spec reconciliation can produce a new generation; planning
independently revalidates the new full digests. The helper holds one
nonblocking private-bundle lock and carries the validated state identity and
bytes into the final compare-and-swap, where it rechecks both state and rules
predecessors immediately before atomically replacing the rules. It syncs the
private directory and then publishes matching render state. A private I/O
failure while publishing that state returns only
`RENDER_STATE_PUBLICATION_INCOMPLETE`; exact current decision-rendered rules
let the next locked render finish publication, including re-syncing the private
parent directory when replacement completed before directory sync failed.
Missing, malformed,
ownership-mismatched, unsafe, hard-linked, or concurrently changing predecessor
evidence remains blocked, and generic `UNSAFE_TARGET` is not retryable.

## Ownership and transitions

The generated marker is:

<!-- markdownlint-disable MD013 -->

```text
<!-- project-agent-instructions:managed-v3 manifest-sha256=DIGEST decision-sha256=DIGEST body-sha256=DIGEST -->
```

<!-- markdownlint-enable MD013 -->

The marker digests are deterministic projections of repository-portable spec,
scope, evidence, and rendered-decision inputs. Absolute paths, private receipt
locations, personal global instructions, human-authored prefix, and rationale
never change managed-region bytes. The full private manifest and decision
retain runtime bindings. A separate private
`project-agent-instructions.ownership.v3` receipt binds the exact region
marker fields and project path. Marker-only ownership is insufficient.

Inspection reads one exact-schema registry under a dedicated owner-only shared
root in the same private workspace bucket. The registry is serialized by an
advisory lock held across authority preflight, target mutation, final state,
conditional publication, and recovery release. It maps a canonical project,
Git-root, scope, and target-path subject digest to a monotonic generation,
active, pending, blocked, or retired status, whole-target digest, optional exact
receipt, and optional source-state digest. First use atomically publishes a
complete generation-zero registry root. An existing entry is authoritative:
initialized-but-missing, unsafe, or malformed registry bytes fail closed, an
active entry must match the exact current whole target, pending binds one
interrupted apply to its exact receipt and state digest without granting
observer authority, blocked carries no rewrite authority, and a retired entry
is a durable tombstone.

When the subject has no entry, inspection may scan sibling lifecycle sessions
once as an unordered legacy evidence set. It bootstraps only when every relevant
safe sealed event agrees on the same exact active receipt and target digest.
Any retirement, incompatible active generation, malformed or unsafe relevant
evidence, ambiguity, or absence of proof publishes a receipt-free blocked
subject generation and preserves the adoption gate. Later evidence removal
does not rescan that subject; exact-digest adoption may publish a superseding
active generation. One exact completed apply awaiting registry publication
instead publishes a pending generation; only its matching receipt and state
writer can advance it to active. Session IDs, timestamps, directory order, and
filesystem metadata never establish causal order. The current bundle remains
the only
input to render, apply, and verify; callers never copy a prior receipt
themselves.

Transitions are:

- missing plus `needed` -> exclusive `created` and active ownership receipt;
- tracked human-owned `AGENTS.md` plus `needed` -> guarded `attached`, with the
  original bytes retained exactly as the human prefix;
- receipted intact v3 region plus identical rules and current portable marker
  projections -> `existing-sufficient`, no project write;
- receipted intact v3 region plus changed spec, renderer, or evidence
  projection -> guarded `refreshed`, even when the rendered body is unchanged;
- receipted intact v3 region plus changed rules -> guarded `refreshed` and new
  receipt;
- unreceipted intact v3 region plus exact `adopt` approval -> `adopted`, or guarded
  `refreshed` when rules changed;
- exact active workspace-registry entry, including unanimous sealed-history
  bootstrap -> imported into the current private bundle, then
  `existing-sufficient` or guarded `refreshed` without a second adoption
  approval;
- a receipt already established in the current session plus marker-only drift
  -> guarded `refreshed` without adoption approval, but only when project, Git
  root, scope, target path, and managed-body digest remain identical;
- intact v3 region plus `not-needed` and exact `retire` approval -> guarded
  `retired`, restoring the exact human prefix or removing the file when no
  prefix exists, and recording a retired receipt;
- missing plus `not-needed` -> private state only;
- active human-owned file plus `existing-sufficient` -> private state only.

Human prefix edits remain allowed and are preserved by later refreshes. An
edited marker/body mismatch transfers the managed region out of automation
ownership. An intact v1 or v2 marker returns `LEGACY_GENERATED_FILE`; there is
no migration or compatibility shim.

## Recovery and reload

Create is exclusive and rechecks lock/backup absence at its descriptor-anchored
mutation boundary. Attach, refresh, and retirement compare the exact inspected
whole-file bytes under a mode-`0600` lock while preserving the human prefix.
Mutations are anchored to the inspected project-root directory identity so a
parent-directory swap cannot redirect them. These transitions retain and fsync
a same-directory backup until ownership, final state, and the matching
workspace-registry generation are durable; only then is the backup removed and
the directory fsynced again. A surviving lock or backup blocks all later
actions until a human inspects the exact files and resolves the artifact;
automation never removes it speculatively.

Final state uses `project-agent-instructions.state.v3` and binds the manifest,
decision, ownership receipt, current target, active instruction, and outcome.
`verify` replays discovery, requires the matching current registry generation,
and checks all final postconditions.

`created`, `attached`, `refreshed`, and `retired` report
`reload_required: true`. Because
Codex discovers project instructions once per run, the coordinator must stop
that execution boundary, start a fresh session, rerun/verify the decision, and
read the active instruction file before continuing. Adoption changes private
ownership only and does not require reload.

The ownership registry is also the sole authority for historical private-state
retention. Its internal classifier returns a closed disposition while holding
the ownership lock: pending publication, missing legacy continuity, missing
final manifest/decision evidence, malformed state, or a generation/canonical-
registry-digest mismatch is protected. Matching active, retired, or blocked
registry state may
release the historical session bundle. The
classifier is not a public workflow command and never authorizes repository
mutation.

## Helper commands

Use the installed skill path and absolute caller-owned private paths. For the
placeholders below, `LIFECYCLE_SESSION`, `PRIVATE_PROJECT_AGENT_DIR`,
`SELECTED_PROJECT`, `CODEX_HOME`, and `INSTALLED_SKILLS_ROOT` are absolute:

<!-- markdownlint-disable MD013 -->

```text
python3 INSTALLED_SKILLS_ROOT/project-agent-instructions/scripts/project_agent_instructions.py inspect \
  --project-root SELECTED_PROJECT \
  --spec-owner maintain-project-specs \
  --requirements docs/requirements.md \
  --design docs/design.md \
  --spec-receipt LIFECYCLE_SESSION/spec-receipt.json \
  --runtime-config LIFECYCLE_SESSION/runtime-config.json \
  --codex-home CODEX_HOME \
  --private-root PRIVATE_PROJECT_AGENT_DIR \
  --output PRIVATE_PROJECT_AGENT_DIR/manifest.json

python3 INSTALLED_SKILLS_ROOT/project-agent-instructions/scripts/project_agent_instructions.py render \
  --private-root PRIVATE_PROJECT_AGENT_DIR \
  --manifest PRIVATE_PROJECT_AGENT_DIR/manifest.json \
  --decision PRIVATE_PROJECT_AGENT_DIR/decision.json \
  --output PRIVATE_PROJECT_AGENT_DIR/rules.md \
  --state PRIVATE_PROJECT_AGENT_DIR/render-state.json

python3 INSTALLED_SKILLS_ROOT/project-agent-instructions/scripts/project_agent_instructions.py apply \
  --private-root PRIVATE_PROJECT_AGENT_DIR \
  --manifest PRIVATE_PROJECT_AGENT_DIR/manifest.json \
  --decision PRIVATE_PROJECT_AGENT_DIR/decision.json \
  --ownership PRIVATE_PROJECT_AGENT_DIR/ownership.json \
  --state PRIVATE_PROJECT_AGENT_DIR/state.json

python3 INSTALLED_SKILLS_ROOT/project-agent-instructions/scripts/project_agent_instructions.py verify \
  --private-root PRIVATE_PROJECT_AGENT_DIR \
  --state PRIVATE_PROJECT_AGENT_DIR/state.json
```

<!-- markdownlint-enable MD013 -->

The lifecycle hook requires this exact inspect shape: explicit `--codex-home`,
the current session's canonical private members, and no shell composition.
Relative private paths and environment fallback are not canonical coordinator
inputs and fail at the CLI or lifecycle-hook boundary. The helper may populate
the current session's `ownership.json` from exact workspace-registry authority
or unanimous sealed-history bootstrap; the command never accepts a
prior-session ownership path from the caller.

Both Task Implementer and Agentic SDLC use the same spec owner. The runtime declaration
is always required so that the active profile and discovery-sensitive CLI
overrides are explicit. Declare the base profile and no overrides as:

```json
{
  "schema": "project-agent-instructions.runtime-config.v1",
  "profile": null,
  "overrides": {}
}
```

The helper private root is a dedicated initially empty mode-`0700` directory
outside every Git worktree. Manifest, decision, ownership, and state are direct
children with mode `0600`; the renderer's direct-child mode-`0600` lock file
serializes rules and state publication. The owner-issued spec receipt and
required runtime declaration are mode-`0600` caller inputs outside Git and sit
beside that directory. The lifecycle guard permits only the exact current-session
`runtime-config.json` and direct-child `decision.json` caller inputs. It may
also admit one exact, uncomposed numeric mode-`0600` tightening command for one
of those existing regular files; broader modes, directories, symlinks, sibling
sessions, and authoritative state remain denied. Every authoritative receipt
and state file remains coordinator-owned. The helper
prints bounded JSON; a nonzero result with `status: blocked` is authoritative
and must not be bypassed with direct editing.
