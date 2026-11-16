# Human-console quota requests

Read tenant and project allowances with the selected agent identity. Compare
active measured remaining capacity with peak planned demand, including disks,
network objects, IPs, backend storage and temporary overlap. Missing or unknown
measurements are blockers. Quota headroom does not guarantee physical capacity.

The documented request surface is the human web console:
[Nebius quotas](https://docs.nebius.com/overview/quotas). Do not substitute project
allowance creation for a tenant increase or depend on a private quota client.

For authorized `--quota-mode request`:

1. Use Playwright. Verify a signed-in human identity, the exact selected tenant,
   and admin access. Browser availability alone does not prove any of these.
   If login is needed, hand the authentication step to the user; never solicit
   passwords, MFA codes or session cookies.
2. Freeze exact scope, existing limits and required absolute limits. Inspect
   pending requests in the console/support center. Reuse an equivalent pending
   request and record its receipt rather than submit another.
3. Persist `journal.quota_intent` before clicking submit. On success record the
   receipt and requested status. Lost confirmation remains ambiguous: inspect
   the request list before any retry. The local journal alone is not dedup proof.
4. Poll effective allowance readback for at most ten minutes without creating
   workloads. Return `awaiting_quota` for later resume when approval is pending.
   Requested, approved and effective are distinct outcomes.
5. After approval, refresh demand and usage before provisioning. Preserve quota
   changes during cleanup; they are tenant policy, not disposable resources.

The agent retains project Admin plus tenant Viewer and never receives tenant
Admin for quota requests. A missing human session or unproven administrator role
blocks submission; it does not justify identity fallback or IAM expansion.
