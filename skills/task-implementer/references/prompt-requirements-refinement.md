# Prompt Requirements Refinement

Use this contract after accepting a prompt revision and before locking waves or
dispatching implementation work. The editable prompt is durable user intent;
the requirements document is the compiled implementation contract.

## Source Precedence

Compile from these sources in order, with the newest explicit user statement
winning when two sources conflict:

1. The latest accepted immutable prompt revision, especially `## Ask`.
2. Explicit answers the user provided in chat for this prompt revision.
3. Current repository and product facts that can be inspected safely.
4. Existing accepted requirements and design truth.
5. Older revisions and completed-run history for lineage, never as a reason to
   restore intent the user explicitly superseded.

An omitted section is not a deletion. Existing product truth persists unless
the latest Ask or a clarification answer explicitly changes, removes, or
supersedes it. A completed-prompt edit is a linked fresh objective evaluated
against current repository and product truth, not a textual patch instruction.

## Extraction

Read the entire Ask and any optional or custom headings. Classify every
material statement into the narrowest applicable requirements surface:

- outcome and user-visible behavior;
- users, actors, inputs, outputs, and external systems;
- context and current-state facts;
- functional requirements;
- constraints, safety limits, authorization, compatibility, and performance;
- observable acceptance criteria, including negative criteria;
- verification, test, and evaluation expectations;
- non-goals and explicitly excluded behavior;
- assumptions and dependencies;
- references and safe live-environment instructions.

Do not require the user to pre-sort prose into headings. Do not copy prompt
prose blindly: remove duplication, preserve normative terms, split compound
requirements, and make each acceptance criterion observable. Record an
inference as an assumption until repository evidence or the user confirms it.

## Selective Clarification

Inspect discoverable project facts before asking the user. Ask only when two or
more plausible interpretations would materially change at least one of:

- user-visible behavior or the definition of done;
- an external interface, data contract, migration, or compatibility boundary;
- authorization, destructive action, privacy, security, availability, or cost;
- a required platform, dependency, architecture boundary, or rollout choice;
- acceptance evidence or whether an existing requirement is removed.

Non-blocking uncertainty becomes an explicit assumption or open question and
does not stop useful compilation. Material ambiguity blocks contract lock and
implementation planning. Never invent a default merely to avoid a question.

Give each material question a stable `Q-001`, `Q-002`, ... identifier within
the prompt lineage. One question covers one decision and states why it blocks.
Accept an answer from chat or a later prompt revision. Persist the answer,
source, and accepted revision privately. If a later prompt contradicts an
answer, mark the same question `reopened`; do not allocate a replacement ID.

## Private Refinement State

Keep the refinement ledger at
`<run-dir>/requirements-refinement.json` with schema
`task-implementer/requirements-refinement-v1`. It records:

- prompt ID, accepted revision, and intent digest;
- status: `extracting`, `needs_clarification`, or `ready`;
- categorized extracted statements and assumptions;
- stable questions with `open`, `answered`, or `reopened` status;
- answer source (`chat` or `prompt`), source revision, and conflict note;
- the compiled requirements-region digest and update time.

The ledger is private coordination state. Never place prompt IDs, run IDs,
revision IDs, private paths, raw chat, or internal digests in committed specs.
Do not set `ready` while any material question is open or reopened.

## Requirements Ownership

Task Implementer writes only its registered managed regions in
`requirements.md` and `design.md`. Preserve all human-owned text outside those
markers byte-for-byte. Within the requirements region, update existing stable
IDs when product truth changes and append IDs for genuinely new requirements.
Do not delete accepted truth on omission alone.

Contract lock requires the latest accepted intent digest, a `ready` refinement
ledger, valid managed requirements/design regions, and no unresolved
contradiction. The wave planner verifies that the compiled digest equals the
exact current Task Implementer-owned requirements region before acquiring
repository or worktree resources. A semantic no-effect follow-up may finish
without implementation tasks after recording that current product truth
already satisfies the compiled objective.
