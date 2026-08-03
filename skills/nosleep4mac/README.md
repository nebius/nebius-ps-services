# No Sleep for Mac

`nosleep4mac` is an explicit-only Codex skill that converges one user
LaunchAgent running `/usr/bin/caffeinate -s`. The assertion prevents system
sleep only while macOS considers the laptop connected to AC power. It does not
keep the display awake or change battery sleep settings.

## Design

The public workflow has one operation:

```text
$nosleep4mac
```

The helper runs with no arguments. It creates missing state, repairs stopped or
drifted managed state, and leaves a healthy service exactly unchanged. Its
`--check` mode is an internal, read-only verification path used by the skill
and tests rather than a second user workflow.

The managed plist contains only a generic label, the absolute
`/usr/bin/caffeinate -s` argument vector, and `KeepAlive=true`. Current macOS
documents that `KeepAlive` implies launch-at-load behavior, so the plist does
not duplicate it with `RunAtLoad`.

## Persistence, Idempotency, and Behavior

The helper installs and manages this persistent per-user plist:

```text
$HOME/Library/LaunchAgents/local.nosleep4mac.caffeinate-ac.plist
```

The plist launches `/usr/bin/caffeinate -s` with `KeepAlive=true`. A healthy
rerun is a no-op: it does not rewrite the plist, change its modification time,
restart the service, or change the PID. Missing, stopped, or drifted managed
state is repaired back to the same canonical plist and single launchd job.

After an ordinary restart:

1. The old process ends and its PID disappears.
2. At the next login, macOS loads the per-user LaunchAgent.
3. `KeepAlive` starts and maintains a new `/usr/bin/caffeinate -s` process.
4. On AC power, the process holds the `PreventSystemSleep` assertion.

You do not need to rerun `$nosleep4mac` after an ordinary restart. Apple
documents that login loads per-user agents from the user's
[`Library/LaunchAgents` directory](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html),
and the local `launchd.plist(5)` manual documents that `KeepAlive=true` implies
launch-at-load behavior.

The following boundaries are deliberate:

- The agent does not prevent sleep before the user logs in.
- Logout stops the agent; the next login starts it again.
- Screen locking or display sleep does not stop it because the user remains
  logged in.
- On battery power, the process may remain running but does not prevent system
  sleep. Its AC-only assertion becomes effective again when AC power returns.
- A new PID after a restart is expected and does not violate idempotency.
- macOS may sleep despite an assertion during low-power or thermal emergencies.
- The service does not wake an already-sleeping Mac or guarantee closed-lid
  execution.

## Safety and Recovery

The helper never uses `sudo`, changes `pmset`, installs a LaunchDaemon, or
targets another service. It refuses unsafe paths, updates the plist atomically,
and restores the previous file and loaded state if a changed configuration
cannot be verified. If launchd cannot verify an unload during rollback, the
helper leaves the current plist in place and retains the mode-`0600` prior
backup instead of risking a loaded job with no plist.

For an intentional manual uninstall, stop and verify the exact managed service
before removing its plist:

```bash
label="local.nosleep4mac.caffeinate-ac"
plist="$HOME/Library/LaunchAgents/$label.plist"
target="gui/$(id -u)/$label"

if output="$(launchctl print "$target" 2>&1)"; then
  launchctl bootout "$target"
elif ! printf '%s\n' "$output" \
  | grep -Eqi 'could not find service|service not found'; then
  printf 'ERROR: unable to classify the managed service\n' >&2
  exit 1
fi

if output="$(launchctl print "$target" 2>&1)"; then
  printf 'ERROR: managed service is still loaded\n' >&2
  exit 1
elif ! printf '%s\n' "$output" \
  | grep -Eqi 'could not find service|service not found'; then
  printf 'ERROR: unable to verify the managed service is unloaded\n' >&2
  exit 1
fi

if [[ -e "$plist" || -L "$plist" ]]; then
  rm -- "$plist"
fi
```

This treats an already-absent job or plist as success but stops on every
unclassified launchd error. Do not use `pkill caffeinate`, because unrelated
processes may own other power assertions.

## Files

- `SKILL.md`: explicit invocation, workflow, guardrails, and reporting.
- `references/macos-launchagent.md`: Apple-backed behavior and verification.
- `scripts/nosleep4mac.sh`: no-argument idempotent convergence helper.
- `scripts/test-nosleep4mac.sh`: isolated fake-launchd regression tests.
- `evals/trigger-prompts.md`: explicit activation and boundary examples.
- `agents/openai.yaml`: UI metadata and explicit-only policy.
