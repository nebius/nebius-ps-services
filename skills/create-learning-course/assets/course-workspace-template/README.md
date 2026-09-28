# Course Authoring Starter

This folder contains authoring templates, not a finished course. Replace every
brace-delimited instruction with substantive content. Do not publish unresolved
placeholders or treat the illustrative metadata as a runtime API.

1. Establish the mission, outcomes, prerequisites and course profile.
2. Author conceptual lesson titles and the complete COURSE.md using Objective,
   How it works, Practice and Mental model. Put optional References last.
   Maintain exactly one course GLOSSARY.md and NEXT-STEPS.md, shared by all
   lessons and guides; render each once as its own section. Keep explanations
   connected, define unfamiliar terms and preserve useful teaching when consolidating.
3. Add purposeful guides under reference/labs and at least one original core
   diagram inside each lesson's How it works, beside its explanation.
4. Reuse the copied `tools/assets/textbook-shell.html` and `tools/assets/styles.css`.
5. Configure `tools/course_adapter.py` with the existing project renderer and
   explicit course registration, then run `./build-courses.sh`. The supplied
   adapter fails clearly until configured. It renders complete canonical prose,
   inline CSS/SVG, full escaped source and matching TOC/anchors. The provided
   builder owns atomic output replacement and the read-only freshness check;
   renderer-specific tests establish full prose and source parity.
6. Maintain a JSON source allowlist and run the bundled read-only checker.
7. Perform semantic, browser, safety and applicable target reviews separately.

Building requires Bash and Python 3, plus any declared renderer dependencies.
Git is needed only for Git inventory. The starter imposes no learner hardware
or infrastructure requirement. For a
technical course, add executable sources, tests, version records, isolated
environments and smoke procedures appropriate to the declared target. For a
nontechnical course, use cases, worksheets or other authentic assessment.

Keep planning/review records out of the learner-facing page. Keep mission and syllabus as authoring inputs only; preserve their unique
learner context in the orientation or owning teaching. Render complete teaching,
practical guides, an independent Glossary, bulleted next steps and numbered
official references, using the same shared typography across courses. Keep only the title and concise estimated guided
hours in the banner.

The copied `tools/assets` contains the shared shell, CSS and fragments, so a
configured project can build without the installed skill. `tools/publication.py`
provides explicit-inventory ZIP assembly, configured size budgets, safe paths
and atomic per-file replacement. `python3 -B tools/build_course_html.py --check`
only compares outputs. See the skill portable-publication reference for the
adapter contract, exact companion-link allowlist and hosting limits.
No script installs dependencies or publishes; declare renderer requirements in
the project. Self-contained reading does not require embedding bulky downloads.

Run `python3 -B -m unittest discover -s tests` for the copied packaging checks.
Add complete prose, source and navigation parity tests for your renderer.
