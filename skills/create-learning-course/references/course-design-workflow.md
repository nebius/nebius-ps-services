# Course Design Workflow

## Design From What Learners Must Be Able To Do

Start with a real task, decision or explanation the learner should produce.
Use observable verbs: explain a mechanism, diagnose a cause, implement a
solution, compare alternatives or defend a choice. "Understand networking" is
not an assessable outcome; "trace a request and identify which link limits it"
is. Match the assessment to that verb: recall questions alone do not assess
diagnosis or design. The research basis is in `research-basis.md`.

Map each outcome to teaching, a worked example, practice and a success rubric.
Use this mapping for author review, not a published source-coverage table.
Estimate guided effort from setup, teaching, core practice and feedback, with
reasonable learner uncertainty. Show only the concise estimate in the banner;
do not publish the arithmetic or count optional reading as required time.

## Entry And Competency Progression

A mixed-audience course is not an advanced course with a glossary attached.

1. State the subject, problem it solves, inputs, outputs and whole workflow.
2. Define the smallest vocabulary needed to follow that workflow.
3. Provide a small concrete example, including units and assumptions.
4. Teach the mechanism, components and distinctions from related concepts.
5. Demonstrate a complete task and explain the decisions.
6. Guide a similar task, then fade support for independent transfer.
7. Add optimization, exceptions and advanced trade-offs after prerequisites.

Build a prerequisite graph before lesson numbers. Check every technical noun,
equation and required tool at its first meaningful use. A prerequisite bridge
says what to recall and why, not simply "see lesson 2." A readiness check lets
experienced learners skip familiar explanations without skipping new evidence.

Keep one primary competency per lesson where practical. Split an overloaded
lesson at a real conceptual boundary; do not split merely to hit a word limit.
A short paragraph can define a simple term; a complex mechanism needs enough
connected explanation and worked reasoning to support the stated outcome.

## Explanations That Teach

Begin every core concept with what it is, what it acts on and what it produces.
Explain its operation before applications. Contrast likely confusions: a
logical abstraction versus physical machinery, capacity versus rate, modeled
bound versus measured outcome, or an interface versus its implementation.

Use everyday language without distorting meaning. Introduce symbols after
plain-language relationships, define units and boundaries, and show arithmetic
or decision steps. State when an analogy stops applying. Each example needs
inputs, assumptions, reasoning, result and interpretation.

Do not manufacture depth with repeated "this matters" paragraphs. Add useful
mechanism, examples, counterexamples, failure analysis or constraints. Avoid
unexplained acronyms, ambiguous pronouns, fragments, mixed units and claims
that exceed supplied code. Review wording in context, not with replacement
lists that can silently change meaning.

## Practice, Feedback And Retention

- Use a worked example before the learner solves a comparable new problem.
  Gradually remove hints rather than alternating unrelated difficulty.
- Include short retrieval prompts and delayed review after intervening topics.
  Give feedback or an answer key after a genuine attempt opportunity.
- Mix related problem types once foundational procedures are established.
  Explain how to choose a method, not only how to carry it out.
- Ask explanatory questions: why did this result occur, what alternative
  explanation remains, and what observation would distinguish the two?
- End with an authentic transfer assessment using changed inputs or constraints.
  Publish success criteria, correctness checks and reasoning expectations.
- Offer equivalent accessible ways to demonstrate a competency when the
  particular response medium is not itself the competency being assessed.

Do not promise universal learning gains or label students by supposed fixed
visual/auditory learning styles. These are evidence-informed design choices,
not a guarantee that every course or learner benefits equally.

## Preservation And Topic Ownership

Before a redesign, inspect complete affected prose, implementation, guide and
diagram sources. Inventory unique concepts, assumptions, worked examples,
failure explanations, supported experiment capabilities and safety checks.
Use a private, bounded task audit when permitted, not learner-facing history.

For each candidate duplicate ask whether it has the same learning purpose and
adds no distinct reasoning. Keep a purposeful recall cue, preview or advanced
application and label its role. Consolidate truly repeated material into one
primary lesson; relocate all associated labs, diagrams, helper files and
references with it. Recheck links and practical readiness afterward.

Default to one canonical new path when restructuring. Do not create redirects,
aliases or compatibility wrappers unless requested. Never silently discard
material just because the new outline is shorter.

## Course Profiles And Series

Record domain choices separately from format: learner language, example
language, target hardware/OS, available services, dependency isolation,
numerical tolerances, realistic scale, cost and authorized runtime actions.
Verify version-sensitive official guidance; distinguish candidate versions
from versions actually installed and exercised.

Non-code courses still need practice, evidence and feedback, but may use a
case analysis, role-play, calculation or annotated artifact instead of code.
Do not create empty toolchain or cluster sections. In a series, prerequisites
are competency relationships, not simply the visual catalog order.

## Attribution

The early mission-led workflow drew high-level inspiration from the public
[teach pattern](https://github.com/mattpocock/skills/tree/main/skills/productivity/teach).
The current textbook format and verification contract are independently
maintained. If substantial upstream implementation or text is reused, check
its current license and retain the required notices.
