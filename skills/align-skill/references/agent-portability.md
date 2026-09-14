# Agent Portability

Read this when aligning a skill for multiple agents, changing installation or
invocation policy, or distinguishing shared instructions from host capabilities.

## Preserve One Source

Keep the skill in place. The portable core is `SKILL.md` with `name` and
`description`, plus useful relative scripts, references, assets and evals.
Preserve optional host metadata and functionality. `agents/openai.yaml` is
Codex metadata, not a prerequisite for Claude Code or a portable core.
Host-specific extensions do not establish support for every Agent Skills host.

Keep shared workflow instructions independent of host tools and installation
paths. Put exact host commands and unsupported-capability handling in a
conditional reference. Select capabilities explicitly; never substitute weaker
authorization, invented prompt identities, or a different configuration home.

The executing host and the configured product are separate: `config-codex`
still configures Codex when read by Claude. Preserve intentional Codex settings,
OpenAI metadata, provider APIs and output artifacts. Do not mechanically rename
`AGENTS.md` to `CLAUDE.md`, delete vendor data, or generalize a product-specific
skill beyond its existing purpose.

## Invocation

| Contract | Codex | Claude Code |
| --- | --- | --- |
| Ordinary reusable skill | `allow_implicit_invocation: true` | Default model/user invocation |
| Public explicit-only | `allow_implicit_invocation: false` | `disable-model-invocation: true` |
| Internal coordinator phase | Explicit coordinator routing; implicit policy false | `user-invocable: false`; model routing requires verified workflow context |

For internal phases, preserve the existing run, project, phase and coordinator
checks before any action. Hiding a command is not authorization. Do not disable
model invocation for a phase that the coordinator must load. Reject absent or
stale workflow context without starting a workflow implicitly.

Codex uses `$skill-name`. Claude uses `/skill-name` for standalone skills and
`/skills:skill-name` for this repository's plugin. Help on either host remains
report-only after loading the selected skill, with no subsequent tool calls.

## Validation And Evidence

Every alignment assesses standard fields, strict frontmatter conformity, both
native hosts, and npx discovery/installability. Keep these separate from fresh
runtime and comparative quality evidence. The executing host does not select
which compatibility checks apply.

From the align-skill directory, with the declared Python dependencies available:

```bash
python3 -m pip install -r scripts/requirements.txt
python3 scripts/validate-skill-structure.py --policy agentskills --agent core <target>
python3 scripts/validate-skill-structure.py --policy repository --agent codex --require-evals <target>
python3 scripts/validate-skill-structure.py --policy repository --agent claude --require-evals <target>
python3 scripts/check-npx-compatibility.py <target>
```

Use `--policy agentskills` also for external host checks unless their repository
opts into our stronger conventions. Repository is the validator default;
`--agent core` alone does not mean standard-only. `--require-evals` and the
stateful-workflow profile remain independent, explicit checks. Pure standard
policy does not require Help, Learning Loop, OpenAI metadata or our CSV format.
Present OpenAI metadata is safely parsed on all profiles; native behavior and
tool dependencies still need semantic review.

The six standard fields are name, description, license, compatibility,
metadata and allowed-tools. Validate their actual YAML types and constraints;
metadata maps strings to strings. Quote ambiguous YAML scalar text. The safe
parser preserves YAML merge precedence and explicit overrides while rejecting
duplicate explicit keys, non-mapping frontmatter, unsafe tags and
excessively complex input; file reads are limited to 1 MiB. Keep meaningful
fields; never invent a license or claim dependencies are unnecessary.

`STANDARD_FIELDS: PASS` only covers standard fields. `STRICT_FRONTMATTER:
EXTENSIONS` identifies recognized native fields that standard-only consumers
can reject. Preserve Claude controls such as disable-model-invocation and
user-invocable rather than stripping them for a pass. Unknown extensions fail
with an official-documentation review requirement. Extension type checks do
not establish equivalent hooks, tools or runtime behavior on another host.
Native Claude list-valued allowed-tools or boolean metadata may work there but
fail the standard field contract; report the conflict without lossy coercion.

### npx Distribution

The checker accepts a skill folder, direct catalog, or repository with a skills
catalog. It runs pinned `skills@1.5.26`, with Node >=22.20.0 and npm (Node 24 in
CI). It uses actual `--list` and `add --skill '*' --agent codex claude-code
--copy --yes` in disposable projects, homes and caches. Registry access is
needed to acquire the pinned CLI; telemetry and npm lifecycle scripts are
disabled. It never launches target scripts or configures a real home.

All non-cache payload files are required by default, including metadata.json.
The upstream installer excludes metadata.json; losing such a source resource
is a failure, not an ignored difference. Investigate its use before any rename
or removal. Reject symlinks/special files, missing resources, changed executable
modes, and names that the CLI would normalize. Standard Unicode names can be
valid while failing this stricter cross-installer naming requirement.

A JSON PASS covers discovery, both copied payloads, repeat-install content
convergence and unchanged unrelated sentinels/settings/hooks. It does not
promise stable timestamps or runtime parity. Exit 0 means PASS, 1 means FAIL,
and 2 means UNAVAILABLE; unavailable tooling never satisfies CI. No network is
used by the separate structural validator or offline unit tests.

npx installs skill files. Document sibling skills, shared runtime, dependencies
and hooks in the target's existing instructions/compatibility field where
needed. Use the full catalog and existing plugin or local installer setup for
workflow prerequisites. Never run installation as an unannounced setup action
or infer hook registration from file parity.

Use canonical trigger CSV and quality JSON cases across both agents. Test fresh
installed discovery; do not inject the skill body into a prompt and call that
trigger evidence. Compare against captured working bytes, including existing
accepted changes. Unavailable runtime or grading evidence stays unavailable.

Source repairs belong in the source checkout. Installed plugin caches are not
learning-loop edit targets; report the source change needed when it is not
available or writable.

## Official Sources

- [Agent Skills specification](https://agentskills.io/specification)
- [Codex skills](https://learn.chatgpt.com/docs/build-skills)
- [Claude Code skills](https://code.claude.com/docs/en/skills)
- [Claude Code hooks](https://code.claude.com/docs/en/hooks)

## Workflow Runtime Preservation

Claude native hooks supply `session_id`, `prompt_id` and, inside a worker,
`agent_id`. Shared helpers use a length-framed session/worker identity for
workers; no manufactured prompt identifier is accepted. The authentication
PreToolUse adapter is the sole owner of Claude Bash rewrites and composes native
identity with its authentication changes. Other guards retain deny precedence.
Full workflow installation requires all reviewed hooks. Do not claim worker
parity from a parent-session smoke test alone.

Native plugin hooks read bundled policy from their private rendered runtime,
with a selected-home policy override when present. Codex discovers configured
read-only TOML roles; Claude discovers configured native roles and may offer
the documented built-in Explore candidate unless disabled. Verify exposed tools, permissions and local overrides before
spawning. See [Claude subagents](https://code.claude.com/docs/en/sub-agents) and
[native hook fields](https://code.claude.com/docs/en/hooks).

## Per-Target Preservation Evidence

Before changing any target, capture current working bytes in private temporary
storage and record a compact inventory in the alignment report:

| Behavior | Required comparison |
| --- | --- |
| Purpose, actions, outputs | Existing use cases still produce the intended outcomes. |
| Metadata and invocation | Explicit-only and coordinator-only restrictions stay effective. |
| Resources and dependencies | Installed resources match; missing siblings/runtime are disclosed. |
| Hooks and authority | Native identity, tool denials, worker/commit ownership and Stop ordering remain intact. |
| State and recovery | Host roots, checkpoints, receipts, continuation and rerun semantics retain their owners. |
| Product-specific work | Configuring Codex or Claude still targets the original product. |

Map each edit to its affected behavior. Use deterministic tests where they
prove the effect and matched previous-version/native cases where instruction
behavior needs evaluation. A known regression blocks the affected change;
unavailable runtime evidence stays explicit. Never mark a limitation resolved
by deleting functionality. If equivalent host behavior is unavailable, preserve
the source capability and request a decision only for a concrete unavoidable
behavioral change. Capture no raw logs or secrets in reusable reports.

Additional sources:

- [Agent Skills field specification](https://agentskills.io/specification)
- [Claude frontmatter outside Claude Code](https://code.claude.com/docs/en/skills#using-skill-frontmatter-outside-claude-code)
- [skills CLI and supported agents](https://github.com/vercel-labs/skills)
- [skills CLI copy exclusions](https://github.com/vercel-labs/skills/blob/main/src/installer.ts)
