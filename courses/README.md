# NVIDIA H100 Performance Engineering Courses

This directory contains five standalone, practical courses for engineers using
NVIDIA H100 GPUs on Linux and Slurm.

[Browse the course catalog](https://nebius.github.io/nebius-ps-services/courses/)
for introductions, prerequisites and direct links to all five courses.

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

## Sync labs to a Slurm login node

Run [sync-labs.sh](sync-labs.sh) **on your local computer**, from the `courses`
directory of your Git clone. Open a second local terminal if your current
terminal is already connected to the login node. Supply the node's DNS name,
IP address or an existing SSH alias:

```bash
./sync-labs.sh login.example.com
./sync-labs.sh 192.0.2.10
./sync-labs.sh slurm-login
```

Use the actual address of your login node. SSH resolves DNS names and uses your
normal SSH settings for keys, ports, proxies and host verification. A bare target
always selects **root**, even if SSH configuration specifies a different `User`.
An explicit **user@target** selects that account for both preflight and transfer:

```bash
./sync-labs.sh 192.0.2.10             # root@192.0.2.10
./sync-labs.sh nebius@192.0.2.10      # nebius@192.0.2.10
./sync-labs.sh student@login.example.com
```

IPv6 addresses are also accepted, for example
`./sync-labs.sh student@2001:db8::10`. Command-line account selection takes
precedence over SSH configuration; see the
[OpenSSH configuration documentation](https://man.openbsd.org/ssh_config.5).

If SSH reports `Permission denied (publickey)`, verify that the selected account
permits SSH login with your key. For a cluster using a non-root account, supply
that `user@target` and, when needed, `--identity FILE`. Syncing uses the selected
account's home; it does not create accounts or authorize keys.

The script requires Bash 3.2 or later, Git, SSH and rsync locally, plus rsync and
a POSIX shell on the login node. It works from another directory when invoked
using its path inside the clone.

```bash
./sync-labs.sh --dry-run user@login.example.com
./sync-labs.sh --port 2222 --identity ~/.ssh/id_ed25519 user@login.example.com
./sync-labs.sh --dest training-courses user@login.example.com
./sync-labs.sh --help
```

| Option | Behavior |
| --- | --- |
| `--dry-run` | Preview without changing the remote destination. |
| `--dest NAME` | Direct subfolder of remote home; default `courses`. |
| `--port PORT` | Override the SSH port with a value from 1 through 65535. |
| `--identity FILE` | Supply an SSH private-key file. |
| `-h`, `--help` | Show usage and examples. |
| `--` | End option parsing before the target. |

`NAME` starts with a letter or digit; remaining characters can also be dots,
underscores or hyphens.

The default destination preserves the source course names:

```text
~/courses/
├── index.html
├── gpu-fundamentals/
│   ├── labs/
│   ├── slurm/
│   ├── tools/
│   ├── reference/
│   ├── README.md
│   └── requirements.txt
├── gpu-optimizations/
├── llm-training/
├── llm-inference/
└── custom-cuda-kernels/
```

All courses retain their supporting source files, including applicable build
metadata and instructions. New course folders containing `reference/course.json`
and `labs/` are discovered automatically. The transfer includes current tracked
files, uncommitted edits and new non-ignored files. Git ignore rules exclude
untracked environments, builds, caches, results and profiler outputs; tracked
files remain included even if an ignore pattern matches them. Git internals are
not copied. Safe relative symlinks are preserved; links outside the transferred
tree are skipped, and course source directories must not be symlinks.

Repeat the same command after local edits. One rsync transfer handles the entire
catalog, comparing file size and modification time to skip unchanged contents.
Edits that deliberately preserve both attributes are not detected by this quick
check. Matching remote files are overwritten from the local source, including
newer remote edits. Remote-only experiments and results remain, as do remote
copies of files deleted locally. The destination itself must not be a symlink.

Sync is idempotent: once a run completes, repeating it with unchanged source and
destination state transfers no file contents and leaves destination contents,
permissions and modification times unchanged. A permission-only local edit is
applied without retransferring file contents. After an interrupted transfer,
rerun the same command to finish syncing; completed files and remote-only
results are retained.

After a successful sync, use your SSH terminal to enter a course:

```bash
cd ~/courses/gpu-fundamentals
ls labs/
```

Run that course's documented `sbatch` commands from its course root. The remote
home directory must be accessible to the compute nodes. Set up dependencies on
the cluster using the course instructions; syncing does not install packages or
submit jobs. Ctrl+C stops the local sync; files already transferred remain.
Finish syncing before starting jobs, and rerun after an interrupted transfer
before using the updated files.

## Open a course

| Course | Self-contained course | Estimated guided hours |
| --- | --- | --- |
| GPU Fundamentals | [Read the course](gpu-fundamentals/index.html) | 21 |
| GPU Performance Optimization | [Read the course](gpu-optimizations/index.html) | 36 |
| LLM Training | [Read the course](llm-training/index.html) | 48 |
| LLM Inference | [Read the course](llm-inference/index.html) | 47 |
| Custom CUDA Kernels for GPU Optimization | [Read the course](custom-cuda-kernels/index.html) | 36 |

Lesson 1 of Fundamentals starts with an H100 SXM overview before enlarging one SM. Across the catalog, lessons explain concepts; the linked labs define their setup, supplied code and result checks where students use them.

Each self-contained page uses a light digital-textbook layout: a persistent
side-panel TOC, a wide responsive content frame, soft blue and mint callouts,
and complete source listings. Diagrams sit directly beside their lesson or lab
explanations and fit the available width without horizontal panning. The sidebar
provides course navigation; each figure retains its caption and accessible SVG
title and description without duplicate transcript controls. The banner
contains only the main topic and a brief estimated guided-hours label.

An **All courses** link and **Switch course** disclosure sit at the top of each
sidebar. The switcher identifies the current course and links directly to the
other four. These relative links work on GitHub Pages and in a local checkout.
When downloading individual HTML files, keep the catalog and sibling directory
layout to use cross-course links; each course's lessons, styles, diagrams and
license remain readable on their own.

## Website publication

**For course maintainers.**

The repository welcome page links to this catalog. For initial publication,
merge the reviewed website files into `main`, then open the repository's
**Settings → Pages**. Select **Deploy from a branch**, branch **main**, folder
**/(root)**, and save. Use HTTPS for the published site. The root `.nojekyll`
file lets GitHub serve committed static files without Jekyll processing.
This makes other eligible repository files available under the same site.
The site has no custom deployment workflow, framework or external font
dependency. GitHub still
runs its managed Pages deployment when the publishing branch changes.

`tools/build_course_html.py` generates the catalog and individual pages. Titles,
guided hours and lab counts come from each course's `reference/course.json`;
its stable `slug` identifies the course even if a downloaded folder is renamed.
Catalog introductions and learning outcomes live in the renderer, while
`tools/catalog.css` owns the catalog's embedded styles. Edit these sources
instead of generated HTML. A selected-course build also refreshes the catalog;
rebuild all five pages when shared metadata, navigation, styles or licensing
changes. `--check` always checks the catalog as well as the selected courses.

Run the offline validation commands below before committing generated HTML.
Once Pages is enabled, reviewed changes to `main` publish those committed files.
Confirm the deployment succeeded and the root, catalog and five course URLs
serve the intended revision before declaring a publication complete. See
[GitHub's publishing-source documentation](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site)
for the branch deployment settings.

To preview from a local checkout, serve the repository root with
`python3 -m http.server --bind 127.0.0.1` and open `/courses/` on that server.

## Ownership and license

© 2026 Nebius B.V. These courses are provided free of charge for learning and
education under the repository's [Apache License 2.0](../LICENSE). This notice
describes their educational purpose; it does not restrict the uses permitted
by that license, including commercial reuse. Third-party materials retain
their respective licenses.

The catalog and every course contain a compact attribution footer and the full,
unchanged repository license inside **License and notices**, so the license
travels with each saved HTML file.

## Educational approach

Every lesson has a concise title naming its central subject and uses four
sections: **Objective**, **How it works**, **Practice labs**, then **Mental model**.
The objective states the capability to learn. How it works starts with a
plain-English definition, integrates useful prerequisite connections and
purpose, and follows the causal steps through to their consequences. It contains
at least one accessible diagram explaining the core concept. Unfamiliar terms
such as Parallel Thread Execution (PTX) are expanded in context; common CPU/GPU
names do not need repeated expansions. The closing mental model summarizes
concepts already explained.

Each course preserves its substantial introductory explanation of the subject,
workflow and vocabulary, plus a small worked example, inside How it works.
Training Lab 32 and Inference Lab 35 teach learning versus fixed-parameter
prediction on CPU or an explicitly selected H100 without model downloads.
The linked guides own H100 scope, integrated **Practice** examples and commands,
trade-offs, evidence interpretation, failure analysis and review. Read the
explanation, use its summary to check the relationships, then follow the lab.

Longer explanations use meaningful subheadings. Examples retain assumptions,
intermediate reasoning and limits. The renderer embeds diagrams within the
explanation and validators check every lesson's order, diagram coverage and
complete source-to-HTML narrative parity.

**For course maintainers:** Technical vocabulary follows NVIDIA documentation for CUDA, GPU architecture,
profiling, communication and NVIDIA libraries. Framework-specific concepts use
the owning framework's official names. Define each term in context and verify
its meaning against the relevant source before revising lessons or labs. Plain
explanations and teaching models remain useful, but must not be presented as
formal GPU mechanisms. For timing, name the CPU timer or CUDA events, the
operations included and how completion is established. Distinguish data in GPU
memory from thread blocks resident on an SM, and document tool-specific metric
formulas and aggregation. Review connected glossary entries and diagram labels
together; a keyword replacement or passing validator cannot prove terminology
accuracy.

**For course maintainers:** The same sequence applies when a new topic starts inside a lesson, practical
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

Before running a lab, explain its operation, dependencies, timer, included work and
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
optional extensions, state units, included operations and completion checks, and use code formatting
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

**For course maintainers.**

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

**For course maintainers.**

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
that labels fit the rendered layout. Compact overview layouts keep labels
readable when the article narrows. Arrows identify order, transfer or dependence;
captions explain which relationship to follow. Comparisons have no causal
arrows, timelines state their time direction, and decisions label their branches.

Keep multiline labels vertically centered, wrap long wording without removing
its meaning, and enlarge cards before reducing font size. Leave clear space
around connectors and their captions. The shared SVG font stack is Arial,
Helvetica, then sans-serif. Review all affected SVGs with the embedded course
styles at wide and narrow widths, including an alternate-font fit check.
Asset rendering does not replace the separate full-page browser review.
