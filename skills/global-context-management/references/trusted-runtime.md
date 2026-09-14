# Trusted shared runtime

Codex and Claude use this catalog independently. Each installation keeps its
native identity, private state, authorization, workflow ownership and recovery.
Compatibility does not imply cross-agent execution, handoff or synchronization.
Commit and Worktree continue coordinating within the selected installation.

## Source and installation ownership

The shared runtime consists of `agent_runtime.py`, `hook_runtime.py`,
`trusted_runtime.py` and `task_state_permissions.py`. The local installer copies
the complete support set; native plugins include it in their rendered payload.
Individual script skills require this support. Instruction-only skills do not.
Skills-only npx installation does not register hooks or supply missing siblings.

The canonical bootstrap template belongs to this skill's `assets/` directory.
Its copies run before runtime-dependent imports. The regression suite checks
every embedded copy and the closed dependency graph; update the template,
consumers and installation fixtures together.

## Loading contract

The interpreter, standard library and reviewed entrypoint are trusted. Runtime
selection uses declared catalog-relative resources, adjacent flat-hook support
or the selected agent home's installed support. It never searches arbitrary
ancestors or Python's ambient module path. A missing layout may select another
supported layout; a present unsafe or incomplete bundle fails instead of mixing
resources from multiple installations.

Before executing the loader or any dependency group, capture its regular,
single-link, current-user-owned source files through no-follow descriptors.
Reject group/world-writable files, unsafe ancestry, unstable file metadata and
files larger than 1 MiB. Root-owned system aliases and sticky temporary-directory
ancestors retain their existing narrow exception. They do not authorize
root-owned executable payloads or user-controlled symlinks.

Execute captured source bytes; do not reopen paths through ordinary import
machinery or accept cached bytecode. Reuse only modules registered by this
loader with matching source identity and digest. Unknown module-cache entries
fail, even when their filename appears correct. Failed initialization removes
only the module and package bindings created by that attempt. Successful
verified bindings remain available for later imports.

The closed groups cover runtime, projection, permission audit, global context,
prompt intake and SDLC state. Domain owners retain their state and policy code.
The Nebius authentication graph remains independently owned; Stop subprocess
delegates initialize their own runtime. No general import finder is installed.

## Inspection and recovery

Configuration inspection uses source-only projection and permission auditing.
Installed Python files are comparison inputs, never executable audit helpers.
Permission inspection calls the existing audit with repair disabled; its
directory/file counts, mode checks and supported tree scope remain unchanged.
Explicit permission repair keeps its separate execution requirement.

An incomplete or mixed-version installation requires the complete current
bundle. There is no legacy loader or compatibility fallback. Upgrade support
and consumers together through the selected installation route, then start a
fresh agent session. Retain private workflow records and existing recovery
rules; never infer authority from a repaired installation.

## Validation

Run `scripts/test-trusted-runtime.py` for source/flat/individual layouts,
transitive imports, bootstrap parity, unsafe metadata, module-cache poisoning,
read replacement and failed-load cleanup. Existing installation and template
tests cover native hooks, explicit repair, state preservation and repeated
installation. Run each host independently.

Deterministic tests and copied payload parity do not prove native model
activation or output quality. Keep those evidence lanes separate and report
unavailable credentials or runtime surfaces explicitly.
