# Nebius Capacity Planning

Design-first Nebius compute, storage and network sizing, including Kubernetes
resource allocations, operational headroom, optional read-only quota comparison
and verifiable costs.

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

`-h` and `--help` return help without further reads or actions. No additional
public flags. The skill asks for an unspecified environment; CPU limits are
workload-specific, and prices are included only when verifiable.

## Output and boundaries

The normal output is `docs/nebius-capacity-planning.md` inside the selected
project, beginning with an Executive Summary and linked TOC. Read-only and
Plan Mode return the assessment in chat. Existing identifiable human notes are
preserved. No infrastructure, credentials, quotas or manifests are changed.

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
- [Trigger cases](evals/trigger-prompts.csv): six positive and six near-miss cases.
- [Quality cases](evals/evals.json): two synthetic assessments and observable assertions.

From this skill directory:

```bash
python3 -B scripts/test_capacity.py
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
