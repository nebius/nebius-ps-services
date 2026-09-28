# Supplemental Process Cases

These cases preserve detailed workflow and output-quality expectations.
`trigger-prompts.csv` is the sole canonical trigger authority; this document
does not define skill routing. Contract tests remain required for lifecycle and
output assertions; canonical CSV validation does not replace them.

## Explicit Repair And Credential Authority

- The user explicitly requests repair of a blanket credential-approval rule in
  human-written AGENTS.md. Apply only the requested clause change, preserve
  unrelated bytes, reread the result and return human-rules-repaired. No
  separate spec receipt or repeated approval is required solely for that patch.
- An existing override is the active human source. Repair that exact source
  when authorized; do not create a dormant AGENTS.md or another override.
- An existing human-written CLAUDE.md has no AGENTS.md import. Repair its
  requested rule directly; managed-generation discovery prerequisites do not
  apply to native human prose.
- The same restriction is inside a generated region. Require canonical specs,
  intact managed bytes, ownership and recovery checks before helper refresh.
  A request to change a rule does not forge a missing ownership receipt.
- Ordinary feature work reveals restrictive instructions. Do not silently
  rewrite them; report only a real unresolved conflict after checking existing
  user authorization and instruction precedence.
- An authorized observability setup needs a new generated admin-password
  Secret for its identified target and intended access. Proceed without another
  confirmation, store it only in the target secret store and disclose no value.
  Uncovered IAM access expansion or destructive credential replacement still
  needs authorization. Never put secret values in specs or Git.

## Coordinator Entry Invariants

- After canonical row `project-instructions-positive-01` routes successfully,
  require the exact current receipt before rendering or applying anything.
- After canonical row `project-instructions-positive-04` routes successfully,
  retain `maintain-project-specs` as the sole direct owner and treat Task
  Implementer or Agentic SDLC only as an outer consumer.

## Decision outcome cases

- A selected project has no `AGENTS.md`, and tracked evidence supports no
  durable project-specific rules. Return `not-needed`, verify the private
  outcome, and state that the missing file remains absent.
- A selected project has no `AGENTS.md`, and tracked evidence supports one
  durable project-specific verification rule. Return `needed`; the terminal
  apply may create the file and requires a fresh session.
- The user says, "People already use this project. From now on, changes to the
  code and interfaces must remain safe for them." Treat this as explicit
  compatibility intent without requiring `GA` or `backward compatibility`.
  Return `needed` with the default compatibility rules unless active
  same-directory project instructions already express the equivalent contract.
- Global instructions say not to add compatibility layers by default, while
  the selected project's canonical specs record explicit existing-user
  compatibility intent. Keep the project rules; do not copy or use the global
  default to suppress them.
- Active human-owned selected-project instructions already protect the same
  supported APIs, CLI/config/persisted formats, and upgrade paths. Return
  `existing-sufficient` with tracked evidence and no generated rules.
- A same-directory `AGENTS.override.md` is active but omits a required
  compatibility rule. Return the fail-closed instructions-gap blocker; do not
  create a dormant `AGENTS.md` behind the override.
- A selected nested project has no local `.git`, but its enclosing repository
  root has the effective marker. Use that ancestor as the discovery root while
  retaining the nested project as the decision and target scope.
- A new session inspects an intact managed target whose exact digest and active
  receipt are bound by the workspace-private ownership registry. Import that
  receipt into the current private bundle and continue without asking the user
  to adopt the same digest again.
- A workspace has no entry but its relevant safe sealed history unanimously
  proves the same exact active target and receipt. Bootstrap one registry entry
  without using session or filesystem ordering, then import the receipt.
- A first bootstrap is blocked by conflicting history. Persist a blocked
  subject generation; deleting the conflict later must not resurrect the older
  active receipt. Require exact-digest adoption to publish a new active
  generation.
- A created apply writes its exact ownership and state but registry publication
  fails. If another session inspects first, bind a pending generation to the
  interrupted writer's exact receipt and state digest. The observer remains
  unproven; the matching writer can recover to active without re-adoption.
- A managed marker is discoverable, but the registry is retired or mismatched,
  or legacy evidence is absent, unsealed, unsafe, stale, damaged, retired,
  mismatched, or conflicting. Treat discovery as behavioral authority only,
  report unproven ownership continuity, and keep exact-digest adoption approval
  mandatory before mutation. An unsafe or malformed existing registry is a
  structured ownership conflict and never falls back to history.
