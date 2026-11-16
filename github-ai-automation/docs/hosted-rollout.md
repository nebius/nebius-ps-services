# GitHub-hosted rollout

The source implements the hosted coordinator, evaluator separation, image
pipeline, read-only rehearsal, scoped rollout permits and historical baseline
adoption. It remains disabled. Native live drivers and focused validation profiles
are not qualified or registered yet. This document distinguishes implementation
from deployment and observed production behavior.

## Deployment boundary

The current checkout contains source and prepared [deployment templates](../deployment/README.md).
Only source CI is installed. Ordinary protected merge and service release
workflows remain active; no controller environment is required for publication.
Installing the enrollment, mandatory-controller policy and effect workflows is a
separate authorized deployment. Keep execution controls off during setup and
qualification. The steps below apply to that later deployment.

## Credential placement

All five environments must allow only the repository default branch. Keep
non-secret controls at repository level, without environment overrides.

| Environment | Credentials available to its jobs |
| --- | --- |
| `github-ai-automation` | Private state bucket, endpoint and state access-key pair |
| `github-ai-evaluation` | `AI_AUTOMATION_TF_API_KEY` only |
| `github-ai-live-test` | State access plus the dedicated TEST tenant/project/region and scoped Nebius credentials |
| `github-ai-image-publish` | Job-scoped `GITHUB_TOKEN` with `packages: write`; no inference or cloud credentials |
| `github-ai-state-qualification` | Separate qualification bucket access-key pair, endpoint, bucket name and the production bucket name for a denied-read check |

The state variables use the `AI_AUTOMATION_STATE_` prefix shown in the README.
Never give candidate code the production state key or a signing credential that
can renew its deployment bearer. The live adapter must give the candidate only
its bounded trial authority and backend; its ownership ledger stays with the
trusted supervisor. Gateway runtime identity is separate from deployment identity.
Target identifiers, private endpoints and credential values belong in protected
settings and runtime files, never this public repository.

Create separate private regular, versioned Object Storage buckets for production
state and destructive qualification. Per-trial product backends must be isolated
from both controller state and other trials. The current S3 client supports CAS;
real contention, denial, expiry and interruption checks are still required against
the chosen provider bucket. Storage setup and IAM provisioning are deployment work,
not something this PR workflow silently performs.

**GitHub AI state qualification** is manual and has two jobs. A trusted guard
reads the production bucket's harmless, pre-established
`github-ai-automation/qualification-sentinel.json` object. Setup owns that fixture:
its body is `{"schema":"state-isolation-sentinel/v1","repository_id":<numeric repository ID>}`.
Create it conditionally without overwriting another object, and retain it for
future checks. It is not rollout state and grants no automation authority.
The guard publishes a same-run observation without bucket names or credentials.
The qualification job verifies that observation, then uses only its separate key
to test denial of reading that existing sentinel. This proves that specific
read boundary; cross-bucket write/delete denial and provider key expiry are
separate, untested claims.

The trial checks actual conditional create/update, concurrent CAS, stale fencing,
read-after-accepted-write with injected response loss, anonymous read denial and
version history. It claims a unique invocation marker before writes and deletes
only versions beneath that owned trial prefix, with the owner marker last.
Pre-existing trial data is preserved. Cancellation can leave owned versions;
reconcile the exact recorded trial only after its original writer is terminal.
Do not rerun a prior trial ID or delete an arbitrary prefix. A fresh Actions
attempt has a distinct trial ID. Local provider trials and hosted trials remain
separate evidence.

## Runtime image

1. As part of separately authorized deployment, install the reviewed image
   workflow template through the ordinary maintainer process. Keep PR,
   release, fork and image publication controls off.
2. Set the repository ID and selected Token Factory model names. Set
   `AI_AUTOMATION_CODEX_VERSION=latest`, or one exact npm version. A manual
   **GitHub AI image** run builds and qualifies without publishing while
   `AI_AUTOMATION_IMAGE_PUBLISH=false`.
3. Once publication is authorized, set `AI_AUTOMATION_IMAGE_PUBLISH=true` and
   dispatch the image workflow. Its public package path is
   `ghcr.io/<owner>/<repository>/github-ai-automation`. Configure that package
   public in GitHub Packages; repository visibility alone does not make it public.
   If the first upload creates a private package, anonymous verification fails;
   make the package public and start a fresh workflow dispatch. Failed-only reruns
   change the attempt number and cannot reuse artifacts from successful prior
   jobs; the image pipeline requires one coherent attempt.
4. Require build, real Token Factory qualification, publication, anonymous pull
   and receipt retention to succeed. Set `AI_AUTOMATION_AGENT_IMAGE_REF` to the
   emitted `@sha256:...` reference and
   `AI_AUTOMATION_IMAGE_QUALIFICATION_RUN_ID` to its successful run ID.
5. Retain the prior qualified digest and producer record for rollback. Changing
   the Codex selector never promotes a new runtime automatically. Current runtime,
   skill and model compatibility checks still apply when selecting an older image.

Automatic polling runs hourly only when image publication is enabled. It resolves
the selector to an exact version and SHA-512 npm integrity, compares the reviewed
build inputs and models, and skips unchanged work. Successful no-op runs carry
the original receipt so frequent polling does not lose its build history.
Trusted source changes also trigger detection. Every PR subsequently pulls the
selected image; it does not rebuild or install a new Codex version.

The publisher copies the qualified OCI bytes, including SBOM and provenance,
without rebuilding. Publication has package-write authority in a separate job.
Anonymous verification runs without registry credentials. Private durable state
retains the receipt independently of Actions artifact expiry; first use also
requires the complete producer run to have succeeded.

## Qualification in this monorepo

A dedicated test repository would be a second repository used to manufacture
test PRs and releases. This design does not require one. It uses these distinct
layers in the existing repository:

| Layer | What it establishes | What it does not establish |
| --- | --- | --- |
| Offline tests and temporary Git fixtures | State machines, rejection cases, retries and bounded repairs | Real GitHub permissions or cloud behavior |
| **GitHub AI qualification**, with an existing PR number | Real hosted image/model assessment using the shared evaluator | Comments, branch writes, approval, merge or release |
| Native Nebius trials | Actual product behavior, failure recovery and independent cleanup | GitHub effects or another untested product mode |
| Exact permitted real canaries | Actual effects on a useful PR or legitimate release | General unattended qualification across every project |

The shadow workflow has GET-only GitHub transport and read-only permissions. It
cannot enroll production work, publish comments or turn its result into merge
admission. `AI_AUTOMATION_PR_REVIEW=report` is different: report mode may comment
and label. Use the shadow workflow for the first hosted assessment.

The monorepo still needs explicit project/path impact. Focused validation profiles
bind path patterns to reviewed argument arrays and digest-pinned images. Release
projects retain their own versions and shared-input dependencies. Changes to
automation controls, shared skills or workflow authority need ordinary maintainer
review; the automation must not approve its own deployment PR.

## Native live-test gate

The driver registry intentionally remains empty until the actual driver and
environment pass provisioning, product assertions, negative controls, interruption
and independently verified cleanup. An unavailable driver leaves non-waived PRs
open. Do not register a mocked driver as qualified.

The planned first envelope is two evaluators and one serialized live trial, with
up to five VMs, 16 vCPU, 64 GiB RAM, 380 GiB SSD and five public IPs including
replacement/residual resources. Verify actual quota and capacity before creating
anything. SSH must use the runner's exact /32 and authenticated host keys; VPN
peers must be explicit. The supplied storage project is not implicitly the VM-test
project. Target authority must be established before provisioning.

The cxcli source now has an explicit `preconfigured-static` CPU VM authentication
path. See its [contract](../../services/nebius-cxcli/docs/preconfigured-static-auth.md).
It has local regression coverage, not provider qualification. VPNGW ordinary
Static can use its existing explicit bearer path for an initial narrow trial.
BGP and HA still need an explicit external runtime-credential enrollment contract
through approval, resume, replacement and cleanup. Managed `reuse` is insufficient
because that owner can still reconcile IAM. No BGP/HA qualification is claimed.

Provider bearer expiry and the three-hour operation deadline are different
controls. Nebius IAM tokens have the documented provider lifetime; a local deadline
does not revoke a copied bearer. Trial access-key expiry must be set at the provider
when issuing Object Storage credentials. Candidate processes must not receive
refresh/signing authority. Cleanup must remain possible after candidate expiry.
See [IAM token lifetime](https://docs.nebius.com/rest-api/authentication#get-an-access-token)
and [access-key expiry](https://docs.nebius.com/iam/service-accounts/access-keys#python-sdk-2).

Freeze source/build, scenario, adapter and expected product behavior before each
trial. Fixture setup, native product operations and independent observation are
separate. An outside mutation that performs or bypasses a product-owned step
invalidates affected proof. Keep the failed/intervened evidence and start a new
trial from a proven checkpoint; do not relabel recovery as successful validation.

## Scoped real canaries

**GitHub AI rollout control** inspects or CAS-replaces private rollout state.
Missing state denies production work. Initial state uses `phase=qualification`;
only a human with maintain/admin permission may configure it.

GitHub reports the built-in Maintain role as legacy `permission=write` with
`role_name=maintain`. The permission adapter preserves that distinction for
rollout control and baseline adoption. Ordinary Write and custom roles with only
the legacy Write permission do not acquire this authority.
[GitHub permission response](https://docs.github.com/en/rest/collaborators/collaborators#get-repository-permissions-for-a-user).

Each PR permit contains the exact candidate identity, allowed capabilities and an
expiry of at most 24 hours. Generation changes revoke older admission. Head/base
drift, including an agent repair, needs a new permit. Global mode/fork switches
remain additional ceilings: a permit cannot override disabled mode. The existing
three-hour request deadline and two-repair budget remain unchanged.

Choose an actual useful eligible PR. Permit only its intended stages, observe the
effects independently and retain the record. Never manufacture a trivial PR just
to produce a green merge trial. Leave fork merge off until an actual fork path has
been qualified. Existing backlog PRs require explicit manual admission.

Release canaries use two permits: exact project/source/version for preparation
(which creates the tag), then exact built manifest digest for publication, tied
back to the preparation generation. Use the next legitimate release. After all
required evidence passes, explicitly move to `phase=general` with empty permit
lists, establish the intake epoch, and enable the desired global ceilings.

## Existing cxcli baseline

Use **GitHub AI baseline** to inspect the existing cxcli `0.1.9` release
with its original producer run. Review the returned inspection digest and adopt
that exact digest with the current baseline revision. Adoption uses the release
owner's lock and refuses an active conflicting reservation.

This records `maintainer_adoption`, the original producer/manifest/artifact,
annotated tag and current asset identities and hashes. The older producer did not
record original asset hashes; adoption explicitly retains that proof limitation.
It creates no tag or release, replaces no asset, and does not pretend that current
hashes were historical build attestation. Later release decisions use the same
canonical completed-release verifier.

## Stop and recovery

Disable new admission/publication, revoke canary permits, cancel queued/running
writers, then reconcile their exact run/attempt and accepted effects. Cancellation
requests and expired leases do not prove termination. Unknown writers continue
to occupy their evaluator/live reservations. Before invoking cleanup, recovery
rechecks the observed live reservation under both the global and PR leases;
a changed reservation stops recovery before any driver action.
Preserve credentials and authority
needed for owned cleanup until absence is independently observed. Recovery checks
every five minutes and on relevant callbacks/manual dispatch; GitHub scheduling
does not provide a strict recovery SLA.
