# Remediation Budget Hook Bundle

This optional hook bundle enforces the remediation budget recorded by the
`troubleshoot` skill in the current session's private `current.md` file. The
skill owns semantic classification; the hook only validates the bounded marker,
blocks supported tool calls after exhaustion, and requires the final report.

## Files

- `remediation_attempt_guard.py`: `UserPromptSubmit` and `PreToolUse` handler,
  plus the troubleshooting Stop evaluator called by the arbiter.
- `stop_lifecycle_arbiter.py`: byte-identical single Stop registration shared
  with project-spec observer, prompt-session, and SDLC bundles. It has no
  project-spec delegate.
- `tests/test_remediation_attempt_guard.py`: disposable unit tests.

At runtime the handler may create
`remediation-budget-authorization.json` beside the advertised `current.md`.
That private sidecar is session state, never a repository artifact.

Every explicit `$troubleshoot` invocation also creates mode-0600
`troubleshoot-report-obligation.json` in the same 0700 session directory.
The sidecar records invocation and transactional delivery; it is not a
general workflow lock. The ordinary report has four required sections:
`Outcome`, `Root Cause And Fix`, `Verification`, and `Next Action`, with an
optional bounded `Evidence Appendix`. Schema v3 contains only its schema,
workspace/session/turn bindings, and one status: `active`, `delivered`,
`advisory_incomplete`, `sensitive_detected`, or `fallback`. A v2 sidecar is
preserved and requires a fresh Codex session; it is never migrated in place.

A valid concise report is finalized only after the shared Stop arbiter proves
that no SDLC or prompt-session delegate still needs continuation. Project-spec
status is not a Stop delegate. A peer continuation remains authoritative. An
ordinary incomplete, malformed,
partial, `FAIL`, or `UNKNOWN` report records `advisory_incomplete` and returns
`continue: true`; it requests no corrective turn, denies no later tool, and
emits no generated fallback. Prefer repository-relative inline labels. Inline
or Markdown local targets may instead use repository-relative, native
absolute, home-relative, or strict local `file:` syntax when decoded canonical
resolution keeps them inside the full Git repository root derived from the
event working directory. Without a proven Git root, absolute, home-relative,
and local `file:` forms remain unsafe. This admits sibling-project evidence
while rejecting outside-root, traversal, ambiguous or renderer-active link
syntax, unsafe URI, and symlink escape. Contained format defects are advisory.
Strict fallback normalizes contained targets, redacts unsafe values, and never
truncates reference markup. Sensitive or unsafe ordinary output atomically records
`sensitive_detected`, stops with one generic non-report warning, and requests
no automatic replacement report. Stop cannot retract an
assistant message already rendered by the host. Invalid trusted state, missing
authority, exact remediation-budget exhaustion, and peer Stop policy remain
fail-closed.

Supported outcomes include `DIAGNOSED-FIXED` for a proven cause, applied
owner-correct repair, and passing reproducer, regression, and source or
affected-boundary checks when installed or live activation remains explicitly
unverified. `VERIFIED_FIXED` remains end-to-end proof;
`DIAGNOSED_NOT_FIXED` remains diagnosed but unrepaired.

## Runtime Contract

The event payload and output shapes follow the current
[Codex Hooks guide](https://learn.chatgpt.com/docs/hooks), including
`UserPromptSubmit` turn/prompt fields, bounded `additionalContext`, prompt
blocking, and concurrent same-event command handlers.

- Missing task state or a missing remediation marker fails open for budget
  enforcement. An active ordinary report obligation remains advisory; its
  sensitive-output boundary is independently enforced.
- The shared Stop arbiter evaluates every delegate even after a terminal result
  so peer cleanup and lifecycle transitions run. It preserves the first
  terminal result in delegate order, while a peer continuation still defers
  valid-report finalization. A finalization failure after a peer terminal keeps
  that terminal authoritative and adds only a generic trusted-state warning.
- A present malformed marker fails closed until the parent repairs the exact
  advertised `current.md` file. The denial includes a bounded public-safe
  validation reason without reflecting marker content or filesystem exception
  details; repair itself consumes no attempt and does not require an exhaustion
  report.
- Attempt entries are completed remediation-and-verification records. If a
  partial planned entry is present, the denial lists all missing canonical
  fields together and directs the parent to remove unverified progress or
  complete a verified record atomically instead of repairing one field at a
  time.
- An unsupported attempt result names the two canonical values and directs
  the parent to remove unverified progress into prose or replace a causally
  independent blocker's marker with a fresh empty ledger. The guard does not
  add aliases or reflect the invalid value.
- A marker moved beyond the first 12 KiB or a task-state file above the 1 MiB
  safety limit fails closed instead of being treated as absent.
- Contradictory lifecycle state fails closed: active or resolved markers cannot
  retain a stop trigger, and an exhausted trigger must match a reached limit.
- Canonical v4 state defaults to five attempts and 120 active minutes and permits
  user-authorized profiles only through the hard 10/180 maxima. A
  task-specific earlier stop is workflow guidance in prose, not a marker limit
  override. `override_summary` records only same-blocker continuation.
- The UserPromptSubmit handler parses optional `--attempt-limit=N` and
  `--time-limit-minutes=N` flags only after an exact leading `$troubleshoot`.
  It stores no raw prompt. The selected session profile is atomically written
  to `remediation-budget-authorization.json`, mode 0600, under the session's
  0700 private task-state directory and is bound to hashes of the workspace,
  session, and user turn.
- A bare invocation keeps the session profile, one flag preserves the other
  value, and explicit 5/120 resets the defaults. Active or resolved-state
  changes require both resulting limits to remain strictly above consumed
  attempts and active time.
  A resolved marker remains completed evidence: a later bare invocation does
  not create pending state or gate discovery, while explicit profile flags use
  the profile-only handshake and preserve its non-profile fields. While a
  marker update is pending, only one exact `apply_patch` targeting the
  advertised `current.md` is admitted: `*** Update File` when it exists or
  `*** Add File` only when it is absent. `*** Delete File`, delete/add
  replacement, shell rewrites, and every other tool are denied. Pending feedback reports a precise
  bounded missing-marker,
  invalid-marker, or invalid-transition reason before the repair action instead
  of masking it with generic pending guidance. Next-tranche guidance requires
  one complete canonical fresh marker, including a public-safe
  `blocker_summary`, and distinguishes exact-key same-blocker continuation from
  a new-key causally independent blocker. It refers to the source as the prior
  terminal marker only for a post-exhaustion handoff. For an invalid
  active-resize marker,
  it requires atomic restoration of every non-profile field plus the authorized
  profile fields. A deleted resize marker remains fail-closed: bounded sidecar
  metadata cannot reconstruct it, so guidance requires the exact prior marker
  or a fresh user-authorized troubleshoot session and never suggests a reset.
- Attempt labels are derived from list order as `attempt-1` through the
  configured limit, up to `attempt-10`; an authored ID is ignored. Every active
  recorded retry must have
  a `blocker_key` exactly matching the marker's one blocker, a unique
  `distinct_key`, normalized hypothesis, and normalized `new_evidence`
  summary. Missing or mismatched binding makes the marker invalid, so a ledger
  copied onto a causally independent blocker enters marker repair rather than
  exhaustion. The guard catches structural or textual inconsistency; the skill
  remains responsible for blocker classification, semantic novelty, and
  pre-remediation timing.
- The canonical data schema is `codex/remediation-budget-v4`. The surrounding
  `codex-remediation-budget:v1` HTML marker remains the stable locator.
  Historical v1 data is accepted only when already exhausted and only for
  report delivery; it keeps its original three-attempt ceiling, may omit
  `new_evidence`, never authorizes a retry, and requires the missing
  evidence-record limitation in the report. Previous v2 and v3 state fails
  closed and requires exact marker repair before more work; v3 is not
  reinterpreted and the hook does not maintain a dual-limits compatibility
  path. Newly written state must use v4.
- A causally independent blocker starts with a fresh marker and budget. The
  parent owns that classification and must write an empty ledger. The hook
  requires consistent attempt bindings but does not infer semantic blocker
  identity or detect deliberately false relabeling.
- Exhaustion creates a private terminal lock. Clearing the ledger or relabeling
  the blocker cannot reopen that marker. Deleting `current.md` remains
  fail-closed: only an exact marker restore is admitted before another tool, or
  the next user turn may authorize a fresh same-blocker tranche or causally
  independent blocker.
- The time limit uses parent-accounted active seconds, so time spent waiting for
  the user, model capacity, or an external event does not consume the tranche.
- Once the attempt or time limit is exhausted, every supported tool call is
  denied except an `apply_patch` that updates only that `current.md` file.
- For exhausted state, the Stop evaluator requests one corrected report with the
  marker-bound evidence and includes a bounded, redacted concise report as the
  minimum assistant response. Validation requires
  substantive `Remediation`, `Verification`, `Result`, and `Evidence` fields
  for every positional attempt, bound to the guard's bounded, redacted
  marker-derived summaries under `## Root Cause And Fix` and
  `## Verification`. A report
  containing a detected sensitive value is rejected for correction. The
  marker-derived report fields are capped at 70 characters so the canonical
  fallback remains bounded even with all ten attempts. The
  fallback normalizes pipe characters in attempt remediation and verification
  summaries so marker text cannot collide with the report field delimiters.
  The assistant must return the supplied bounded report verbatim; paraphrasing
  an exact marker-derived field is intentionally rejected with a distinct
  mismatch reason rather than the missing-field reason.
  If the continued response remains incomplete, the hook terminates and emits
  the same concise fallback as a UI/event-stream `systemMessage` warning; that
  warning is not an assistant-authored conversation response. Secret-, private-URL-,
  private-IPv4/IPv6-, internal-hostname/localhost-, cloud-access-key-, and
  Unix/Windows personal-path-shaped values are replaced with generic summaries
  even though the marker contract already requires public-safe content. Public
  vendor documentation URLs, public FQDNs, commit SHAs, and image digests remain
  admissible evidence. For invalid state,
  it requests exact marker repair instead of an exhaustion report; one failed
  repair request then stops with an explicit warning.
- Hosted tools outside Codex's local hook path remain governed by the skill
  contract rather than this mechanical guardrail.
- Matching command hooks start concurrently. This guard can deny the underlying
  tool call, but peer hooks must be independently safe because they may already
  have started.

## Validate

From the `skills/` directory:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 \
  troubleshoot/assets/hooks/tests/test_remediation_attempt_guard.py
python3 -m py_compile \
  troubleshoot/assets/hooks/remediation_attempt_guard.py
```

## Install To A Local Runtime

Installation is explicit because hooks change local Codex behavior:

```bash
python3 troubleshoot/scripts/preflight_report_obligations.py

skills_source_root="$(pwd -P)"
installed_skills_root="${AGENTS_SKILLS_DIR:-${HOME}/.agents/skills}"
codex_runtime_root="${CODEX_HOME:-${HOME}/.codex}"
test "$(cat "${installed_skills_root}/troubleshoot/.install-source-id")" = \
  "local:${skills_source_root}"
mkdir -p "${codex_runtime_root}/skill-backups"
skill_backup_dir="$(mktemp -d \
  "${codex_runtime_root}/skill-backups/troubleshoot.XXXXXX")"
chmod 700 "${skill_backup_dir}"
cp -a "${installed_skills_root}/troubleshoot" "${skill_backup_dir}/"
rsync -ai --delete --omit-dir-times \
  --exclude .DS_Store --exclude .install-source-id \
  troubleshoot/ "${installed_skills_root}/troubleshoot/"

./install-skills.sh \
  --install-hooks troubleshoot/assets/hooks \
  --register-hooks
```

The preflight prints aggregate schema/status counts only, preserves recognized
terminal v1/v2 records, and exits nonzero for active legacy, unknown-schema,
unsupported-status, unreadable, or malformed sidecars. Do not activate after a
nonzero exit.
The targeted sync intentionally preserves the root-source ownership marker so
later full-root installs still recognize the skill. Keep the skill backup
directory together with the hook installer's backup for exact rollback.

Set `CODEX_HOME` first to target a disposable or non-default Codex home. After
installing, restart Codex and review and trust all three hook registrations in
`/hooks`. The normal skill installer does not install or trust this bundle.
The Stop entry is the shared arbiter, not a concurrent remediation-only owner.
