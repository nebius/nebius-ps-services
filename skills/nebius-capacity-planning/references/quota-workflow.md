# Quota checking and request submission

Read for quota flags or an explicit request to submit sizing-based increases.
Use the optional `nebius` skill for scope, authentication and inspection. Keep
public sources independent of internal clients and proprietary automation.

## Freeze the sizing basis

1. Resolve the user-selected tenant, region selector and existing profile through
   the offline scope helper. A profile default is not scope authority. Omitted
   region means all; do not enumerate projects or infer a project selection.
2. Define incremental quota demand per exact provider key, region and unit.
   Aggregate all shared consumers once. Include the selected concurrent peak,
   replacement/upgrade and growth scenarios; do not add mutually exclusive
   scenarios. Deduct reused resources only when already included in usage.
3. State the multiplier and its meaning. Default 1; 2 means free quota for two
   copies of the chosen incremental envelope before provisioning, leaving one
   envelope after one deployment. If the user wants two copies left afterward,
   use 3 only when that interpretation is explicitly settled. It multiplies
   demand, not current usage or the existing limit. Do not multiply growth twice.
4. Use `scripts/plan_quota.py` with normalized integer provider units. For each
   scope: required free quota = ceil(multiplier × envelope); minimum total limit
   = current usage + required free quota, rounded up to the provider increment.
   Request only a positive shortfall; never reduce a larger current limit.
   The helper performs arithmetic only, not scope, freshness or IAM validation.
5. Inspect all pages under a bounded budget; retain collection timestamp and
   completeness. Missing limits, unknown usage/state, inaccessible scopes or
   absent tenant limits stay unresolved. Do not infer project limits or
   inheritance. Do not invent zero usage, unlimited capacity, quota names or RAM quota. When a
   verified protobuf JSON serializer omits scalar zero, normalize it to zero
   only with known active/NOT_USED semantics; unknown states remain unresolved.
6. Report tenant quota fit only; project limits and deployment eligibility remain
   unchecked. Keep verified dimensions alongside unresolved rows. Regions are
   alternative placements unless the design explicitly distributes demand; do
   not multiply the deployment by the number of candidate regions. Global
   quotas are shared across simultaneous consumers, not copied per region.

A quota is a permission ceiling, not a physical reservation. Other tenant
workloads and automatic quota reductions can consume/remove spare headroom.
An “always available” reserve needs recurring checks or separate governance;
this invocation neither schedules monitoring nor guarantees permanent reserve.

## Submit only with explicit authority

For omitted region or `--region all`, prepare per-region request values only.
State that nothing was submitted and that submission needs one concrete region.
An excluded region or unresolved design envelope cannot become a request target.

- Require the user's direct quota-request authorization and exact tenant/region,
  plan, multiplier and requested total limits. Present the concrete request
  ledger before execution; do not re-ask when the scope is already authorized.
- Verify an existing human IAM session for the selected tenant. A profile name
  alone is insufficient: bind the selected existing configuration as well as
  profile, explicitly disable any client-default impersonation, and verify the
  same human on the request interface. Use provider identity metadata and validate account
  kind and tenant membership/access. Ignore ambient agent credentials when
  human identity was requested. Never impersonate a service account, switch
  identity, log in, parse credential stores, print tokens or repair IAM here.
- Prefer a current official request interface. The documented console quota
  change workflow creates requests for review; quota allowance create/update
  is not a substitute. Verify the current console navigation (Limits/Quotas,
  Requests or support center labels may differ). Use available authorized
  browser tooling if no supported request API is exposed. Never copy internal
  request clients into the public skill or silently install a client.
- Every cloud-access script must use the official Nebius Python SDK and the
  existing owner runtime. Non-SDK local request adapters, raw HTTP clients and
  CLI subprocess fallbacks are not permitted. If no supported SDK approval-request
  interface exists, use the authorized console workflow through host-permitted
  browser tools or return manual request values marked unsubmitted. Never use
  SDK quota-allowance create/update as a substitute. Preserve human identity
  and disable client-default impersonation; profile names alone prove neither.
- Refresh relevant allowance and request records immediately before submission.
  List existing requests with complete pagination. Match exact tenant,
  quota key, region, total limit and status. Reuse an adequate pending request;
  report a smaller pending request for reconciliation rather than duplicate or
  edit it silently. A failed/denied request is evidence, not permission to retry
  blindly. Approved requests still require effective allowance readback.
- Freeze only verified deficient keys and their justified minimum targets.
  Provider suggestions cannot add scopes/keys or lower a target below the
  required envelope. Do not accept materially higher limits without authority.
- Submit once with automatic write retries disabled. Retain safe request and
  operation IDs; independently read each request to verify parent, key, region,
  requested total, state and creator where exposed. If creator is unavailable,
  retain pre-submit human identity evidence and state the attribution limit.
- On timeout or ambiguous success, reconcile list/get evidence before retrying.
  If a request cannot be identified unambiguously, stop submission and report
  reconciliation required. Never claim failed submission solely from timeout.
- Do not approve requests or update allowances directly. Submission, approval
  and usable quota are separate states. After approval, re-read allowances
  before claiming sufficient quota. Preserve safe receipts in the private task
  state or selected project report; exclude tokens, raw dumps and private URLs.

## Evidence and fallback

Report the selected scope/identity type, envelope and multiplier, timestamp,
per-key limit/usage/headroom/required-free/minimum-total/increase, completeness,
request IDs/status and effective quota after any approved change. Keep
sufficient, insufficient, unresolved and not inspected distinct. If the sibling,
user authentication, request UI/API or permission is unavailable, finish the
calculation and report the exact blocker plus manual request values. No silent
credential fallback or fabricated successful submission.

## Offline planner input

`python3 -B scripts/plan_quota.py < normalized-input.json` reads JSON and emits
JSON. Input must be UTF-8 and at most 1 MiB in bytes. Malformed encoding,
unsupported numeric literals and excessive nesting return a structured error
with exit 2, without a traceback. Python 3.10+ standard library only; no cloud
or credential access. Example:

```json
{
  "multiplier": "2",
  "collection_complete": true,
  "dimensions": [{
    "scope": "tenant-example", "region": "example-region",
    "quota_name": "compute.instance.non-gpu.vcpu", "unit": "count",
    "envelope": 176, "limit": 400, "usage": 80,
    "state": "STATE_ACTIVE", "usage_state": "USAGE_STATE_USED",
    "increment": 1
  }]
}
```

This yields 352 required free, 432 minimum total, 32 shortfall and a 432 request
candidate. `increment` defaults to 1; use 1073741824 for a byte quota when the
request UI accepts whole GiB. Unknown limits/usage are JSON null. Rows are
already aggregated by scope/key/region; duplicates fail. Unknown/partial rows
never become request candidates. Exit 0 means arithmetic completed, not quota
sufficiency or submission; exit 2 means invalid input. Output retains explicit
units and does not include an overall deployment-pass claim.

## Official evidence

- [Quota workflow and automatic reduction](https://docs.nebius.com/overview/quotas)
- [Quota allowance listing](https://docs.nebius.com/cli/reference/quotas/quota-allowance/list)
- [Quota states](https://docs.nebius.com/terraform-provider/reference/resources/quotas_v1_quota_allowance)

Refresh these sources and the selected interface during each live run.
