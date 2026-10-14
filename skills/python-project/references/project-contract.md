# Python Producer and Readiness Contract

## Entry Points

The internal producer is `scripts/python_project.py`:

```text
render --request <json> --output <private-candidate-directory>
validate --manifest <candidate-manifest.json>
bootstrap --target <repository-root> --bundle <private-bundle> --candidate-set <id>
```

Normal user requests remain natural language. New creation routes through
scaffold-project; standalone maintenance keeps its existing package manager.
Both skills allow implicit selection. Candidate/bootstrap scopes never route
back to creation. These internal actions introduce no new public skill flags.

## Closed Request Version 1

All fields are required; unknown fields and unsupported profiles fail:

| Field | Contract |
| --- | --- |
| schema_version | Integer 1 |
| candidate_set_id, component_id, materialization_unit_id | Safe lowercase identifiers |
| profile | python-package |
| component_root | Normalized relative directory; `.` for a sole root Python unit |
| project_name, package_name | Safe distribution name and non-keyword import identifier |
| description | Single-line text, encoded for TOML |
| project_kind | application or library |
| python_requires | >=3.11,<3.14; >=3.12,<3.14; or >=3.13,<3.14 |
| profiles | Unique selected cli, api, systemd values; base package always exists |
| service_module | Null unless systemd; a non-colliding module inside the package |
| dependencies | Normalized names mapped to bounded >=minimum,<maximum ranges |
| assigned_paths, excluded_paths | Exact repository file paths; assigned set equals selected output |

`default_request()` supplies explicit editable inputs and `expected_paths()`
derives profile paths. After changing profile/module/root fields, recompute the
assigned paths before rendering. A library may have a CLI; API and systemd
require application kind. Normalization fills selected CLI/API dependency
defaults before hashing; explicit bounded dependencies override those defaults.
Unselected profile dependencies are absent unless explicitly requested.

Base artifacts include pyproject, package init, test policy, package test and
installed-wheel helper. CLI adds entrypoints and help/version tests. API adds
FastAPI health/readiness and in-process ASGI tests. systemd adds a matching
importable service module and packaged unit resource. Nested units also own a
README and Makefile. Root integration requirements go to their content owners.
The starter service performs no real workload and is never installed or started.

New metadata omits unspecified author/license declarations. Existing licensing
files remain untouched. Do not add license-file references without producing
and validating the referenced file through its assigned owner.

## Candidate Validation

The producer uses repo-owned templates, no generator or package-manager calls.
Private output is outside Git worktrees, in 0700 directories with 0600 files.
Manifest schema 1 uses the coordinator's canonical JSON (including trailing
newline), normalized input digest, exact files/modes/digests, passed offline
validation and pending readiness. Every candidate is regenerated in memory for
content comparison. Unknown fields, malformed JSON, duplicate keys, non-finite
values, unsafe paths, foreign files and edited payloads fail.

Python validation is mandatory for direct-user and app-stack origins. Old
unvalidated Python candidates must be regenerated, not translated. Root README
must be represented by an integration operation; nested README is Python-owned.
uv.lock, .venv, caches and setuptools-scm's generated _version.py are never
static candidates. Ignore the package's generated version file.
Python integration recommendations escape literal paths for gitignore. A
brownfield ignore merge may append the exact generated rules from validated
Python candidates in the same bundle, preserving original bytes and rejecting
duplicates or unrelated new rules.

For app-stack application components use language python, manager uv and profile
python-package. Framework is fastapi when API is selected, otherwise typer for
CLI, otherwise null. Technology/version selections must match producer inputs.
Supported capability IDs are package-kind (application/library), cli (typer),
api (fastapi), systemd (systemd). Required selections must be generated;
non-required or undeclared profiles must not be generated. Unknown IDs fail. The canonical
technology name and runtime remain bound by the coordinator's graph checks.

## Bootstrap and Readiness

Bootstrap is post-apply work within the original creation authorization. Files-only,
plan-only and offline constraints take precedence. The static executor's
network/install flags describe its own phase, not permission for arbitrary
commands or a prohibition on separately declared Python setup.

Bootstrap locks the target against cooperating apply/setup processes, verifies
fully applied source and directory identity, rejects symlinked environment/lock
targets, and constrains uv to the component's .venv. It clears uv project and
working-directory overrides and rejects ancestor uv workspaces before commands
can alter a parent lock. Workspace integration is outside the core producer.
Known generated version, build, cache and egg-info outputs reject symlinks,
special files and hardlinked files before commands run. The lock coordinates
cooperating scaffold processes; it does not sandbox unrelated concurrent writers.
Commands are derived from the producer contract; manifest command strings are
never executed.

The sequence is missing-lock generation with uv lock, uv lock --check,
uv sync --locked, locked Ruff and pytest, and installed-wheel smoke. Existing
locks are checked and reused; unexplained stale locks fail instead of silently
upgrading dependencies. No formatter runs against applied source.

Readiness is private and separate from the static journal. It binds bundle and
input digests, project metadata, lock digest, uv/Python versions and check
results. Every invocation rechecks evidence; receipts never authorize skipping
validation. A failed setup records applied-not-ready and retains the scaffold.
Do not copy raw command output, index URLs or credentials into receipts.
Systemd service activation is outside scope; report platform verification
separately from import/resource checks.

## Validation

Run test_python_project.py and test_python_bootstrap.py for offline contracts.
Set RUN_PYTHON_BOOTSTRAP_TESTS=1 for real disposable uv environment, behavior,
wheel and rerun checks. Keep existing template tests. Source checks, installed
copies and fresh-host implicit routing are separate evidence.
