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
Escaped source and valid HTTPS links provide positive controls. The fixture
tests mechanics, not complete course pedagogy.
No test launches a browser, fetches a resource, runs a lab or installs packages.
The source checker is read-only and does not render Markdown.

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

## Reporting

Use STATIC_PASS, RUNTIME_PASS, QUALITY_PASS, NOT_RUN, UNAVAILABLE or FAIL with
the exact scope. Missing clean runners, browser access, target execution or
baseline output remain explicit limitations. Never infer comparative quality
from new deterministic tests. Remove only task-owned temporary baselines after
comparison, or document owner, reason and a bounded retention deadline.
