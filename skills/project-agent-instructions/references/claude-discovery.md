# Claude Discovery

Use the same canonical `AGENTS.md` ownership, managed tail, approval, rendering,
paired spec receipt, recovery and verification workflow on Claude. Inspect with
`--agent claude --agent-home <native-home>`; the Codex variant uses
`--agent codex --agent-home <native-home>`. No old home flag alias exists.

Before inspection, read the effective native instructions and settings in the
current trusted Claude session. Include user, managed, project, local, rules,
imports and any CLI-selected sources in their observed order. The helper
independently checks a required discovery floor and every declared byte digest;
the declaration cannot establish complete host loading by itself. Record static
discovery separately from a fresh `/context` observation. Unknown or incomplete
effective discovery context stops the operation.

Require an existing tracked, non-ignored project `CLAUDE.md` containing a
native `@AGENTS.md` import (standalone or within prose), or `.claude/CLAUDE.md` with `@../AGENTS.md`.
The helper does not create or edit that file. If absent, prepare the exact
import change through the user's separately authorized project-instruction
work before inspection. Preserve all existing native instructions and imports.

Create an owner-private `0600` declaration outside Git:

```json
{
  "schema": "project-agent-instructions.claude-runtime-config.v1",
  "session_sha256": "<sha256-of-native-hook-bound-session-identity>",
  "instruction_files": [
    {"path": "<absolute-canonical-instruction-path>", "sha256": "<file-sha256>"}
  ],
  "settings_files": [
    {"path": "<absolute-canonical-settings-path>", "sha256": "<file-sha256>"}
  ]
}
```

Use the installed native hook context (`SKILLS_AGENT=claude` and
`SKILLS_SESSION_ID`); never invent session identity or persist its raw value.
List each source once, including private ignored `CLAUDE.local.md` files as
conflict context; never copy their contents into generated rules. Include imported instruction files, with the exception
of the managed target itself: its digest belongs to the transaction's target
record, so a legitimate target change does not invalidate its own context.
The import source, settings and all other instructions remain frozen across
apply and replay. Changed, omitted, linked, retargeted or session-stale sources
require fresh inspection. No declared `verified` boolean grants authority.

The existing v3 transaction schema retains its `codex_home` field spelling as
an opaque protocol key for the selected native home. Replay derives the host
from the digest-bound native declaration schema; it does not route Claude
through Codex configuration or change existing Codex state shapes.

The generated body retains this repository's 4 KiB ceiling. This is independent
of the size of Claude's inherited instructions and is not a vendor context
limit. The canonical target may be absent before creation or after exact
generated-only retirement; the import remains unchanged. Report that state
and verify actual loading only when a target exists, after reload. Never claim
an absent target was loaded, or rewrite the import as part of retirement.

Sources: [Claude memory and AGENTS.md imports](https://code.claude.com/docs/en/memory),
[Claude native hook identity](https://code.claude.com/docs/en/hooks).
