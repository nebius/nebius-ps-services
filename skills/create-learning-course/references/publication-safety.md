# Publication Safety

Use this reference before calling any generated course public-safe, before
turning private material into course content, or before preparing course files
for sharing.

## Default Policy

Course artifacts are public-safe by default. If the user wants a private-only
course, mark that status in `COURSE.md` and `PUBLICATION-REVIEW.md`. Private
course status does not allow storing prohibited data. Secrets, private
endpoints, customer data, regulated data, raw logs, credential material, and
non-public URLs remain never-write.

Never add these to generated course files, examples, reports, task state, or
final answers:

- API keys, access tokens, certificates, passwords, private keys, cookies, or
  credential file paths
- private endpoints, internal hostnames, non-public URLs, internal repository
  names, customer names, account IDs, tenant IDs, project IDs, cluster names,
  or support ticket identifiers
- raw logs, stack traces, telemetry dumps, transcripts, screenshots, or
  environment dumps that may contain secrets
- proprietary scripts, internal operating procedures, or confidential source
  text that is not explicitly approved for public release
- personal data, student data, customer data, or regulated data

Use placeholders instead: `{API_TOKEN}`, `{PRIVATE_ENDPOINT}`,
`{CUSTOMER_NAME}`, `{PROJECT_ID}`, `{INTERNAL_SYSTEM}`, `{REDACTED_LOG}`.

## Private Source Handling

When source material is private or internal:

1. Use it only to understand the learner need and rough shape of examples.
2. Extract public-safe abstractions: concepts, anonymized workflows, generic
   mistakes, and neutral scenarios.
3. Replace identifiers, logs, paths, endpoints, and names with placeholders.
4. Cite only public sources in public course artifacts.
5. Store non-secret private planning notes only when the user explicitly asks,
   and place them outside the publishable course root, such as
   `<course-folder>.private/NOTES.md`.
6. Do not copy private source excerpts into reusable skill files.

If a private detail is essential to the course, stop and ask whether the course
can use a generalized placeholder instead. Never persist prohibited data.

## Source And License Review

Before sharing a course:

- Prefer original explanations over copied source text.
- Keep quotes short and cite the source.
- Check whether diagrams, screenshots, examples, datasets, or code snippets
  have a license that permits the intended use.
- Do not copy paywalled, proprietary, or confidential training material into
  course files.
- Use generated placeholder examples when real customer or infrastructure
  examples would expose sensitive details.

## HTML Lesson Safety

HTML lessons should be inspectable, local, and dependency-light:

- no analytics, trackers, beacons, or telemetry
- no remote JavaScript
- no remote fonts or unreviewed CSS frameworks
- no embedded private URLs
- no hidden form submission or network calls
- local CSS under `assets/` when possible

Interactive lessons may use small inline scripts only when they do not call the
network, collect personal data, or hide behavior.

## High-Stakes Topics

For medical, legal, financial, safety, security, compliance, or certification
training:

- cite authoritative public sources
- include scope limits in `COURSE.md`
- add an expert-review requirement to `PUBLICATION-REVIEW.md`
- avoid claiming certification, compliance, or professional advice
- distinguish educational examples from operational instructions

## Static Review

Before reporting that a course is public-safe:

1. Read `PUBLICATION-REVIEW.md` and update every checklist item.
2. Inspect changed files for placeholder quality, private identifiers, and
   missing citations.
3. Use file-listing scans when useful, but do not paste suspected secret values
   into chat. If a match appears, report the file and risk class only.
4. Confirm optional sibling private notes are not included in any public bundle.
5. Confirm every public artifact has only public-safe sources and examples.

Suggested local scan classes:

- secret words: key, token, secret, password, credential, certificate
- private infrastructure: internal, private, localhost, cluster, tenant,
  project, account, endpoint
- customer or personal data markers: customer, email, phone, address, ticket
- raw-log markers: traceback, stack trace, request id, authorization, cookie

Treat scans as prompts for manual review, not proof of safety.

## Publication Review Result

Use these statuses:

- `Draft`: course is incomplete or unreviewed.
- `Private`: course intentionally contains non-public material and must not be
  shared.
- `Public-safe pending review`: no known sensitive content, but human review is
  still needed.
- `Public-safe reviewed`: static review completed, no known sensitive content,
  citations checked, and required expert reviews are either complete or clearly
  marked as open.
