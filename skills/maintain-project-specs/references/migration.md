# Legacy Spec Migration

## Accepted source pairs

- Task Implementer managed regions using
  `task-implementer/{requirements,design}-v1` and stable `TI-*` IDs.
- Agentic SDLC full documents using `agentic-sdlc.*.v1` and stable `REQ-*` /
  `FEAT-*` IDs.
- Canonical `maintain-project-specs.*.v1` pairs.
- An already-canonical v2 pair, which is an idempotent no-op.

Mixed pairs, one-file migrations, ambiguous markers, and edited ownership
metadata fail closed.

## Transaction

Migration takes an owner-only advisory lock outside Git, writes both exact
backups and a digest-only journal into a private staging directory, and
atomically publishes that complete directory as the recovery transaction
before either repository file changes. A process interruption before publish
can leave only an ignorable private staging directory, which the next locked
`migrate` or `recover` validates and removes. In-process failure restores the
original pair without consuming either backup. A later `recover` first
validates the exact journal schema, both backup digests, and each current target
against the recorded before/after digests. Tampered or incomplete published
evidence is retained and fails closed.

After a successful migration or rollback, the complete published transaction
is atomically renamed to a private disposable cleanup bundle before any child
is removed. If cleanup is interrupted, the canonical transaction name is
already free; the next locked `migrate` or `recover` validates and removes the
partial cleanup bundle before proceeding.

Migration preserves every existing record ID and human-owned byte outside the
managed regions. Compact Task Implementer records and heading-only canonical
v1 records are expanded into the rich v2 requirement/design structure while
preserving both core and `TI-*` IDs. Additional canonical v1 metadata fields
are retained as labelled design or requirement details; genuinely absent
legacy fields receive explicit migration disclosures instead of invented
project facts. Canonical v1 and Agentic SDLC v1 records gain independent design
readiness/delivery markers and separate implementation/verification evidence
sections. Historical requirement status `accepted` maps to `active`;
historical design status `planned` maps to `ready` plus `not-started`;
historical `implemented` maps to `ready` plus `implemented`; historical
`active` design work maps to `ready` plus `unassessed`. Migration never infers
verified delivery.

The converted pair is validated in memory before publication. After migration,
only the shared v2 validator may issue a receipt. Former validator entrypoints
are adapters to it; normal inspection does not accept v1 as a compatibility
path.
