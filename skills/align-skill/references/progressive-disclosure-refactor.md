# Progressive Disclosure Refactor

Read this reference only when a target `SKILL.md` exceeds the 500-line soft
budget or semantic review shows that its always-loaded instructions are
overloaded. Do not load it for an ordinary concise target.

The goal is lower always-loaded context without losing behavior. Do not
summarize every section mechanically or optimize for a line number alone.

## Establish The Baseline

Before editing:

1. Capture the target's current working bytes in an owner-only, task-owned
   temporary directory outside the repository. Use that snapshot instead of
   `HEAD` when the worktree is dirty.
2. Record the validator-compatible logical line count using
   `len(skill_text.splitlines())` or the validator's reported value. Do not use
   `wc -l`, which undercounts a final line without a newline.
3. Record token cost only when a compatible tokenizer or runner exposes it.
   Use the same model, tokenizer, and method for before-and-after comparison.
   Otherwise record `UNAVAILABLE`; do not install a tokenizer only for this
   measurement, substitute a character-based estimate, or send private target
   content to an external tokenizer or runner without authorization.
4. Create a preservation ledger for safety constraints, decision-changing
   rationale, non-obvious gotchas, preconditions, failure and retry behavior,
   stop conditions, validation requirements, and output contracts.

## Classify Instruction Blocks

Classify coherent instruction blocks by behavior, not merely by headings:

| Class | Destination or action |
| --- | --- |
| Always-needed core | Keep concise routing, scope, workflow, guardrails, validation, and output instructions in `SKILL.md`. |
| Conditional knowledge | Move provider, domain, troubleshooting, or workflow variants into focused one-level `references/` files. |
| Deterministic repeated work | Prefer a tested `scripts/` helper when exact repeatable execution is safer and cheaper than prose. |
| Reusable artifact | Move templates, schemas, starter content, and long examples into `assets/`. |
| Behavior-neutral content | Remove duplicated or passive prose only when its absence cannot change a decision, constraint, route, validation, safety outcome, or output. |
| Independent job | Recommend a separate skill only when the block has its own trigger and outcome and can operate without the parent workflow. |

Record each block's class, destination, and preservation-ledger entries before
moving or deleting it. Mixed blocks must be split semantically so always-needed
guardrails do not become conditional by accident.

## Refactor By Ownership

- Keep the shortest sufficient directive in `SKILL.md` for every required
  action and safety boundary.
- For each extracted reference, add an exact directive such as
  `Read references/aws-deployment.md when the target provider is AWS.` Avoid
  vague folder-level routing.
- Keep references one level below `SKILL.md`. Do not make one reference the
  required router for another reference.
- Keep one normative owner for each rule. Remove duplicated copies after the
  owner and required-read route are established.
- Preserve concise rationale beside a directive when it changes whether, when,
  or how the directive is applied. Move deeper background only when the
  routing condition guarantees it will be loaded before that decision.
- Do not replace precise failure, retry, or stop behavior with a shorter but
  less actionable summary.

## Decide Whether To Split A Skill

Length alone never justifies another skill. Recommend a separate skill only
when all of these are true:

- it has a distinct user intent or trigger boundary;
- it produces an independently meaningful outcome;
- it does not require the parent workflow's always-loaded state or sequence;
- its separation improves routing without duplicating shared safety rules.

If a sibling skill already exists and is writable in the selected scope, align
the boundary between them. If a new skill is required, route initial naming and
scaffolding to `skill-creator`; do not turn `align-skill` into a second
scaffolder.

## Allow A Justified Exception

The target may remain above 500 lines when semantic classification shows that
the retained content is genuinely always needed or safety-critical. Record:

- the before-and-after line counts;
- compatible before-and-after token cost, or `UNAVAILABLE` and why;
- which retained blocks keep the file above budget;
- why loading those blocks conditionally would weaken behavior or safety;
- which alternatives were considered and rejected;
- the non-regression evidence used for the retained workflow.

Reject generic explanations such as "the skill is complex." The warning stays
informational, but an unsupported exception blocks a full alignment claim.

## Evaluate The Result

Run the same realistic prompts and inputs against the captured baseline and the
refactored target in clean contexts when a compatible runner is available.
Check that:

- behavior and output quality remain equal or improve;
- safety constraints, rationale-dependent decisions, and rare gotchas remain
  effective;
- conditional references are loaded only under their stated conditions;
- independently triggered jobs are routed to the correct owner;
- always-loaded line or token cost decreases when extraction was appropriate;
- a justified over-budget target does not regress merely to reduce size.

Report line counts deterministically. Report token and timing comparisons only
when the runner exposes compatible values; otherwise use `UNAVAILABLE`. Keep
static, runtime, and quality evidence in their existing separate lanes.

After comparison and rollback needs end, remove the exact resolved task-owned
temporary tree with the repository's approved scoped cleanup method. Never
target the system temporary root, a broad parent, an unresolved variable, or a
path not proven to belong to the current task. If policy requires retention,
report the exact retention reason, owner-only permissions, cleanup owner, and
deadline instead of silently leaving the snapshot behind.

## Stop Conditions

Stop and report the unresolved decision instead of editing when the proposed
move would hide an always-needed guardrail, the destination owner is unclear,
the split requires an unauthorized new skill, or no useful baseline exists for
a safety-sensitive material rewrite.
