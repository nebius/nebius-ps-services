# Claude Local Setup

Read this for configuration or reconciliation. The caller's request selects
the scope; an inspection never applies changes. Existing session authority
and script-execution rules remain applicable.

## Native Layout And Dependencies

`CLAUDE_CONFIG_DIR` defaults to `~/.claude` and is independent of the invoking
agent. Use an absolute path and reject symlinked target components. The
configuration lives in `CLAUDE.md`, `settings.json`, and `agents/*.md`;
repository-owned workflow state uses `task-state/`. Existing Claude auto memory,
transcripts, authentication and plugin caches have their own owners.

The skill has no separate hook payload. Complete hook setup needs the reviewed
catalog containing the existing seven hook owners and
`global-context-management/scripts/{agent_runtime,hook_runtime}.py`. Locate
that catalog in the repository checkout or the installed complete plugin.
For a standalone copied skill, native inspection/recovery remains available,
but report missing hook dependencies before any full-setup edits. Offer the
repository's documented complete local or plugin installation route; do not
download or execute missing dependencies implicitly.

Inspect the effective configuration sources before editing. Managed, command
line, local and project settings can override user settings. A user-file check
does not prove the effective runtime value. Claude uses `CLAUDE.md` and native
rules, with explicit imports when requested; it does not automatically read
Codex instruction files. Do not generate project instruction files as part of
personal setup. [Settings](https://code.claude.com/docs/en/settings),
[Instructions](https://code.claude.com/docs/en/memory)

## Inspect And Patch

1. Read relevant files without echoing their contents. Parse JSON with duplicate
   key detection. Record file identity and byte digests privately for the
   selected patch; compare them again immediately before writing.
2. Run the checker below. Its failures identify surfaces to inspect, not
   permission to regenerate them. `--native-only` omits hook checks and must not
   be reported as complete setup. The checker is data-only: it neither imports
   a selected source package nor starts MCP processes.
3. Compare the exact requested changes. For `CLAUDE.md`, preserve user text and
   update only one managed block. Empty/stale blocks are incomplete. Duplicate
   managed markers or conflicting policy headings need review; never delete
   user-authored guidance to make the check pass.
4. For settings, add requested missing members or make specifically authorized
   replacements using exact-match edits. Preserve unrelated values, whitespace
   and ordering. The empty recovery template is a missing-file baseline, not
   the desired state of an existing file. Native JSON has no managed comments.
5. Back up only files whose bytes will change, in a private task-owned directory
   outside Git. Preserve original modes and record intended post-change hashes
   privately. Use exact old-text matches and recheck identity immediately before
   publication. If bytes/identity changed, stop and inspect the new version.
6. Run each writer sequentially. After hook installation or an MCP CLI command,
   reread affected files before planning another edit. Reparse settings and
   verify that every unrequested member is unchanged. An unchanged rerun must
   leave bytes, mtimes and backup inventory unchanged.
7. Create missing directories privately and copy missing reviewed role files.
   Prefer mode `0700` for home/private state and `0600` for configuration and
   roles. Do not silently change modes of pre-existing user files. Replace
   drifted assets only with proven previous managed provenance or an approved
   diff; unknown drift is a conflict, not an obsolete template.

The skill intentionally uses ordinary exact-match editing for existing files;
it does not expose a second apply CLI. Where the host cannot provide safe
targeted edits or a file has active concurrent writers, stop and report the
specific affected file. No automatic whole-file rollback is safe under drift.

## Hooks And Private State

Choose one installation route for this package:

Before the first hook install, create a missing
`hooks/global_context_policy.json` with `auto_read_only_subagents: false` and
`include_agent_descriptions: false` unless delegation was explicitly selected.
Preserve an existing policy unless its change was requested. The source bundle
contains an enabled example; the installer creates that example only when the
destination policy is absent and preserves existing operator choices. This
pre-install step keeps a fresh config-claude setup opt-in for both hook routes.

- **Local:** the existing `./install-skills.sh --agent claude` installs all
  skills and reviewed hooks, reconciles managed registrations and preserves
  unrelated settings. Run it only when its execution is authorized. It does
  not render personal instructions/settings/roles. Explicit hook-only modes
  retain their documented refresh/registration flags; inspect installer help.
- **Plugin:** keep the enabled plugin's native hooks. Verify the package and
  effective enabled scope. Do not add the same handlers to user settings or
  rewrite the plugin cache. Native plugin hooks use their private runtime data
  through the existing adapter.

Inspect project/local settings and CLI plugin selection as well as user
settings for competing registrations. The checker verifies user-scope static
ownership; higher scopes require separate native inspection. Preserve all
unrelated handlers and exactly one shared Stop arbiter for this package.
Never delete a custom handler to resolve a conflict without authorization.

Create `task-state/` with mode `0700`; leave it empty. Hooks own lazy creation
of session scaffolds and the parent owns rolling summaries. Audit descendants
without printing contents. Preserve the existing remediation protocol marker.
The Task Implementer workspace is optional, must remain outside Git and have
private modes. If explicitly selected, create `task-implementer/` and add its
exact path to `permissions.additionalDirectories` only when access is needed;
preserve existing entries and permissions. With enabled Bash sandboxing,
inspect whether that exact path also needs `sandbox.filesystem.allowWrite`.
Never change permission mode or disable a sandbox as a side effect of storage.
[Permission directories](https://code.claude.com/docs/en/permissions),
[Sandbox paths](https://code.claude.com/docs/en/sandboxing)

Automatic delegation is optional. When selected, update the existing policy at
`hooks/global_context_policy.json` with `auto_read_only_subagents: true` and
`include_agent_descriptions: false`, preserving other user policy. No new policy
format or hook owner is introduced. Native roles are `repo-mapper`,
`test-strategist` and `risk-reviewer`. Their tool allowlist, not a permission-mode
label, limits them to inspection; native role/CLI/managed overrides must still
be checked before spawning. Shell tests belong to the parent. Native inherited
hooks can maintain private state and must be accounted for separately.

## Trusted-Local Profile

Only explicit selection authorizes this profile. On a fresh target, the
reviewed profile sets `permissions.defaultMode: bypassPermissions` and
`sandbox.enabled: false`. Existing permission changes require an explicit
decision about those values, not generic template convergence. Keep deny/ask
rules, organization policy, login settings and environment overrides intact.
Claude's bypass mode skips ordinary permission prompts; native managed and
other safeguards still apply. Do not promise unrestricted operation or change
`disableBypassPermissionsMode` to evade a restriction.
[Native permission modes](https://code.claude.com/docs/en/permissions)

## Checker Interface

```bash
python3 scripts/check-local-idempotency.py \
  --claude-home /absolute/private/claude-home \
  --source-root /absolute/reviewed/skills --hook-route local
```

Paths here are placeholders, not literal setup targets. Flags:

| Flag | Meaning |
| --- | --- |
| `--claude-home PATH` | Absolute target; defaults to the native environment/home. |
| `--source-root PATH` | Complete reviewed catalog; defaults to the skill's parent. |
| `--hook-route local\|plugin` | Expected registration owner; defaults to local. |
| `--native-only` | Omit hook/dependency checks; never proves complete setup. |
| `--require-trusted-local` | Check the two explicitly selected profile values. |
| `--require-delegation` | Check the existing policy enables delegation. |
| `--require-task-implementer-workspace` | Check private storage outside Git; verify effective tool access separately. |
| `--require-mcp NAME` | Repeat for selected catalog servers; checks registration presence/shape. |
| `--mcp-config PATH` | Exact native application JSON for selected user MCP checks; required with `--require-mcp`. |
| `-h, --help` | Print helper usage without inspecting configuration. |

The checker prints redacted JSON with surface states and file digests. Exit 0
means `STATIC_PASS` for its declared scope; exit 1 means `NOT_ALIGNED`; malformed
arguments exit 2. Optional MCP checks do not validate credentials, endpoints,
server equivalence, permissions or connectivity. Compare selected definitions
structurally and verify connection separately through the native MCP UI.

## Fresh-Session Validation And Recovery

After authorized application, restart Claude Code. Inspect `/status` for
configuration sources, `/context` for loaded instructions and `/hooks` for hook
registrations. Inspect recursive user/project agent definitions by declared
name, plus CLI and managed overrides; `/agents` is not an effective-definition
inspector on current versions. Test one bounded native role invocation and a
complex prompt, independently observing its private task-state path and allowed
tool behavior. Keep fixture preparation separate from observed hook events.
Record Claude version and the evidence scope; do not transfer Codex trust
instructions or claim runtime activation from a file comparison.

Restore a backup only after comparing the target with the recorded
post-change identity/digest. If a user or Claude changed it meanwhile, inspect
and construct a new narrow reverse patch. For newly created files, report
their ownership before any removal. Never roll back authentication, transcripts,
MCP app state or unrelated settings by restoring a broad home snapshot.
