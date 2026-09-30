# Maintaining the courses

For course maintainers. Learners start with the
[course website](https://nebius.github.io/nebius-ps-services/courses/index.html)
or the [local catalog](../index.html).

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

`tools/build_course_html.py` generates the shared `lab-guide.html` from `README.md`,
the catalog and individual pages. Titles,
guided hours and lab counts come from each course's `reference/course.json`;
its stable `slug` identifies the course even if a downloaded folder is renamed.
Catalog introductions and learning outcomes live in the renderer, while
`tools/catalog.css` owns the catalog's embedded styles. Edit these sources
instead of generated HTML. A selected-course build also refreshes the shared guide and catalog;
rebuild all seven pages when shared metadata, navigation, styles or licensing
changes. `--check` always checks the shared guide and catalog as well as the selected courses.

The reader-facing order is Soperator, Lab Guide, GPU Fundamentals, GPU Performance
Optimization, LLM Training, LLM Inference, Custom CUDA Kernels, then Advanced Labs.
`course_builder.config.CATALOG_GROUPS` owns the presentation groups and derives
the eight-entry sequence used by cards, the learning path, and every course/guide
menu. `COURSES` remains the seven real course packages; never add the README-derived
guide to that build registry. Its generated title is Lab Guide, and it has no
invented guided hours. Each menu has one current-page marker and a separate
catalog backlink. Display order does not change course prerequisites.

Keep every Course overview to three short paragraphs in its canonical
`COURSE.md`: purpose and outcomes, audience and prerequisites, then practical
scope and the next step. Retain brief course-specific hardware and safety limits.
Merge shared setup, submission, logs and workload-profile guidance into the
root README's existing Lab Guide sections. Preserve unique interpretation,
qualification and completion guidance in the embedded course README or owning
lab before removing an overview repetition; authoring-only mission and syllabus
files do not provide a learner-facing destination.

The shared browsing instructions use `nebius-cxcli grafana show` and
`nebius-cxcli soperator profiling show` for current forwarding commands,
password-retrieval commands and browser URLs. Keep installation and routine
access separate. Do not duplicate service names, Secret bindings or port mappings
in the guide; retain the explicit-target, loopback and paired Nsight HTTP/TURN
instructions and the installation-selected Nsight username.

Navigation validation lives in the canonical `tools/validate_course_template.py`
and the text-only `tools/validate_text_course.py`. After changing the shared
template, run `python3 -B tools/sync_course_tools.py`; do not edit generated
standalone validator copies individually.

After editing lesson text, embedded guides, lab sources, diagrams or styles,
run `./build-courses.sh` from `courses`, then refresh your browser. From
the repository root, use `./courses/build-courses.sh`; invoking the script by
its path also works from other directories. The wrapper requires `python3`,
rebuilds the shared guide, catalog and every registered course, and then checks source-to-HTML
parity. Use `./build-courses.sh --help` for usage.

The wrapper separates the verification phase with a cyan **Checking...**
heading. Current pages appear in green; stale or missing pages appear in red,
and checking stops at the first failure. Direct Python `--check` runs use the
same status colors. Redirected streams, `TERM=dumb` or any defined `NO_COLOR`
use plain text.

Unchanged inputs produce identical HTML content across runs, although file
modification times may change. Each document keeps its authored wording:
renaming text in `COURSE.md` does not rename matching text in `README.md` or
`SYLLABUS.md`. Update each intended source before rebuilding. If a build fails,
earlier pages may already have been refreshed; fix the reported error and rerun.
The wrapper does not install dependencies, run the full course validators,
synchronize remote files or publish the website.

Run the offline validation commands below before committing generated HTML.
Once Pages is enabled, reviewed changes to `main` publish those committed files.
Confirm the deployment succeeded and the root, catalog, shared guide and seven course URLs
serve the intended revision before declaring a publication complete. See
[GitHub's publishing-source documentation](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site)
for the branch deployment settings.

To preview from a local checkout, serve the repository root with
`python3 -m http.server --bind 127.0.0.1` and open `/courses/` on that server.

## Educational approach

All seven learner pages use `tools/course.css`: the course title is H1, major
sections are H2, lesson fields and guide titles are H3, and nested concepts
are H4. Keep **Glossary** in its own top-level section with a definition list.
Use numbered **Official references** and one complete bullet per **Where to Go
Next** direction, retaining its explanation, question, scope and links. In
`NEXT-STEPS.md`, write `- **Topic**` and indent every following paragraph by two
spaces; the renderer keeps the whole topic inside that bullet.

`MISSION.md` and `SYLLABUS.md` remain maintainer authoring inputs for scope and
identity checks. They are not embedded or linked in learner pages. Keep useful
audience and scope context in `COURSE.md` and readiness/completion guidance in
the course README. Do not add duplicate displayed mission or syllabus sections.

Every lesson has a concise title naming its central subject. The five conceptual
GPU courses use **Objective**, **How it works**, **Practice** and **Mental model**.
Optional **References** come last. The text-only Soperator course uses **Practice** for reading checks with answers
and intentionally has no diagrams, labs or runtime assets. The advanced
communication course is labs-only and retains complete practical guides.
All lesson fields render as semantic subheadings in the same visual style.
The objective states the capability to learn. How it works starts with a
plain-English definition, integrates useful prerequisite connections and
purpose, and follows the causal steps through to their consequences. It contains
at least one accessible diagram explaining the core concept in GPU courses. Unfamiliar terms
such as Parallel Thread Execution (PTX) are expanded in context; common CPU/GPU
names do not need repeated expansions. The closing mental model summarizes
concepts already explained.

Every course and its table of contents end with exactly one **Where to Go Next**,
then one **Glossary**, then final **Official references** before the license
footer. Do not add Glossary or Where to Go Next sections to lessons or guides.
Keep distinct optional learning options in `NEXT-STEPS.md`; repeated next-lesson
pointers may be removed when the ordered TOC and destination objective preserve
them.

Keep glossary abbreviations and terms in `GLOSSARY.md` in A–Z order by displayed
term, ignoring case and Markdown formatting. Merge terms from lessons and
performance-tool guides, preserving distinct definitions and abbreviation
expansions. Write each entry as `- **Term** — Definition`; the builder renders a
semantic definition list. Lesson References, when included, follow Mental model
and end the lesson.

Each GPU course preserves its substantial introductory explanation of the subject,
workflow and vocabulary, plus a small worked example, inside How it works.
Training Lab 32 and Inference Lab 35 teach learning versus fixed-parameter
prediction on CPU or an explicitly selected H100 without model downloads.
The linked guides own H100 scope, local worked explanations, concise **Practice** commands,
trade-offs, evidence interpretation, failure analysis and review. Read the
explanation, use its summary to check the relationships, then follow the lab.

Longer explanations use meaningful subheadings. Examples retain assumptions,
intermediate reasoning and limits. The renderer embeds diagrams within the
explanation and GPU validators check every lesson's order, diagram coverage and
complete source-to-HTML narrative parity. The separate text-profile validator
checks the same narrative and navigation integrity without diagram requirements.

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

**For course maintainers:** Keep each lab self-contained: a concise purpose,
needed concepts and worked reasoning, then short Practice instructions and
supported commands. Explain useful prerequisites locally instead of sending
learners to reading lists. Each lesson’s Practice distinguishes previews
from full execution. The shared README setup covers cluster deployment, access and course sync, shared profiling
tools, private Grafana and readiness. Experiments belong to their owning labs.

Numerical acceptance checks reject non-finite reference and candidate values
before applying tolerances or aggregating errors. Distributed experiments
keep every rank participating through the shared verdict. The theory and
lab guide explain both the comparison and its limits; successful samples
do not establish whole-model equivalence or target-runtime qualification.

Before running a lab, explain its operation, dependencies, timer, included work and
numerical acceptance check in your own words. Reused profiler skills transfer
to a new workload, but its measured
bottleneck does not: collect evidence from the actual baseline and candidate.

Each of the 110 executable labs has an authored introduction followed by seven practical
sections: **Before you start**, **Concepts and code path**, **Practice**,
**Check your results**, **Investigate the behavior**, **If something goes wrong**,
and **Takeaways and next step**. These explain the actual code structure,
supported commands, result fields, numerical gates, and meaningful extensions.
Exact lab titles link from their lessons to the practical section. Each GPU course
also includes a link to the shared setup and run guide.

Throughout the catalog, explanations distinguish supplied experiments from
optional extensions, state units, included operations and completion checks, and use code formatting
for exact commands, options and implementation names.

Each lesson’s Objective and Practice provide the competency and activity;
the course guide retains readiness checkpoints and completion criteria. Read
lessons in their numbered order: lab numbers identify files and are not a separate execution
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

The GPU courses report four independent evidence lanes:

1. Source and static validation.
2. Installed dependency compatibility.
3. Runtime activation of CUDA, compilers, models, or engines.
4. Live execution on the declared H100/Slurm target.

Local or static checks never prove H100 performance. Raw runtime artifacts can
contain environment details and remain private; only reviewed summaries belong
in public course material.

Published summary CSVs use CRLF records and have manifest-bound byte hashes.
The root `.gitattributes` preserves these bytes and recognizes CRLF during
whitespace checks. Do not normalize their line endings after publication.

## Offline validation

**For course maintainers.**

```bash
./build-courses.sh
python3 tools/validate_all_courses.py
python3 -m pytest -q
```

GPU and engine workloads are intentionally excluded from offline validation.
The Soperator text-only validator checks its exact metadata, lesson and syllabus
identities, complete generated prose, public references and navigation without
requiring lab folders or runtime dependencies. Synchronization discovers a
course through `reference/course.json` and `COURSE.md`, so the text-only package
travels with the catalog and six practical GPU packages.

Navigation checks cover lesson order, lab identities and local destinations.
Each course's `PUBLICATION-REVIEW.md` records the latest reviewed HTML identity,
checks and evidence limits. The [course standardization review](course-standardization-validation.md)
and [format review](course-format-validation.md) retain historical preservation,
teaching-flow and layout evidence for their recorded artifacts.

Conceptual Practice sections start with the action for that lesson, explicitly identifying previews and revisits, followed by every assigned guide exactly once in the intended reading order. The builder and standalone validators preserve that context and reject missing, duplicated or mismatched assignments. Keep the text-only reading course and labs-only advanced route in their declared profiles.
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

## Profiling assets and source validation

**For course maintainers.**

`tools/submit_lab.py`, `fabric_guard.py`, `install_fabric_tools.py`, `course_evidence.py`, `inspect_results.py`, `publish_results.py`, `profile_lab.py`, `server_capture.py`, `course_setup.py`, `verify_monitoring.py`, and `readiness.py` are the canonical shared helpers; course-local copies make each lab kit independent. The inspector prints completed measurements and their exact artifact paths for a submitted job. Throughput units identify the measured quantity: tokens/s, samples/s or requests/s in the inspector, lab guides and Grafana panels. `tools/build_observability.py` renders the 116 JSON dashboards (110 labs and six setup dashboards) from explicit measurement recipes. `tools/sync_course_tools.py` maintains support-file parity before rebuilding HTML.

The shared README installs private Grafana and Pushgateway with
`nebius-cxcli grafana install --config ./config.yaml --target CLUSTER_TARGET --pushgateway`,
then discovers connections and imports each course dashboard folder once using
`nebius-cxcli grafana import`. The course helper reads the current cxcli v2
manifest, accepted artifact inventory and live owned services; it writes only
private connection files and never installs resources or changes catalogs.
Verification uses the native `cxcli-pushgateway` scrape job and local metrics.
Both helpers require explicit kubeconfig and context. Old receipts and installer
flags are unsupported; use a fresh discovery directory after installation changes.
The README embeds `docs/grafana.png`; the shared guide embeds the same PNG bytes
with responsive sizing and alternative text. Experiment guides retain their
assigned dashboards, publication and inspection steps. The shared HTML guide is
generated from README.md; rebuild it and all course pages after source changes.
Keep its setup, run and dashboard-browsing sections concise. Specialized runtime
commands live in owning course/lab guides; the renderer resolves shared-guide
links to those generated sections. sync-labs.sh remains a source-transfer and
SSH handoff, with dependency installation and private configuration transfer separate.

Every executable recipe declares Nsight Systems applicability, the process to capture, a report view, and whether GPU telemetry is relevant. The 99 applicable labs capture a GPU worker, distributed rank, vendor process, or owned server; 11 CPU/model, artifact, health, protocol and RDMA wire-measurement exceptions explain their evidence limits. Six CPU/protocol dashboards omit GPU telemetry. Standalone validators reject mismatched capture commands, dashboard identities, query metrics and units. See [the all-lab audit](lab-profiling-audit.md).

Completed private JSON artifacts are authoritative. The publisher validates two selected slots, serializes updates by workspace/course/lab/profile, and confirms the same generation through VictoriaMetrics. Selected-result panels use VictoriaMetrics wall-clock evaluation so they remain visible when the time picker ends at the experiment end. GPU and node telemetry follow the chosen historical interval. Pushgateway is replaceable cache state; retain the local selection directory and republish artifacts if cache state is lost. cxcli owns Pushgateway persistence and lifecycle. A persistent learner workspace has one authoritative checkout and selection directory; do not publish the same workspace from independent clones.

```bash
python3 -m venv .venv-validation
.venv-validation/bin/pip install -r requirements-validation.txt
.venv-validation/bin/python -m pytest tests -q -p no:cacheprovider
python3 tools/sync_course_tools.py --check
python3 tools/build_observability.py --check
python3 tools/build_course_html.py
```

Source validation does not establish live GPU or cluster readiness. Each new target must pass the shared README worker/container, single-scrape-owner, private-access and ingestion checks before its experiments can qualify that environment. The completed prepared-H200 campaign covers all 110 labs in both profiles; it does not qualify a new installation or the documented H100 target.

See [profiling validation](profiling-validation.md) for source evidence, completed campaign coverage and remaining target qualification limits.
