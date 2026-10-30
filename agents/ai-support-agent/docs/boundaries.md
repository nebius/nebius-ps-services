# Boundary checks and publication

## Before transferring anything publicly

1. Identify the candidate files, outgoing commits and every intended destination,
   including Git branches, PRs, Pages, logs and release artifacts.
2. Review ownership, rights and classification. Use synthetic examples and
   placeholders. Do not copy a private design workspace or deployment bundle.
3. Run the public scan over the candidate source and every produced artifact.
   Also scan the outgoing local commit range before any push. Changes made after
   review require a new scan and review of the changed candidate.
4. Resolve findings. Treat unknown classification or uninspected content as
   pending review, not a clean publication decision.

The checker cannot identify all confidential prose or personal/customer data.
Its success does not authorize publication or prove the absence of secrets.
There is no probabilistic customer-data filter. Ordinary synthetic names,
emails and messages are accepted. Classification/rights review remains necessary.

## Commands

```bash
python3 -B tools/check_boundaries.py scan --root . --destination public --git-files
python3 -B tools/check_boundaries.py scan --root . --destination public --git-files --history origin/main..HEAD
python3 -B tools/check_boundaries.py scan --root /path/to/output --destination public
python3 -B tools/check_boundaries.py scan --root /path/to/private-repository --destination private --git-files
python3 -B tools/check_boundaries.py scan --root /path/to/compiled-bundle --destination application-bundle
```

Run history scans from the selected project directory; the range covers all
outgoing commits affecting that directory, including files subsequently deleted.
For a first publication, use `--history HEAD` to inspect its entire local history.
Shallow or incomplete history is rejected. Git lazy fetching and replacement
objects are disabled; missing objects cause a sanitized input failure.
Other changed monorepo directories and workflow files need their own scoped scan.
`--git-files` includes tracked files even when a new ignore rule matches them,
and includes nonignored untracked files. Omit it for build output directories so
ignored outputs cannot escape inspection. A missing/empty range or scan is an error.

`--private-values /path/outside/scan-root/values.json` adds exact comparisons
against a private JSON array of identifiers or endpoints. Use it only on a
trusted workstation or private CI. Keep that input outside publication roots;
never upload it to public CI or save matched values in reports. Generic public
checks have no access to private values. Findings contain only redacted relative
locations, line numbers and stable codes. Exit codes are 0 (no detected violation),
1 (violation or uninspected content), and 2 (invalid/unreadable check input).

Public structured deployment fields require obvious placeholders, such as
`<project-id>`, `EXAMPLE_PROJECT` or `${PROJECT_ID}`. This is an example-format
rule, not a claim that an identifier can be proven real from its appearance.

## Coverage and limits

Checks cover private-key material, literal credential fields, Kubernetes Secret
payloads, known private values, structured deployment identities, recognizable
private/credential-bearing URLs, forbidden runtime files and symlinks. Private
deployment fields must live under `clusters/dev/` or `clusters/prod/`; an explicit
environment declaration must match its directory. Required privacy rules apply
regardless of filename, and allowed infrastructure files still undergo content checks.

ZIP/wheel and TAR/gzip archives are inspected in memory without extraction,
including nested archives and image layers exported as TAR. Archives containing
traversal, links or Git metadata fail. Limits are 16 MiB per file, 64 MiB expanded
archive content, 2,000 members and three archive levels. Unreadable, oversized,
unsupported binary or malformed archive inputs fail closed. There is no support
yet for fetching images, OCI manifests, Zstandard layers, compiled binaries,
images/fonts, PDF or semantic classification of document contents. A future
release pipeline needs a qualified inspector for each such format before release.

Recognized credential patterns are deliberately finite; obfuscated values,
arbitrary renamed credential fields and unknown internal domains may escape
detection. Neither ignore rules nor this tool replace review. Do not weaken
the scan by excluding a real credential, skipping an artifact or accepting an
unchecked package because a source-only check passed.

## Application bundles and infrastructure artifacts

Private Git may retain reviewed, nonsecret generated Terraform and sanitized
infrastructure manifests/reports. It must not retain Terraform state/plans,
kubeconfigs, private keys, database dumps or raw runtime logs. Infrastructure
artifacts remain outside application configuration bundles; private executable
product extensions are prohibited.

The current narrow bundle check accepts only top-level `application`,
`integrations`, `policies`, `secret-references` and `target-binding`
files with JSON/YAML extensions. It rejects other paths, executable artifacts,
Git metadata and links. Release selection and deployment receipts stay detached
from the hashed configuration payload; `release` files are rejected here. This path allowlist is a tooling guardrail, not an
implementation of the future schema-aware configuration compiler. Component
value subsets require that compiler's reviewed allowlist before becoming supported.

## Environment target comparison

```bash
python3 -B tools/check_boundaries.py verify-target --desired /path/to/desired.json --observed /path/to/observed.json
```

Both JSON objects must contain exactly `environment` (`dev` or `prod`),
`project_id`, `cluster_id`, `state_namespace`, `object_namespace`,
`backup_namespace` and `secret_namespace`, all as nonempty, nonplaceholder strings.
Every field must match. Obtain observed bindings independently from the selected
execution context; copying desired values into observed values proves nothing.
The command does not query a cluster, validate credential authority or perform
an apply. The future deployment owner must obtain fresh observations and call
this check before any apply/activation; current CI is not live-target evidence.

Prod stays unconfigured until separately supplied. Do not substitute Dev bindings.
Promote public artifact versions, then create and review each environment's
configuration; never promote credentials, customer records or state by copying Dev.

## Private tool distribution

Private repositories may mirror this exact public checker under `.ci/` with a
reviewed SHA-256 pin, checking the pin before execution. This is a distribution
copy, not a private implementation or a runtime configuration bundle. Update the
public source first, rerun tests, then refresh the mirror and pin together.
Hash equality detects drift relative to the pin; it does not establish a trusted
publisher or replace review of changes to the tool, pin and CI configuration.
