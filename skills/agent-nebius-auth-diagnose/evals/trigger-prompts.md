# Diagnose Trigger Evals

| Prompt or state | Expected behavior |
| --- | --- |
| Nebius command has no leading selector and no explicit task project | Let the hook resolve sanitized `profile current` plus that profile's configured `parent-id`; do not print or persist the project ID. |
| Default profile has no valid configured `parent-id` | Deny and ask for an explicit task project or a corrected default profile; do not choose from credentials, cwd, or memory. |
| Ambient `NEBIUS_PROFILE` conflicts with the config-owned default | Ignore the ambient profile during fallback discovery. |
| Known task project, then missing-selector hook denial | Reuse the selected project and retry the original outer payload once with exactly one leading selector; do not rediscover, verify, or invoke setup. |
| Selected project A, then a later user turn explicitly selects project B | Replace A with B for later Nebius-sensitive payloads; do not carry the stale A selector forward. |
| Selected project A, then non-explicit task evidence conflicts between A and B | Discard the carried selection and ask; do not choose from profiles, filenames, cwd, or memory. |
| `nebius --help`, `nebius --version`, or a Nebius executable path probe | Treat it as Nebius-sensitive and put the selector at byte zero of the outer Bash payload. |
| Proposed compound payload mixes a local-only probe and a Nebius command | Split it into separate Bash calls; leave the local probe unprefixed and put one selector at byte zero of the Nebius call. |
| Compound payload contains only Nebius-sensitive segments | Put one selector before the entire outer payload, not before each segment. |
| Nebius command is under `env`, `timeout`, `bash -c`, a guard, pipeline, substitution, or nested shell | Put one selector before the outer wrapper or construct; do not place it inside the nested command. |
| Selector is non-leading, nested, invalid, or duplicated | Reconstruct the payload with exactly one valid selector as the first raw shell token and retry once. |
| Local-only `git`, `rg`, or unrelated help command follows project discovery | Leave it unprefixed; do not inject Nebius credential context into unrelated commands. |
| Selected project credential is missing | Report it and tell the user to invoke setup explicitly; do not create it. |
| Credential/profile auth fails persistently | Report evidence and the exact explicit setup invocation; do not run a setup dry-run implicitly. |
| Explicit profile conflicts with the selector | Remove the explicit profile and retry through selector-derived auth. |
| Command assigns or unsets managed auth | Remove the command-local mutation and retry without setup. |
| Command prints an access token | Replace it with the intended operation or exact redirected verification. |
| Raw-token child is required | Use the protected `exec-token` helper so the token exists only in the child. |
| Raw-token helper child has no leading selector | Deny; automatic default-profile discovery does not authorize a raw-token child. |
| Idempotent API adapter maps real auth failure to `77` | Use one protected `retry-idempotent` refresh and retry. |
| Admin profile is expired but runtime may work | Run read-only `verify`; do not activate or require admin auth. |
| Runtime works but later explicit IAM setup lacks admin auth | Report `blocked-admin-auth`, not agent credential failure. |
| Project works but tenant quota allowance is denied | Report missing tenant read authorization and require explicit setup for tenant `viewer`. |
| Valid repair lease covers local mode/profile drift | Route to `repair-local`; do not touch IAM or rotate credentials. |
| Current evidence contains two project IDs | Ask the user; do not choose from profiles, filenames, or memory. |
| Valid selector and runtime auth already work | Do not trigger unnecessary setup. |
| Agent is asked to enumerate or manage tenant IAM | Refuse; diagnosis is limited to auth and quota-read checks. |
