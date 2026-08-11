# Legacy Spec Migration

## Accepted source pairs

- Task Implementer managed regions using
  `task-implementer/{requirements,design}-v1` and stable `TI-*` IDs.
- Agentic SDLC full documents using `agentic-sdlc.*.v1` and stable `REQ-*` /
  `FEAT-*` IDs.
- An already-canonical pair, which is an idempotent no-op.

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

Migration preserves every existing record ID. Task Implementer regions are
retagged without changing their body or surrounding bytes. Agentic SDLC
documents retain their content while ownership metadata changes and the full
content becomes the canonical managed region.

After migration, only the shared validator may issue an authoritative receipt.
Former validator entrypoints are adapters to it.
