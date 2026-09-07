# Create Learning Course

Explicit-only course authoring for any subject, using a consistent light,
self-contained HTML textbook with a side TOC, definition-first lessons,
contextual SVG diagrams and practical guides.

```text
$create-learning-course Create a beginner-to-advanced course on <subject>
$create-learning-course Revise <course-folder> without losing useful explanations
$create-learning-course --help
```

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
- [Course starter](assets/course-workspace-template/README.md)
- [HTML shell](assets/textbook-shell.html) and [light styles](assets/styles.css)

The shell is not a complete course or Markdown renderer. A future course
retains or creates its own deterministic builder and full prose-parity tests.
The bundled checker is deliberately read-only and dependency-light.

## Local Checks

```bash
python3 scripts/check_course.py /path/to/course/index.html \
  --course-root /path/to/course \
  --sources-manifest /path/to/course/reference/sources.json
python3 -B -m unittest discover -s scripts -p 'test_*.py' -v
```

The source manifest is a JSON array of every UTF-8 file expected in an embedded
listing; use an empty array for a course with no source listings. The checker
verifies a bounded HTML/identity/accessibility/source-byte contract. It does
not establish prose completeness, diagram geometry, browser behavior, domain
accuracy, secret freedom or target execution.

Exit status is 0 for passing bounded checks, 1 for course-format violations,
and 2 for invalid arguments or unreadable/invalid input files. Malformed
required HTML attributes produce validation failures rather than tracebacks.
Malformed reference URLs are also course-format failures (exit 1). Source
listings reject unescaped comments, declarations and processing instructions;
the same text is accepted when properly escaped as literal source.
Help requires no course files and performs no course inspection.

Strict skill structure and evaluation definitions are checked separately with
the repository's skill validator. See [evaluation cases](evals/process-cases.md)
for runtime and comparative quality lanes. Updating this source does not
install or activate the skill.
