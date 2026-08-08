# Prompt Refinement Contract

Treat the accepted prompt snapshot as durable user intent and
`docs/requirements.md` as compiled product truth. Only `## Ask` is required;
optional and custom headings are supplemental context, not a form the user must
complete.

Use newest explicit user intent first, then chat answers for the accepted
revision, safely inspected repository facts, existing requirements/design, and
older lineage history. Omission is not deletion. Preserve accepted truth unless
the user explicitly changes, removes, or supersedes it. A completed-prompt edit
is a fresh full objective against current product truth, not a textual delta.

Classify the full Ask into outcomes, actors, inputs/outputs, context,
functional requirements, constraints, acceptance and negative criteria,
verification/test/evaluation, non-goals, assumptions, dependencies,
references, and safe live-environment instructions. Deduplicate prose, split
compound requirements, preserve normative terms, and make criteria observable.

Inspect discoverable facts before asking. A question blocks only when plausible
answers materially change behavior, an external/data/compatibility contract,
safety or authority, availability or cost, architecture/platform choice,
acceptance evidence, or deletion of accepted truth. Record other uncertainty as
an assumption or non-blocking open question.

Allocate stable lineage-scoped `Q-001`, `Q-002`, ... identifiers. One question
covers one decision and explains why it blocks. Accept answers from chat or a
later prompt revision and record their source revision privately. A conflicting
later prompt reopens the same ID.

The private `requirements-refinement.json` schema is
`agentic-sdlc/requirements-refinement-v1`. Keep its accepted prompt identity,
revision, intent digest, extracted categories, questions, answer provenance,
compiled requirements digest, and status (`extracting`,
`needs_clarification`, or `ready`) outside Git. `ready` requires a compiled
requirements digest and no material open or reopened question. Never place
prompt/run IDs, private paths, raw chat, or internal digests in committed specs.

After writing the file and saving `ready`, invoke the private
`refinement-verify` action owned by `sdlc-start` with the exact workspace and
run. Do not route to design until it proves that the latest accepted prompt
identity and intent digest match the exact current `docs/requirements.md`
bytes.
