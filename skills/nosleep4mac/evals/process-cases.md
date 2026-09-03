# Supplemental Process Cases

These cases preserve detailed workflow and output-quality expectations.
`trigger-prompts.csv` is the sole canonical trigger authority; this document
does not define skill routing.

## Canonical Routing Coverage

Fresh routing checks use CSV rows `nosleep-positive-01` through
`nosleep-positive-03` and `nosleep-negative-01` through
`nosleep-negative-07`. This supplemental file intentionally defines no
additional routing cases.

## Static Runtime Check

Confirm that `agents/openai.yaml` keeps
`policy.allow_implicit_invocation: false`. Runtime activation is proven only
after observing a fresh Codex surface load an installed copy.
