# Owned Test Evidence

Apply this contract only inside the explicitly requested three-tier live test.
The ordinary deterministic verifier does not launch application tests.

After local ship has produced the clean promoted deployment, the outer verifier
runs each of `unit`, `api`, `database`, `migration` and `vertical` through the
private lifecycle helper. Supply a distinct dotted Django test label for each
class, selected from the reviewed product tests:

```text
three_tier_lifecycle.py --verification-root <owned-root> \
  --expected-verification-id <immutable-id> run-test-class \
  --test-class unit --label tasks.tests.test_unit
```

This is an internal helper, not a new public skill action. It holds the same
generation lock as Compose and runs a fixed `DiscoverRunner` adapter using
`docker compose exec -T web python`. It reads result counters directly from
`unittest.TestResult`, suppresses product stdout/stderr, and records no raw
test output. The command has a five-minute timeout. Product tests must use the
standard Django runner interface; unsupported output never becomes PASS.

Run all five classes before the first UAT browser stage and before the declared
UAT fixture reset. Django database tests use a separate test database; review
vertical HTTP tests for live-fixture effects. Do not execute them between UAT
stages or treat fixture setup as proof of a product transition.

Each immutable receipt under `evidence/tests/` binds the verification ID, test
class and label, runner digest, exact promoted source, owned build/image and
container, process exit status, executed count and failure/skip counters. The
lifecycle state binds its path and digest. PASS requires a successful exit,
positive executed count and no failures, errors, skips, expected failures or
unexpected successes. An interrupted or failed attempt cannot be retried into
success within the same trial. Preserve it and start a fresh trial after repair.
Repeating the identical completed action only verifies and reuses its receipt.

In `three-tier-results.json`, each non-GUI test entry has `status: PASS`,
`assertions` equal to its observed executed-test count, and `evidence` containing
exactly its canonical receipt path. The historical field name `assertions`
counts completed tests, not individual assertion statements. Do not hand-author
receipts, inflate counts, relabel one execution, or substitute log files.

The GUI entry reuses the existing owned `uat-before-restart` and
`uat-after-restart` receipt paths, in order. Its count is the sum of the passing
tests in their hash-verified Playwright reports. It needs no second GUI runner
or copied artifact. Missing or modified receipts, source/image drift, failed
reports and foreign identities fail admission. Partial reports downgrade
unverified PASS claims instead of repeating them as passing tests.

These checks establish execution provenance and outcomes. Reviewing the test
labels and assertions remains necessary to judge product coverage. Offline
synthetic fixtures prove the reader/runner contract, not a real application run.

Official interfaces: [Django testing](https://docs.djangoproject.com/en/5.2/topics/testing/overview/),
[Django test runner](https://docs.djangoproject.com/en/5.2/topics/testing/advanced/#django.test.runner.DiscoverRunner),
[Python unittest results](https://docs.python.org/3.12/library/unittest.html#unittest.TestResult),
and [Compose exec](https://docs.docker.com/reference/cli/docker/compose/exec/).
