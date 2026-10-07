# Run-labs preparation alignment

## Delivered scope

The canonical `skills/run-labs` skill now consumes the five-script managed
preparation contract. The README's project-local installation command refreshes
`.agents/skills/run-labs`; no global skill, hook or agent configuration changes
are required.

The previous campaign copied a course into a `small` or `large` leaf directory,
which could not satisfy managed runtime identity checks and had no prepared
runtime store. Campaign copies now retain the course slug inside the existing
lab/profile namespace. A private `.course-runtime-source.json` identifies the
original prepared catalog; its receipts, installation generations and model
paths stay there. Relevant copied source inputs participate in fingerprint
validation. Source-owned adapters execute from the frozen copy, and optional
container runners mount that copy alongside the managed runtime.

Every executable recipe stage resolves through its actual launcher binding,
including prerequisites, diagnostics and optional variants. Dry-run exposes the
script and selector. Sync validates native activation and records complete
runtime proof before preflight can finish. Missing, stale or hardware-skipped
runtimes block jobs and identify the preparation command. Lab campaigns never
install dependencies, compile replacement toolchains or download models.

The environment may identify an absolute remote `prepared_root`; the default is
the remote account's `~/courses`. The Lab Guide remains the learner preparation
reference. Monitoring discovery names the explicit regular setup `monitoring`
action. Native vLLM, optional TensorRT-LLM and metadata-only audit guidance now
match their runtime owners.

New executable plans use `native-jobs/v2`. Earlier plans retain inspection and
owned cancellation, but must not resume execution under the new contract.
Original jobs, failed evidence and published results are not migrated or erased.

## Preserved behavior and source ownership

| Surface | Result |
| --- | --- |
| Public interface | Existing run/status/resume/cancel actions, selectors, workload defaults, dry-run and help remain. Help also describes the campaign-directory argument explicitly. |
| Native host policy | Existing standard frontmatter and Codex implicit-invocation metadata remain unchanged; no Claude-specific extension was introduced. |
| Authority | Prepared infrastructure, exact-target recovery, private connections and evidence replacement retain their boundaries. No new authority for dependency installation or infrastructure provisioning. |
| State and recovery | Source freezing, claims, write-ahead dispatch, uncertain-job reconciliation, owned cancellation and replacement journals remain. Old executable plans fail before source or remote access. |
| Evidence | Qualification, comparison cardinality, collection, native report checks, actual visual review and publication gates remain mandatory. |
| Resources | All 27 source payload files, including references, scripts, metadata, evals and target-owned specifications, are installed with byte and executable-mode parity. |

The source skill remains one focused workflow: its core grew from 200 to 212
logical lines. No split or progressive-disclosure exception is needed. The
prepared-runtime details live in `references/environment.md`. The learning loop
captures the reusable rule that preparation follows actual launchers and copied
source inputs, not course labels or inherited environment overrides.

## Verification

- **STATIC_PASS:** portable Agent Skills, Codex and Claude structural checks,
  standard fields and strict frontmatter. Both repository-host checks report
  only the intentional existing `docs/` folder warning.
- **STATIC_PASS:** 18 canonical trigger cases (11 positive, 7 near-miss negative)
  and 22 output-quality case definitions. Added cases cover a prepared native
  campaign, preparation-only routing, missing optional TensorRT-LLM and stale
  copied CUDA inputs. Definitions are not model eval results.
- **STATIC_PASS:** 452 focused tests across campaign control, runtime loading,
  preparation selection, collection/evidence, native jobs, container mounts and
  delivery. Tests exercise all 110 recipe selections, exact optional launchers,
  unchanged repeated sync, failed source proof, stale/missing/skipped runtimes,
  unsafe bindings, copied-source tampering, source-adapter rebasing and original
  receipt preservation. The pre-edit dry-run has no preparation inventory and
  uses v1; the aligned deterministic checks prove v2 inventory and binding.
- **STATIC_PASS:** all eight native course validators, complete HTML/archive
  build and freshness, six-course shared-helper parity, Ruff, Markdown,
  ShellCheck and whitespace checks.
- **STATIC_PASS (distribution):** real pinned `skills@1.5.26` discovery, copied
  payloads, repeat-install convergence and unrelated-home/configuration
  preservation in disposable Codex and Claude projects.
- **STATIC_PASS (installed files):** the exact README installation command,
  `npx --yes skills add ./skills/run-labs -a codex --yes`, succeeds using the
  locally resolved skills CLI 1.7.0. Source and project-local installed payloads
  match; the installer owns the updated project skill lock.
- **UNAVAILABLE (fresh runtime triggering and comparative model quality):**
  native CLIs exist, but no API credentials are available to the isolated clean
  runner. Existing account credentials were not copied into test homes. No
  runtime-trigger or model-quality pass is inferred from static or install proof.
- **NOT_RUN (live target):** no SSH campaign, GPU job, preparation installation,
  real container execution or new Grafana/Nsight evidence campaign was run.

The initial focused run found the expected installed-copy drift before the
requested install. A later source check caught stale generated guide bytes
before regeneration. Both gates pass against the final delivery. Fixture mode
and CSV newline issues found during development were corrected and rechecked.

Read-only code-review and security lanes found no remaining blocking issue in
the changed skill and direct runtime dependencies. Native command arguments,
private output ownership and experiment acceptance were preserved. These local
checks do not qualify actual distributed hardware or operational runtime access.

## Evidence and vendor boundaries

Local evidence group: `run-labs-preparation-alignment-6ckq5i8t`. It includes
`tests-verified.log`, `structure.json`, `alignment-checks.json`,
`npx-disposable.json`, `npx-install.log`, `installed-parity.json`,
`baseline-comparison.json` and final completion checks. Logs and working baselines
stay outside reusable skill source.

Current official documentation confirms local sources, agent selection and
project scope in the [skills CLI](https://github.com/vercel-labs/skills), the
[Agent Skills fields](https://agentskills.io/specification), and native
[Claude skill metadata](https://code.claude.com/docs/en/skills). Explicit source
and runtime mounts follow the
[Apptainer bind contract](https://apptainer.org/docs/user/latest/bind_paths_and_mounts.html).
The five setup commands, runtime fingerprints and campaign behavior are
repository-owned contracts established by source and deterministic tests.
No OpenAI invocation behavior was changed or inferred from the installer.

Both pre-edit working-byte baselines used owner-only temporary storage. After
comparison and final installation verification, the task owner removed only
those two exact baseline trees; sanitized hashes and review evidence remain.
No secrets, private endpoints, raw cluster logs or historical results were added
to the skill. No Git commit or external publication was performed.
