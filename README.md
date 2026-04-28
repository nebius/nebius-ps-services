# nebius-ps-services

## Nebius Professional Services - AI/ML Deployment Solutions

Welcome to the official repository of the **Nebius Professional Services (PS)** team.
This repository provides a curated set of **Terraform modules** and **Helm charts** designed to streamline the deployment, orchestration, and scaling of AI/ML workloads on the **Nebius AI Cloud** platform.

---

## 📋 What’s in This Repo?

This repo includes reusable, field-tested infrastructure components that simplify and accelerate AI/ML deployment workflows.
These tools are built for **production-readiness**, **scalability**, and **performance optimization** on the Nebius platform.

The repository also includes reusable developer tooling under `skills/`.
Notable examples include the `attach-ubuntu` Codex skill for launching an
Ubuntu test container, bootstrapping project build dependencies into a
container-specific virtual environment, preserving Git metadata for
subprojects, and attaching VS Code to it on macOS with Docker Desktop, plus
the `align` Codex skill for end-to-end project review and fix passes that
reconcile code, module wiring, tests, CI workflows, CLI/help output, config,
README/design docs, and documentation without speculative rewrites. The same
`skills/` set also includes `create-pr` and `review-pr` for branch-safe GitHub
pull request creation and merge-readiness review/fix flows with safer
default-branch handling and conservative branch-update guidance.

---

## 🔧 Use Cases

- Deploy distributed ML training jobs (e.g., with Ray)
- Schedule AI/ML workloads efficiently (e.g., with Kueue)
- Bootstrap cloud-native ML pipelines on Nebius with minimal effort
- Implement best practices for infrastructure as code (IaC) using Terraform and Helm

---

## Dependency Automation

The repository uses Dependabot for dependency update pull requests.

- GitHub Actions major, minor, and patch update pull requests may be created by `.github/dependabot.yml`.
- GitHub Actions minor and patch updates are grouped for simpler review.
- The companion auto-merge workflow is responsible for deciding which eligible Dependabot pull requests may be auto-approved and auto-merged.
- GitHub Actions updates, including majors, may be auto-merged only when the pull request is Dependabot-authored, scoped to workflow automation files, and processed with the dedicated `dependabot-automerge` environment credential.
- Python dependency updates from the `uv` and `pip` ecosystems may also be auto-approved and auto-merged when the pull request is Dependabot-authored and every changed file stays within Python dependency manifests or lockfiles such as `pyproject.toml`, `uv.lock`, `requirements*.txt`, `constraints*.txt`, `poetry.lock`, `pdm.lock`, `Pipfile`, and `Pipfile.lock`.
- Dependabot pull requests that touch source code or other non-dependency files remain ineligible for repo-level auto-merge.

---

## 🪪 License

Copyright 2025 Nebius B.V.

Licensed under the Apache License, Version 2.0 (the "License"); you may not use this file except in compliance with the License.

You may obtain a copy of the License at <http://www.apache.org/licenses/LICENSE-2.0> Unless required by applicable law or agreed to in writing, software distributed under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the License for the specific language governing permissions and limitations under the License.

---
