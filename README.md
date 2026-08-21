# nebius-ps-services

Nebius Platform Services: reusable AI/ML deployment building blocks for Nebius AI Cloud.

This repository contains Terraform modules, Helm charts, CLI services, examples,
and reusable Codex skills. Root-level files should stay focused on repository
orientation and cross-project policy. Project-specific behavior, release notes,
and operating instructions belong in the owning project folder.

## Repository Layout

| Path | Purpose | Local docs |
| --- | --- | --- |
| `.github/` | Repository automation, dependency updates, and shared workflows. | [root changelog](CHANGELOG.md) |
| `services/` | Service and CLI projects. | service-local `README.md` / `CHANGELOG.md` files |
| `platform-infra/` | Reusable Terraform modules and examples for Nebius infrastructure. | [README](platform-infra/README.md), [changelog](platform-infra/CHANGELOG.md) |
| `helm-charts/` | Reusable Helm charts. | chart-local `README.md` / `CHANGELOG.md` files |
| `skills/` | Public reusable Codex skills and the local skills installer. | [README](skills/README.md), [changelog](skills/CHANGELOG.md) |
| `examples/` | Example deployments and reference configurations. | example-local docs where present |

## Common Use Cases

- Deploy and operate Nebius AI/ML infrastructure with Terraform.
- Package platform services and validation workloads with Helm.
- Generate and deploy customer-facing Nebius configuration with service-local
  tooling.
- Build and publish reusable service, chart, and image release workflows.
- Use reusable Codex skills for project alignment, PR workflows, shell/Python
  quality, Helm, Terraform, Nebius automation, and release helper authoring.

## Changelog Policy

The root [CHANGELOG.md](CHANGELOG.md) tracks repository-wide process,
automation, and documentation changes only.

Project-specific release notes belong in a `CHANGELOG.md` next to the owning
project or chart.

## Dependency Automation

The repository uses Dependabot for dependency update pull requests.

- GitHub Actions major, minor, and patch update pull requests may be created by
  `.github/dependabot.yml`.
- GitHub Actions minor and patch updates are grouped for simpler review.
- The companion auto-merge workflow decides which eligible Dependabot pull
  requests may be auto-approved and auto-merged.
- GitHub Actions updates, including majors, may be auto-merged only when the
  pull request is Dependabot-authored, scoped to workflow automation files, and
  processed with the dedicated `dependabot-automerge` environment credential.
- Python dependency updates from the `uv` and `pip` ecosystems may also be
  auto-approved and auto-merged when every changed file stays within Python
  dependency manifests or lockfiles such as `pyproject.toml`, `uv.lock`,
  `requirements*.txt`, `constraints*.txt`, `poetry.lock`, `pdm.lock`,
  `Pipfile`, and `Pipfile.lock`.
- Dependabot pull requests that touch source code or other non-dependency files
  remain ineligible for repo-level auto-merge.

## License

Copyright 2025 Nebius B.V.

Licensed under the Apache License, Version 2.0 (the "License"); you may not use
this file except in compliance with the License.

You may obtain a copy of the License at <http://www.apache.org/licenses/LICENSE-2.0>.
Unless required by applicable law or agreed to in writing, software distributed
under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
CONDITIONS OF ANY KIND, either express or implied. See the License for the
specific language governing permissions and limitations under the License.
