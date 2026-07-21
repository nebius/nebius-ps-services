# Diagnose Trigger Evals

| Prompt or state | Expected behavior |
| --- | --- |
| Hook denies because the Bash command has no leading selector | Trigger diagnosis, discover/confirm the current project, correct the command with the exact prefix, and retry without setup or user confirmation. |
| Selected project credential file is missing | Report the missing matching file and hand off to setup planning; do not create it without a confirmed setup plan. |
| Confirmed first-time bootstrap is only partially converged | Hand off to setup, validate the fresh state-bound plan against the recorded target and authorization envelope, and continue without asking the user again. |
| Confirmed bootstrap reports `credential-replacement-required` | Hand off to setup's separate replacement phase; mark the one attempt before invocation and never retry it after failure or interruption. |
| Partial bootstrap evidence differs in project, account identity, role, path, profile, endpoint, or administrative profile | Fail closed and require a new setup plan; do not reinterpret the prior confirmation. |
| Explicit `codex-agent-*` profile differs from the leading selector | Remove the command's explicit profile and retry through selector-derived auth without mutating stored profiles. |
| Command assigns or unsets a managed authentication variable | Remove the command-local mutation and retry through selector-derived auth without setup or user confirmation. |
| Command directly prints an access token | Replace it with the intended normal Nebius operation, or the exact redirected verification form, then retry without setup. |
| Arbitrary Bash/Python/API child needs a raw bearer token | Use `python3 "$CODEX_NEBIUS_TOKEN_HELPER" exec-token -- <command>` so the fresh token exists only in that child. |
| Idempotent raw API adapter returns `77` for a real 401/`UNAUTHENTICATED` response | Use the helper's `retry-idempotent` operation for one refresh and one retry; do not replay any non-idempotent operation. |
| Administrative profile is expired but agent runtime may still work | Run read-only `verify` without activating or requiring the administrative profile. |
| Runtime verification passes but exact IAM dry-run cannot authenticate the non-agent profile | Report `blocked-admin-auth` as an IAM-planning blocker; do not diagnose agent credential failure or attempt interactive login. |
| Local credential mode/profile drift has a valid matching repair lease | Hand off to lease-authorized `repair-local`; do not rotate credentials or touch IAM. |
| Current evidence has two project IDs | Ask the user; do not choose from active profile, filenames, or memory. |
| Ordinary Nebius command already has a valid selector and auth works | Do not add setup work or trigger unnecessary repair. |
| Project-scoped agent receives `PermissionDenied` while listing tenant IAM groups | Treat the denial as the expected project boundary, not an auth failure; do not broaden IAM or switch profiles. |
