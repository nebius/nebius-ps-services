# PyTorch for GPU Performance Engineering

[Read the course](index.html) · Estimated guided hours: 3

A visual PyTorch foundation for engineers who can read basic Python. Eighteen
focused lessons use short examples and diagrams to explain tensor operations,
model execution, memory, transfers, GPU timing, batching and compilation. Each
example teaches one relationship, and each performance connection stays brief.
Definitions explain concepts before their examples. Lesson 2 follows grid, row
and column indices through small selections and shape changes; lesson 9 explains
how a linear layer uses the feature axis. PyTorch API names use bold monospace.
Broadcasting uses one matrix-plus-bias example; model composition and transfers
use compact, direct code. No labs, practice or setup are required; CUDA-specific
examples are marked for reading on any computer.

Read directly before [GPU Fundamentals](../gpu-fundamentals/index.html), then
continue to [GPU Performance Optimization](../gpu-optimizations/index.html). Use
[GPU Performance Tools](../gpu-performance-tools/index.html) for profiler
command explanations, and the [Lab Guide](../lab-guide.html) when you later
prepare to run practical courses.

## Maintainer notes

Canonical teaching is in `COURSE.md`; lesson identities and reading duration are
in `reference/course.json` and `SYLLABUS.md`. Original diagrams are registered
in `reference/visual-manifest.json` and placed explicitly in the owning
explanation. The course has no execution environment or runtime package. Use
`####` for subsections within a lesson field; the renderer preserves them as
`h4` beneath the field's `h3` heading, including fields with diagrams.

From the courses root, run `./build-courses.sh` to rebuild the shared catalog,
guide and course publications. `python3 -B tools/validate_text_course.py` checks
all reading profiles; `python3 -B tools/build_course_html.py --check` checks
freshness without writing. See `PUBLICATION-REVIEW.md` for evidence and limits.
