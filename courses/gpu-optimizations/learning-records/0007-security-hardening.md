# Security hardening decision

## Decision

Treat generated performance evidence as private by default even though the
course source and synthetic labs are public. A shareable course report is a
separately reviewed summary, not a raw Slurm log, profiler trace, telemetry
dump, or environment inventory.

## Safeguards added

- `VAL-EXEC-001`: normal validation is static and no longer executes lab or
  launcher source from the revision being reviewed.
- `DATA-PRIV-001`: tooling preflight suppresses hostnames, executable paths,
  scheduler identifiers, GPU UUIDs, and raw DCGM discovery output.
- `DATA-PRIV-002`: the course now defines private collection, retention,
  redaction, and safe-summary rules for runtime evidence.
- `SUPPLY-CHAIN-001`: installation guidance requires exact approved versions
  or hash-locked requirements rather than mutable unqualified installs. The
  course does not present its direct requirement as a complete lock.
- `PY-FILE-001`: result files use random course run IDs, owner-only modes, and
  exclusive creation; profiler launchers use private unique directories.
- Portable-tree validation rejects bytecode and tool caches that may retain
  local paths, and launchers disable bytecode generation before Python starts.

## Compatibility boundary

The labs, Slurm allocation model, profiler questions, metrics, and evidence
interpretation are unchanged. Raw operational identifiers remain available to
authorized cluster operators through site-owned systems, but the course does
not print or retain them in its portable results.
