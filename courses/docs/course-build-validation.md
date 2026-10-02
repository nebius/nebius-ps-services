# Course build and browser validation

## Scope and outcome — 2026-10-02

Reviewed `build-courses.sh`, `build_course_html.py`, every module in
`course_builder`, and the shared archive/publication helpers. The current
registry contains eight courses, including GPU Performance Tools, plus the
catalog and README-derived Lab Guide. Six practical courses also produce
results ZIPs. The build reads canonical sources, validates all planned outputs
and publication limits, then replaces individual files atomically. Its final
`--check` is read-only.

All ten HTML pages and six archives rebuilt successfully and retain exactly
their task-start bytes. No presentation repair was needed. Shared styles,
headings, lesson/lab TOCs, appendix order, complete embedded listings and profile
exceptions passed the native checks. Existing teaching, native jobs and published
evidence remain unchanged.

## Repairs

- Reject duplicate lesson fields and prose outside a field instead of silently
  dropping text. Treat headings and field labels within fenced examples as
  literal source, including a fence that opens on the field-label line.
- Apply the existing CSS resource policy to both reading-course validators;
  reject external SVG references, automatic refresh, tracking attributes,
  forms and legacy background/resource attributes.
- Update maintainer documentation for eight courses, nine reading resources,
  ten HTML outputs and the current native-job ownership.

The 26 new regression cases cover these boundaries. Twenty-three cases exposed
missing protections or the intermediate inline-fence regression before their
repairs; positive controls preserve existing supported behavior.

## Source and static evidence

- **484 focused tests pass**, covering the shell wrapper, renderer, Markdown,
  full prose/source parity, course metadata, all profiles, layout/TOCs,
  contextual diagrams, archives, publication budgets and the new regressions.
- `./build-courses.sh` passes its build and complete HTML/ZIP freshness phases.
- `python3 -B tools/validate_all_courses.py` passes all six practical and two
  reading validators; `sync_course_tools.py --check` passes.
- Bash syntax, ShellCheck, scoped Ruff, configured Markdown lint and whitespace
  checks pass. Independent parsing confirms balanced structure in all ten HTML
  documents. Final read-only code/security review found no remaining concrete
  defect in the repaired paths.
- All 16 generated output hashes match the task-start snapshot. The six archives
  and original evidence retain their bytes. No dependencies were installed.

## Browser and visual evidence

**30 Playwright Test cases pass:** ten complete documents at 1440×1000,
390×1000 and 320×1000. The installed runner is Playwright 1.57.0; the actual
browser is Chrome 154.0.8037.93, headless, with isolated contexts and page
JavaScript disabled. Browser processes and contexts were closed after execution;
no personal profile was used.

Assertions cover the ordered nine-resource switcher and current identity,
working keyboard menu/TOC/skip navigation, exact lesson/lab TOC titles and
fragments, unique IDs, closing appendices, loaded inline images, source-panel
expansion, keyboard horizontal scrolling within local scrollers, SVG containment
and transformed text bounds, and absence of page-level horizontal overflow.
Reflow also passes with both root and body text doubled. The pages make no
automatic HTTP(S) requests.

Visual inspection covered every page across the desktop/mobile captures, with
selected navigation, prose, figures, code panels and endings. The reviewed
captures retain consistent typography, spacing, wrapping and readable TOC
markers without clipping or overlap. This is a rendering review, not a new
semantic or technical review of every lesson and experiment.

Local evidence group `course-build-audit-l57_57x5` contains
`publication.spec.cjs`, `playwright.config.cjs`, `browser-complete.json`,
`artifact-hashes.json`, and `browser-complete/publication-PAGE-publication-WIDTH/`
with `top.png`, `navigation.png`, `content.png`, `figure.png` and `ending.png`
where applicable. Per-test identity attachments record each exact HTML digest,
browser and viewport. Final traces were disabled; logs and screenshots remain.

Earlier harness failures were resolved at the harness: native scroll positioning
avoids a Playwright stability wait; intentional supporting-guide short labels
are checked separately from exact lesson/lab identities; rotated SVG text uses
transformed browser bounds. These corrections did not change course HTML.

## Generic skill checker boundary

The installed `create-learning-course/scripts/check_course.py` was run on all
eight pages with explicit source/link manifests in a local fixture. It does
**not pass** the repository's existing presentation/profile variants:

- It rejects the inert `data:,` favicon and valid `small` footer elements; its
  unsupported-element stack handling then reports unbalanced/unclosed markup.
  Independent HTML balance and browser checks pass.
- It requires conceptual lessons, Practice and per-lesson diagrams for profiles
  that deliberately omit them: labs-only Advanced Labs, text-only Soperator and
  reference-only GPU Performance Tools.
- On Advanced Labs it labels numeric-start local SVG fragment references as
  external CSS resources. Those references target inline marker IDs; native
  passive-SVG validation and the browser's zero-network checks pass.

No embedded-source mismatch is reported across the 127 included listings. The
native course validators supply the profile-specific static gate; the generic
checker remains an explicit tooling limitation. It was not weakened or modified.

## Artifact identities

All HTML SHA-256 values below are unchanged by this audit.

| HTML artifact | SHA-256 |
| --- | --- |
| `index.html` | `a79d028275e67f879cc4ab030043e0443eee19ae06ccc7ab147e9cd46df0cbb4` |
| `lab-guide.html` | `2e46a300d93a6e83ac41c0bb05cf4255be591a55d577f7d73016f5cdc62216f9` |
| `soperator/index.html` | `4cade4b410df9b2f353c3c031cbcd6e60f84be957ba768f06aa04ca694d2a5a1` |
| `gpu-fundamentals/index.html` | `438d6646e2be39a81edb9a3254d685adb12a1a88cd91b34e483645720520fa21` |
| `gpu-performance-tools/index.html` | `f019f39c6ab42c1caff4bb57272bb9abcefaa1fc0ccd6a5218ac02563ec07b60` |
| `gpu-optimizations/index.html` | `324a75bacd5c3a3b54764689ea07d294e0293c1f0f2556a0a0a8ecd7b4350e97` |
| `llm-training/index.html` | `fbe476041507374b4853e8ad5050bc47d461d972511a0246f5b773acebff5e7c` |
| `llm-inference/index.html` | `ec32b8fd5f7e9e45de293644a96cdfd39f8b8b14f753df1435ac2739cd212524` |
| `custom-cuda-kernels/index.html` | `a2cf0c0db10e3c720546c320e6b01bec53ea1e3cf6214a218aebd01315e7fb54` |
| `advanced-gpu-communication/index.html` | `abbf94aa377c8b4e9edfb2de21e24235c42ca60e1db7d5fe180b47ec893b34ad` |

## Remaining evidence boundaries

This audit verifies local building, source/static contracts and browser
presentation. It does not establish installed GPU toolchains, live Slurm/GPU
execution, performance measurements or external deployment. Existing runtime
and target-qualification limitations in each publication review remain in force;
this record does not declare blanket publication readiness.
