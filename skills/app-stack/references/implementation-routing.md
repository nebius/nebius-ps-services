# Implementation Routing

Use this reference only after the user requests implementation, scaffolding,
modernization, or repository changes.

## Ownership Model

`app-stack` owns:

- the requirements-to-stack decision;
- cross-layer architecture and dependency ordering;
- required, conditional, deferred, and rejected classifications;
- specialist-skill routing and non-overlapping ownership;
- end-to-end validation and final stack coherence.

Matching specialist skills own their established workflows. Discover them from
the current installed skill metadata rather than assuming every named skill is
available. Common routing examples include:

| Work | Specialist direction when available |
| --- | --- |
| Open-ended product or architecture exploration | `brainstorm` |
| Current product, framework, protocol, or pattern research | `research` |
| Complete solution design and implementation plan | `design` |
| Architecture decision stress test | `system-design-rules` |
| Python project structure, packaging, API, CLI, or service scaffolding | `python-project` |
| Hosted website construction | `sites-building`, especially when `.openai/hosting.json` exists |
| Terraform structure and modules | `terraform` |
| Helm charts | `helmchart` |
| GitHub Actions | `github-workflows` |
| Shell automation | `shell-scripting` |
| Security review and safe remediation | `apply-security` |
| Changed-scope final reconciliation | `align` |

Use only the skills that match the selected stack and requested action. Respect
explicit-only invocation policies and every specialist's stricter safety rules.

## Implementation Sequence

1. Re-read project instructions, current state, design, manifests, tests, and
   unrelated worktree changes.
2. Lock the stack decision, assumptions, deferred components, interfaces, data
   ownership, and operational boundaries.
3. Identify true prerequisites: repository skeleton, contracts, authentication,
   migrations, shared test harness, or deployment safety.
4. Build the smallest vertical slice that proves one real user or system flow
   across its required layers.
5. Add optional infrastructure only when the slice reaches the requirement that
   justifies it.
6. Test component contracts and the end-to-end flow, including failure and
   recovery paths.
7. Align source, tests, configuration, examples, README/design docs, and the
   `[Unreleased]` changelog.
8. Report implemented, deferred, and unverified decisions separately.

Do not scaffold every technology in the decision record. Create only `Required`
components. Add a `Conditional` component when its documented trigger is
already true.

## Cross-Layer Contracts

Before parallel or specialist work, assign one owner for each:

- public route, command, event, and schema;
- authoritative data model and migration;
- UI route and server-state boundary;
- task, schedule, workflow, or event consumer;
- infrastructure resource and deployment configuration;
- shared generated artifact;
- validation gate and documentation surface.

Define the API and data contracts before separate agents or skills edit both
sides. Avoid two workers modifying the same file or inventing incompatible
contracts independently.

## Safety Boundaries

An implementation request authorizes local project changes needed for the
selected stack. It does not automatically authorize:

- production or external-service changes;
- package, image, chart, or release publication;
- cloud, Kubernetes, Terraform, database, CI/CD, IAM, or credential mutation;
- destructive migration or deletion;
- new paid services or cost-incurring environments.

Use static validation, local tests, builds, dry runs, and disposable
environments first. For live changes, confirm the exact target, authorization,
blast radius, rollback, and non-production status where required.

Never print or persist secrets. Use placeholders in public reusable sources and
examples. Verify dependency sources, licenses, maintenance status, and current
official installation guidance before adding them.

## Completion Evidence

Return:

- selected stack and implementation slice;
- specialist skills used and ownership boundaries;
- files and public contracts changed;
- dependencies and services added, with justification;
- migrations, deployments, or live actions performed or deferred;
- tests, builds, lint, security, and alignment results;
- remaining risks, conditional components, and revisit triggers.
