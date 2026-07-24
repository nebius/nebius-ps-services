# Code Info

`code-info` summarizes local project folders or GitHub repositories in
copy/paste-ready Markdown. It is strictly read-only and does not change code or
project files.

## What It Does

- Extracts a concise description from package metadata or the primary README.
- Counts documented features from recognized README sections.
- Separates comparable code, test, documentation/configuration, overall, and
  per-language LOC.
- Counts source LOC per top-level component or package.
- Reports tracked repo size, `.git` size, and repository link when available.
- Counts test files, package/module markers, and source module files.
- Detects common CLI command paths and counts through three levels.
- Separates package-manager scripts from application commands.
- Counts project packages plus direct and statically selected/resolved
  dependencies from supported Python, Node.js, Go, and Rust manifests,
  lockfiles, or Go module requirements.
- Compares code LOC approximately with pinned Redis and SQLite source trees
  measured by the same method.
- Reports build or binary artifact sizes when present.
- Reads common coverage artifacts without running tests.
- Reads a GitHub repository archive when the repo is not cloned locally.
- Stops after reporting; follow-up code changes are a separate task.

## Architecture

```text
Target folder
  |
  v
scripts/code_info.py
  |-- metadata_analysis.py
  |-- cli_analysis.py
  |-- dependency_analysis.py
  `-- scan_common.py
  |
  v
Markdown metrics report
```

## Workflow

1. Run `scripts/code_info.py` against the current project folder, or use
   `--github-repo <owner>/<repo>` when the repository is not cloned locally.
2. Review repo-link sensitivity before sharing the report outside the project.
3. Paste the generated Markdown report as the answer.
4. Do not edit, format, build, test, install, generate coverage, or stage files
   while using this skill.

## Core Concepts

- Metrics are best-effort and evidence-backed.
- The script is read-only and does not run project code, tests, builds,
  installs, package managers, or generators. Only explicit remote GitHub mode
  makes a network request.
- Git inspections run with `GIT_OPTIONAL_LOCKS=0` to avoid refreshing or
  locking the Git index as a side effect.
- Remote GitHub analysis uses `GH_TOKEN`, `GITHUB_TOKEN`, or `gh auth token`
  when available, then downloads a tar archive into a temporary directory that
  is removed after the report. Token values are never printed or stored.
- Missing metrics stay missing; the skill does not create artifacts to fill
  gaps.
- Feature, CLI, and dependency counts are static best-effort evidence; missing
  or unsupported evidence remains unavailable instead of becoming zero.
- Famous-project baselines are pinned, source-linked, and offline at report
  time. LOC is size context, not a quality or complexity score.
- Coverage is reported only when a coverage artifact already exists.

## Files

- `SKILL.md`: runtime workflow and output rules.
- `scripts/code_info.py`: Markdown report orchestration and target handling.
- `scripts/metadata_analysis.py`: project description and documented-feature
  analysis.
- `scripts/cli_analysis.py`: static CLI hierarchy analysis.
- `scripts/dependency_analysis.py`: package, dependency, workspace, and
  benchmark analysis.
- `scripts/scan_common.py`: shared safe traversal and path-formatting helpers.
- `scripts/test_code_info.py`: temporary-fixture regression tests.
- `references/famous-project-loc.json`: versioned public LOC baselines.
- `agents/openai.yaml`: UI metadata.
