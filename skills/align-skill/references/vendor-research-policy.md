# Vendor Research Policy

Use this policy whenever a target skill references a product, framework, SDK,
CLI, API, cloud service, hosted platform, package manager, or security control.

## Source Priority

1. Official vendor documentation.
2. Official GitHub repositories from the vendor.
3. Official API references.
4. Official examples.
5. Repo-local evidence.

Non-authoritative sources such as blogs, tutorials, Stack Overflow, forum
answers, generated examples, and memory may be used only as leads. Do not use
them as final support for vendor-specific behavior.

## Rules

- Do not rely on memory for vendor-specific behavior.
- Check current official documentation before changing commands, flags,
  examples, SDK usage, authentication guidance, API behavior, or service
  limits.
- If official documentation conflicts, prefer the newest official vendor
  documentation and disclose the conflict.
- If no official documentation can verify a behavior, mark it as unverified.
- If the vendor docs do not cover a repo-specific wrapper, use local code and
  tests as repo evidence and mark the vendor boundary clearly.

## What to Verify

Verify any target skill content involving:

- CLI commands, flags, config files, or environment variables.
- SDK classes, methods, parameters, return values, and auth flows.
- Cloud resources, IAM roles, quota, regions, project or tenant scopes, and
  billing-sensitive operations.
- Kubernetes, Helm, Terraform, CI/CD, package publishing, databases, and
  external service writes.
- Security controls, secret handling, credential storage, token scopes, and
  production access.

## Reporting

Final reports must list:

- Vendor docs checked.
- Behavior confirmed by official docs.
- Behavior inferred from repo evidence.
- Behavior left unverified and why.
