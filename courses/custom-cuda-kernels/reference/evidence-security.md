# Evidence security

Keep raw build logs, original profiler reports, scheduler output, device inventory, and
runtime artifacts private. Public summaries may include official component
versions, H100 family, compute capability, shapes, metrics, correctness, and
the scoped decision. Remove usernames, absolute paths, hostnames, scheduler
identifiers, private image registries, endpoints, credentials, and customer or
model data before publication.

## Shared viewer copies

A successful profiling stage automatically delivers a verified copy of its native
reports to the installed Nsight viewer directory. These copies are readable by
the existing shared viewer audience; the viewer does not provide per-user report
isolation. Submit only workloads whose report contents are approved for that
audience. Private originals, logs and other results remain in the job directory.
Check `viewer_delivery` and use the exact viewer paths printed by the job;
no manual copy or permission change is needed after `sbatch`.
