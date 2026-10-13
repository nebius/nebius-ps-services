# PyTorch for GPU Performance Engineering

[Read the course](index.html) · Estimated guided hours: 3

A visual foundation for engineers who can read basic Python. Sixteen lessons
explain tensor shapes and operations, numerical formats, model execution,
memory, transfers, batching, GPU timing and compilation. Small commented
examples and diagrams connect each operation to its inputs, outputs and costs.

No labs, practice or setup are required. CUDA examples can be read on any
computer; reading them does not establish GPU performance or runtime support.

Read after [GPU Performance Tools](../gpu-performance-tools/index.html) and before
[GPU Fundamentals](../gpu-fundamentals/index.html), then continue with
[GPU Performance Optimization](../gpu-optimizations/index.html). Use the
[Lab Guide](../lab-guide.html) when preparing for practical courses.

## Maintainer notes

Canonical teaching is in `COURSE.md`; lesson identities and reading duration are
in `reference/course.json` and `SYLLABUS.md`. Original diagrams are registered
in `reference/visual-manifest.json` and placed explicitly in the owning
explanation. The course has no execution environment or runtime package. Use
`####` for subsections within a lesson field; the renderer preserves them as
`h4` beneath the field's `h3` heading, including fields with diagrams.
For a two-panel comparison, surround ordinary Markdown with
`<!-- comparison:start -->`, `<!-- comparison:next -->` and
`<!-- comparison:end -->` on separate lines. Each nonempty panel may contain
its heading, explanation, registered diagram and fenced code. Nesting is
rejected; wide screens show columns and narrow screens retain reading order.

From the courses root, run `./build-courses.sh` to rebuild the shared catalog,
guide and course publications. `python3 -B tools/validate_text_course.py` checks
all reading profiles; `python3 -B tools/build_course_html.py --check` checks
freshness without writing. See `PUBLICATION-REVIEW.md` for evidence and limits.
