# NVIDIA H100 Performance Engineering Courses

This directory contains five standalone, practical courses for engineers using
NVIDIA H100 GPUs on Linux and Slurm.

```text
gpu-fundamentals
       ↓
gpu-optimizations
       ├── llm-training
       ├── llm-inference
       └── custom-cuda-kernels
```

The catalog order is:

1. [GPU Fundamentals](gpu-fundamentals/README.md)
2. [GPU Performance Optimization](gpu-optimizations/README.md)
3. [LLM Training](llm-training/README.md)
4. [LLM Inference](llm-inference/README.md)
5. [Custom CUDA Kernels](custom-cuda-kernels/README.md)

Fundamentals and Optimizations are prerequisites for all three specialized
courses. LLM Training and LLM Inference are not prerequisites for Custom CUDA
Kernels.

## Open a course

| Course | Self-contained course | Estimated guided hours |
| --- | --- | --- |
| GPU Fundamentals | [Read the course](gpu-fundamentals/index.html) | 21 |
| GPU Performance Optimization | [Read the course](gpu-optimizations/index.html) | 36 |
| LLM Training | [Read the course](llm-training/index.html) | 48 |
| LLM Inference | [Read the course](llm-inference/index.html) | 47 |
| Custom CUDA Kernels for GPU Optimization | [Read the course](custom-cuda-kernels/index.html) | 36 |

Each self-contained page uses a light digital-textbook layout: a persistent
side-panel TOC, a wide responsive content frame, soft blue and mint callouts,
and complete source listings. Diagrams sit directly beside their lesson or lab
explanations and fit the available width without horizontal panning. The sidebar
provides course navigation; each figure retains its caption and accessible SVG
title and description without duplicate transcript controls. The banner
contains only the main topic and a brief estimated guided-hours label.

## Educational approach

Every course begins with an authored **Start here** introduction that defines
the subject, explains its value and workflow, introduces essential vocabulary
and includes a small worked example. Introductory diagrams stay beside those explanations. Newcomers
follow the prerequisite route; experienced readers use the readiness checkpoint.
Training Lab 32 and Inference Lab 35 teach learning versus fixed-parameter
prediction on CPU or an explicitly selected H100 without model downloads.

Every lesson defines its concept and connects prerequisites, purpose, mental
model and mechanism. It ends with **Practice labs** links. The linked guides
own H100 scope, integrated **Practice** examples and commands, trade-offs,
evidence interpretation, failure analysis and review. Read the theory first,
then make a prediction, run the lab and explain the result.

Beyond the course entry, every lesson begins with **What it is**: a plain-English
definition of its core concept, its basic operation and essential distinctions
before objectives or optimization advice. Acronyms and first-use vocabulary
are explained in context. These primers supplement the detailed mechanisms
and worked examples; they do not replace them with summaries.

The same sequence applies when a new topic starts inside a lesson, practical
guide or optional study entry. First explain what kind of thing it is and how
its essential parts work together; then introduce its purpose, mechanics,
trade-offs and application. Expand acronyms in context and distinguish nearby
ideas. A name, benefit or glossary link alone is insufficient. For a concept
already taught along the prerequisite route, use a brief reminder where needed
instead of repeating the full explanation.

Every lab's **Theory preparation** identifies the lessons to read before its
first full execution. Those lessons explain what each computation, measurement,
validation or coordination technique is, why it is used and how to apply it.
The guide connects that theory to the supplied code; it is not the only home
for a technique's explanation. Prerequisite-course concepts may be reused,
while later or optional topics cannot be hidden requirements for an earlier
experiment. A code preview does not authorize running a script whose remaining
techniques have not yet been taught.

Numerical acceptance checks reject non-finite reference and candidate values
before applying tolerances or aggregating errors. Distributed experiments
keep every rank participating through the shared verdict. The theory and
lab guide explain both the comparison and its limits; successful samples
do not establish whole-model equivalence or target-runtime qualification.

Before running a lab, explain its operation, dependencies, timing boundary and
numerical acceptance check in your own words. Reused profiler skills transfer
to a new workload, but its measured
bottleneck does not: collect evidence from the actual baseline and candidate.

Each of the 94 labs has an authored introduction followed by seven practical
sections: **Before you start**, **Concepts and code path**, **Practice**,
**Check your results**, **Investigate the behavior**, **If something goes wrong**,
and **Takeaways and next step**. These explain the actual code structure,
supported commands, result fields, numerical gates, and meaningful extensions.
Exact lab titles link both ways between their lessons and the practical section.

Throughout the catalog, explanations distinguish supplied experiments from
optional extensions, state units and timing boundaries, and use code formatting
for exact commands, options and implementation names.

Each syllabus provides an ordered lesson-by-lesson route, the competency to
build, the appropriate lab activity, and readiness checkpoints. Read lessons
in that order: lab numbers identify files and are not a separate execution
sequence. Early previews introduce vocabulary without requiring advanced
experiments; optional branches are explicitly separated from core completion.

Each complete topic and experiment has a primary course owner. Fundamentals
explains hardware behavior; Optimizations teaches general measurement and
intervention; Training owns learning updates and gradients; Inference owns
request execution and serving; Custom Kernels owns CUDA implementation.
Related vocabulary is not duplicate teaching. A preview, prerequisite refresher,
baseline comparison or capstone revisit states what new question it serves. Standalone
preflight and helper copies keep each course independently runnable.

**Lab mechanisms and evidence** preserves additional worked procedures and
deeper interpretation guides. Core and optional exercises are labeled separately.
Official vendor references appear at the end of each course for deeper study
and version checks.

Each course closes with **Where to Go Next**, an optional reading list
of current technologies and advanced concepts. Each entry defines its topic,
offers a study question, states hardware or maturity limits, and links to
official documentation. Find it in the sidebar or the course's NEXT-STEPS.md.
These directions do not add required labs, guided hours or dependency upgrades.
The shared renderer publishes each complete guide before the final references;
standalone validators check placement, navigation, complete narrative parity
and each reading link's destination.

## Evidence boundaries

Every course reports four independent evidence lanes:

1. Source and static validation.
2. Installed dependency compatibility.
3. Runtime activation of CUDA, compilers, models, or engines.
4. Live execution on the declared H100/Slurm target.

Local or static checks never prove H100 performance. Raw runtime artifacts can
contain environment details and remain private; only reviewed summaries belong
in public course material.

## Offline validation

```bash
python3 tools/build_course_html.py
python3 tools/build_course_html.py --check
python3 tools/validate_all_courses.py
python3 -m pytest -q
```

GPU and engine workloads are intentionally excluded from offline validation.

Navigation checks cover lesson order, lab identities and local destinations.
The closing study section and its TOC link must both use the title from
`NEXT-STEPS.md`; standalone validators reject a mismatch.

Lab narrative checks include comparison tables: headers, rows and cell text
must survive rendering in order. Literal pipes in prose and fenced shell
examples remain part of the content being checked.

The CPU regression suite also exercises malformed capstone evidence, complete
stream termination, generated-response validation, seeded initialization,
profiler event fields, embedded preflight commands, and benchmark tokenizer
revision forwarding. These checks use local fixtures and captured commands;
they do not start serving engines or submit Slurm jobs.

Fault-injection checks require training acceptance to reject missing backward
or optimizer work. KV-cache checks require inference execution without saved
autograd tensors and reject non-finite prompt or decode results. Standalone
validators compile Python in memory and disable bytecode output for help probes,
so validation does not leave shared temporary bytecode files.

Numerical acceptance regressions also reject infinite scalar sums and non-finite
errors in each sampled gradient, tensor-parallel comparison and padded prompt.
They retain finite passing controls and check that invalid training errors reach
the rank-consensus call before failure.

The BF16 library-first lab checks each path against an independent FP64
reference with an explicit intermediate-rounding allowance. CPU tests retain
the cancellation case that defeated the former pairwise comparison and reject
shared corruption, missing bias/ReLU, and invalid outputs before timing.

Publication checks prune excluded `.venv*` directories before traversal while
checking the same course files. The 24 BF16 corruption cases use small,
independent CPU fixtures; the original 512-square cancellation and seeded
acceptance cases remain. The complete offline test command above remains the
correctness gate. Use `python3 -m pytest -q --durations=20` to inspect setup,
call and teardown costs when investigating slower feedback.

Presentation tests load a fresh renderer once per test invocation. Guide-command
tests extract each lab's CLI options once within that course test, while still
checking every command's shell syntax, source paths and options. This reuse
does not share module state or option caches between tests, and the full serial
pytest command remains the correctness gate.

Launcher-to-client regressions also exercise the Triton evidence-directory and
shared run-ID handoff with HTTP replaced by local fixtures. Quick-start checks
require qualified-environment guidance and private submitter-side file settings
before live-job examples; candidate dependency installation is not qualification.

The checks cover course and lab identities, navigation, complete embedded
source, evidence wording, and publication safety, including C++ headers.
When a host C++ compiler is available, the test suite also compiles and exercises
the CUDA course's device-independent argument parser. This does not compile or
validate its CUDA kernels; use the separate target build and H100 gates.

## Maintain a lab guide

Edit the canonical guide at `COURSE/reference/labs/SOURCE_STEM.md`, beside the
course's existing reference guides. Its heading is `# Lab NN: Descriptive title`;
the number matches the executable filename. Keep the seven section headings in
the order above and put supported commands in Bash fences. Describe conceptual
extensions explicitly rather than suggesting unimplemented flags or metrics.
Define any new operation, tool, numerical measure or execution technique before
requiring its use. Explain a formula's quantities before asking learners to
interpret its result, and use the supplied code to verify the explanation.

`reference/course.json` assigns each executable to its core/optional scope and
one or more lesson numbers. The builder uses those records for exact titles,
TOC entries, and lesson links; it rejects missing or orphaned guides. Rebuild
HTML after changing a guide or source. Validators check narrative/source parity,
lesson links, and command syntax without executing GPU jobs.

Overview diagrams apply label-fit limits to the slots their selected layout
actually uses. Full-width captions retain their complete text. After editing
diagram metadata, SVGs, or shared styles, rebuild every page and run the
diagram and source-parity checks; helper-level text checks alone do not prove
that labels fit the rendered layout.

Keep multiline labels vertically centered, wrap long wording without removing
its meaning, and enlarge cards before reducing font size. Leave clear space
around connectors and their captions. The shared SVG font stack is Arial,
Helvetica, then sans-serif. Review all affected SVGs with the embedded course
styles at wide and narrow widths, including an alternate-font fit check.
Asset rendering does not replace the separate full-page browser review.
