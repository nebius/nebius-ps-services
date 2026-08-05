---
name: container
description: "Use for creating, reviewing, hardening, optimizing, and troubleshooting OCI container images and containerized application stacks. Covers Dockerfile or Containerfile, build contexts, .dockerignore, BuildKit and buildx, Docker and compatible OCI tooling, Compose, image metadata, runtime contracts, non-root execution, signals, secrets, storage, networking, health behavior, multi-platform images, GPU device requirements, SBOM, provenance, vulnerability policy, and production-readiness validation. Do not use for application architecture, language-specific project scaffolding, GitHub Actions implementation, registry release publishing, or Helm and Kubernetes resource authoring when a dedicated sibling skill owns that work."
---

# Container Engineering

## Help

For `$container --help` or `$container -h`, return concise help and stop before
any workflow step. Include the purpose, invocation policy, public usage/actions,
and `-h, --help` plus only documented skill-level options; say "No additional
public flags" when none exist. For internal or coordinator-only skills, state
that boundary and that no standalone public workflow action exists. After the
selected `SKILL.md` is loaded, help is report-only: do not call any additional
tools, inspect project state, or modify files, private state, Git, or external
systems. Never
expose private helper actions or treat help as workflow authorization.

## Purpose

Create and maintain secure, reproducible, portable, operable, and
production-ready container images and containerized application stacks.

Own the container artifact and its runtime contract from repository source
through validated local image behavior. Stop before registry publication,
remote deployment, or orchestrator-specific resource generation.

## Use This Skill For

- Creating or reviewing Dockerfiles, Containerfiles, build contexts, ignore
  rules, Compose files, or Bake definitions.
- Improving BuildKit/buildx cache correctness, reproducibility, performance,
  multi-stage construction, metadata, or final-image composition.
- Defining and validating process, user, signals, ports, health, configuration,
  secrets, filesystems, storage, logging, networking, resources, or devices.
- Hardening non-root execution, read-only compatibility, capabilities, seccomp,
  privilege escalation, and namespace requirements.
- Defining SBOM, provenance, vulnerability, signing, and verification
  requirements without publishing or signing artifacts.
- Troubleshooting image builds and local container runtime behavior.
- Coordinated container candidates assigned by `scaffold-project`.

## Invocation Scope

- `standalone`: inspect or modify explicitly selected container files in an
  existing repository.
- `coordinated-candidate`: receive exact assigned paths and approved inputs,
  write exact candidates only into the private scaffold bundle, and never
  modify the target.

The skill may own a container file inside a language-owned component
directory. Ownership is per file, not per directory.

## Required Reads

1. Read applicable repository instructions and inspect existing image,
   application startup, Compose, CI, deployment, release, and package files.
2. Classify the workload with
   [workload-profiles.md](references/workload-profiles.md).
3. Read only the references matching the task:
   - Build contexts, Dockerfile/Containerfile, stages, dependencies, cache, or
     metadata: [image-build-standard.md](references/image-build-standard.md).
   - Process, user, signals, health, configuration, filesystems, storage,
     logging, networking, or resources:
     [runtime-standard.md](references/runtime-standard.md).
   - Compose services, overrides, networks, volumes, or profiles:
     [compose-standard.md](references/compose-standard.md).
   - Docker Engine, host configuration, rootless operation, or alternative OCI
     engines: [engine-host-standard.md](references/engine-host-standard.md).
   - Digests, scanning, SBOM, provenance, signing, or verification:
     [supply-chain-standard.md](references/supply-chain-standard.md).
   - ARM64, AMD64, buildx, cross-compilation, emulation, or manifest lists:
     [multi-platform-standard.md](references/multi-platform-standard.md).
   - Kubernetes or Helm handoff:
     [kubernetes-contract.md](references/kubernetes-contract.md).
   - NVIDIA, CUDA, GPU, MIG, CDI, or device injection:
     [gpu-containers.md](references/gpu-containers.md).
   - Failure investigation or final evidence:
     [troubleshooting-validation.md](references/troubleshooting-validation.md).
   - Existing Python/React/Vite typed templates or scaffold candidates:
     [scaffold-profiles.md](references/scaffold-profiles.md).
4. Verify version-sensitive Docker, OCI, Kubernetes, Sigstore, NVIDIA, or
   framework behavior against current official documentation before changing
   guidance or claiming support.

## Workflow

1. Classify the task as create, review, optimize, harden, troubleshoot, or
   migrate, and select the workload profile.
2. Separate image build, application runtime, host engine, distribution, and
   orchestrator concerns.
3. Record the host platform, builder platform, target platforms, and platforms
   actually runtime-tested as distinct facts.
4. Define the runtime contract before implementation:
   - image repository, tag/digest policy, and supported platforms;
   - entrypoint, arguments, working directory, UID/GID, stop signal, and
     maximum shutdown duration;
   - ports and startup, readiness, and liveness semantics;
   - configuration names and secret interfaces, never secret values;
   - read-only compatibility plus writable, temporary, and persistent paths;
   - logging, ingress, egress, DNS, and dependencies;
   - measured startup peak, steady-state resource behavior, and OOM evidence;
   - capabilities, seccomp, namespaces, privilege, shared memory, and devices;
   - labels, vulnerability policy, SBOM, provenance, signing, and verification
     requirements.
5. Apply controls as:
   - `Required`: production baseline unless the workload is not production.
   - `Conditional`: apply when the workload and target platform support it.
   - `Exception`: record reason, compensating control, owner, and review
     condition.
6. Implement the smallest justified change while preserving repository
   conventions. Use the typed renderer for its supported scaffold profiles;
   do not splice request text into Dockerfile instructions.
7. Delegate implementation across ownership boundaries instead of reproducing
   sibling workflows.
8. Validate in increasing order of side effects: static inspection, local
   configuration checks, build, disposable smoke test, and separately
   authorized network or supply-chain checks.
9. Report skipped evidence and every platform that was not tested. Do not
   describe the result as production-ready when required evidence is absent.

## Production Baseline

Require production images to use trusted explicitly versioned bases,
deterministic dependency inputs, controlled build contexts, deliberate
multi-stage construction, minimal runtime content, and standard OCI metadata.

Keep credentials out of ARG, ENV, files, layers, logs, and attestations. Use
BuildKit secret or SSH mounts when a build legitimately needs credentials.

Run the application as a known non-root UID/GID unless an exception is
documented. Use exec-form commands, correct PID 1 behavior, bounded graceful
shutdown, stdout/stderr logging, explicit writable paths, and external durable
state. Define health semantics without treating an image `HEALTHCHECK` as a
substitute for separate orchestrator startup, readiness, and liveness probes.

Use immutable release identity, avoid `:latest` in production, record the
resulting digest, and require the configured vulnerability, SBOM, provenance,
signing, and verification evidence. Do not invent CPU or memory limits without
measurements.

Treat read-only roots, tmpfs, capability removal, no-new-privileges, runtime
seccomp, rootless engines, user namespaces, distroless/scratch images, image
health checks, and init processes as conditional controls whose compatibility
must be demonstrated.

## Delegation Boundaries

- Application architecture, services, and technology topology: `app-stack`.
- Language packaging, dependencies, tests, and source structure: the matching
  language skill.
- GitHub Actions YAML: `$github-workflows`, using this skill's build and
  evidence requirements.
- Registry tags, pushes, release publication, and signing actions:
  `$publish-image`.
- Kubernetes and Helm resources: `$helmchart`, using the runtime contract.
- Disposable Ubuntu VS Code environments: `$attach-ubuntu`.
- Broad security assessment: `$apply-security`.
- Cross-system failures not confined to the image or local runtime:
  `$troubleshoot`.

## Guardrails

- Do not push, sign, prune, delete images, alter daemon configuration, deploy,
  or start privileged containers without a separate explicit request.
- Do not use privileged mode, Docker socket mounts, host PID/IPC/network
  namespaces, unrestricted capabilities, or broad device access by default.
- Do not put secrets in image metadata, Compose files, committed `.env` files,
  default environment values, examples, logs, or reports.
- Do not use source bind mounts or the container writable layer for production
  state.
- Do not force Docker Compose, Kubernetes, distroless, Alpine, rootless mode, a
  scanner, or a signing tool without the approved architecture and workload
  evidence.
- Do not claim multi-platform support from emulated compilation alone.
- Do not claim GPU support from `nvidia-smi` alone or expose GPUs through
  privileged mode.
- General Compose audits must not weaken the closed scaffold-profile validator.
- Local builds, pulls, runs, and networked checks require explicit opt-in.

## Validation

Use `scripts/container_audit.py` for default offline inspection. Its `--build`,
`--runtime-test`, `--supply-chain`, and `--allow-network` modes are separate
explicit opt-ins.

Use `scripts/container_smoke_test.py` only for an explicitly authorized
disposable local runtime test. It defaults to no pull, no network, no published
port, bounded resources, dropped capabilities, no-new-privileges, redacted
output, and task-owned cleanup.

For existing typed Python or React/Vite profiles, use
`scripts/render_container_asset.py`; its Compose validator owns only the closed
local scaffold profile.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

## Output Contract

Return:

- workload profile, task mode, engine/builder, deployment target, and
  assumptions;
- selected design and rejected alternatives;
- the complete runtime contract;
- files created or modified and their purpose;
- commands run, bounded evidence obtained, and checks skipped;
- blockers, required fixes, optional improvements, and exceptions;
- remaining sibling-skill handoffs.
