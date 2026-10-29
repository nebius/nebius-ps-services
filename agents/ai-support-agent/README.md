# AI Support Agent

Public, reusable support-agent software and tooling. Application services are
not implemented here yet. The current deliverable is the information-boundary
checker and its synthetic regression tests; it does not install an application.

## Information ownership

| Area | Owner and permitted information |
| --- | --- |
| Public product | Reusable source, generic charts/schemas/migrations/skills, synthetic tests, documentation and public release artifacts. |
| Infrastructure and protected runtime storage | Resources, credentials, evidence objects, workspaces, telemetry, caches, Terraform state/plans and recovery backups. |
| Private deployment Git | Environment-specific nonsecret configuration, reviewed infrastructure artifacts, release selections, policies and secret references. |
| Internal PostgreSQL | Application records/settings, active configuration, identities, approvals and receipts; separate knowledge, memory and workflow ownership. |

Private Git owns desired deployment settings. PostgreSQL owns application-managed
settings, activated revisions and runtime facts. Secret values stay in protected
runtime custody; only references and metadata enter configuration/database records.
One-way authentication verifiers retain their existing identity-database owner.

Actual deployment identifiers, private endpoints, credentials, personal/customer
records and confidential evidence must not enter this public project or its
Git history, website, logs, provenance or release packages. Private Git is not
a secret store or a runtime database. See [boundary checks](docs/boundaries.md).

## Run the checks

Python 3.10+ and Git 2.47+ are sufficient; no package installation or network access
is required. From this project directory:

```bash
python3 -B -m unittest discover -s tests
python3 -B tools/check_boundaries.py scan --root . --destination public --git-files
python3 -B tools/check_boundaries.py --help
```

A passing scan reports no findings, exits zero, and still requires human
classification review. Scan **before** the first public push, PR or upload.
Public CI is an additional check after transfer, not prevention of that transfer.

Dev/Test uses synthetic fixtures by default. Importing real data requires
deliberate handling review. The application must not guess whether a plausible
name, email or support message is real and block developers on that guess.

Configuration payload checks keep release-selection metadata outside the hashed
bundle. See the [handling guide](docs/boundaries.md) for the supported file allowlist.

## Development status

GitHub CI checks this tooling and public files. It receives no private repository,
deployment identifiers, cloud credentials or customer datasets. Runtime services,
image/chart pipelines and live isolation qualification remain future work.
No operational data or cloud resource is created by the checker.
