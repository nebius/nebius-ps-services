# Portable Reading, Downloads and Builds

Read this when creating or revising a full course build or companion downloads.

## Delivery responsibilities

Keep canonical prose, diagrams, CSS, teaching images and complete escaped source
listings embedded for offline reading. Large result archives, datasets and other
companion downloads remain ordinary links; do not encode ZIPs into HTML or add
JavaScript just to download them. A separately saved HTML remains readable;
downloads and shared guides require the companion tree or the website.

Name files for their contents. For experiment results prefer
`<course>-lab-results.zip`, with one useful combined archive and descriptive
member directories. Preserve source bytes and provenance; do not manufacture
results, recursively bundle directories or include private outputs. A worksheet
or dataset is not a lab result. Omit downloads when the course has none.

Keep labels separate from their links, using existing heading and style rules.
For the practical-course pattern use Practical labs, then Download results,
then Download all lab results: followed by the descriptive archive link. Use
Setup the lab environment: with a shared guide link when appropriate. Do not
add duplicate introductory routes or repeat complete file lists per lab.

Executable runtime files come from the documented checkout or synchronization
workflow. Do not add a redundant lab-kit archive when this already delivers the
complete sources and requirements. A standalone course may need a kit when that
is its actual delivery method. Keep reading, downloads and execution distinct.

## Reusable build scaffold

Copy the workspace template into a course-owned project. Its executable
`build-courses.sh` invokes `tools/build_course_html.py`, then `--check`, from any
working directory. Help needs no Python. Neither command installs dependencies,
runs learner code, contacts external services or publishes.

Implement `tools/course_adapter.py` using the project's established renderer.
Register courses explicitly; implement `plan_outputs(project_root)` returning a
nonempty dictionary of publication-root-relative output paths to complete bytes.
Use the copied `tools/assets` shell, CSS and fragments. The adapter owns complete
Markdown rendering, TOC/identity generation, full prose/source parity, declared
link rewrites and rejection of unsupported syntax or unresolved authoring slots.
Do not implement a new Markdown dialect. The supplied adapter deliberately fails
until configured; it is not a finished renderer. Declare renderer dependencies
in the project's existing dependency mechanism, without implicit installation.

Set `PUBLICATION_ROOT`, `INVENTORY`, `MAX_FILE_BYTES` and `MAX_SITE_BYTES` in the
adapter. Use `git` inventory for a full repository root (requires Git), or
`directory` for a dedicated publication tree (counts every file). No size limit
is assumed without a selected hosting target. Both inventory modes reject
symlinks/special files; Git submodules need an explicit complete inventory design
and currently fail. Missing inventory never becomes a partial-size success.

`publication.results_zip(root, members, max_file_bytes=...)` takes an explicit
member-name to source-path mapping. Members are sorted with fixed timestamps and
preserved executable permissions. The author owns public-safe input selection.
Archive member names belong to a virtual namespace: validate them lexically,
independently of similarly named files or symlinks in the source tree. Source
paths still require regular files and symlink-free containment. Validate the
publication root even for an empty inventory.
No code executes from archive members. Shared helpers validate output paths,
preflight all candidate sizes, compare bytes in read-only checks and atomically
replace individual changed files. Unchanged outputs are not rewritten. A later
filesystem failure can leave earlier files refreshed: repair and rerun; the
build is not a multi-file transaction.

The copied project has no runtime dependency on the installed skill. The skill
checker remains a separate authoring check. Scaffold regression fixtures cover
reading-only, technical download and shared-guide collection shapes; these prove
orchestration, not universal Markdown rendering or teaching quality.

## Exact local-link allowlist

The checker accepts optional `--links-manifest` and `--publication-root` helper
arguments. The links manifest is a course-contained JSON array of exact local
href strings, for example `reference/example-lab-results.zip` and
`../lab-guide.html#setup`. The publication root defaults to the course root;
explicitly select the common parent for shared guides. Validate all declarations,
existing targets, HTML fragments and symlink-free containment. Undeclared local
links, active-resource attributes and unsafe schemes remain rejected. This is
separate from the embedded-source allowlist; neither list authorizes arbitrary
file inclusion. These helper arguments are not public skill flags.

## Hosting budgets

For GitHub branch-root publication, count the entire eligible repository tree,
including original evidence and external ZIPs, overlaying planned outputs once.
Git history is not the published tree; existing oversized historical blobs need
a separate review. For a deployment artifact, measure that artifact's full tree.

GitHub blocks regular Git files larger than **100 MiB (104,857,600 bytes)** and
limits published Pages sites to **1 GB**. Use a conservative **1,000,000,000-byte**
site budget. Do not silently interpret 100 MB as 100 MiB. Report largest file,
total and remaining capacity. Fail before generated-file replacement when a cap
is exceeded; do not remove teaching, truncate evidence or upload automatically.
Size checks do not prove a successful deployment or future growth capacity.

Sources: [GitHub file limits](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github),
[GitHub Pages limits](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits).
