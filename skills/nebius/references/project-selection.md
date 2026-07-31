# Nebius CLI Project Selection

Use this reference when a Nebius implementation needs one authoritative
project ID for CLI, SDK, inspection, or automation work.

## Authority Order

Resolve project context in this order:

1. An explicit project ID in the current user request or task contract.
2. Workspace configuration explicitly owned by the current task.
3. When the workflow permits the user's Nebius CLI default as authority, the
   config-owned current profile's configured `parent-id`.
4. Ask rather than guess when no single authoritative project remains.

An explicit current-task project always overrides the default-profile
fallback. Do not infer a project from credential filenames, the working
directory, unrelated task state, or persistent memory alone.

## Canonical CLI Lookup

The Nebius CLI documents:

- [`nebius profile current`](https://docs.nebius.com/cli/reference/profile/current)
  as printing the profile selected by the CLI; the result may come from the
  config-owned active profile, `--profile`, or `NEBIUS_PROFILE`; and
- [`nebius config get`](https://docs.nebius.com/cli/reference/config/get) as
  reading a configuration property, with `parent-id` defined as the parent to
  operate on by default and `--profile` selecting the profile.

To resolve the config-owned default rather than an ambient override:

1. Clear `NEBIUS_PROFILE` and any application-owned project, credential,
   token, or impersonation overrides for the lookup subprocess.
2. Run `nebius profile current`.
3. Validate exactly one non-empty profile name.
4. Run:

   ```bash
   nebius config get parent-id --profile <profile>
   ```

5. Validate exactly one non-empty identifier.
6. If the consuming workflow specifically requires a project, verify that the
   configured parent satisfies that project contract; `parent-id` is a generic
   default-parent setting.

Do not parse `~/.nebius/config.yaml` with `awk`, regular expressions, or
line-order assumptions. Direct parsing can select the first matching
authentication block rather than the profile selected by the CLI, and it
couples automation to the YAML layout instead of the documented command
contract.

## Implementation Safety

For hooks, agents, scripts, and long-running automation:

- invoke the CLI with an argument array rather than shell interpolation;
- provide no interactive stdin;
- bound execution time and captured output;
- accept only a single validated value from each lookup;
- keep stderr out of user-facing errors when it may contain identifiers or
  local paths;
- do not print or persist the resolved project unless the task's output
  contract requires it;
- keep explicit project selectors higher priority than fallback discovery; and
- fail closed when the profile, parent, or project type is absent, malformed,
  ambiguous, or inconsistent with current task evidence.

The lookup establishes project authority only. Authentication and
authorization still need their own verification for the intended operation.
