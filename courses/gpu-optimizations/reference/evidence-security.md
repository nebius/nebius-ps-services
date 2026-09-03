# Evidence security and safe sharing

The course source is designed to be public, but runtime evidence is
sensitive by default. Slurm output, PyTorch traces, Nsight reports, DCGM
discovery, and serving benchmarks can contain infrastructure identifiers,
filesystem paths, command lines, workload shapes, model names, or request
content even when the course labs themselves use synthetic data.

## Collect privately

1. Use synthetic or explicitly public inputs. Do not profile customer prompts,
   proprietary model inputs, credentials, or regulated data.
2. Run `umask 077` in the submitting shell before `sbatch`. The supplied
   launchers also apply that mask to files created by the job, but the Slurm
   output file can be opened before the script begins.
3. Keep `results/`, profiler reports, traces, and Slurm output in storage that
   only the learner or approved course reviewers can read.
4. Do not pass credentials on command lines or enable shell tracing. Remove
   unrelated secret-bearing environment variables before profiling, following
   the cluster owner's approved procedure.
5. Never commit generated evidence. The course `.gitignore` reduces accidental
   staging; it is not an access-control or redaction boundary.

## Share summaries, not raw artifacts

Before material leaves the approved environment, create a separate summary
that excludes:

- hostnames, node lists, job IDs, GPU UUIDs, serial numbers, and account or
  project identifiers;
- usernames, home directories, executable paths, internal endpoints, and
  private repository paths;
- environment-variable values, tokens, cookies, request headers, and command
  lines containing credentials;
- customer data, prompts, model names that are not public, raw traces, and raw
  logs.

Report only the evidence needed for the learning decision: anonymized run ID,
node count, GPU family, software versions, public workload definition,
correctness result, aggregate timing, and a written interpretation of relevant
profiler evidence. A reviewer should inspect the sanitized copy before it is
shared. Redact by producing a new file; do not overwrite the private original.

## Course-specific safeguards

- Lab JSON uses the canonical `gpu-course-result/v1` schema and a random run ID
  instead of a Slurm job ID.
- New result files are created exclusively with owner-only permissions.
- Profiler launchers create owner-only, randomly named result directories.
- The tooling preflight suppresses hostnames, executable paths, GPU UUIDs, and
  DCGM discovery output.
- The normal course validator performs static validation and does not execute
  Python labs or Slurm launchers.
- The portable tree rejects Python bytecode and Ruff cache directories, and
  every launcher disables Python bytecode creation before Python starts.
- `requirements.txt` is a direct compatibility constraint, not a lock; live
  work requires an approved hash-locked file or immutable image digest.

Live site policy remains authoritative. If a profiler or telemetry command is
not approved, record a sanitized blocker and ask the cluster owner rather than
weakening permissions.
