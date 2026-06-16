# Apply Security Rulebook

## Contents

- Review mindset
- Area rule matrix
- Severity and confidence
- Safe auto-fix policy
- Safe override policy
- Production hardening extension
- Policy-as-code compatibility

## Review Mindset

- Safety first: prevent credential exposure, privilege escalation, public
  exposure, supply-chain tampering, unsafe deserialization, injection, command
  execution abuse, insecure crypto, and secret leakage.
- Preserve flexibility: prefer secure configurable defaults, explicit opt-ins,
  and documented exceptions over deleting features.
- Minimal diffs: change only what is needed to remove or reduce the risk.
- Secure by default: new defaults should be restrictive and controlled
  overrides should be explicit.
- Evidence based: classify by severity, confidence, exploitability, and blast
  radius. Lower confidence when runtime context is unknown.
- Framework aware: detect common frameworks from repository evidence, but do
  not invent framework-specific behavior.

## Area Rule Matrix

### Terraform

Review `.tf`, `.tfvars.example`, `providers.tf`, `backend.tf`, `versions.tf`,
environment roots, and modules.

- Provider pinning: require `required_providers` with source and version
  constraints. Prefer committed dependency lock files for roots where
  `terraform init` runs. Avoid unbounded or floating provider versions.
- Least privilege IAM: flag wildcard actions, wildcard resources, broad
  managed policies, admin policies, cross-account assumptions without
  conditions, and missing conditions where applicable.
- Secrets in state: flag inline secrets, sensitive values, hardcoded tokens,
  generated passwords stored in state, backend credentials in configuration,
  and unsafe outputs.
- Public endpoints: flag public security group ingress, public load balancers,
  public buckets, public databases, open CIDR ranges, and public IP assignment.
- Preferred fixes: variables with safe defaults, explicit `allow_public_*`
  flags, narrow IAM actions/resources, condition blocks, `sensitive = true`,
  external secret managers, dynamic or short-lived credentials, and remote
  backends with access control.

### Kubernetes

Review `.yaml`, `.yml`, base manifests, overlays, generated manifests when
committed, service accounts, roles, bindings, pods, controllers, services, and
network policies.

- Privilege: flag `privileged: true`, `hostPID`, `hostIPC`, `hostNetwork`,
  broad capabilities, `allowPrivilegeEscalation: true`, and missing seccomp
  where a workload can reasonably support it.
- `hostPath`: allow only documented, narrow, read-only paths with explicit
  justification.
- RBAC: flag `cluster-admin`, wildcard verbs/resources, broad
  `ClusterRoleBinding`, default service account use, and unnecessary
  cross-namespace permissions.
- NetworkPolicy: flag namespaces or workloads with no ingress or egress
  controls. Default-deny is usually a plan-first change because it can break
  traffic.
- `securityContext`: prefer non-root execution, read-only root filesystems
  where compatible, dropped capabilities, `seccompProfile: RuntimeDefault`,
  `runAsNonRoot`, and controlled `fsGroup`/`runAsUser` settings.
- Preferred fixes: add pod/container security context without breaking write
  paths. If the app writes to disk, preserve required writable mounts with
  `emptyDir` or explicitly documented paths.

### Helm

Review `Chart.yaml`, `values.yaml`, `values*.yaml`, templates, helpers,
NOTES.txt, schema files, and rendered output when available.

- Secrets: flag values, templates, NOTES, ConfigMaps, annotations, and env vars
  containing secret-like values. Replace with `existingSecret`, `secretKeyRef`,
  ExternalSecret patterns already used by the repo, or required value
  references.
- Images: flag `latest`, mutable tags, missing digest support, and missing
  image pull policy logic. Prefer digest support while keeping tag override
  flexibility.
- Exposure: flag `LoadBalancer`, `NodePort`, public ingress, wildcard hosts,
  insecure annotations, and missing TLS controls.
- Preferred fixes: add values such as `service.type`, `ingress.enabled`,
  `ingress.tls`, `existingSecret`, `image.digest`, `image.tag`,
  `securityContext`, `podSecurityContext`, `networkPolicy.enabled`, and
  `allowPublicExposure` with safe defaults.

### CI/CD

Review GitHub Actions, GitLab CI, CircleCI, Buildkite, Jenkinsfile, reusable
workflows, and shell scripts used by workflows.

- Token leakage: flag echoing secrets, `set -x` near secrets, printing env,
  uploading secret-containing artifacts, secrets in command arguments, and
  long-lived cloud keys.
- Event trust: flag unsafe `pull_request_target` usage and privileged workflows
  that execute untrusted code.
- Permissions: flag missing permission blocks, `write-all`, broad
  `contents: write`, `id-token: write`, `packages: write`, `actions: write`,
  and `pull-requests: write` when not needed.
- Supply chain: flag unpinned or mutable third-party actions, curl-pipe-shell,
  unverified downloads, and package install scripts in privileged contexts.
- Preferred fixes: top-level `permissions: read-all` when compatible, per-job
  overrides only when required, OIDC for cloud authentication, action pinning
  when feasible, no untrusted script injection, and separate privileged release
  workflows from untrusted PR workflows.
- Do not add third-party scanners unless the repository already uses them or
  the user approves them.

### Application Baseline

Review language source, dependency manifests, lockfiles, config files, tests,
and framework routes/middleware when present.

- Secrets: hardcoded credentials, tokens, API keys, private keys, passwords,
  connection strings, env dumps, and secret-bearing logs.
- Input validation: untrusted input reaching commands, file paths, SQL/NoSQL,
  LDAP, templates, HTML, URLs, HTTP clients, deserialization, or dynamic code.
- Injection: SQL, shell, template, path traversal, log injection, LDAP, XXE,
  unsafe dynamic evaluation, and unsafe URL handling.
- Deserialization: unsafe parsing of untrusted serialized objects, YAML,
  pickle-like formats, Java serialization, JSON-to-object binding risks, and
  custom parsers without validation.
- Crypto: weak algorithms, insecure randomness for security tokens, static
  IVs, hardcoded keys, disabled certificate validation, and custom crypto.
- Auth: missing authorization checks, role bypasses, insecure session/cookie
  flags, insecure defaults, and unauthenticated admin endpoints.
- Error handling: stack traces, tokens, headers, cookies, and internal paths in
  logs or HTTP responses.
- Dependencies: prefer existing lockfiles and configured scanners. Do not add
  scanners without approval.
- Dangerous defaults: debug mode, permissive CORS, trust-all TLS, public bind
  addresses, insecure file permissions, and unsafe temporary files.
- Backward compatibility: do not change public APIs, auth flows,
  serialization formats, data formats, or routes without approval.

### Python

- Flag `os.system`, `os.popen`, `subprocess` with `shell=True`, unsafe command
  construction, and user-controlled command arguments.
- Flag `eval`, `exec`, `compile`, dynamic imports, unsafe template rendering,
  unsafe plugin loading, `pickle`, `marshal`, `shelve`, multiprocessing
  `Connection.recv`, unsafe YAML loaders, unsafe archive extraction, and
  `tempfile.mktemp`.
- Flag `random` for passwords, tokens, sessions, reset links, or keys.
- Flag SQL f-strings, string formatting, concatenation, or interpolation.
- Flag disabled TLS verification, unverified SSL contexts, plaintext HTTP for
  sensitive traffic, and credentials in URLs.
- Prefer argument arrays, parameterized queries, `pathlib` validation,
  `NamedTemporaryFile`/`mkstemp`, `secrets`, safe loaders, and redaction
  helpers.
- Do not replace serialization formats, database query semantics, or auth
  behavior without approval.

### Java

- Flag `ObjectInputStream` on untrusted data, missing `ObjectInputFilter`,
  custom `readObject` risks, and broad class allowlists.
- Flag SQL string concatenation, `Runtime.exec` with untrusted input,
  `ProcessBuilder` with shell wrappers, LDAP/JNDI injection, and unsafe
  template rendering.
- Flag XML parsers without secure processing or unsafe external entity
  handling.
- Flag insecure algorithms, weak randomness, static IVs, hardcoded keys,
  disabled hostname verification, and trust-all certificate managers.
- Prefer `PreparedStatement`, serialization filters, secure XML parser
  features, `SecureRandom`, narrow command invocation, canonical path
  validation, and secret externalization.
- Do not change serialization compatibility, crypto protocols, or auth flows
  without approval.

### JavaScript and Node.js

- Flag `child_process.exec`, `execSync`, `spawn` with `shell: true`, unsafe
  command construction, and untrusted input in commands.
- Flag `eval`, `new Function`, `vm` with untrusted input, dynamic
  `require`/`import` from user input, and unsafe template rendering.
- Flag prototype pollution via unsafe object merge, deep merge of untrusted
  input, `__proto__`, `constructor`, and `prototype` assignment.
- Flag path traversal, archive extraction risks, public file serving without
  path normalization, SSRF with user-controlled URLs, unsafe `innerHTML`,
  direct HTML injection, JWT decode without verification, weak cookie flags,
  missing CSRF where applicable, permissive CORS, and risky regex patterns on
  untrusted input.
- Prefer `execFile` or `spawn` without shell, URL allowlists, path resolution
  checks, structured validation, redacted logging, secure cookie flags, and
  verified tokens.
- Do not change auth middleware, token validation behavior, or public routes
  without approval.

### TypeScript

- Include all JavaScript and Node.js checks.
- Flag disabled `strict`, disabled `noImplicitAny`, disabled
  `strictNullChecks`, broad `any` on trust boundaries, unsafe assertions, and
  unchecked `unknown` input.
- Flag external input trusted only because of TypeScript types. Recommend
  runtime validation for HTTP requests, queues, config files, and environment
  variables.
- Flag missing guards on security-relevant values such as user identity, tenant
  ID, role, token claims, and authorization context.
- Prefer `unknown` at trust boundaries, explicit narrowing, staged strictness,
  and runtime validation adapters.
- Do not enable strict mode across a whole project automatically if it causes
  many type errors. Produce a staged migration plan.

### Rust

- Flag `unsafe` blocks/functions/traits, mutable statics, raw pointer
  dereferences, FFI boundaries, and missing safety comments.
- Flag `Command` invoking `sh`, `bash`, `cmd`, or `powershell` with
  user-controlled input.
- Flag untrusted deserialization without size limits, schema validation, or
  allowlists.
- Flag `unwrap`, `expect`, `panic`, `unreachable`, and indexing on untrusted or
  network-facing paths.
- Flag path traversal, symlink-sensitive canonicalization assumptions, unsafe
  temporary files, broad permissions, hardcoded secrets, and `Debug` output of
  secret-bearing structs.
- Prefer minimizing unsafe scope, adding safety comments, validating before
  unsafe boundaries, `Command` args without shell, `Result` propagation, and
  avoiding sensitive debug output.
- Do not remove unsafe code automatically. Plan first unless the fix is local,
  obvious, and validated.

### Bash and Shell

- Flag unquoted variables, command substitutions, globs, arrays, and paths
  where word splitting or filename expansion is not intended.
- Recommend `set -euo pipefail` only after checking control-flow impact.
- Flag `eval`, unsafe `xargs`, unsafe `find -exec`, user-controlled command
  construction, backticks, `set -x` around secrets, env dumps, credentials in
  arguments, predictable temp paths, unsafe `rm -rf`, wildcard deletes,
  world-writable files, unsafe `chmod`, curl-pipe-shell, unsigned downloads,
  unqualified privileged commands, unsafe `IFS`, and exported secrets.
- Prefer quoting, arrays, `mktemp`, cleanup traps, `command -v`, no `eval`,
  tracing disabled around secrets, argument validation, and safe temp dirs.
- Do not add strict mode, rewrite control flow, or change destructive
  operations without approval.

## Severity and Confidence

### Severity

- Critical: exposed credentials, public write access, admin CI token exposure,
  unauthenticated admin route, remote command execution, unsafe deserialization
  reachable from untrusted input, or privilege escalation to cluster/cloud
  admin.
- High: public database/admin endpoint, privileged container without clear
  need, cluster-admin binding, wildcard IAM write permissions, shell injection,
  SQL injection, trust-all TLS, or hardcoded production secrets.
- Medium: missing NetworkPolicy, mutable image tags, broad read permissions,
  missing security context controls, permissive CORS, weak crypto, missing
  runtime validation on trust boundaries, or unsafe file path handling.
- Low: missing documentation, weak defaults with explicit safeguards elsewhere,
  minor compiler hardening gaps, or non-sensitive debug logging.
- Info: improvement suggestions without direct risk.

### Confidence

- High: repository code clearly shows the risky path and the exploitability is
  not dependent on unknown deployment context.
- Medium: the risky pattern is present, but reachability, environment, or
  compensating controls are not fully known.
- Low: the pattern is suspicious but could be safe depending on runtime
  configuration, generated code, or external controls.

## Safe Auto-Fix Policy

Safe to patch directly when the change is local, reviewable, and unlikely to
break intended behavior:

- missing compatible `securityContext` fields
- obvious log redaction
- unquoted Bash variables where word splitting is not intended
- `shell=True` removal when equivalent args are clear
- missing workflow `permissions: read-all`
- `sensitive = true` on Terraform variables or outputs
- non-breaking Helm values that preserve override flexibility

Plan first:

- public endpoint changes
- IAM or RBAC narrowing
- NetworkPolicy default-deny
- TypeScript strict mode
- Java deserialization behavior
- crypto changes
- auth changes
- CORS changes
- production service exposure
- shell strict mode

Never auto-fix without explicit approval:

- credential rotation
- git history rewriting
- deleting resources
- disabling features
- changing public APIs
- changing database schemas
- replacing serialization protocols
- changing auth algorithms
- changing externally visible routes

## Safe Override Policy

Every intentional exception should include:

- explicit opt-in variable or value
- owner
- reason
- narrow scope
- `reviewBy` date
- visible warning in scan output

Examples:

- `allowPublicExposure: true`
- `allowPrivileged: true`
- `allowUnsafeDeserialization: true`
- `allowShellExecution: true`
- `allowInsecureTls: true`
- `securityException.owner`
- `securityException.reason`
- `securityException.scope`
- `securityException.reviewBy`

## Production Hardening Extension

- Treat public exposure as denied by default unless an explicit
  `allowPublicExposure` or equivalent variable exists.
- Require exceptions to include owner, reason, scope, and `reviewBy` date.
- Separate development, staging, and production defaults when environment
  intent is visible.
- Add staged rollout plans for stricter TypeScript, NetworkPolicy, IAM, and
  runtime security changes.
- Keep a no-auto-fix list for availability, authentication, authorization,
  crypto, data retention, and external network access.

## Policy-as-Code Compatibility

Phrase findings so they can later map to OPA, Conftest, Checkov, tfsec,
Terrascan, or cloud-native policy engines:

- stable finding ID
- normalized area
- resource/file selector
- risk condition
- allowed exception fields
- remediation fields
- severity and confidence
