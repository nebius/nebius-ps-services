# Evaluation Procedure

`trigger-prompts.csv` is the only routing authority. The skill remains
explicit-only. `evals.json` defines output-quality cases; it is not a second
trigger catalog.

## Static Lane

Run strict skill structure validation and the focused tests:

```bash
python3 -B -m unittest discover -s scripts -p 'test_*.py' -v
```

Tests construct a small local fixture from the HTML/CSS/SVG assets and exercise
negative cases, including nonliteral source markup and malformed reference URLs.
Escaped source and valid HTTPS links provide positive controls. Tests also
reject bare, empty, valued and mixed-case `attributionsrc` attributes;
the CLI reports attribution markup as a format failure without a traceback.
These controls enforce the existing no-tracking rule without following links.
The fixture uses the real lesson fragment and tests local diagram
ownership, visible labels/order, nested explanation containers and nonempty
prose outside figure text. Negative controls cover missing, reordered and duplicate sections,
caption-only or misplaced diagrams, cross-lesson coverage and trailing content.
Course-appendix controls require exactly one independent Where to Go Next and
one Glossary shared by multiple lessons, before final Official references.
Reject missing/duplicate course sections and lesson/guide-local or nested
headings and containers. Reject appendices before teaching or practical work,
and lesson/guide content inside an appendix even before its identifying heading.
Glossary checks preserve nonempty term/definition
pairs, unique case-insensitive A–Z keys, inline formatting and ordinary terms.
Keep optional lesson References last and accept nested numbered procedures
within a course-level next-step bullet.
Presentation controls reject paragraph-only or ordered next-step options,
unnumbered references, nested course glossaries, and published mission/syllabus
headings or navigation. Compare retained orientation and lesson content with
working source during revision; semantic preservation and typography still
need review. These test mechanics, not complete course pedagogy or glossary coverage.
No deterministic test launches a browser, fetches a resource, runs a lab or installs packages.
The source checker is read-only and does not render Markdown.

## Browser Behavior Lane

For full-course cases 1-3, inspect whether permitted browser verification uses
owned headless Playwright Test with Chrome and isolated state, preserves the
desktop, 390px and 320px checks, and combines repeatable assertions with visual
review. Evidence identifies the artifact, actual browser/version, headless mode,
viewports, assertions, screenshots/traces and cleanup after success or failure.
Optional headless, isolated MCP exploration is reported separately.

Also assess the unavailable or denied-browser scenario: permitted authoring
continues, the required browser gate remains pending, and the agent neither
requests screen unlock nor bypasses the denial or claims publication readiness.
Do not deny tools merely to run the static lane; evaluate this scenario only
in an authorized behavioral runner or review its expected handling as a
definition, clearly distinguishing the two kinds of evidence.

Cases 4-5 preserve review-only and lesson-only scope with no implicit browser
execution. No case gains dependency-install, publication or live-lab authority
from the browser default. Do not infer this lane, screen-lock behavior or
fresh-session activation from static checks or installed-file parity. Updating
these definitions does not require a live browser trial or full SDLC rerun.

## Fresh Routing Lane

In an authorized fresh session with the skill discoverable, evaluate every
canonical CSV row. Positive help must stop after the instruction file loads;
the other positives honor the requested full, syllabus-only or review-only scope.
The review-only quality case must leave course and skill sources unchanged and
must not execute the example. Negative rows must not implicitly select
this explicit-only skill. Source metadata alone cannot earn RUNTIME_PASS.

## Comparative Quality Lane

Capture the previous working bytes in owner-only temporary storage before
editing. Run each eval in separate clean contexts with identical prompts and
fixtures, once against that baseline and once against the revised skill.
Use disposable output roots. Do not reuse the authoring conversation.

Assess every assertion with artifact/line evidence, including whether a
beginner can explain the concept before the first exercise. Check accuracy,
retained depth, realistic lab behavior and clarity with judgment, not a keyword
score or a minimum word count. Measure time/tokens only when the runner exposes
comparable evidence.

For the revision case, stage the synthetic fixture as course input; do not
pretend its inline snippet is a complete runnable course. The agent should
create canonical files and correct unsupported claims within the request.

The lesson-only waiting-line case can run as a bounded clean-context writing
comparison without a full package or browser. Apply the same request and
references to baseline and revised source; score conceptual titles, causal
completeness, local abbreviations, diagram meaning and summary ownership.
The standalone lesson must not contain Glossary or Where to Go Next sections;
verify FIFO and WIP are explained in context before their summary use, any
References remain last, and optional onward suggestions stay in the handoff.
For full-course cases, assess exactly one course Glossary and Where to Go Next,
covering all lessons without local copies. In the nontechnical case, require
ordinary glossary terms without inventing abbreviations. Verify preserved
meanings and useful onward options semantically when consolidating duplicates.
State exactly which cases ran. An output comparison is not a fresh installed
routing test and does not prove the quality of every future course.

## Reporting

Use STATIC_PASS, RUNTIME_PASS, QUALITY_PASS, NOT_RUN, UNAVAILABLE or FAIL with
the exact scope. Missing clean runners, browser access, target execution or
baseline output remain explicit limitations. Never infer comparative quality
from new deterministic tests. Remove only task-owned temporary baselines after
comparison, or document owner, reason and a bounded retention deadline.
