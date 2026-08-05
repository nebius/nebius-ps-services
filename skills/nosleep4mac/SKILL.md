---
name: nosleep4mac
description: "Use only when the user explicitly requests $nosleep4mac to install, converge, verify, or repair a per-user macOS LaunchAgent that keeps the logged-in Mac awake on AC power with `/usr/bin/caffeinate -s`, including while the screen is locked. Do not use for temporary caffeinate sessions, display-sleep prevention, battery sleep changes, closed-lid promises, or system-wide daemons."
---

# No Sleep for Mac

## Help

For `$nosleep4mac --help` or `$nosleep4mac -h`, return concise help and stop before
any workflow step. Include the purpose, invocation policy, public usage/actions,
and `-h, --help` plus only documented skill-level options; say "No additional
public flags" when none exist. For internal or coordinator-only skills, state
that boundary and that no standalone public workflow action exists. After the
selected `SKILL.md` is loaded, help is report-only: do not call any additional
tools, inspect project state, or modify files, private state, Git, or external
systems. Never
expose private helper actions or treat help as workflow authorization.

## Purpose

Ensure one per-user LaunchAgent keeps the current logged-in Mac awake while it
is connected to AC power. Preserve normal display sleep, screen locking, battery
sleep, logout behavior, and system power settings.

## Invocation Policy

Require explicit user invocation. Running `$nosleep4mac` authorizes one bounded,
idempotent convergence of the canonical user LaunchAgent without a second
confirmation prompt.

## Required Reads

- Read `references/macos-launchagent.md` before changing the plist, commands,
  verification rules, or documented macOS behavior.
- Inspect the current source helper before running it. Use the source copy for
  development and the installed copy only when source-installed parity has been
  established.

## Writes

The helper may create or replace only:

```text
$HOME/Library/LaunchAgents/local.nosleep4mac.caffeinate-ac.plist
```

It may load or restart only:

```text
gui/<current-uid>/local.nosleep4mac.caffeinate-ac
```

Do not change `pmset`, Lock Screen settings, other LaunchAgents, system
LaunchDaemons, credentials, or repository-external state.

## Workflow

1. Confirm macOS, the logged-in non-root user, AC/battery state, and the current
   plist and launchd job.
2. Run the helper with no arguments:

   ```bash
   nosleep4mac/scripts/nosleep4mac.sh
   ```

3. Let the helper converge the canonical state:
   - create and load missing state;
   - leave matching healthy state byte-for-byte unchanged;
   - start a matching unloaded or stopped job without rewriting the plist;
   - atomically replace safe drifted content at the managed path and reload only
     the exact managed service;
   - restore prior file and loaded state if a changed configuration cannot be
     verified.
4. Use the helper's internal read-only check after convergence:

   ```bash
   nosleep4mac/scripts/nosleep4mac.sh --check
   ```

5. Report whether the result was `installed`, `updated`, `repaired`, or
   `unchanged`, plus power source, PID, and assertion status.

## Idempotency

- A healthy rerun must not rewrite the plist, change its modification time,
  restart launchd, or change the PID.
- Missing or stopped canonical state is repaired without creating duplicate
  jobs or processes.
- Convergence uses a kernel-held file lock that is released automatically if
  the helper exits or is killed.
- Drift at the exact managed regular file is backed up temporarily, replaced
  atomically, and restored on failed verification.
- Unsafe or ambiguous paths fail closed without mutation.

## Failure Handling

- Reject non-macOS systems, root execution, missing GUI login domains,
  symlinks, non-regular targets, unexpected ownership, and group/world-writable
  managed files or directories.
- Treat launchctl, plist, process, power-source, or assertion output that cannot
  be classified as a failure. Do not guess.
- Never make a domain-wide launchctl change. Never kill processes by name.
- If a changed plist cannot start and verify, restore the prior file and prior
  loaded state before returning failure.
- If launchd cannot verify the exact service is unloaded during rollback, leave
  the current plist in place and retain the private prior backup for recovery.

## Must Not

- Do not use `sudo`, `pmset` writes, a LaunchDaemon, `launchctl load` or
  `launchctl unload`, `pkill`, or `killall`.
- Do not add `caffeinate -d`, `-i`, `-u`, or another flag that changes the
  display or battery contract.
- Do not claim the service wakes an already-sleeping Mac, survives logout,
  guarantees closed-lid execution, or overrides low-power and thermal safety.
- Do not commit personal names, usernames, literal home directories, private
  URLs, secrets, customer data, or machine-specific identifiers.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

## Validation

Run:

```bash
bash -n nosleep4mac/scripts/nosleep4mac.sh \
  nosleep4mac/scripts/test-nosleep4mac.sh
shellcheck nosleep4mac/scripts/nosleep4mac.sh \
  nosleep4mac/scripts/test-nosleep4mac.sh
bash nosleep4mac/scripts/test-nosleep4mac.sh
```

Also run the repository-owned `align-skill` structure validator against
`nosleep4mac`.

Use live `--check` only after an explicitly authorized local convergence. Do
not automate screen locking or disconnecting power; report those manual checks
separately.

## Output Contract

Report:

- Apple documentation and local man pages checked;
- whether the helper installed, updated, repaired, or reused state;
- plist validation, job state, exact process, current power source, and
  assertion result;
- repeated-run no-op evidence;
- live lock or power-transition checks run or skipped;
- remaining limitations and the exact rollback reference.
