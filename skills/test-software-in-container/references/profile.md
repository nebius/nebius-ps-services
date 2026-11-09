# Offline profile and evidence contracts

The helpers require Python 3.11+ standard library only. They do not discover
releases, pull/build images, run commands, inspect artifacts or authenticate
claims. The agent and reviewed product harness supply verified observations.
Keep operational files private and free of secrets/private URLs. JSON inputs
are limited to 4 MiB; duplicate keys and non-finite numbers are rejected.

## Profile v2

All schemas use the `test-software-in-container` prefix. Old schemas and old
flags fail; there is no compatibility parser. Start with the smallest example:

| Asset | Purpose |
| --- | --- |
| `assets/source-test.json` | Default current-source integration suite with a synthetic independent inventory. |
| `assets/install.json` | Explicit installation, no historical releases needed. |
| `assets/image-test.json` | Test preinstalled software, optional named API suite. |
| `assets/package-service.json` | Install, historical upgrade and same-version patch. |
| `assets/saved-state.json` | Pure state migration without a fictional package baseline. |

All example hashes are synthetic structural fixtures, not usable runtime evidence.
Replace identities with independently observed hashes before a real campaign.

Common profile fields:

- `schema`: `test-software-in-container.profile.v2`.
- `bundle_sha256`: full execution bundle identity, covering normalized relative
  paths, bytes, file/directory modes and symlink targets of reviewed inputs. No secrets.
- `environments`: nonempty array with unique `id`, `image`, platform-specific
  `image_digest`, `platform`, `profile_sha256`, `fixture_sha256`, `setup_sha256`
  and `operations`. The environment profile hash covers baseline-affecting target
  layout and prerequisites, not the whole candidate-dependent campaign profile.
- `defaults`: optional `image` and `platform`. Omitted image resolves to
  `ubuntu:24.04` for prepared bases. Installed-target testing requires an explicitly declared image.
  Digests must already match resolved values; changing a selector requires resolving
  and freezing the profile again. Source-only fallback uses the observed engine platform, resolved by the agent before calling this helper.
- `suites`: mapping of name to project-defined category `kind`, `purpose`, `origin`,
  applicable `operations`, `test_targets` (source/installed when test applies),
  definition/input `sha256`, independently collected `inventory`, and optional
  `exemptions`. Names are not built-in commands. Every requested suite must apply
  to every generated case; explicitly N/A all-tests operations generate no cases.
  See [suite evidence](suite-evidence.md) for exact inventory/result schemas.
- `default_suites`: optional mapping with `source`, `installed`, `lifecycle` arrays.
  Unknown/incompatible defaults fail. Source needs a nonempty resolved selection.
  Absent source defaults allow discovery of one unambiguous documented default,
  otherwise a user choice. Explicitly empty source defaults require a choice and
  prohibit fallback. Installed/lifecycle empty defaults retain core checks only.
- `runtime_policy` and operation/suite `entrypoint`: optional reviewed harness
  instructions and budgets. Helpers hash these fields but never execute or enforce
  them. The agent/harness must validate them before use.

All SHA-256 values are lowercase 64-hex strings; image digests add `sha256:`.
Canonical JSON uses sorted keys, no spaces and default ASCII escaping, as in
`tsc_support.contracts.fingerprint`. Environment identity contains exactly `image`,
`image_digest`, `platform`, `profile_sha256`, `fixture_sha256`, `setup_sha256`.

### Operations and checks

Each environment's `operations` maps mode names to configuration. Declare
non-applicability as exactly `{"not_applicable": "reason"}`. All-tests records
these exclusions; explicitly requesting an excluded operation produces a blocked
case. Missing lifecycle configuration is blocked, never silently skipped.
All-tests adds standalone test cases only if a profile declares installed-target test; other
images are not assumed to contain installed software.

An executable configuration contains `checks` (check ID to kind), `exclusions`,
optional `fault_points`, and operation-specific inputs/baseline below. The planner
requires `execution` for every case. Source test requires `source` and at least
one selected suite; it does not require fabricated installed/smoke/package checks.
Other operations require `smoke`; install/installed-test/upgrade require `installed`; patch and migration require their own invariant check. Every planned
check is required. Outside source testing, declare each category `packages`, `services`, `repeat`, `no_op`
with checks or an exclusion reason. Recovery needs fault points or an exclusion.
Do not make exclusions after observing a failure.

Custom check IDs cannot reuse `execution`, `installed`, `smoke`, `patch`,
`migration`, `interruption`, `recovery`, `source` or the `suite.` prefix. Custom checks may
use installed/smoke/patch/migration or optional kinds except recovery. Named
suite checks use kind `suite` and come only from selected frozen definitions.

Each declared fault point creates a fresh trial in addition to a normal trial.
This applies to every operation, including installation. Freeze the actual
observable boundary and supported recovery procedure in the harness contract.
The planner retains path and fault declarations so the checker rejects omitted
normal/fault trials or selected predecessor/path coverage even after resealing.

### Operation inputs

| Mode | Required identity |
| --- | --- |
| install, upgrade | Global `artifact`: `version`, `source_sha256`, platform-keyed `artifacts` each containing `sha256`. |
| test, target=source | Operation `inputs`: `source_sha256`, `dependencies_sha256`, `harness_sha256`; separately frozen suite inventory. |
| test, target=installed | Operation `inputs.software_sha256`: expected installed-software descriptor/manifest. No install artifact required. |
| patch | Operation `inputs`: `source_state_sha256`, `payload_sha256`, `workflow_sha256`, `postconditions_sha256`. |
| migration | Operation `inputs`: `source_state_sha256`, `tool_sha256`, `workflow_sha256`, `postconditions_sha256`. |

Test configurations must explicitly declare `target: source|installed`. Bare helper
invocation defaults to source test and auto suites; installation is explicit. Source
and lifecycle campaigns cannot combine. All-tests internally uses installed test
targets; combining individual test/lifecycle flags needs explicit installed target.

Patch and migration also need an operation `baseline` with `image_digest`,
`state_sha256`, `environment_sha256` and `verified: true`. Its state digest must
match the source-state input. Pure data migration requires a verified source
fixture and tool, not an invented installed predecessor package. Same-version
patches are distinguished by payload/workflow/postconditions, not version labels.

### Historical upgrades

Only upgrade uses `releases`, `supported_paths` and top-level `baselines`.
Releases are authoritative oldest-to-newest records with `version`, Boolean
`stable` and platform-keyed `artifacts`. No target/duplicate labels. Never infer
ordering from version spelling. Select up to four stable predecessors by default;
explicit `from_releases` can choose exact catalog labels, including prereleases.

`supported_paths` lists ordered version arrays ending at the target artifact.
Declare supported direct pairs and sequential chains. Backward/unknown paths
are invalid; unsupported selected paths remain blocked. Sequential uses declared
multi-hop paths, otherwise a declared terminal single hop; both avoids duplicate
terminal trials. Missing history/target produces an unresolved blocked case.
When the target is missing, retain supported-path declarations and validate their
basic shape; defer target-dependent path validation until its identity is known.
Independent operations can still be planned.

`baselines` maps environment ID → predecessor version → `image_digest`,
`dependencies_sha256`, `installed_artifact_sha256`, `environment_sha256`,
`verified: true`, `target_present: false`. Environment hash must match canonical
resolved environment identity. Missing/mismatched baselines block execution.

The baseline key hashes environment identity plus baseline metadata. Source
environment keys additionally bind source/dependencies/harness inputs. It excludes
candidate and full bundle identity so verified predecessor images can be reused
across candidates. Verify actual contents; metadata equality is not cache proof.
The full profile, bundle, artifact and suite identities still bind the campaign.

## Helper use

From this skill directory, with an existing private output directory:

```bash
python3 -B scripts/plan_matrix.py --profile /private/output/profile.json \
  --upgrade --past-releases 4 --output /private/output/plan.json
python3 -B scripts/check_evidence.py /private/output/plan.json \
  /private/output/results.json --output /private/output/report.json
python3 -B scripts/check_evidence.py /private/output/plan.json \
  /private/output/results.json --format markdown \
  --output /private/output/report.md
```

Paths are placeholders. Helper outputs are new files (0600), never overwritten;
the skill's public `--output` selects a new private directory (0700). Helpers
otherwise print to stdout. `project_context.py [project] --list-suites [--profile file]`
is static discovery only; no profile returns NEEDS_PROJECT_INSPECTION. The agent
inspects project docs for that unresolved case. `source_manifest.py` hashes an
explicit reviewed input list without executing it. The agent/harness owns `--artifact`, `--engine`,
`--parallel`, `--cache`, runtime resolution, `--plan-only` and `--preflight-only`.
These are not arguments to the offline planner. Run each helper's `--help` for
its exact subset. Unknown plan-only identities stay unresolved in the agent's
plan, not fabricated to satisfy the strict helper.

Planner exit 0 means expanded, including blocked cases; exit 2 means invalid
input/output failure. Checker exits 0 for consistent supplied PASS observations,
1 for FAIL/INCOMPLETE and 2 for invalid/mismatched evidence. No exit authenticates
execution. Plans are bounded to 10,000 cases and 4 MiB; split larger campaigns.

## Results and reports v2

Results schema is `test-software-in-container.results.v2`; `plan_sha256` must
match the frozen plan. Cases may be absent (NOT_RUN). Each result supplies:

- Exact `id`, `baseline_key`, `platform`, `image_digest` and `inputs_sha256`
  (canonical hash of the planned case's `inputs`).
- `execution_mode`: `native`, `emulated` or `not-run`, based on observed execution.
- Finite nonnegative `elapsed_seconds`, at most one year; optional `phase_seconds`
  for preflight, preparation, collection, suite, operation, verification, recovery and cleanup.
- `checks`: planned `id`, `status` (PASS/FAIL/INCOMPLETE/BLOCKED/NOT_RUN), `evidence` and
  structured `observation`. Absent checks become NOT_RUN.

PASS/FAIL require nonempty contained relative evidence paths. Store sanitized
records separately; helpers validate path syntax, not file contents or truth.
PASS observations have these additional requirements. Suite receipts are checked
for PASS, FAIL and INCOMPLETE; a supplied outcome cannot contradict their contents:

| Kind | Required fields |
| --- | --- |
| execution | `entrypoint_observed: true`, `intervened: false` |
| installed | `probe_origin: "installed-artifact"` |
| smoke | `assertions_verified: true` |
| source | `probe_origin: "frozen-source"`, matching `source_sha256`, `restricted_access_verified: true` |
| patch | `delta_verified: true`, `invariants_preserved: true` |
| migration | `target_state_verified: true`, `invariants_preserved: true` |
| no_op | Integer `mutations: 0` |
| services | `real_manager: true`, `stable_window_observed: true` |
| interruption | `boundary_observed: true` |
| recovery | `product_owned: true`, `intervened: false` |
| suite | `receipt` with exact plan/case/inventory identities and derived outcome; see suite-evidence.md |

Assertions need independent evidence. Record meaningful package, state, service
and behavioral observations for all checks, including kinds without mechanical
PASS field requirements. Synthetic receipts are only parser tests.

The checker rejects unknown/duplicate cases/checks, identity mismatches,
contradictory observations, PASS for blocked plans and executed claims in not-run
cases. Reports preserve operation/suite coverage, exclusions and phase timing.
Failed checks yield FAIL; otherwise missing/blocked checks yield INCOMPLETE;
all-N/A does not pass. Seals detect drift and internal inconsistency, not forgery.
