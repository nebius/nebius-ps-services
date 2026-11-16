# Superseded Revision Candidate Retirement

This is an automatic private Worktree transition, not another public command.
It applies to exact unmerged workers superseded by sealed design or test
revisions. Cancellation and generic retained recovery keep their existing owners.

Revision preparation publishes immutable native termination and reconciliation
proof, then releases only the stopped workers' shared scheduler slots. It rotates
the superseded wave's fairness turn. Existing checkout reservations remain held.
Failed candidates remain in place while the revision is pending and being sealed.

The next `wave-prepare`, after a reviewed replacement plan establishes a fresh
basis, archives those candidates before reserving replacement worktrees. The
owner requires exact assignment, retention, termination, sealed revision, current
coordinator and Git identities, quiescent writers/validators and released runtime
leases. No caller supplies an arbitrary cleanup path.

## Private Recovery Evidence

Each assignment has an owner-private directory under
`execution/<execution>/retired-workers/<assignment-digest>/`:

- `snapshot.json`: immutable `workflow/revision-candidate-archive-v1` containing
  exact identity bindings, every working-tree file including ignored/untracked
  bytes, modes, empty directories and symlink text, raw Git index, staged and
  resolve-undo blob objects, binary staged patch and a self-contained branch
  bundle. Binary payloads use base64.
- `archived.json`: immutable `workflow/revision-candidate-archived-v1` binding
  the complete snapshot's SHA-256 after readback and fresh candidate comparison.
- `retired.json`: immutable `workflow/revision-candidate-retired-v1` binding
  the archive receipt after exact path, Git registration and ref absence.

Directories are private and artifacts have mode `0600`. A candidate is bounded
to 10,000 entries and 64 MiB of regular-file content; Git captures are bounded
to 64 MiB each and 120 seconds, and the serialized snapshot to 192 MiB. Oversize
or sensitive material, split/unmerged indexes, gitlinks, nested repositories,
special files, hardlinks, unknown
ownership and content drift stop retirement without discarding the candidate.
Symlink targets are recorded without following or extracting them.

The branch bundle preserves history independently of later Git collection.
For authorized recovery, decode it into a protected location, clone its recorded
branch. Import each decoded index blob with `git hash-object -w --stdin` and
require the returned object ID to equal its recorded key before restoring the
raw index. This retains conflict resolve-undo state that a staged patch omits.
The binary staged patch separately describes changes relative to the saved HEAD.
The raw index preserves its recorded state; working-file payloads retain
the independent unstaged state. Review paths and symlink targets before any
recovery extraction. There is no automatic extraction or product-code import.

## Removal And Replay

The owner rechecks archive integrity and candidate bytes before unlocking and
removing the exact archived dirty worktree. This narrowly scoped force removal
is allowed only after the recoverable archive is verified. Branch deletion uses
the recorded old tip and rejects another checkout of that ref. Only fresh absence
permits shared physical-capacity and outer-resource release.

Interrupted removal resumes from the same immutable archive; missing or changed
evidence, a replacement path/ref, or unresolved Git registration fails closed.
Completed replay verifies archive and absence without repeating release effects.
Promotion verifies completed retirement again. Original assignments, failures,
termination observations, retention records and repair counters remain unchanged.

Archives are diagnostic recovery evidence. They never satisfy task acceptance,
authorize copying an unadmitted implementation, or erase the original failed trial.
