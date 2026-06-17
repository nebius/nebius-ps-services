# Apply Security Examples

## Contents

- Terraform
- Kubernetes
- Helm
- CI/CD
- Python
- Java
- JavaScript
- TypeScript
- Rust
- Bash

Use these as patterns, not templates to paste blindly. Preserve local style,
existing helper APIs, and intended behavior.

## Terraform

Risk: public ingress lacks an explicit exception.

Before:

```hcl
resource "aws_security_group_rule" "web" {
  type        = "ingress"
  from_port   = 443
  to_port     = 443
  protocol    = "tcp"
  cidr_blocks = ["0.0.0.0/0"]
}
```

After:

```hcl
variable "allow_public_web_ingress" {
  type        = bool
  description = "Allow public HTTPS ingress for the web endpoint."
  default     = false
}

resource "aws_security_group_rule" "web" {
  count       = var.allow_public_web_ingress ? 1 : 0
  type        = "ingress"
  from_port   = 443
  to_port     = 443
  protocol    = "tcp"
  cidr_blocks = ["0.0.0.0/0"]
}
```

Compatibility note: plan first for existing public endpoints because disabling
the rule can affect availability.

## Kubernetes

Risk: container can gain extra privileges and lacks default runtime hardening.

Before:

```yaml
containers:
  - name: app
    image: example/app:1.2.3
```

After:

```yaml
securityContext:
  runAsNonRoot: true
  seccompProfile:
    type: RuntimeDefault
containers:
  - name: app
    image: example/app:1.2.3
    securityContext:
      allowPrivilegeEscalation: false
      capabilities:
        drop:
          - ALL
```

Compatibility note: verify image user and write paths before adding
`readOnlyRootFilesystem: true`.

## Helm

Risk: chart lacks digest support and hardcodes a mutable tag.

Before:

```yaml
image:
  repository: example/app
  tag: latest
```

After:

```yaml
image:
  repository: example/app
  tag: "1.2.3"
  digest: ""
```

Template pattern:

```gotemplate
{{- $image := printf "%s:%s" .Values.image.repository .Values.image.tag -}}
{{- if .Values.image.digest -}}
{{- $image = printf "%s@%s" .Values.image.repository .Values.image.digest -}}
{{- end -}}
image: {{ $image | quote }}
```

Compatibility note: keep tag override flexibility unless the user requires
digest-only deployment.

## CI/CD

Risk: workflow gets broad default token permissions.

Before:

```yaml
name: ci
on: [pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
```

After:

```yaml
name: ci
on: [pull_request]
permissions: read-all
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
```

Compatibility note: add per-job write permissions only for jobs that need
them. Do not grant secrets or write tokens to untrusted PR code.

## Python

Risk: shell command construction can execute attacker-controlled input.

Before:

```python
subprocess.run(f"tar -tf {archive_path}", shell=True, check=True)
```

After:

```python
subprocess.run(["tar", "-tf", str(archive_path)], check=True)
```

Compatibility note: only auto-fix when the command has an obvious argument
array equivalent. Preserve shell behavior only with an explicit, justified
exception.

## Java

Risk: SQL query uses string concatenation with untrusted input.

Before:

```java
String sql = "select * from users where email = '" + email + "'";
ResultSet rs = connection.createStatement().executeQuery(sql);
```

After:

```java
String sql = "select * from users where email = ?";
try (PreparedStatement stmt = connection.prepareStatement(sql)) {
    stmt.setString(1, email);
    try (ResultSet rs = stmt.executeQuery()) {
        // Existing result handling.
    }
}
```

Compatibility note: preserve query semantics. Do not change authentication or
authorization behavior as part of an injection fix.

## JavaScript

Risk: shell execution with user-controlled input.

Before:

```javascript
const { exec } = require("node:child_process");
exec(`git show ${ref}`, callback);
```

After:

```javascript
const { execFile } = require("node:child_process");
execFile("git", ["show", ref], callback);
```

Compatibility note: validate `ref` separately if the command accepts values
that could change operation semantics.

## TypeScript

Risk: external input is trusted because of a type assertion.

Before:

```typescript
const body = req.body as CreateUserRequest;
await createUser(body.email, body.role);
```

After:

```typescript
function isCreateUserRequest(value: unknown): value is CreateUserRequest {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Record<string, unknown>;
  return typeof candidate.email === "string" && candidate.role === "user";
}

if (!isCreateUserRequest(req.body)) {
  throw new Error("Invalid request");
}

await createUser(req.body.email, req.body.role);
```

Compatibility note: prefer local runtime guards on trust boundaries before
proposing project-wide strict mode changes.

## Rust

Risk: untrusted input can trigger a panic.

Before:

```rust
let user_id = parts[1].parse::<u64>().unwrap();
```

After:

```rust
let user_id = parts
    .get(1)
    .ok_or_else(|| Error::InvalidInput("missing user id".into()))?
    .parse::<u64>()
    .map_err(|_| Error::InvalidInput("invalid user id".into()))?;
```

Compatibility note: use the repository's existing error type and style. Do not
remove `unsafe` automatically unless the replacement is local and validated.

## Bash

Risk: `eval` executes user-controlled text.

Before:

```bash
eval "$command"
```

After:

```bash
case "$command" in
  status)
    run_status
    ;;
  deploy)
    run_deploy
    ;;
  *)
    printf 'unsupported command: %s\n' "$command" >&2
    exit 2
    ;;
esac
```

Compatibility note: do not rewrite command dispatch if it changes supported
behavior. Plan first when the command language is part of the public interface.
