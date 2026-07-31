# Safety Model

Read this reference before finalization or apply.

## Preflight Invariants

- Canonical repository-relative NFC paths only, with ASCII control characters
  and DEL rejected.
- Positive owner-specific artifact paths only; unknown extensions and
  extensionless source scripts are rejected.
- No absolute paths, backslashes, empty segments, `.` or `..`.
- No exact, ancestor, Unicode, or case-folded collisions.
- No symlinks, FIFOs, sockets, devices, or non-regular target files.
- No `AGENTS.md`, `AGENTS.override.md`, configured reserved paths, or
  case-folded `.git`, `.hg`, or `.svn` repository-control paths.
- Apply holds one nonblocking private bundle lock across validation, journal
  mutation, and target writes. A concurrent apply fails before changing the
  target or journal.
- One supported content owner per operation.
- Every candidate exists under the private bundle and hashes to its payload
  name.
- Every architecture source still matches its recorded digest and identity.
- The private bundle is a real `0700` directory outside the target and any Git
  worktree; its manifest, payloads, journal, apply lock, and backups are regular
  `0600` files.
- Planned directory identity and absence conditions still hold before the
  first target write.
- Every operation is at its recorded before or after state.

Any known conflict blocks all writes.

## Apply Mechanics

- Create an absent target root under an unpredictable staging name, open and
  fsync that directory, then publish it with a platform-native atomic
  no-replace rename while retaining the opened descriptor. Recheck that the
  public target path still names the same directory before operations.
- Open directories with no-follow descriptor-relative operations.
- Create new files through a private temporary file and an atomic hard-link
  publication that cannot replace an existing destination.
- For semantic merges, lock and revalidate the original descriptor, retain a
  `0600` private backup, atomically replace with exact candidate bytes, fsync,
  and verify the result.
- Recheck every operation immediately before writing.
- Keep an atomically rewritten `0600` journal after every operation.
- Record device, inode, and mode for each executor-created target or parent
  directory immediately after creation. Permit it on a later run only when the
  matching private journal binds that exact identity; otherwise an appearing,
  missing, or replaced directory blocks before another target write.

POSIX advisory locks cannot prevent an unrelated process that deliberately
ignores the lock from writing the same inode in the final instruction window.
The executor narrows that window through repeated digest and identity checks
and verifies the after-state. Treat active concurrent writers as a blocker;
do not scaffold a target being modified by another process.

## Recovery

- `complete`: every operation is after-state.
- `partial`: at least one operation applied before later drift or failure.
- `before`: safe to apply if all other paths are before or after.
- `after`: already applied; do not rewrite or change mtime.
- `conflict`: neither recorded before nor after; stop for review.

Do not auto-rollback. A rollback could overwrite legitimate concurrent work.
Use private backups only as review evidence or for a separately approved
recovery plan.

## Platform Boundary

Apply supports macOS and Linux with POSIX descriptor and locking primitives.
New-root publication requires macOS `renameatx_np(RENAME_EXCL)` or Linux
`renameat2(RENAME_NOREPLACE)` and a filesystem that supports the corresponding
no-replace operation. Apply fails closed when that primitive is unavailable.
Other platforms may plan, finalize, validate, and inspect status but must block
apply until equivalent no-follow and atomic-publication semantics exist.
