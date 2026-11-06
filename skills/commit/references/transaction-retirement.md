# Historical transaction audit and retirement

This is an internal administrative helper, not a new `$commit` action. It never
executes or upgrades historical grants, switches branches, modifies Git refs,
calls GitHub, or creates a successful workflow closure. Use the selected agent's
normal private home; never redirect it to another user's state.

## Audit

```text
python3 <commit>/scripts/transaction_retirement.py audit --repo-root <root>
python3 <commit>/scripts/transaction_retirement.py audit --repo-root <root> \
  --task sessions/<session-digest-prefix>/tasks/<receipt-digest>.json
```

Audit reads only and returns one disposition per unclosed task, or the selected
exact task. An unknown schema, malformed/private path, pending effect, unresolved
claim or missing object remains retained. Any scratch-validation journal requires
its original owner; even a claimed terminal journal is not administrative proof
that its scratch refs are gone. Supported historical schemas are read
only as evidence. Missing old publication fields mean unobserved outcomes.
Owner inactivity and remote settlement are always unverified by local audit.
An unarchived or old session is not evidence of relinquishment or termination.

Preserve the selected record's complete audit object in an owner-only private
file for a later retirement operation. Do not treat a successful audit command
or an empty local blocker list as retirement authority.

## Establish evidence

The root administrator must independently establish both conditions:

- The owning session explicitly relinquished this exact operation, or its
  termination and inability to resume are independently established.
- All local and remote effects are settled, including effects omitted from
  older schemas. Inspect the actual remote outcomes when necessary.

If either condition cannot be established, retain the record and explain why.
Do not manufacture evidence from timestamps, missing fields, generic booleans,
another session's state, or the desire to clear an inventory.

The administrator supplies a private JSON attestation with exactly these fields:

```json
{
  "schema": "commit-transaction.retirement-evidence.v1",
  "audit_sha256": "<digest returned by audit>",
  "owner": {
    "status": "relinquished",
    "references": [{"path": "<absolute private proof file>", "sha256": "<digest>"}]
  },
  "effects": {
    "status": "settled",
    "references": [{"path": "<absolute private proof file>", "sha256": "<digest>"}]
  }
}
```

Owner status may instead be `terminated`. Proof files must be bounded,
owner-only regular files under the selected agent home. References identify
existing concrete evidence; the helper verifies bytes and bindings, while the
root administrator owns the semantic assessment. Keep proof concise and free
of secrets or raw logs. Never copy these runtime artifacts into the repository.

## Retire and recover

```text
python3 <commit>/scripts/transaction_retirement.py retire --repo-root <root> \
  --audit <private-selected-audit.json> --evidence <private-attestation.json>
```

The helper locks the original repository, compares the full audit again,
archives original task/checkpoint/attempt/associated-claim evidence and referenced
proof bytes durably, then atomically replaces the canonical grant with an
administrative tombstone. Shared receipts, authorizations, claims and checkpoints
remain in place; superseded shared artifacts are not copied into this archive.
There is never an absent-grant interval permitting historical `begin` to recreate
authority. Retirement is not a successful completion claim.

After interruption, repeat the exact operation. Once the tombstone exists,
replay validates archived proofs rather than requiring their original locations
to survive rotation. The archive retains the selected audit and attestation for
that replay. Unknown changes block recovery; never delete the tombstone, rebuild
authority, or silently replace changed archive evidence.

Both commands expose `--help` and `-h`. Neither installs skills or proves native
workflow activation. Retained historical records do not by themselves block new
v4 operations.
