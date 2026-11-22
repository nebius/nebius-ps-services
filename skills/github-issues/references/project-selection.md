# Project selection and execution binding

Read before collecting selected issues, even when the invocation has no flags.
Project scope is a repository folder, not a GitHub Projects board or a keyword.

## Resolve the project

Validate all argument syntax first using [GitHub operations](github-operations.md).
Bind each repository identity, then resolve project scope before issue effects:

1. Inventory recognized project roots from the repository's catalog/workspace
   declarations, manifests, instructions, documentation and source boundaries.
   An arbitrary directory or a README alone does not prove a project boundary.
2. An explicit `--project` overrides current-folder scope. Preserve whether the
   input explicitly uses path syntax (`./`, a separator or trailing `/`) before
   normalizing leading `./`, repeated separators and trailing `/`. Reject absolute paths, `..`,
   backslashes, control characters, comma lists and globs. A path containing `/`
   or explicitly supplied as a path must match an exact recognized root. A bare name must match exactly one
   project's basename, including any top-level project with that name; do not
   prefer a top-level match over another project with the same basename. Thus
   `api` is ambiguous when both `api` and `services/api` exist, but `./api`
   selects the top-level project exactly. Keep this path intent through preflight.
3. With no `--project`, capture the invocation folder before changing any tool
   working directory. Use its nearest recognized enclosing project root.
   Thus invocation in `services/vpngw` or its `src` subfolder selects
   `services/vpngw`. A container folder such as `services` with no recognized
   enclosing project is unresolved; never silently turn it into a full backlog.
4. At repository root, infer a sole unambiguous recognized product. If multiple
   products exist, ask for one before backlog discovery or effects. A genuine
   single-project repository can use its root as that product; root/container
   ownership must not silently admit child products. Only explicit `--project .`
   or `./` selects whole-repository scope, including all primary project groups.
   This differs from an individual issue having repository-root ownership.
5. With `--repo` and no explicit project, map the inferred repository-relative
   root exactly in each selected repository. Never substitute a basename,
   another checkout's root or whole-repository scope when it is missing.
   Resolve explicit names independently in each repository. Use an already-known
   matching checkout or read-only repository tree evidence; do not clone.
   An inferred root product `.` is not explicit whole-repository scope: prove
   that each target root is also a sole product, or ask for its product. Keep
   selection origin separate from the normalized path so multi-repository
   review cannot widen implicitly.
6. Report the repository, normalized selected root, explicit/current-folder
   origin and evidence. Ambiguous names show exact candidates and ask only for
   the unresolved choice. Unknown paths can suggest a likely correction but
   never apply it automatically. Unresolved scope leaves that repository pending;
   if it could affect an explicit filter cap, the whole bounded selection stays pending.

Before opening another checkout, verify its Git repository identity. Discover
the project from available source evidence instead of asking the user to repeat
an already-resolvable path. If no matching local checkout exists, remote reads
may establish membership, but plan saving and implementation remain pending.

## Determine membership

For a project root, include an issue only when its primary owner is that root
or evidence proves that it affects that project. Trace the supported contract,
discussion, linked changes and source dependencies. Record the reason and
evidence for each match. Labels, names and path mentions are leads, not proof.
Whole-repository scope admits all issue candidates after normal object checks.

Keep the primary owner and affected-project list separate. A shared-library
issue affecting `services/vpngw` stays owned by the shared library. Its plan and
execution remain under that owner, with scoped changes to affected consumers
declared explicitly. Count each issue once under its primary owner. Being
outside the selected project is a filter exclusion, not a not-planned
assessment or a reason to change issue state.

- No issue/filter selector: enumerate all open repository candidates and all
  continuation pages, then filter by the resolved project. Search hints cannot
  prove complete membership. Resolve material uncertainty before ordered effects.
- `--issues`: check every repository/number pair, then intersect existing issues
  with project scope. Report known exclusions separately from missing numbers,
  PR objects and inaccessible or uncertain matches. Closed selected issues
  are assessed without reopening.
- `--filter`: require open state and every exact requested label (AND), then
  project membership. With --issues, intersect the requested numbers and still
  check every repository/number pair. Without an explicit cap, exhaust all
  matches; with --issue-batch, admit oldest first after project filtering.
  Exclusions consume no slots; earlier uncertain matches block capped selection.
  Do not count unvisited issues or refill after freezing.

Refresh project membership before effects. If a frozen member no longer matches,
skip it without refill or scope expansion. Uncertain membership stays pending.
Complete source collection and complete project mapping are separate claims.
Unbounded/number-mode gaps block that repository's ordered effects while
independent repositories may continue. `--priority` orders only admitted members.

## Bind the actual work target

Only `--action create-plan` enables this binding/artifact path. Every batch plan records repository identity, primary project root, affected
shared components, permitted repository-relative change paths, reviewed revision
and validation working directories. Do not put machine-specific absolute paths
in reusable plans. An overview records the selected project as well as each
batch's actual owner; the selected project need not own every batch.

Before implementation, resolve those bindings to a matching local checkout,
verify root containment and load all applicable ancestor/project instructions.
Follow the selected implementation route's source/branch/dirty-state rules.
Use explicit command working directories for the actual project or repository
as its checks require. Never implement in the review session's folder merely
because it is current; never relocate shared-owner work to the filter's project.
Ambiguous checkout identity or ownership requires a focused clarification.
