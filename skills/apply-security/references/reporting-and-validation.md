# Reporting and Validation

## Contents

- Scan report
- Patch report
- JSON findings
- SARIF-style findings
- Validation commands
- Limitations
- Staged rollout guidance
- Final merge checklist

## Scan Report

Use this shape for Markdown scan output:

```markdown
## Findings

### TF-IAM-001: Wildcard IAM write permissions

- Area: Terraform
- File and line: `modules/app/iam.tf:42`
- Severity: High
- Confidence: High
- Exploitability: Medium
- Blast radius: Project-wide cloud resources
- Risk: The role can modify all resources in the account.
- Root cause: Policy action and resource are both wildcarded.
- Recommended fix: Narrow actions and resources to the specific service calls
  and ARNs required by the module.
- Feature impact: May require adding explicit permissions for currently
  undocumented operations.
- Safe override: Keep only with `securityException.owner`, `reason`, `scope`,
  and `reviewBy`.
```

Sort by severity, exploitability, blast radius, then confidence. Keep
speculative findings separate from confirmed findings.

## Patch Report

Use this shape after edits:

```markdown
## Changes

### CI-PERM-001: Added workflow token permissions

- Summary: Added top-level `permissions: read-all`.
- Reason: The workflow did not need write access for PR tests.
- Compatibility note: Jobs that publish artifacts or comments may need explicit
  per-job write permissions.
- Verification: `python -c 'import yaml, pathlib; ...'` passed.
- Remaining risks: Third-party action pinning was not changed.
```

Show exact diffs when the user asks for them or when the patch is difficult to
review from the summary alone. In normal Codex code-editing flows, the local
Git diff is already available, so summarize the high-signal changes.

## JSON Findings

When the user asks for JSON, emit an array of findings:

```json
[
  {
    "id": "K8S-RUN-001",
    "area": "Kubernetes",
    "file": "deploy/app.yaml",
    "line": 31,
    "severity": "High",
    "confidence": "High",
    "exploitability": "Medium",
    "blast_radius": "Pod privilege escalation",
    "risk": "Container allows privilege escalation.",
    "root_cause": "allowPrivilegeEscalation is true.",
    "recommended_fix": "Set allowPrivilegeEscalation false and drop capabilities if compatible.",
    "feature_impact": "May break workloads that intentionally need elevated privileges.",
    "safe_override": "Require allowPrivileged plus owner, reason, scope, and reviewBy."
  }
]
```

Do not include raw secret values in JSON. Redact suspicious values and report
only the file, line, key name, and secret class.

## SARIF-Style Findings

When the user asks for SARIF, produce valid SARIF 2.1.0 when feasible. At
minimum, map:

- finding ID to `ruleId`
- severity to `level`
- file/line to `locations[].physicalLocation.artifactLocation.uri` and
  `region.startLine`
- risk and remediation to `message.text`
- confidence, exploitability, blast radius, and safe override to rule or result
  properties

If a full SARIF writer is not practical in the current task, state that the
output is SARIF-style JSON and identify the missing fields.

## Validation Commands

Use repository-native checks first. Do not fail the task only because a tool is
missing; report missing tools as limitations.

Before running a command, check whether it can mutate files, create cache or
build directories, contact an external service, install missing tools, submit
dependency data, or use live credentials. Run those commands only when that
behavior is acceptable for the task and target environment. Prefer configured
package scripts and local binaries over package-manager auto-install behavior.

Terraform:

```bash
terraform fmt -check
terraform validate
terraform init -lockfile=readonly
# Only when lockfile maintenance is approved or explicitly in scope:
terraform providers lock -platform=<os_arch>
```

Kubernetes:

```bash
kubectl apply --dry-run=client -f <file-or-dir>
kubeconform <file-or-dir>
kubeval <file-or-dir>
```

Helm:

```bash
helm lint <chart-dir>
helm template <release-name> <chart-dir>
```

CI/CD:

```bash
python -c 'import sys, yaml; [yaml.safe_load(open(p)) for p in sys.argv[1:]]' .github/workflows/*.yml
bash -n <script>
shellcheck <script>
```

Python:

```bash
python -m compileall <package-or-dir>
pytest
ruff check
mypy
```

Java:

```bash
mvn test
gradle test
javac <files>
```

JavaScript and TypeScript:

```bash
npm test
yarn test
pnpm test
npm audit
./node_modules/.bin/tsc --noEmit
./node_modules/.bin/eslint .
```

Rust:

```bash
cargo fmt --check
cargo clippy
cargo test
cargo check
```

Bash:

```bash
bash -n <script>
shellcheck <script>
```

Use only commands that are present or commonly expected for the repository.
Prefer configured package scripts over generic commands when available. Treat
`npm audit` as an external registry submission, and do not run package-manager
commands that install missing tools unless dependency installation is already
approved for the task.

## Limitations

This skill cannot safely infer:

- whether a public endpoint is intentionally exposed without environment,
  threat model, and owner context
- whether IAM/RBAC permissions are all required without runtime call evidence
- whether NetworkPolicy default-deny will break traffic without service-flow
  knowledge
- whether changing auth, crypto, serialization, or CORS behavior is safe
  without product requirements and compatibility tests
- whether a secret-like value is active, expired, fake, or already rotated
- whether generated manifests match live cluster admission policy unless live
  validation is explicitly approved in a non-production environment
- whether framework-specific security behavior exists unless repository code
  or current official docs confirm it

Lower confidence and ask for approval rather than guessing in these cases.

## Staged Rollout Guidance

Use staged plans for changes that can break production behavior:

- TypeScript strictness: add local runtime guards first, then enable stricter
  compiler flags per package or folder.
- NetworkPolicy: start with observed traffic inventory, add namespace labels,
  test allow rules, then enable default-deny.
- IAM/RBAC: add audit logging or dry-run policy simulation where available,
  narrow read permissions before write permissions, then remove wildcards.
- Runtime security: add non-root, seccomp, and dropped capabilities first;
  only add read-only root filesystems after write paths are known.

## Final Merge Checklist

- Findings are prioritized by severity, confidence, exploitability, and blast
  radius.
- Every patch explains risk, change, feature impact, verification, and
  remaining risk.
- No secrets, fake secrets, internal hostnames, private endpoints, or customer
  data were added to files or output.
- No destructive, availability-impacting, auth, authz, crypto, data-retention,
  or external-network change was applied without approval.
- Exceptions include owner, reason, scope, and `reviewBy` date.
- Repository-native validation ran, or missing tools and skipped checks are
  explicitly documented.
- Public APIs, auth flows, serialization formats, schemas, and externally
  visible routes are unchanged unless approved.
- Third-party dependencies or scanners were not added unless approved or
  already used by the repository.
