# Create Requirements

`sdlc-create-requirements` is an Agentic SDLC authoring adapter to
`maintain-project-specs`. It is authored in this repository and is
installed into a Codex runtime only when `install-skills.sh` is run.

## What It Does

Convert user intent into durable, testable product requirements in the
canonical managed region of `docs/requirements.md`, including an optional guarded Live Experiment
Environment section for later evaluation and UAT.

## Main Boundaries

- Do not change the design managed region. Its exact bytes still participate
  in the owner-managed paired validation/publication transaction.
- Do not create execution plans.
- Do not implement code or tests.
- Do not rename existing requirement IDs.
- Do not store raw credentials, private endpoints, customer data, or raw logs.

## Primary Inputs

- User prompt or approved change request.
- Existing `docs/requirements.md` when present.
- Existing `docs/design.md` for impact awareness only.
- Optional non-production or disposable live experiment environment details.
- Optional Jira, Slack, Confluence, GitHub, or pasted context.

## Output

- A canonical v2 requirements/design pair exists and passes the shared owner;
  this adapter changes only the requirements managed region.
- Every requirement has acceptance criteria, validation method, test method, and evaluation method.
- Live Experiment Environment status is recorded.
- Open questions and change log are explicit.
