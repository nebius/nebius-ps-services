# Troubleshoot Skill

`troubleshoot` is a causal-investigation and repair skill for difficult software
and infrastructure failures. It is implicitly invokable, but its activation
does not expand the authority of the surrounding task.

## Core Model

```text
INTAKE -> DISCOVERY -> BASELINE -> MODEL -> HYPOTHESES -> EXPERIMENTS
       -> LOCALIZED -> PROVEN -> REMEDIATED -> VERIFIED -> REPORTED
```

The workflow preserves evidence before functional edits, maintains competing
hypotheses, chooses discriminating experiments, localizes the earliest
divergence, and requires a causal mechanism before claiming root cause.
Discovery inventories technologies, versions, deployment model, active
configuration, components, dependencies, ports, protocols, authentication, and
control and data flows, then compares the observed system with matching
official vendor architecture before diagnostics begin.

Every consequential command tests a stated hypothesis with expected supporting
and falsifying evidence, a timeout or output bound, and a defined next branch.
The workflow prohibits indefinite tails, arbitrary sleeps, passive terminal
waiting, and broad unfiltered log dumps.

## Evidence-Gated Completion

Troubleshooting records a component verification matrix, normalized incident
timeline, and layered log-coverage ledger. Component, application, container or
orchestrator, systemd, OS, kernel, network, storage, GPU, and hardware evidence
is examined when relevant or explicitly recorded as unavailable, unsafe, or not
applicable.

The internal evidence freezes its included and excluded components and
dependencies, exercised control and data paths, and incident-window start and
end. The component matrix explicitly records DNS or service-name resolution
and restart history. The log ledger contains exactly one ordered record for component,
application or job, container or orchestrator, service manager, OS and kernel,
network and firewall, storage, and GPU or hardware. Primary local log reading is
baseline evidence gathering; only remote Grafana queries use observability
admission gates.

The user-visible report is a concise projection: outcome, root cause and fix,
verification and unverified scope, then an exact owner/action/done-when next
step. `FAIL` and `UNKNOWN` remain internal evidence states; they do not stop the
agent while another safe bounded result can change the decision. Detailed
matrices and ledgers appear only in an optional evidence appendix.

`DIAGNOSED-FIXED` means the cause and owner are proven, the owner-correct repair
is applied, and the reproducer, focused regression, and source or affected
boundary pass for the fixed scope. Installation or live replay may remain
explicitly unverified. `VERIFIED_FIXED` remains complete end-to-end proof;
`DIAGNOSED_NOT_FIXED` means no owner-correct repair was applied.

## Design Handoff

`troubleshoot` does not invoke `design` for ordinary system modeling,
hypothesis work, or a localized invariant-restoring repair. After the cause is
proven, it routes a durable remediation through `design` before implementation
only when the change affects architecture topology, component or service
responsibilities or boundaries, a public interface, data ownership or
lifecycle, a migration, or a cross-component workflow. Implementation size,
algorithmic complexity, concurrency difficulty, or a large rewrite inside one
existing private boundary does not make a repair design-scale.

The handoff supplies the proven causal chain, violated invariant, requirements,
constraints, non-goals, fixed technologies, and regression oracle. `design`
owns the solution design and `/plan` handoff; `troubleshoot` retains causal
proof, post-implementation verification, and final reporting. Inside Agentic
SDLC, the causal handoff goes first to `sdlc-classify-failure`, which records
the failure class, retry state, and `next_recommended_skill`; the coordinator
then routes to the recorded design, plan, or other owning phase.

## Agentic SDLC Diagnostic Mode

`troubleshoot` is absent from the happy path. The classifier selects it only
for an ambiguous, persistent, intermittent, or cross-boundary failure. The
diagnostic session stays in the registered private integration worktree,
preserves the exact failed commit and criteria, and may use only reversible,
uncommitted instrumentation that is removed before handoff.

It does not commit a product fix or call design, planning, or implementation.
It returns one structured `diagnosis-v1` to `sdlc-classify-failure`, including
the earliest divergence, violated invariant, causal chain, counterfactual,
alternatives eliminated, confidence, regression oracle, evidence references,
and the bounded owner handoff. Missing evidence and competing hypotheses stop;
failure to find an implementation bug does not authorize redesign.

Each autonomous blocker tranche defaults to five remediation attempts and 120
active minutes. Users can set a session profile up to 10/180 with optional
flags, for example
`$troubleshoot --attempt-limit=10 --time-limit-minutes=180 <problem>`. A bare
invocation keeps the saved profile; one flag changes only that field; explicit
5/120 resets the defaults. The UserPromptSubmit hook binds selected values to a
private session authorization so free-text prose cannot change marker limits.
A task-specific earlier workflow stop stays in prose and leaves active status
and a null stop trigger.
Before every retry, the agent must acquire new logs, a new stack trace, new code
inspection evidence, or an equivalent observation and derive a genuinely new
falsifiable hypothesis. Rewording the same hypothesis or reusing evidence is
not sufficient. If the retry gate cannot be satisfied, the agent does not patch
again; it returns to discovery, modeling, or safe evidence collection. It
reports early only when decisive evidence has no safe alternative, authority or
safety requires user action, the user asks to stop, or the budget is exhausted.
It reports each non-terminal failure, then
stops all tools and returns the complete report at the first numeric limit. A
new user instruction starts fresh state; it never reopens the exhausted tranche.
A causally independent blocker starts with an empty ledger; its next completed
remediation and verification become attempt 1. Planned or in-progress work
stays in prose and never becomes a partial attempt object. Prior attempts,
elapsed active time, exhaustion state, and stop trigger do not carry over.
Every counted attempt records the exact marker blocker key. A missing, mixed,
or carried binding makes the marker invalid and enters repair instead of
exhausting the new blocker. Marker validation and repair consume no attempt.
Pending authorization feedback retains the precise marker or transition error
and gives complete canonical repair guidance. Fresh-state guidance calls its
source the prior terminal marker because it can follow either resolved or
exhausted state. Invalid active-resize markers
must restore their exact pre-resize non-profile state; deleted resize markers
fail closed and require the exact prior marker or a fresh user-authorized
session rather than a budget reset.

Every explicit `$troubleshoot` invocation also creates a terminal report duty
in session-private `troubleshoot-report-obligation.json`, even when no
remediation marker exists. Success, blocking, tool or coordination error,
ordinary stop, unresolved work, and exhaustion all use the same concise report.
An ordinary incomplete or malformed report records `advisory_incomplete` and
continues; it does not request another turn, deny tools, or emit a generated
fallback. Sensitive output receives one bounded redaction path. Exhaustion
remains strict and preserves marker-derived blocker and attempt evidence in the
same concise envelope. A host process that dies before Stop can only be reported
after same-session resume.

For incidents, stabilization and diagnosis remain separate. A restart,
rollback, failover, retry, cache clear, or scale change can mitigate impact but
does not prove why the failure occurred.

## Live Product Validation

Live verification separates causal ownership, target recovery state, and proof
lineage. Each trial freezes its declared product workflow; changing that
declaration starts a new lineage and cannot clean earlier evidence. Codex may
operate the declared workflow and perform authorized stabilization or recovery,
but an out-of-band mutation that performs, bypasses, or pre-satisfies a
product-owned step marks the affected evidence intervened. Nominally read-only
observation is also classified by effect when it can alter criterion-relevant
state or execution. After owner-correct repair, verification resumes from a
declared or independently proven known-good product-supported checkpoint before
the earliest product divergence or contamination, proves earlier writers are
quiescent, observes the product perform the relevant transition, and checks
authoritative postconditions independently. A healthy target or successful
no-op after manual pre-satisfaction is mitigation, not proof.

## Observability Evidence

Runtime observability is a gated experiment, not a default first step. After
building the system model and hypotheses, `troubleshoot` uses
`$nebius-grafana-query` in evidence-provider mode only when scoped telemetry can
change the investigation decision and non-Grafana evidence shows that one
matching metric, log, trace, platform, or change signal is expected to exist for
the deployed target. A symptom or generic datasource possibility is not enough.
Deterministic local failures, already conclusive evidence, unproven signal fit,
missing authority, unresolved deployed selectors, and unbounded time windows
make the path ineligible with zero Grafana calls.

Only after those gates pass does the provider perform one lazy datasource
readiness check per investigation. Troubleshooting starts with one cheapest
query for the single matching signal; the cumulative six-query fast and
four-query deep allowances are ceilings, not targets. Each additional query
must answer a newly recorded decision-changing question, and a missing signal
does not trigger broad telemetry-family fan-out. Total and per-stage remaining
budgets round-trip with every response, including verification.
`troubleshoot` interprets the returned structured facts and retains
causal-proof ownership. Correlation never becomes root cause by itself. Passive
production telemetry remains read-only and does not authorize remediation.

When Grafana connectivity is unavailable, the investigation does not install,
repair, or repeatedly check it. Optional telemetry is skipped; decisive missing
runtime evidence produces `BLOCKED_MISSING_EVIDENCE` with the exact data gap.

## Repair Boundary

- Code and tests may be repaired when the surrounding request asks to solve the
  problem and does not prohibit changes.
- Confirmed non-production systems may receive bounded, reversible live changes
  with an identified target, dry run where available, and rollback path.
- Production and unconfirmed targets remain read-only until the user authorizes
  the exact live action.
- Destructive, irreversible, credential, IAM, data, public-exposure, deletion,
  material-cost, and material-availability changes always require action-specific
  approval.

## Progressive Disclosure

- `SKILL.md` owns the universal state machine, authority model, conditional
  design-routing gate, anti-thrashing gates, completion criteria, and output
  contract.
- `references/investigation-protocol.md` owns detailed evidence and proof rules.
- `references/software-failure-playbooks.md` owns code and shell failure classes.
- `references/infrastructure-failure-playbooks.md` owns installed and distributed
  stack failure classes.
- Technology-specific procedures live in `references/slurm.md`,
  `references/soperator.md`, `references/kubernetes.md`,
  `references/nebius.md`, `references/linux.md`, `references/network.md`,
  `references/storage.md`, `references/gpu.md`, and
  `references/code-debugging.md` and are loaded only after stack discovery.
- `references/live-product-validation.md` owns product-versus-intervention
  boundaries, evidence lineage, owner-correct repair, clean replay, and
  claim-scope rules for live product testing.
- `references/technique-selection.md` maps causal questions to diagnostics.
- `references/verification-and-reporting.md` owns closure and reporting.
- `references/observability-evidence.md` owns the runtime-evidence eligibility,
  scope, staged-query, and unavailable/partial contract.
- `references/remediation-budget.md` owns attempt identity, limits, durable
  marker state, continuation tranches, and exhaustion reporting.
- `assets/hooks/` contains the optional `UserPromptSubmit`, `PreToolUse`, and
  `Stop` guard bundle.
- `scripts/` contains deterministic evidence helpers and their tests.
- `evals/` contains trigger and process evaluation cases.

## Helper Commands

```bash
python3 troubleshoot/scripts/collect_evidence.py \
  --root . \
  --output /tmp/evidence.json

python3 troubleshoot/scripts/repeat_command.py \
  --runs 50 --timeout 120 --out /tmp/runs.json -- command arg

python3 troubleshoot/scripts/compare_evidence.py \
  /tmp/good.json /tmp/bad.json \
  --ignore /collected_at \
  --max-differences 1000 \
  --out /tmp/diff.json
```

The collector and comparator are read-only toward inspected state. The repeated
runner executes the exact supplied argv without a shell and inherits that
command's side effects, so use only a safe, authorized, idempotent reproducer.
Do not pass secrets in argv. Redaction is best-effort: treat output artifacts as
sensitive and review them before sharing or committing. Runner exit `0` means
measurement completed; inspect `pass_count` and `pass_rate` for command results.
Exit `2` means a helper or launch error and `130` means interruption.
Comparison paths use JSON Pointer, including the empty string for the document
root. The comparator counts all differences but retains only the requested
bounded number and reports whether the list was truncated.

## Validation

```bash
python3 \
  /Users/example/.codex/skills/.system/skill-creator/scripts/quick_validate.py \
  troubleshoot
PYTHONDONTWRITEBYTECODE=1 \
  python3 align-skill/scripts/validate-skill-structure.py \
  --profile stateful-workflow troubleshoot
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s troubleshoot/scripts \
  -p 'test_*.py'
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s troubleshoot/assets/hooks/tests \
  -p 'test_*.py'
```

Static metadata validation establishes trigger readiness, not observed runtime
activation. Use fresh Codex sessions for positive and negative trigger checks.

## Important Files

- `SKILL.md`: runtime contract.
- `agents/openai.yaml`: UI metadata and implicit invocation policy.
- `references/`: detailed investigation guidance.
- `assets/hooks/`: optional local remediation-budget enforcement.
- `scripts/`: deterministic evidence utilities and tests.
- `evals/`: trigger and process evaluation material.
