# Architecture decisions and research

Research checked on 2026-11-10. The implementation adopts the useful separation
in the brainstorm and adjusts it to the repository's existing skills and the
organization's identity constraints. Documentation support is not proof that a
particular model/runtime combination works.

| Idea | Decision and implementation |
| --- | --- |
| Actions orchestrator | Adopt. Dispatch, bounded continuation and scheduled recovery are sufficient; no external workflow engine. |
| GitHub-hosted runners | Adopt. All jobs use hosted Ubuntu. Nebius resources are disposable test targets, never GitHub runners. |
| Codex CLI | Resolve the latest/exact repository selector once to a version and npm integrity; qualify the resulting image before explicit digest promotion. Isolate Codex in a non-root container with no write credentials. |
| Direct Token Factory Responses | Retain the direct protocol with a restricted forwarding relay. The relay isolates the long-lived key; it does not translate protocols. |
| Provider/model configuration | Model candidates are environment variables; the credential is an environment secret. The approved endpoint is pinned in relay code. |
| Kimi K2.7 Code default | Reject as a serverless default: the vendor removed it on November 9, 2026. Kimi K3 is a candidate requiring repository-specific qualification. |
| Dedicated GitHub App | Excluded by organization constraint. Use job-scoped GITHUB_TOKEN and explicit dispatch; no PAT fallback. |
| Provider-neutral skills | Adopt. Reviewed Markdown/resources are bundled with an explicit dependency manifest and digest. Personal hook payloads are excluded. |
| Review plus deterministic checks | Adopt. Findings are evidence, never authorization. Exact source, CI, cleanup, waiver revision and GitHub protections gate effects. |
| Independent merge policy | Adopt with the existing merge-pr broker as the sole merge API owner. A shared PR lease is required even by the old effect helper. |
| Automatic repairs/conflicts | Same-repository repairs require independent verification and focused credentialless checks before non-force publication. Localized conflicts cap the agent delta at five files/200 non-marker lines. Larger conflicts use one verified replacement with inherited authority; the original fork is never written. |
| PR lifetime live waiver | Authorized human dispatch records immutable repository/PR identity in private CAS state. It survives commits/reopenings until revoked; it cannot override failed/intervened trials or known defects. |
| PR-specific live scenarios | Generate minimal typed scenarios; only independently qualified trusted capabilities may execute cloud actions. Unsupported capabilities leave the PR open. |
| Purpose and closure | Assess complete contribution, accepted baseline and human rationale first. Two independent no-benefit assessments may authorize closure after fresh proof checks; uncertain changes remain open. Objective redundancy proofs remain supported. |
| Multiple laptop/runner publishers | Replace with clients of the same hosted authorities. There is no parallel local merge/tag publication path in enrolled repositories. |
| Releases by project folder | Use a reviewed package-input registry, including shared cxcli catalog/module dependencies. Runtime variables enable releases, not redefine their scope. |
| Next version | Immutable per-change semver fragments in the original PR; highest bump since the last verified release wins. No extra preparation PR. |
| GitHub merge queue | Not used by this built-in-token broker. An enforced queue or unmet protection leaves the PR open; no bypass actor. |
| Runner migration/ARC sizing | Out of scope. No Nebius VM runner, Kubernetes runner, ARC, GPU runner or migration machinery is installed. |
| Benchmarking and rollout | Keep disabled until actual provider, isolation, CAS, live, merge and release trials pass. Local tests and a built image do not satisfy that gate. |

## Request and fork boundaries

Repository Actions controls separate automation domains:
`AI_AUTOMATION_PR_REVIEW=disabled|report|repair|merge` selects PR authority,
`AI_AUTOMATION_RELEASE_PUBLISH=false|true` controls release publishing, and
`AI_AUTOMATION_MERGE_FORK_CONTRIBUTION=false|true` limits fork eligibility.
Defaults remain `disabled`, `false`, and `false`. Use repository values without
environment shadows. YAML fallback values do not override configured values;
after deployment, changing a control applies to subsequent runs without a code
change. The renamed controls have no compatibility aliases or state migration.

`requests.py` owns schema-v2 admission and terminal state. Automatic intake handles
new PRs in the current epoch; ready/update/reopen events and recovery resume only
active records. Draft deadlines start at readiness. A new manual dispatch may
create a successor after terminal state and reconciled effects; a rerun cannot.
Review intent, deadline, repair budget and capability ceiling survive continuation.

`stages.py` binds cached assessments and actual validation receipts to source,
request, accepted context, policy, runtime and skill identities. Purpose precedes
repair/CI/live; review/alignment precede CI; live follows CI. No-benefit closure
also freezes human explanation/discussion and refuses conflicting human reviews.
Closure compensation requires attributable bot closure and rechecks state events;
uncertainty never authorizes reopening a human-closed PR.

The repository Actions variable `AI_AUTOMATION_MERGE_FORK_CONTRIBUTION` defaults
false. True adds eligibility, never source-write authority or a skipped gate.
Candidate contracts and CI v2 receipts retain both repository identities and
head ref, plus exact head/base/tree and request ID. The protected broker consumes
review-v2 pointers and independently checks the active request ceiling/current
restriction before approval and merge. `source:fork` and the single bot comment
are informational. New restrictions apply on the next job evaluation; immediate
stop also requires cancellation of queued/running jobs.

A trusted controller artifact admits each dispatched CI workflow. CI receives
five candidate inputs (PR/head/base/request/source pointer), alongside five
post-merge inputs. It fetches the base repository's PR head ref, verifies SHA/tree,
and executes without environment secrets. CI discovery in enrolled repositories
does not publish status for the unadmitted open backlog.

Rollout remains disabled. Refresh the activation epoch, qualify the pinned runtime
image and focused check profiles, reconcile legacy writers/effects, and explicitly
retire old operation state before starting fresh manual requests. Empty validation
and live capability registries represent unavailable qualification, not success.

## Hosted execution and qualification

`execution.py` separates prepare/evaluate/supervise/accept/live/finalize. Workers
receive a bounded snapshot and write local evidence; only trusted ingestion can
persist it or advance a request. `scheduler.py` reserves exactly two evaluators
with CAS, counts starting/running/unknown jobs, and requires terminal GitHub proof
before release. `live_slot.py` retains a separate reservation across lease expiry
until the original writer stops and owned cleanup is independently observed.
Recovery revalidates that reservation under the global and PR leases before
invoking a driver; an earlier terminal-run observation cannot authorize cleanup
of a successor trial.
The five-minute recovery schedule is a recovery opportunity, not an availability
or latency guarantee from GitHub.

`rollout.py` adds a default-deny durable gate. Qualification permits bind exact
repository/PR/head/base/tree, capabilities, expiry and generation. Head/base changes
require renewed admission even after an automation repair. Release preparation
and publication use separate permits because preparing a release creates a tag.
Global switches are ceilings and remain off until explicitly changed.

`qualification.py` runs the shared assessment with a GET-only GitHub adapter,
read-only workflow permissions and local evidence. A state-only guard supplies
the retained runtime receipt to a separate inference job. Shadow reports cannot
be ingested as production worker evidence. Local tests, hosted shadow, native
cloud trials and actual permitted effects are separate proof layers; the monorepo
needs no synthetic test repository or manufactured release.

`images.py` and `image_cli.py` verify OCI blobs, the amd64 configuration, non-root
UID, SBOM/provenance bindings, actual Codex/tool behavior and clean anonymous pull.
Registry index digests identify runtime evidence; full digest references launch
containers. Docker engine image IDs are not portable evidence identities.
Successful publication receipts are retained in private state and verified against
the completed producer before first use. Selection rechecks runtime/skill closure
and model identities. See [hosted rollout](hosted-rollout.md) for deployment gaps.

`baseline.py` adopts an existing cxcli release only after an exact human-reviewed
inspection and release-owner CAS. The record distinguishes maintainer adoption
from original build attestation; it does not invent historical hashes or modify
tags/assets. The shared release verifier checks retained tag and asset identities.

## Evidence that changed the brainstorm

GitHub suppresses most workflow events caused by GITHUB_TOKEN writes, while
explicit workflow/repository dispatch remains available. The controller therefore
requests candidate CI and downstream processing explicitly. A default-ref run's
`head_sha` is the workflow revision; a separate receipt binds the candidate that
was actually checked out and tested.
[GitHub workflow triggering](https://docs.github.com/en/actions/how-tos/writing-workflows/choosing-when-your-workflow-runs/triggering-a-workflow).

Reusable workflows receive explicit inputs and permissions from their caller.
The release owner reserves a tag, calls service builders with read-only contents
permissions and no environment secrets, then publishes from another job that
never executes the candidate.
[Reusable workflows](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows).

GitHub secret names cannot use hyphens or the reserved GITHUB_ prefix. This is why
the key slot is `AI_AUTOMATION_TF_API_KEY`; a fake value is not installed.
[Secrets reference](https://docs.github.com/en/actions/reference/security/secrets).

Token Factory documents a Responses endpoint with streaming and tool fields.
That supports trying direct integration, but does not prove Codex's complete
streaming/tool/schema loop for a selected model. The official November notice
replaces the retired Kimi K2.7 Code serverless model with Kimi K3.
[Responses reference](https://docs.tokenfactory.nebius.com/api-reference/inference/create-a-response),
[November deprecations](https://docs.tokenfactory.nebius.com/november-2026-deprecation-notice).
The Nebius AI Cloud connector did not cover this separate Token Factory site;
these pages were retrieved directly through the official-site research fallback.

Nebius regular Object Storage supports conditional object operations. Filesystem
buckets have only best-effort conditional behavior and are unsuitable for these
leases. ETags are opaque CAS tokens, not content hashes; stored evidence uses
separate SHA-256 hashes. Real contention and interruption tests are still required
against the selected bucket and SDK.
[S3 compatibility](https://docs.nebius.com/object-storage/interfaces/s3-api-compatibility).

## Proof and recovery boundaries

Each PR operation retains its original deadline, repair count and controller-owned
heads. GitHub run ID/attempt and source tree are independent fields. Candidate CI
receipts are reconstructed from trusted producers if a callback loses lease
contention. A new run or attempt invalidates stale CI admission.

The merge journal distinguishes a proven pre-submission rejection from an
accepted or uncertain request. Only proven non-submission permits a bounded fresh
attempt. Unknown effects require authoritative reconciliation. A new review does
not erase a pending effect. Waiver mutation and merge submission use the same PR
lease.

Branch-update acceptance is also unconfirmed: the journal remains pending until
the observed head has the expected original head and base as parents. Status
reporting cannot retire a request with unresolved effects. After publishing a
comment, reporting rereads the candidate and readiness before applying a terminal
outcome; changed candidates remain eligible for recovery within the same budget.
Expiration ends the active request but never clears an unresolved effect or lets
a successor bypass reconciliation.

A release reservation spans version allocation through publication even though
short leases are renewed per active stage. Other source revisions cannot replace
a pending reservation. Requests without a tag assertion are retained before
dispatch and retried after the reservation completes. Explicit tag assertions
enter durable recovery only after validation against the allocated version;
when another source is pending, retry the assertion after it completes.
Original asset hashes are persisted before upload. Draft
publication becomes public only after downloaded assets match; existing files are
never silently overwritten. Completed releases can be reverified from their
retained original manifest after temporary build artifacts expire.

The live driver boundary is implemented, but the shipped registry contains no
qualified driver. This is an explicit deployment blocker for non-waived PRs, not
a claim of successful Nebius product validation. Provider credentials, scoped
state/cloud credentials and real hosted lifecycle qualification remain required.

## PR lifecycle completion

[The lifecycle contract](pr-lifecycle.md) connects typed usefulness, applicability,
diagnosis, feature inventory and issue assessments to existing deterministic
owners. `adoption.py` transfers execution once to a verified repository-owned
replacement; `lifecycle.py` reconciles status and terminal obligations under the
canonical PR lock. `issue_resolution.py` verifies linked issues independently.
The merge broker gains no issue-write authority. Local tests remain distinct
from hosted and live qualification.
