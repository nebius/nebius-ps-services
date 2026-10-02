# Building and publishing the course website

The site remains static HTML generated with Python's standard library. There is
no JavaScript application, package installation or server-side runtime. All seven
courses keep their existing content, layout and styling.

## Authoring architecture

```text
courses/
├── build-courses.sh                 # rebuild and check the whole catalog
├── tools/
│   ├── build_course_html.py         # stable CLI entry point
│   ├── course.css                  # shared course styles, embedded at build time
│   ├── catalog.css                 # catalog styles, embedded at build time
│   ├── course_archives.py          # deterministic ZIPs and atomic file writes
│   └── course_builder/
│       ├── config.py               # course registry and presentation constants
│       ├── metadata.py             # canonical parsing and inventory
│       ├── markdown.py             # supported Markdown and explicit link policy
│       ├── visuals.py              # diagrams and shared passive SVG validation
│       ├── content.py              # reusable lesson, lab and guide markup
│       ├── downloads.py            # ordinary external download links
│       ├── shell.py                # common page head, navigation and footer
│       ├── pages.py                # catalog, shared guide and course profiles
│       └── build.py                # complete output plan, checks and replacement
└── COURSE/
    ├── COURSE.md                   # canonical teaching
    ├── index.html                  # generated standalone reading page
    └── reference/
        ├── diagrams/               # authored SVGs, embedded in HTML
        ├── grafana/                # original dashboard JSON
        ├── lab-results/            # original manifest-owned public evidence
        └── COURSE-lab-results.zip
```

Keep authoring CSS in the shared files and course-owned diagrams with their
course. The generated pages embed CSS, teaching images, diagrams and complete
source listings, so saving one HTML file preserves offline reading. Downloading
ZIPs requires the companion files or the website. Soperator remains text-only
and has no lab downloads.

## Diagram placement

Place each diagram's Markdown image marker immediately after the paragraph,
list, table or worked example that explains it. A lesson diagram stays inside
**How it works**; a lab diagram stays inside its manifest-declared guide section.
The manifest and visual plan declare ownership, not a section-end insertion point.

- Detailed lesson SVG: `![Exact registered title](reference/diagrams/name.svg)`.
- Detailed lab SVG: `![Exact registered title](../diagrams/name.svg)`.
- Generated overview: `![Exact registered title](#diagram-N-title-slug)`, using
  its existing figure ID from the ordered visual plan.
- Tools workflow: `![A measured optimization loop](#tools-measurement-loop)`.

The builder resolves only registered figures in their owning section, checks
the title and consumes each marker exactly once. Missing, duplicate, unknown or
wrong-section markers fail the build. It never appends unplaced figures at the
end of a section. Secondary mentions use ordinary links to the primary figure.
Review the surrounding prose and diagram together; source order alone cannot
establish topic relevance.

## Course downloads

Each practical course has a **Download results** subsection under **Practical labs**:

- **Download all lab results:** followed on a new line by the link
  **Grafana dashboards, Small and Large results**.
- **Setup the lab environment:** followed on a new line by **Lab setup guide**,
  which opens the shared `../lab-guide.html` page.

These two links replace the repeated setup/run/online-guide paragraph and
introductory dashboard reminder. Lab-specific dashboard pointers remain beside
their owning lab.

The sole download is the results ZIP. It contains all dashboards, including
environment readiness, and both workload profiles:

```text
COURSE-lab-results.zip
├── grafana-dashboards/*.json
├── small/LAB/{manifest.json,summary.csv,results and screenshots}
└── large/LAB/{manifest.json,summary.csv,results and screenshots}
```

There are no nested ZIPs. Canonical dashboard and evidence files stay in place.
The manifest and every artifact retain their original bytes and checksums.
Individual dashboard anchors and lab references point to the course download
group instead of repeating long file lists. There is no lab-kit ZIP. Original
programs, launchers, tools and dependency files remain in each course folder;
`sync-labs.sh` copies them to the cluster with their executable permissions.
Complete source listings remain available in the HTML for reading.

The builder and the source-owned `skills/run-labs/scripts/evidence.py` exporter
use the same archive assembler. Export retains its existing course lock,
identity/checksum checks and journaled evidence replacement; it does not run the
HTML builder itself. Regenerate pages and results archives after source changes.

## Build and validation

The build requires Python 3 and Git.

Run from `courses/`. The wrapper builds HTML and the six results ZIPs, then
checks both against their sources:

```bash
./build-courses.sh
python3 -B tools/validate_all_courses.py
python3 -B tools/sync_course_tools.py --check
```

For selected courses, use `python3 -B tools/build_course_html.py COURSE ...`.
The catalog and shared guide are always included. `--check` writes nothing and
checks both HTML and ZIP bytes against canonical inputs.

The README is the single source for `lab-guide.html`. Its **Read this guide
online** link sits immediately under **How to set up the lab**, and its final
paragraph supplies the README attribution. The guide renderer omits that exact
standalone link and final attribution from the article, preserving the shared
instructions and using the standard HTML license footer once. Keep build and
publication-budget details in this maintainer guide.

The builder renders and validates every selected output before replacing any
file. Source, checksum, SVG and Markdown errors therefore leave the previous
outputs intact. Writes are atomic per file; an I/O failure during replacement
can leave a partially refreshed output set. Repair the I/O problem and rerun.
ZIP timestamps, ordering, compression and permissions are deterministic.

Only manifest-owned JSON, CSV and PNG evidence enters the results ZIP.
Missing assets, symlinks, path traversal, duplicate evidence and checksum or
identity mismatches fail. The canonical archive name is `<slug>-lab-results.zip`;
rebuilding never retires that archive or deletes the original results tree.

The supported Markdown subset includes headings, paragraphs, flat lists,
tables, fenced code, inline code, bold and declared links. Nested lists and
single emphasis fail instead of silently losing structure. Intentional
plain-text links are explicitly scoped to their owning source in `markdown.py`;
new unknown destinations fail. All authored SVGs share one passive policy.

## GitHub Pages size and publication

The repository publishes from `main` at `/` with `.nojekyll`. Commit generated
HTML and the results ZIPs with their source changes before the reviewed merge. The
builder does not commit, push, merge or publish.

External ZIPs reduce HTML weight but still count toward the hosted site size.
Count eligible files across the entire repository, including original evidence,
ZIPs and other projects; exclude Git history and ignored local environments.
GitHub documents a [1 GB maximum published site size](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits)
and [100 MiB maximum Git file size](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github).
The build and read-only check enforce 104,857,600 bytes per file and
1,000,000,000 bytes for the full repository publication candidate before replacing
outputs. Git must be available to inventory tracked and nonignored untracked files;
planned output bytes replace existing sizes once. Missing inventory, symlinks and
submodules fail instead of silently undercounting. The report prints total size,
largest file and remaining capacity in decimal MB (1 MB = 1,000,000 bytes), with
two decimal places and thousands separators. The displayed file and site limits
are 104.86 MB and 1,000.00 MB; checks still compare exact byte counts. Overflow
diagnostics show the excess with six decimals so even a one-byte overflow is
visible. Build and check failures, including file, site and archive limits, are
red on terminal stderr. Redirected streams, `TERM=dumb` and any defined
`NO_COLOR` remain plain text. Limits do not trigger automatic splitting or
content deletion. Archive export also enforces the per-file limit.
Recheck both as evidence grows. Local size and browser validation do not establish
successful deployment; verify the actual Pages build and deployed revision after
merge.
