# Diagnose Trigger Evals

| Prompt or state | Expected behavior |
| --- | --- |
| Hook denies a command with no leading selector | Discover the project, correct the command, and retry without setup. |
| Selected project credential is missing | Report it and tell the user to invoke setup explicitly; do not create it. |
| Credential/profile auth fails persistently | Report evidence and the exact explicit setup invocation; do not run a setup dry-run implicitly. |
| Explicit profile conflicts with the selector | Remove the explicit profile and retry through selector-derived auth. |
| Command assigns or unsets managed auth | Remove the command-local mutation and retry without setup. |
| Command prints an access token | Replace it with the intended operation or exact redirected verification. |
| Raw-token child is required | Use the protected `exec-token` helper so the token exists only in the child. |
| Idempotent API adapter maps real auth failure to `77` | Use one protected `retry-idempotent` refresh and retry. |
| Admin profile is expired but runtime may work | Run read-only `verify`; do not activate or require admin auth. |
| Runtime works but later explicit IAM setup lacks admin auth | Report `blocked-admin-auth`, not agent credential failure. |
| Project works but tenant quota allowance is denied | Report missing tenant read authorization and require explicit setup for tenant `viewer`. |
| Valid repair lease covers local mode/profile drift | Route to `repair-local`; do not touch IAM or rotate credentials. |
| Current evidence contains two project IDs | Ask the user; do not choose from profiles, filenames, or memory. |
| Valid selector and runtime auth already work | Do not trigger unnecessary setup. |
| Agent is asked to enumerate or manage tenant IAM | Refuse; diagnosis is limited to auth and quota-read checks. |
