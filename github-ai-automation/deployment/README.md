# Prepared deployment

These templates preserve the complete automation implementation without activating
it when its source is merged. Only `github-ai-automation-ci.yml` is installed in
`.github/workflows` for source validation.

A separately authorized, reviewed deployment installs `ai-automation.json.template`
as `.github/ai-automation.json`, `merge-policy.json.template` as the merge policy,
and the workflow templates under `.github/workflows` with `.template` removed.
The prepared service release workflows are build-only; they must be deployed
together with their shared release owner. Follow the [hosted rollout guide](../docs/hosted-rollout.md)
for qualification and controls before admitting any automated work.

Until then, the installed ordinary protected merge and tag release workflows
remain the canonical owners. Maintainers continue using `$create-pr`, `$merge-pr`
and `$publish-release`. No controller environment or credentials are needed.

The installed AI Support Agent CI now verifies scaffold retirement. Preserve its
workflow identity while old completion receipts can reference it. A later reviewed
change may remove its policy entry and listeners while retaining the workflow;
only after those receipts settle may another change remove the workflow itself.
