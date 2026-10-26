# Nebius Capacity Planning

Design-first Nebius compute, storage and network sizing, including Kubernetes
resource allocations, operational headroom, optional quota comparison, explicitly authorized increase requests and
verifiable costs.

## Usage

Invoke `$nebius-capacity-planning` with a project/design and assessment request.
Claude uses `/nebius-capacity-planning` or `/skills:nebius-capacity-planning`.
Ordinary matching requests may select the skill implicitly.

```text
Use $nebius-capacity-planning to size this project's Dev/Test environment.
Include per-container requests/limits, node pools, storage and networking.

Use $nebius-capacity-planning to compare this design with my tenant's quotas.
Use my selected human profile and keep all cloud inspection read-only.
```

`-h` and `--help` return help without further reads or actions. Use `--tenant`,
`--project` and `--profile` for explicit scope and existing identity; select
`--quota-check` for read-only comparison or `--quota-request` for authorized
human-account increase requests. `--quota-multiplier N` defaults to 1 and
multiplies the incremental scenario envelope before adding current usage. The skill asks for an unspecified environment; CPU limits are
workload-specific, and prices are included only when verifiable.

## Output and boundaries

The normal output is `docs/nebius-capacity-planning.md` inside the selected
project, beginning with an Executive Summary and linked TOC. The summary gives
the selected profile, per-pool exact presets/counts/boot disks, storage and
network allocations, external prerequisites, surge/growth and unresolved gates.
Split allocation tables retain a common component/workload/container key. Read-only and
Plan Mode return the assessment in chat. Existing identifiable human notes are
preserved. Infrastructure, credentials and manifests are unchanged; request
mode submits approval requests, never edits allowances or approves them.

Design-only assessment needs no cloud credentials, SDK, sibling skill or
runtime hooks. Optional live inspection requires the separately available
`nebius` skill, authorized existing authentication and its declared runtime.
Single-skill npx installation does not supply that sibling or its setup.
Missing access produces a qualified report, never a fabricated quota pass.

The calculator requires Python 3.10+ and uses only the standard library.
It accepts normalized effective Pod budgets; it does not parse manifests,
simulate placement, determine effective quotas or contact cloud services.

## Files and validation

- [SKILL.md](SKILL.md): public workflow, help and safety boundary.
- [Sizing methodology](references/sizing-methodology.md): evidence and accounting.
- [Kubernetes sizing](references/kubernetes-sizing.md): resource semantics and scenarios.
- [Nebius assessment](references/nebius-assessment.md): optional inspection and region decisions.
- [Calculator contract](references/calculator.md): normalized inputs, outputs and limits.
- [Report template](assets/report-template.md): complete project deliverable.
- [Quota workflow](references/quota-workflow.md): human identity, reserve math and request verification.
- [Trigger cases](evals/trigger-prompts.csv): assessment/check/request positives and adjacent near misses.
- [Quality cases](evals/evals.json): synthetic assessments and quota-mode cases and observable assertions.

From this skill directory:

```bash
python3 -B scripts/test_capacity.py
python3 -B scripts/test_quota.py
python3 -B scripts/calculate_capacity.py < assets/example-capacity.json
```

From the parent skills directory, with its declared validator dependencies:

```bash
python3 -B align-skill/scripts/validate-skill-structure.py --policy agentskills --agent core nebius-capacity-planning
python3 -B align-skill/scripts/validate-skill-structure.py --policy repository --agent codex --require-evals nebius-capacity-planning
python3 -B align-skill/scripts/validate-skill-structure.py --policy repository --agent claude --require-evals nebius-capacity-planning
python3 -B global-context-management/scripts/check-plugin-manifests.py --catalogs
python3 -B align-skill/scripts/check-npx-compatibility.py nebius-capacity-planning
```

The last check downloads the pinned installer into disposable storage; it does
not install into real homes. Static/tests and package-copy parity do not prove
native model triggering or generated report quality. Use fresh native sessions
for trigger checks and matched no-skill baseline runs for this new skill's
quality cases; record those evidence states separately.

## Validation evidence

Source validation on 2026-10-20:

| Lane | Evidence |
| --- | --- |
| Static and deterministic | `STATIC_PASS`: 27 arithmetic tests, portable/Codex/Claude structure, canonical trigger definitions, catalogs/specs and scoped lint. |
| Distribution | Disposable pinned npx installation passed discovery, copied-file parity, repeat installation and isolation for Codex and Claude; no real homes changed. |
| Output quality | `QUALITY_PASS` for two explicit-skill, read-only subagent assessments compared with independent no-skill baselines. All 13 numerical/coverage/safety assertions passed. |
| Native triggering | `UNAVAILABLE`: isolated native runner has no usable API-key authentication; the attempted batch was interrupted without a completed trigger result. No implicit-trigger claim. |
| Report persistence and cloud | `NOT_RUN`: forward cases returned chat reports; no live tenant, workload, report refresh or performance qualification. |

The no-skill comparison exercised sidecar/init stage accounting, per-node
placement reasoning, failure/rollout envelopes, independent storage copies,
quota shortfalls and incomplete prices. Both arms reached the core numerical
answers; the skill arm additionally supplied the structured component budgets,
exact publicly documented candidate and explicit qualification ledgers. This
is scoped forward-test evidence, not proof for arbitrary designs or native
host activation. Timing/token comparisons were unavailable.

Code-review and security lanes found no remaining blockers. The initial plugin
ordering finding was fixed and the catalog proof rerun. Follow-up alignment
repaired a calculator defect that hid proven overloads when another demand
term was unknown. Four regressions cover partial totals, unbounded limits and
inactive unknown terms; complete unknown totals and margins remain null. New source guidance
captures reusable accounting and evidence boundaries; fixtures contain only
synthetic values. The existing skills CI job runs calculator tests and lint.

## Report-contract refinement (2026-10-25)

The opening summary now enumerates exact provisioning quantities and unresolved
gates. Split resource tables retain container identity. Storage guidance now
requires backup cadence, replacement overlap, complete recovery chains and
filesystem attachment preparation or preattached restore space.

- Static: 27 calculator regressions, portable/Codex/Claude structure, 13 trigger
  definitions, plugin catalogs, paired specs, Markdown/Ruff and whitespace checks
  passed. The old template failed the provisioning-field and container-key
  contract checks; the revised template passed.
- Distribution: disposable npx 1.5.26 discovery, copy parity, repeat installation
  and isolation passed for both hosts. No real installed home was changed.
- Output quality: one offline synthetic forward assessment passed all four
  new provisioning-summary assertions. This was a bounded read-only helper run,
  not a fresh native-trigger test or a matched new no-skill comparison.
- Report delivery: a design-only project report was saved and checked for
  component coverage, unique container joins, links and scenario arithmetic.
  Existing-report refresh with user notes, live quotas, physical capacity and
  workload/storage performance remain unverified.
- Nested code/security review found only two minor documentation issues,
  corrected before final validation. No cloud or authentication changes occurred.

## Quota mode examples

```text
$nebius-capacity-planning ./project Dev/Test --tenant <tenant-id> --project <project-id> --profile <human-profile> --quota-check --quota-multiplier 2
$nebius-capacity-planning ./project Dev/Test --tenant <tenant-id> --project <project-id> --profile <human-profile> --quota-request --quota-multiplier 2
```

The second invocation explicitly authorizes approval-request submission for
verified shortfalls. Pending requests do not increase usable quota. Two times
means free headroom for two complete selected envelopes before deployment;
permanent reserve and physical availability are not guaranteed. Missing optional
`nebius` support still permits offline planning but blocks live submissions.

## Quota-mode validation (2026-10-25)

- `STATIC_PASS`: 27 capacity and 22 quota-planner regressions, strict portable/
  Codex/Claude structure, 17 trigger definitions, catalog/spec and scoped lint.
  Exact JSON decimal multiplication is tested across the stdin interface.
- Distribution: pinned disposable npx installation passed discovery, both-host
  copy parity, repeat convergence and isolation; real homes remain unchanged.
- `QUALITY_PASS`: two offline check/request rehearsal outputs passed eight
  assertions against the captured working-byte baseline. Both arms calculated
  the CPU deficit; the updated arm additionally supported the public modes,
  preserved larger sufficient limits and specified duplicate reconciliation.
  This is bounded helper output evidence, not native host activation.
- Native `RUNTIME_PASS` is not claimed: isolated native authentication was
  unavailable. Real request submission is a separate evidence lane and is not
  proven by these fixtures.
- Code/security review found decimal-rounding, missing CI test wiring and test
  formatting defects; all three were repaired and focused checks rerun.

### Source alignment follow-up (2026-10-25)

The quota planner now enforces its 1 MiB input boundary in UTF-8 bytes and
returns structured exit-2 errors for invalid encoding, extreme numeric
exponents and excessive nesting. Five added stdin regressions include valid
non-ASCII input. The captured working baseline reproduced three defects;
each fails safely after repair, and the existing nesting behavior is preserved.

The report template now exposes reserve arithmetic and request receipts in the
summary and quota section. It separates pending/approved records from effective
allowances and removes a blanket no-mutation statement that contradicted the
authorized request mode. A matched offline report rehearsal passed all five
request-mode assertions; both arms preserved the arithmetic and submission
guards, while the baseline identified the template contradiction. This is
`QUALITY_PASS` for bounded output comparison, not native activation.

Final code/security review found no remaining findings. All 49 deterministic
tests, strict structure, catalogs and scoped lint passed. Disposable npx checks
passed discovery, both-host copy parity, repeat installation and isolation.
Native triggering remains `UNAVAILABLE` because the isolated runner lacks
authentication. No installed skills or cloud resources changed in this pass.
