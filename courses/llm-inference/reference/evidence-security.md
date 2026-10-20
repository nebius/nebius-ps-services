# Evidence security and safe sharing

The course source is designed to be public, but runtime evidence is sensitive
by default. Slurm output, checkpoints, model-cache paths, server logs, metrics,
and request traces can contain infrastructure identifiers, filesystem paths,
model names, prompts, or environment details.

## Collect privately

1. Use synthetic or explicitly public prompts, datasets, and models. Do not use
   customer prompts, credentials, proprietary model inputs, or regulated data.
2. Complete software preparation in the [Lab Guide](../../lab-guide.html#lab-preparation)
   and review its completion summary. Follow the Lab Guide’s guidance for any failures or hardware skips before submitting.
   Its private parent directories protect scheduler logs even when Slurm opens
   them before the job body starts.
3. Keep `results/`, checkpoints, model caches, metrics, server logs, and Slurm
   output in storage that only approved course participants can read.
4. Do not pass credentials on command lines or enable shell tracing. Remove
   unrelated secret-bearing environment variables according to site policy.
5. Never commit generated evidence. The course `.gitignore` is not an access-control
   or redaction boundary.

## Shared viewer copies

A successful profiling stage automatically delivers a verified copy of its native
reports to the installed Nsight viewer directory. These copies are readable by
the existing shared viewer audience; the viewer does not provide per-user report
isolation. Submit only workloads whose report contents are approved for that
audience. Private originals, logs and other results remain in the job directory.
Check `viewer_delivery` and use the exact viewer paths printed by the job;
no manual copy or permission change is needed after `sbatch`.

## Share summaries, not raw artifacts

Before material leaves the approved environment, create a separate summary
that excludes:

- hostnames, node lists, job IDs, GPU UUIDs, serial numbers, and account or
  project identifiers;
- usernames, home directories, executable paths, internal endpoints, and
  private repository or model-cache paths;
- environment-variable values, tokens, cookies, request headers, credentials,
  and command lines that expose them;
- customer data, private prompts, unpublished model names, raw traces, raw
  server logs, and generated text that is not explicitly public.

Report only the evidence needed for the learning decision: anonymized run ID,
node count, GPU family, software versions, public workload definition,
correctness result, aggregate training or serving metrics, and a written
interpretation. Redact by creating a new file; do not overwrite the private
original.

## Course-specific safeguards

- JSON, checkpoints, client results, server logs, and metrics use a random run
  ID instead of a Slurm job ID.
- New private artifacts are created exclusively with owner-only permissions;
  verified viewer copies use the shared access described above.
- Serving endpoints are loopback-only; distributed worker traffic still
  requires a trusted private fabric and site-approved network controls.
- The normal course validator performs static validation and does not execute
  Python labs, load models, start servers, or submit Slurm launchers.
- The portable tree rejects generated caches, and launchers disable Python
  bytecode creation before Python starts.

Live site policy remains authoritative. If model access, serving, or evidence
collection is not approved, record a sanitized blocker and ask the cluster
owner rather than weakening permissions.
