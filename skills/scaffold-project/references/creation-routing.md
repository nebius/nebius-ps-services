# Creation and Maintenance Routing

Choose the workflow from the requested outcome, not the presence of a language
name or the word build. Implicit selection never expands task authority.

| Request | Route and completion |
| --- | --- |
| Create a Python library, CLI, API or service project | scaffold-project coordinates; python-project renders and bootstraps |
| Create a repository with Python, frontend and deployment components | scaffold-project assigns each file to the appropriate specialist |
| Add a new component to an existing repository | scaffold-project adds bounded files and preserves existing content |
| Improve Python tests, dependencies, packaging or lint | python-project maintains the existing project directly |
| Build an existing wheel | Python build tooling; no scaffolding |
| Compare frameworks or choose architecture | design/app-stack; no premature materialization |
| Plan, explain or review a scaffold | Report the plan without target/environment writes |
| Explicit python-project request to create a project | Hand coordinator ownership to scaffold-project once |

## One Coordination Flow

```text
creation intent and selected requirements
  -> scaffold-project: inspect target, choose topology, reserve path owners
  -> specialists: exact private candidates and integration requirements
  -> validate and finalize exact bundle
  -> guarded static apply using internally supplied digest
  -> Python bootstrap: real lock, owned environment, checks and wheel smoke
  -> report applied / ready / pending evidence separately
```

Check bounded specialist scope before routing the prompt. Candidate and bootstrap
calls never re-enter the creation coordinator. Do not require the user to name
skills, type a digest, or repeat approval for work already covered by creation.
Existing stack choices and routine Python defaults can establish a simple
project; only material unresolved choices need clarification.

## Topology and Ownership

A Python-only project may use one Python materialization unit at `.`. It must be
the sole unit; `.` is never a file path. Multi-component projects use disjoint
nested units. Root-plus-nested overlapping unit plans fail clearly rather than
moving existing code or inventing precedence rules.

Root README/Makefile/LICENSE content belongs to scaffold-project, root ignore
rules to gitignore, root CI to github-workflows. Their operations bind the sole
root unit when present; otherwise root cross-cutting sets use null. Python owns
its exact package files and explicitly assigned nested README/Makefile. The
Python producer's integration recommendations do not grant ownership to write
those root files. CI follows the selected automation requirement.

## Completion and Recovery

A static journal marked complete proves scaffold application only. Default
Python creation also calls bootstrap for each supported component. A setup
failure reports applied but not ready and preserves files and private evidence.
Retry setup under the same task authority, rechecking source and lock state.
Do not rewrite or upgrade a valid lock during a repeat request. Changed authored
files are conflicts, not permission to overwrite user work.

Files-only and offline requests leave setup pending. Missing supported tools,
unsupported profiles or platform checks stay explicit. Project creation does
not initialize Git, publish, deploy, install system services or install skills.
