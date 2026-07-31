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

## Behavior

| Event | Result |
| --- | --- |
| User login | launchd loads and maintains the user agent. |
| Screen lock or display off | The login session and service continue. |
| AC power | `caffeinate -s` holds `PreventSystemSleep`. |
| Battery power | Process remains; its AC-only assertion does not apply. |
| AC power restored | The assertion becomes effective again. |
| User logout | The per-user agent stops. |
| Restart before login | The agent waits for that user to log in. |
| Low power or thermal emergency | macOS may sleep despite an assertion. |

The service does not wake an already-sleeping Mac and does not guarantee
closed-lid execution.

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
