# macOS LaunchAgent Contract

## Authoritative Behavior

Current local macOS manual pages are the command-level authority for the
installed operating system:

```bash
man caffeinate
man launchd.plist
man lockf
launchctl help bootstrap
launchctl help bootout
launchctl help kickstart
```

The installed `caffeinate(8)` manual defines `-s` as a system-sleep assertion
valid only on AC power. The installed `launchd.plist(5)` manual defines
`KeepAlive=true` as continuously maintaining the job and states that it implies
`RunAtLoad`; do not specify both.

The installed `lockf(1)` manual documents kernel-backed exclusive file locking,
including its file-descriptor form for protecting a section of a shell script.
The helper uses that form with a zero-second timeout, keeps all rollback work
inside the locked section, and releases the lock automatically on process exit.

Apple's archived
[Creating Launch Daemons and Agents](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html)
documents the per-user `Library/LaunchAgents` location, unique labels,
`ProgramArguments`, and `KeepAlive`. Apple's
[Service Management overview](https://developer.apple.com/documentation/servicemanagement)
describes LaunchAgents as processes managed on behalf of the currently logged-in
user.

Apple's
[`kIOPMAssertionTypePreventSystemSleep`](https://developer.apple.com/documentation/iokit/kiopmassertiontypepreventsystemsleep)
documentation states that power assertions are suggestions and that macOS may
sleep during low-power or thermal emergencies.

## Canonical Plist

The helper owns this exact logical configuration:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>local.nosleep4mac.caffeinate-ac</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/caffeinate</string>
        <string>-s</string>
    </array>
    <key>KeepAlive</key>
    <true/>
</dict>
</plist>
```

Do not add a shell, environment-dependent executable path, display assertion,
idle-sleep assertion, user-activity assertion, log path, root identity, or
system-wide installation.

## Verification

Verify the exact job rather than matching every caffeinate process:

```bash
launchctl print "gui/$(id -u)/local.nosleep4mac.caffeinate-ac"
pmset -g ps
pmset -g assertions
```

On AC power, require all of:

- canonical plist with mode `0644`;
- exact launchd job in the current GUI domain;
- running `/usr/bin/caffeinate -s` process;
- `PreventSystemSleep` status `1`;
- a `caffeinate` assertion owned by the job PID.

On battery, the job and process may remain running while the system-sleep
assertion is ineffective. That is the intended AC-only behavior.

## Operational Limits

- A screen lock does not log out the current user, so the user agent remains in
  that GUI login domain.
- Logout stops the user agent; next login loads it again.
- The assertion does not wake a sleeping machine.
- Closed-lid behavior depends on macOS and hardware conditions and is not part
  of this skill's guarantee.
- Preventing sleep lets CPU, GPU, disk, and network work continue indefinitely
  on AC, which may increase heat and power consumption.
