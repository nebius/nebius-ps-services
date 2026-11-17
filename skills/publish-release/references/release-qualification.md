# Exact-commit release qualification

For ordinary repositories, `.github/merge-policy.json` declares `release_profiles`
keyed by project directory. Each profile identifies its publisher and required
CI producers, job IDs, complete matrix job names and required report artifacts.
The profile is read from the immutable merged commit and its digest is retained.
A missing or ambiguous profile fails clearly; current-main policy cannot replace
historical policy. Already-tagged releases keep their original publication proof.

Before creating a tag, call the private owner action:

```text
python3 <skill>/scripts/release_session.py qualify --project-dir <project> --tag <tag>
```

It verifies existing proof or journals and explicitly dispatches only missing
producers on trusted default-branch workflow code. An uncertain dispatch is
reconciled by its unique operation identity before any further action; it is
never blindly repeated. Poll this action within `verification-budget`; keep
minute progress. Pending is not success. A failed qualification requires repair
and an explicitly selected complete rerun, never an implicit rerun request.

`qualification_sha`, `qualification_profile` and `qualification_id` are paired
workflow inputs, mutually exclusive with candidate/broker modes. Test jobs have
read-only permissions. `release-qualification/v1` records numeric repository,
profile digest, tested commit/tree, workflow revision, run/attempt, required job
outcomes and report identities/digests. The tag publisher independently verifies
these facts. Required matrix members, native cases and package histories cannot
be skipped or narrowed. Partial job reruns do not combine earlier attempts.

Qualification and referenced report evidence are retained for 14 days. Missing,
expired, forged, incomplete or superseded evidence blocks. A new exact-commit
qualification may be requested; unrelated CI/Pages and optional live campaigns
are not added. Keep final tagged runtime version, wheel build/install/CLI checks
and independent remote asset byte verification.

State observation uses short locks and compare-and-set snapshots. Sleeping or
read-only network polling never holds the checkout-wide state lock. Effects have
one OS-lock owner; no timeout lease steals authority. Concurrent resume or
identity reconciliation invalidates an older observation. The four phase budgets
are each 28,800 seconds; existing deadlines change only through explicit resume.
