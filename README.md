# nebius-ps-services

Nebius Platform Services: reusable AI/ML deployment building blocks for Nebius AI Cloud.

This repository contains Terraform modules, Helm charts, CLI services, examples,
GPU engineering courses, and reusable Agent Skills. Root-level files should
stay focused on repository orientation and cross-project policy.
Project-specific behavior, release notes,
and operating instructions belong in the owning project folder.

## Repository Layout

| Path | Purpose | Local docs |
| --- | --- | --- |
| `.github/` | Repository automation, dependency updates, and shared workflows. | [root changelog](CHANGELOG.md) |
| `services/` | Service and CLI projects. | service-local `README.md` / `CHANGELOG.md` files |
| `courses/` | Seven courses: a text-only Slurm/Soperator introduction, five GPU foundations and specializations, and advanced sixteen-GPU communication labs. | [course catalog](https://nebius.github.io/nebius-ps-services/courses/), [authoring and validation](courses/docs/course-builder.md) |
| `platform-infra/` | Reusable Terraform modules and examples for Nebius infrastructure. | [README](platform-infra/README.md), [changelog](platform-infra/CHANGELOG.md) |
| `helm-charts/` | Reusable Helm charts. | chart-local `README.md` / `CHANGELOG.md` files |
| `skills/` | Public reusable Agent Skills and the local skills installer. | [README](skills/README.md), [changelog](skills/CHANGELOG.md) |
| `examples/` | Example deployments and reference configurations. | example-local docs where present |

Codex and Claude marketplace catalogs live at the Git root and point to the
unchanged `skills/` catalog. See the [installation guide](skills/README.md#native-plugins-and-npx).

## Common Use Cases

- Start with Slurm and Soperator, then learn GPU fundamentals, performance
  optimization, LLM training, LLM inference, custom CUDA kernels and advanced
  communication through the [course catalog](https://nebius.github.io/nebius-ps-services/courses/).
- Deploy and operate Nebius AI/ML infrastructure with Terraform.
- Package platform services and validation workloads with Helm.
- Generate and deploy customer-facing Nebius configuration with service-local
  tooling.
- Build and publish reusable service, chart, and image release workflows.
- Use reusable Agent Skills for project alignment, PR workflows, shell/Python
  quality, Helm, Terraform, Nebius automation, and release helper authoring.

## Repository Website

The [repository website](https://nebius.github.io/nebius-ps-services/) is published
through GitHub Pages and links to the course catalog. Pages deploys from branch
`main` and folder `/(root)`. Protected broker merges explicitly request a Pages
build after their exact-result CI passes; see
[protected merge setup](.github/merge-automation.md#completion-and-recovery).
The root `.nojekyll` enables static publication without a custom
deployment workflow. Other eligible repository files become available through
Pages as well. Initial setup, course build and validation instructions live in
[course builder guide](courses/docs/course-builder.md).

## Changelog Policy

The root [CHANGELOG.md](CHANGELOG.md) tracks repository-wide process,
automation, and documentation changes only.

Project-specific release notes belong in a `CHANGELOG.md` next to the owning
project or chart.

## Dependency Automation

Dependabot opens updates using the schedules, groups and ecosystems in
`.github/dependabot.yml`. Every update, including Docker, waits for local review
and meaningful validation before a trusted operator starts merging.

Ordinary and Dependabot PRs share one path: local review, safe repairs, fresh
review and CI, protected Actions merge, then exact-result CI and Pages
verification. Source repairs are allowed when safe; each new head or base
requires fresh review. Standalone review never merges, and automatic PR/CI
and scheduled events only report CI or recover already-authorized completion.

[Protected merge setup](.github/merge-automation.md) documents the operator list,
required protection, bootstrap and live acceptance. Completion CI can run from
a newer workflow revision; its exact-result artifact identifies the tested commit.
Verify skill installation and default-branch workflow deployment separately
from source tests, CI and live completion.

## Reusable Skill CI

Changes to the canonical project-spec owner and its ordinary-session, Task
Implementer, Agentic SDLC, hook-installer, and project-instruction adapters run
the focused `skills-project-specs-ci.yml` contract suite. The workflow validates
the single v2 parser/publisher contract, nonblocking intake hooks, worker
intent/spec inheritance, typed spec-gap reporting, and safe hook retirement.

## License

Copyright 2025 Nebius B.V.

Licensed under the Apache License, Version 2.0 (the "License"); you may not use
this file except in compliance with the License.

You may obtain a copy of the License at <http://www.apache.org/licenses/LICENSE-2.0>.
Unless required by applicable law or agreed to in writing, software distributed
under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
CONDITIONS OF ANY KIND, either express or implied. See the License for the
specific language governing permissions and limitations under the License.
