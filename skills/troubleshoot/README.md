# Troubleshoot Skill

`troubleshoot` is a causal-investigation and repair skill for difficult software
and infrastructure failures. It is implicitly invokable, but its activation
does not expand the authority of the surrounding task.

## Core Model

```text
INTAKE -> BASELINE -> MODEL -> HYPOTHESES -> EXPERIMENTS
       -> LOCALIZED -> PROVEN -> REMEDIATED -> VERIFIED -> REPORTED
```

The workflow preserves evidence before functional edits, maintains competing
hypotheses, chooses discriminating experiments, localizes the earliest
divergence, and requires a causal mechanism before claiming root cause.

Autonomous remediation is bounded by default to three distinct failed repairs
or 60 active minutes for the same blocker. The agent reports failures 1 and 2,
then stops all tools and returns a complete troubleshooting report at the first
reached limit. Only an explicit user instruction starts another tranche.

For incidents, stabilization and diagnosis remain separate. A restart,
rollback, failover, retry, cache clear, or scale change can mitigate impact but
does not prove why the failure occurred.

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

- `SKILL.md` owns the universal state machine, authority model, anti-thrashing
  gates, completion criteria, and output contract.
- `references/investigation-protocol.md` owns detailed evidence and proof rules.
- `references/software-failure-playbooks.md` owns code and shell failure classes.
- `references/infrastructure-failure-playbooks.md` owns installed and distributed
  stack failure classes.
- `references/technique-selection.md` maps causal questions to diagnostics.
- `references/verification-and-reporting.md` owns closure and reporting.
- `references/remediation-budget.md` owns attempt identity, limits, durable
  marker state, continuation tranches, and exhaustion reporting.
- `assets/hooks/` contains the optional `PreToolUse` and `Stop` guard bundle.
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
