# Missing Settings Recovery

Use this workflow only for a missing native `settings.json`. Existing files,
including malformed JSON, use inspection and patch-only reconciliation.

The minimal baseline is `{}`. The explicitly selected trusted-local baseline
contains only the two reviewed permission/sandbox keys. Neither baseline copies
models, personal trust, credentials, environment values, application state,
plugin registrations or hook settings from another machine. Add selected
integrations through their owning setup path afterward.

After verifying or creating the native home as a private directory, and when
execution is authorized:

```bash
python3 scripts/create-recovery-config.py \
  --claude-home /absolute/private/claude-home --profile minimal
```

- `--claude-home PATH`: existing absolute Claude home; default is
  `CLAUDE_CONFIG_DIR` or `~/.claude`.
- `--profile minimal|trusted-local`: default `minimal`; trusted-local requires
  explicit selection and never overrides managed restrictions.
- `-h, --help`: report usage without reads or writes.

The helper validates its allowlisted asset, writes a private temporary file,
flushes it and publishes with an exclusive hard link relative to an open
directory descriptor. It never replaces an existing target. A concurrent
creator wins without losing data. The completed settings have mode `0600`.
No backup is created because no existing file is modified.

`CREATED` and `CREATED_WITH_WARNING` both mean publication occurred; a durability
or cleanup warning requires inspection, never blind creation retry. `EXISTS`
means use patch-only handling. `FAILED` means inspect the sanitized failure
scope before retrying. Native private authentication/session state cannot be
recovered from this public baseline.

Refresh public recovery assets only after an explicit source-update request.
Rebuild from a documented allowlist, never a live-config dump, and update the
renderer/tests together when the reviewed baseline changes. Existing-machine
preferences do not silently become a public default.
