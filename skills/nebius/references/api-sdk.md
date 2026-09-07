# API and SDK integration

Reviewed 2026-09-07 against the published `nebius==0.6.7` package and official
[Python SDK](https://github.com/nebius/pysdk) and
[request guide](https://docs.nebius.com/sdk/python/send-request).

## Choose the interface

| Need | Interface and boundary |
| --- | --- |
| Typed Python infrastructure automation | Generated versioned SDK clients; maintained examples here |
| Manual discovery | Nebius CLI, explicit profile/parent and command `--help` |
| HTTP integrations | [REST API](https://docs.nebius.com/rest-api), verify method in current OpenAPI spec |
| Other languages | Official [Go SDK](https://github.com/nebius/gosdk) and [JavaScript SDK](https://github.com/nebius/js-sdk); no maintained language copies here |
| Object payload transfer | S3 data API via boto3; Nebius SDK for full bucket management |
| Declarative lifecycle | Terraform/Helm owner, not an SDK side channel |

## Authentication and scope

All bundled inspectors require explicit resource scope. Resolve authority using
`project-selection.md` before passing an ID; credentials do not establish
resource ownership. `assets/sdk/runtime.py` accepts either `credentials_file`
or SDK CLI `Config` selection. An explicit profile/config uses `no_env=True`;
ambient token/profile/endpoint values cannot replace it. With neither explicit
selector, the documented SDK Config environment semantics apply. `parent_id`
remains explicit and `no_parent_id=True` prevents implicit resource parenting.
There is no shell token fallback, custom auth-file parsing, global environment
mutation or login repair. Avoid supplying a short-lived static token to a long
workflow; select a renewable source. Auth errors must be sanitized.

For standalone reuse, add the copied skill's `assets` directory to the caller's
Python import path, then import `sdk.runtime`, `storage.buckets`, etc. Do not
import the monorepo service packages. Python >=3.10 is required.

## Shared lifecycle

`rpc(method, request)` waits for a request, with bounded authentication and
request/retry budgets. `collect_pages` returns all pages or raises: repeated
continuation tokens, duplicate/missing identities, malformed pages, deadlines
and bounds cannot become complete inventories. Defaults: 120 seconds, 1000
pages, 100000 items. A completed list is not an atomic snapshot across services.

`submit(method, request)` disables write retries (`retries=1`), retains the
operation object, calls `sync_wait`, and checks `done()` and `successful()`.
Request `.wait()` alone only receives the operation. `.id` is the operation ID;
`.resource_id` is the resource ID. The default operation budget is 300 seconds;
callers choose service-appropriate explicit budgets. An uncertain write raises
`CloudError` with safe IDs and `outcome="reconcile-required"`. If no ID was
received, inspect exact authorized name/parent/request evidence; do not create
again or adopt a collision automatically.

`create_and_verify` combines submission with identity/name and service-specific
readiness checks. It receives an explicit readiness predicate and separate
operation/readiness budgets. Validate both budgets and the readiness/readback
callables before submitting a create. The bucket helper likewise validates its
readiness budget before cloud access. Dependency preflight remains the caller's job.
Use resource-version preconditions where that API supports them; re-read on a
conflict. Never invent a universal idempotency header or blanket retry policy.

`owned_sdk` closes only the client it creates, using a bounded `sync_close`.
Inside async code use native `async with SDK(...)`, `await request` and
`await operation.wait(...)` with equivalent deadlines; do not invoke these
synchronous helpers or recreate the SDK for every call.

## Inspectors and output

Existing VPC/quota names and selectors remain documented in `README.md`.
All inspectors emit one JSON object: `scope`, `data`, `complete`, `errors`.
Quota reports additionally declare `mode: raw|effective`, controlled only by
`--raw`; empty scopes no longer switch output schema. Exit 0 means complete
collection, 1 incomplete/failed, and 2 invalid arguments. A quota inspector does
not compare a requested workload or approve a deployment. Human output is capped
at 20 top-level records; JSON contains all successfully collected records.
Failed collection is identified by component/code without raw SDK errors.

## Validation and failure boundaries

Offline mocks test semantics; the pinned real-SDK check constructs and
serializes messages without authenticating. Neither proves cloud availability.
Do not log whole requests, tokens, credentials, secret-bearing specs/status, or
tracebacks containing provider details. Keep CLI/API versions in evidence and
recheck generated schemas before upgrading the validation pin.
