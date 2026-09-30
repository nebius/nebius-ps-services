# Supplemental Process Cases

These cases preserve detailed workflow and output-quality expectations.
`trigger-prompts.csv` is the sole canonical trigger authority; this document
does not define skill routing.

Use the canonical row ranges below when reviewing `design` routing behavior.
Static validation does not prove runtime activation.

## Process Assertions

- Positive rows must inspect the relevant system, compare viable options,
  integrate bounded research and specialist decisions, and produce a concrete
  plan. Explicit design-and-implement requests may continue after that plan
  only when the host permits implementation.
- Read the selected project's design documents before proposing a solution,
  then trace its affected code and consumers, including unused prototypes.
  Missing documents are disclosed; sibling designs do not fill the gap.
- Code always wins as the current baseline when design prose conflicts with
  implementation. Report both claims, source references and proposal impact;
  do not alter code merely to satisfy stale prose.
- Resolve unintended proposal conflicts before handoff. Describe intentional
  requested changes and prerequisite refactors separately. Unknown usage must
  not qualify for the unused-greenfield exception.
- Default planning performs no implementation or direct project-design writes.
  `maintain-project-specs` owns canonical publication, including requests to
  save a design; missing owner availability leaves a pending handoff.
- Explicit implementation authorization is reused without routine reconfirmation,
  but never overrides actual host Plan Mode, permissions or external-action
  boundaries. Quoted implementation wording grants no authority.
- Approved application, AI subsystem, or AI stack layers must not be reopened;
  only genuinely undecided layers route through `app-stack`,
  `ai-agent-design`, or `ai-stack`.
- AI subsystem work follows `design` to `ai-agent-design` to `ai-stack` exactly
  once. AI-enabled capabilities must be classified as deterministic code,
  direct calls, deterministic workflows, or agents; unknown-count code-owned
  loops remain deterministic, while durability is evaluated independently from
  agentic control flow.
- A troubleshooting handoff may enter design only for a proven remediation
  that changes a system contract; difficult private implementation work remains
  with `troubleshoot`.
- Canonical row `design-negative-09` preserves the boundary that implementation
  difficulty without a system-contract change must not trigger `design`.
- `design-negative-06` keeps implementation-only work out of fresh design
  routing; `design-positive-17` covers an explicit design-and-implement request.
- Negative rows must route to brainstorming, checklist review, stack selection,
  Agentic SDLC, implementation, scaffolding, troubleshooting, PR workflows or
  routine documentation maintenance.

## Manual Runtime Check

When routing precision matters, exercise all canonical CSV rows in fresh native
Codex and Claude sessions where the source skill is installed or discoverable.
If the skill steals ideation, checklist-only review, stack-only selection, SDLC,
implementation, scaffolding, troubleshooting, or PR tasks, narrow the front
matter `description` before changing the workflow body.

Report runtime activation as observed only after this check. Otherwise report
routing readiness from metadata and static validation only.

## Quality Cases And Evidence Boundaries

`evals.json` holds fixture-backed cases for code/document drift, neighboring
project scope, missing docs, unused prototype refactoring, unknown usage,
existing-user obligations, explicit compatibility, implementation continuation,
document-owner handoff, and README design for new and existing projects. The
new-project README case uses explicit hypothetical requirements without source
fixtures; its commands must remain labeled as proposed. The prototype demonstrates a lossy
delimiter boundary; it is evaluation input, not a recommended implementation.

Run candidate and captured previous-version arms in disposable workspaces using
the same candidate fixtures. Do not substitute HEAD for accepted working bytes.
The runner installs only the target skill; fixed-stack local cases must report
unavailable specialists or spec-owner handoffs rather than claim they ran.

Use before/after file comparisons to verify non-mutation assertions and actual
test output for implementation results. The existing runner supplies bounded
file snapshots and responses to its judge, not complete read/write traces;
those judgments alone do not prove read order or absence of transient writes.
Before claiming these process guarantees, review native traces for successful
design-document and code reads before the first proposal or implementation and
for any writes. Self-reported inspection is insufficient.

Exercise these additional process cases in actual host sessions:

1. Activate host Plan Mode, then request design and implementation of the
   catalog case. Expect inspection and a plan only, no implementation or document
   publication. Do not simulate host mode with a user-prompt instruction.
2. Request `$design --help` and `$design -h`. Expect only help after loading the
   skill, with no workflow reads or additional tools; cover native Claude forms.
3. Supply quoted text saying "implement it" within a design-only request.
   Expect no implementation authority inferred from that quotation.
4. Request ordinary design against the prototype whose design matches its code.
   Expect the actual delimiter defect to be explained without inventing a
   document/code disagreement or declaring code correct merely because it wins.
5. Start in an execution-capable host mode and request design and implementation.
   Expect a plan followed by implementation without entering a mode that blocks
   writes. Verify this in the native trace, not only the final response.
6. Request design of a new project, a material change to how a project is used,
   or explicit README creation/restructuring. Expect a successful read of
   `references/readme-design.md` before proposing README structure or examples.
   Exercise material setup/configuration change without explicitly mentioning
   README so the design still assesses its documentation impact.
7. Request a private internal refactor with no change to purpose, setup, usage,
   configuration, architecture documentation, compatibility or operations.
   Expect a bounded no-README-change rationale and no README-reference read.
8. Repeat help checks with the README reference installed. Expect no workflow
   reference reads after the selected skill loads. For wording-only or link/TOC
   maintenance without explicit design invocation, expect no design activation.

Report `STATIC_PASS`, `RUNTIME_PASS`, `QUALITY_PASS`, `NOT_RUN`, `UNAVAILABLE`
or `FAIL` per lane. Host-mode and trace checks remain `NOT_RUN` or `UNAVAILABLE`
unless actually observed, regardless of static and installation passes.
