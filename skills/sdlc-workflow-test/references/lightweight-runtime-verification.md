# Lightweight runtime verification

Required for full lightweight verification after deterministic preflight.

1. Run the disposable golden-path workflow when full verification is requested.
   Use the disposable project only. Explicitly load and follow these phase
   skills in order:
   `sdlc-start` (direct objective acceptance),
   `sdlc-create-requirements`, `sdlc-gather-context`,
   `sdlc-create-design`, `sdlc-auto-steering`,
   `sdlc-create-plan`,
   `sdlc-prepare-execution`, `sdlc-tdd`,
   `sdlc-implement-plan`, `sdlc-validate-codes`, `sdlc-unit-tests`,
   `sdlc-evaluate`, `sdlc-update-documents`, `align`,
   `sdlc-commit`, and `sdlc-uat-tests`. Run `sdlc-update-documents` again
   after UAT when final docs changed. Do not use `sdlc-merge-pr`, and do not
   create a real PR.
2. Verify rerun and change-request behavior.
   Repeat `$sdlc-start continue [project-folder]` with no objective
   changes, then accept a new objective revision with the safe change
   request from the checklist and confirm stable IDs, immutable locked plans,
   scoped changes, refreshed evidence, and no duplicate commits.
3. Verify failure routing and steering behavior.
   Inject one controlled failure at a time in the disposable project, verify
   a proven mechanical cause bypasses troubleshooting, an ambiguous cause
   enters `troubleshoot` exactly once and returns its diagnosis to
   `sdlc-classify-failure`, unresolved evidence stops, and a proven localized
   defect re-enters through a corrective plan and appended wave. Exercise a
   budget stop without resetting counters, then repair and rerun. Accept the
   pause/no-PR instruction as steering and verify explicit continuation.
4. Verify continuation and optional harness smoke checks.
   Exercise Stop continuation with fake state and, where available, run safe
   GUI and TUI smoke checks against local disposable targets only.
5. Persist and ingest live results.
   Use the identity in `verification-context.json`. Write only the v3 manifest
   and relative evidence paths defined by `../assets/live-results.schema.json`.
   Collect raw evidence with `../scripts/collect_live_evidence.py`; do not copy or
   hash assertion/profile artifacts ad hoc. Each evidence profile must name its
   exact allowed source schema and at least one collector-produced source
   artifact. Profile status and assertion booleans alone are never provenance.
   Structurally valid generic artifacts are PARTIAL only. PASS is fail-closed
   until both the assertion and its canonical profile source have dedicated
   machine-semantic validators; never promote a source by relabeling it.
   Deterministic and merge-safety profiles are verifier-derived rather than
   supplied in the live manifest: they become valid only when every mapped
   deterministic capability passes. Merge safety also requires executed
   qualification, protected admission and recovery regressions; hook denials
   alone are insufficient, and these checks do not qualify a real remote merge.
   Lightweight PASS remains fail-closed until its collector derives exact
   claims from checkpoint, Git, test, steering, document, and repaired-failure
   bytes. Three-tier PASS requires the copied source to byte-match
   the canonical result resolved through the profile source identity and owned
   lifecycle under the verification root, and pass the strict layer, artifact, phase,
   Git, ordered-GUI, correlation, and restart-persistence validator.
   Evidence must stay under `evidence/<lane>/` within the verification root,
   use private permissions, match the exact preserved baseline/final Git
   identity, include a real selected-scope golden-path commit, keep every commit
   in the live history inside the selected nested project, exclude private SDLC
   state, and never contain prompt bodies or secrets. Rerun the verifier with
   `--live-evidence PATH`. Aggregate identity and Git history bind the lightweight
   fixture; the three-tier source identity and history bind its own lifecycle.
   Validate live evidence before cleanup; removed canonical evidence cannot
   later be replaced by a sanitized report or copied source alone.
