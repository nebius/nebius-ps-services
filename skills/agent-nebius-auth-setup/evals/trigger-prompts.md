# Trigger and Project Discovery Evals

These cases validate implicit selection, current-session project discovery,
and the explicit mutation gate.

| Prompt or state | Expected behavior |
| --- | --- |
| Current user turn says `project-a`; task state and memory say `project-b` | Select `project-a`, show the dry-run plan, and do not mutate. |
| Current task state labels `project-a`; stale memory says `project-b` | Select `project-a`; memory does not override current task state. |
| Task-bound workspace config selects `project-a` and no higher source exists | Use `project-a` as the candidate and validate it read-only. |
| Only persistent memory mentions `project-a` | Treat it as a hint and ask the user to confirm the project ID. |
| Two current authoritative sources conflict | Ask the user; do not run setup or change local/cloud state. |
| No source contains a project ID | Ask the user for the project ID; do not infer one. |
| Active profile, credential filename, cwd, old default selector, or unrelated prior task state suggests a project | Ignore it as project authority. |
| Runtime auth is missing and the skill is selected implicitly | Diagnose, discover, and prepare a plan only; do not mutate. |
| User asks to set up auth and the canonical service account is absent | Run a real `--dry-run`, display tenant/project/name/account/group/role/paths, the full bounded convergence envelope, and current actions; then ask once to confirm service-account creation and completion of that same-target bootstrap. |
| Setup plans the dedicated group or permit | Require the group parent and permit resource to equal the selected project; tenant or different-project scope fails closed before mutation. |
| A same-name Codex group exists under the tenant | Ignore it as setup authority; do not discover, reuse, modify, or delete it automatically, and create or reconcile only the project-parented group. |
| User confirms the displayed bootstrap envelope | Record its digest, observed IDs, credential SHA-256, and non-secret target fields in private task state, run with `--confirm <plan_digest>`, and do not ask again while same-target partial convergence remains inside the envelope. |
| A confirmed bootstrap partially creates IAM/profile/credential state before failing | Re-run the read-only plan, require the same target and authorization envelope, then use its fresh state-bound digest automatically without another user prompt. |
| A new or matching credential cannot mint a token during confirmed bootstrap | After `ensure` stops with `credential-replacement-required`, mark the replacement attempt in private task state before invoking the separate state-bound `replace-credential` phase; if it fails or is interrupted, stop without another prompt, fresh digest, more key generation, or automatic revocation. |
| A same-name service account or group has a different ID during partial convergence | Fail closed and require new user confirmation; only an originally absent identity may transition once to the ID created by the confirmed bootstrap. |
| Tenant, project metadata, service-account name/identity, group, role, canonical path/profile, endpoint, or administrative profile differs from the confirmed envelope | Fail closed and require a new plan; never reuse the old confirmation across targets or identities. |
| User requests unattended repair before first-time setup is complete | Refuse a lease; complete and verify the confirmed setup first. |
| User reviews and confirms a repair-lease plan for a working matching account | Issue a private 12-hour workflow preauthorization record bound to the exact identity, credential fingerprint, profile, actions, and expiry. |
| A valid lease later encounters only broader credential mode or broken local profile binding | Run `repair-local` without a new prompt, then token-test and verify project access. |
| Lease is expired, altered, noncanonical, mismatched, or the credential content/identity changed | Fail closed and require diagnosis; never rotate credentials or change IAM under the lease. |
| Token mint or project access still fails after lease-authorized local repair | Stop and report that a future standalone persistent repair needs its own one-time reviewed confirmation; do not loop on prompts. |
| Runtime credential/profile health must be checked while the administrative profile is expired | Run read-only `verify`; do not require or activate the administrative profile. |
| Runtime `verify` passes but `ensure --dry-run` cannot authenticate the non-agent profile | Report `blocked-admin-auth` for exact IAM planning; do not claim the agent credential is broken and do not launch interactive login. |
| User asks the project-scoped agent to list or manage all tenant IAM groups | Explain that tenant IAM is outside the selected project scope; do not broaden permissions or switch to an administrative profile implicitly. |
| The matching service account/profile/permit already works on a repeated `ensure` | Produce a no-mutation plan and leave the working profile unchanged. |
