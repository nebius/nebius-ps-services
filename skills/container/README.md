# Container

`container` owns container engineering from repository source through a
validated OCI image and documented runtime contract.

## Capabilities

- Dockerfile and Containerfile design, build contexts, `.dockerignore`,
  BuildKit, buildx, cache, reproducibility, and final-image composition.
- Runtime identity, process, signals, ports, health, configuration, secrets,
  filesystems, storage, logging, networking, resources, and devices.
- Local Docker execution and Compose for development, testing, demonstrations,
  and approved single-host production profiles.
- Hardening, multi-platform evidence, GPU containers, SBOM, provenance,
  vulnerability policy, troubleshooting, and validation.

## Boundaries

The skill does not select the application architecture, own language packaging,
author GitHub Actions, publish or sign images, deploy Compose, or generate
Kubernetes and Helm resources. It produces the artifact and runtime contract
those sibling skills consume.

Builds, pulls, runtime tests, networked scans, and other local engine mutations
are explicit opt-ins. The helpers never push, sign, prune, deploy, or alter
daemon settings.

## Package Layout

- `SKILL.md`: scope, workflow, guardrails, and output contract.
- `references/`: workload-specific build, runtime, Compose, host,
  supply-chain, platform, Kubernetes, GPU, and validation standards.
- `assets/`: retained typed Python and React/Vite scaffold profiles; no generic
  Dockerfile template.
- `scripts/render_container_asset.py`: typed renderer and closed local Compose
  profile validator.
- `scripts/container_audit.py`: offline-first static and local-image audit.
- `scripts/container_audit_source.py`: offline source and policy checks.
- `scripts/container_audit_docker.py`: Docker inspection, validation, build,
  and task-owned cleanup.
- `scripts/container_supply_chain.py`: repository-selected local scanner and
  SBOM evidence.
- `scripts/container_audit_types.py`: shared stable finding and error types.
- `scripts/container_smoke_test.py`: bounded disposable local runtime test.
- `scripts/container_runtime_common.py`: shared bounded subprocess capture used
  by both validation helpers.
- `evals/trigger-prompts.md`: trigger and ownership-boundary examples.
- `agents/openai.yaml`: UI metadata and implicit invocation policy.

Runtime activation is not proven by source validation alone. Installation and
a fresh Codex session are separate steps.
