# Evidence security and safe sharing

The course source is designed to be public, but runtime evidence is sensitive
by default. Slurm output, PyTorch traces, and benchmark results can contain
infrastructure identifiers, filesystem paths, command lines, workload shapes,
or environment details even when the labs use synthetic data.

## Collect privately

1. Use synthetic or explicitly public inputs. Do not benchmark customer data,
   credentials, proprietary inputs, or regulated data.
2. Run `umask 077` in the submitting shell before `sbatch`. The supplied
   launchers also apply that mask to files created by the job, but the Slurm
   output file can be opened before the script begins.
3. Keep `results/`, traces, and Slurm output in storage that only the learner or
   approved course reviewers can read.
4. Do not pass credentials on command lines or enable shell tracing. Remove
   unrelated secret-bearing environment variables according to site policy.
5. Never commit generated evidence. The course `.gitignore` is not an access-
   control or redaction boundary.

## Share summaries, not raw artifacts

Before material leaves the approved environment, create a separate summary
that excludes:

- hostnames, node lists, job IDs, GPU UUIDs, serial numbers, and account or
  project identifiers;
- usernames, home directories, executable paths, internal endpoints, and
  private repository paths;
- environment-variable values, tokens, cookies, request headers, credentials,
  and customer or proprietary data;
- raw traces, raw logs, and command lines that expose cluster details.

Report only the evidence needed for the learning decision: anonymized run ID,
node count, GPU family, software versions, public workload definition,
correctness result, aggregate timing, and a written interpretation. Redact by
creating a new file; do not overwrite the private original.

## Course-specific safeguards

- Lab JSON uses a random run ID instead of a Slurm job ID.
- New result files are created exclusively with owner-only permissions.
- The normal course validator performs static validation and does not execute
  CUDA labs or Slurm launchers.
- The portable tree rejects generated caches, and launchers disable Python
  bytecode creation before Python starts.

Live site policy remains authoritative. If an evidence-collection command is
not approved, record a sanitized blocker and ask the cluster owner rather than
weakening permissions.
