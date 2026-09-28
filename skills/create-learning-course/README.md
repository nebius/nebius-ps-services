# Create Learning Course

Explicit-only course authoring for any subject, using a consistent light,
self-contained HTML textbook with a side TOC, definition-first lessons,
contextual SVG diagrams and practical guides.

```text
$create-learning-course Create a beginner-to-advanced course on <subject>
$create-learning-course Revise <course-folder> without losing useful explanations
$create-learning-course --help
```

Every lesson has a conceptual title and follows **Objective → How it works →
Practice → Mental model → References (if included)**. How it works integrates
definitions, prerequisite connections, purpose, mechanism, examples and
limitations; unfamiliar abbreviations are explained locally. Every explanation
contains a meaningful core diagram. Mental model summarizes prior teaching.

Each course has exactly one **Where to Go Next** and one **Glossary**, shared
by all lessons and guides, before final Official references. Onward options
relate to completed competencies and add no required dependencies or gates.
Glossary entries cover all taught key terms and abbreviations with expansions
and definitions, sorted A–Z by displayed key ignoring case. Merge duplicates
without losing distinct meanings; do not create local copies. Lesson-only
revisions update owning course sections where available; standalone lessons
keep terms in context and onward suggestions in the handoff without adding
appendices or an unrequested package. Practice covers code and non-code work.

All courses share typography and heading roles. Official references use numbered
lists; each Where to Go Next option is a bullet. The course-wide Glossary has
its own section. Mission and syllabus remain authoring inputs: preserve their
unique prerequisites, readiness and safety context in the orientation or owning
lesson, without rendering those planning sections or navigation entries.

The format preserves complete canonical teaching and source, not summaries.
Practical guides explain purpose, prerequisites, architecture, steps, result
checks, investigation, failure diagnosis and transferable lessons. Nontechnical
courses use appropriate cases or exercises without invented runtime machinery.

## Resources

- [Instruction core](SKILL.md)
- [Teaching and preservation](references/course-design-workflow.md)
- [Course format and generation contract](references/course-format.md)
- [Practical-work standard](references/practical-work.md)
- [Publication safety and evidence](references/publication-safety.md)
- [Research basis](references/research-basis.md)
- [Build alignment evidence](references/build-validation.md)
- [Portable reading, downloads and build tools](references/portable-publication.md)
- [Course starter](assets/course-workspace-template/README.md)
- [HTML shell](assets/textbook-shell.html) and [light styles](assets/styles.css)

The shell is not a complete course or Markdown renderer. A future course
retains its renderer or implements the provided adapter, with full prose-parity
tests. The starter now includes a build/check wrapper, safe publication helpers,
deterministic explicit-inventory ZIP assembly and configurable hosting budgets.
The bundled checker is deliberately read-only and dependency-light.

## Browser Verification

Permitted full-course browser checks default to headless Playwright Test with
an owned Chrome process and isolated context. Keep desktop, 390px and 320px
coverage, keyboard use, zoom/reflow, mobile TOC, local scrollers and visual
diagram review. Record the inspected artifact, actual browser/version,
headless mode, viewports, assertions, screenshots/traces and cleanup in the
publication review; close owned browser resources even when checks fail.

Optional headless, isolated Playwright MCP exploration is separate from
repeatable acceptance checks. Screen unlock, foreground windows and monitor
selection are not prerequisites. If browser execution is unavailable or
denied, continue permitted authoring and keep the required gate pending.
Review-only and lesson-only requests gain no implicit browser work. This
default grants no installation, publication or lab-execution authority and
does not promise execution through sleep, a closed lid or a multi-day run.
See the [browser evidence contract](references/publication-safety.md#browser-verification).

## Local Checks

```bash
python3 scripts/check_course.py /path/to/course/index.html \
  --course-root /path/to/course \
  --sources-manifest /path/to/course/reference/sources.json
python3 -B -m unittest discover -s scripts -p 'test_*.py' -v
```

The source manifest is a JSON array of every UTF-8 file expected in an embedded
listing; use an empty array for a course with no source listings. The checker
verifies a bounded HTML/identity/accessibility/source-byte contract, including
numbered reference and bulleted next-step lists, an independent course Glossary,
no mission/syllabus headings or navigation, lesson-section order, matching
visible headings, exactly one course Where to Go Next before one Glossary,
both after all teaching and practical work, no local copies or appendix-wrapped
lessons/guides (even before the appendix heading), nonempty glossary pairs and
unique A–Z keys, optional lesson
References last,
nonempty explanation outside diagram text and a core SVG figure inside each
How it works. It rejects
old section layouts; there is no compatibility switch. It does
not establish glossary coverage or expansion accuracy, prose completeness,
diagram geometry, browser behavior, domain
accuracy, secret freedom or target execution.

Exit status is 0 for passing bounded checks, 1 for course-format violations,
and 2 for invalid arguments or unreadable/invalid input files. Malformed
required HTML attributes produce validation failures rather than tracebacks.
Malformed reference URLs are also course-format failures (exit 1). Source
listings reject unescaped comments, declarations and processing instructions;
the same text is accepted when properly escaped as literal source.
Reference links reject both `ping` tracking and `attributionsrc` reporting,
including empty or valueless attributes; ordinary HTTPS links remain allowed.
Help requires no course files and performs no course inspection.

Strict skill structure and evaluation definitions are checked separately with
the repository's skill validator. See [evaluation cases](evals/process-cases.md)
for runtime and comparative quality lanes. Updating this source does not
install or activate the skill.

For external companion links, supply `--links-manifest` with a JSON array of
exact relative hrefs, and `--publication-root` when a guide is shared outside
the course. These are checker arguments, not skill flags. Targets must exist,
fragments must resolve and paths must remain symlink-free inside the root.
