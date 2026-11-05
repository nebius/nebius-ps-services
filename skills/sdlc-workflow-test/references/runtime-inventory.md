# Runtime inventory observations

The private lifecycle `record-runtime` operation owns discovery. After an owned
build/start or restart, supply only `--web-path /` and `--health-path /healthz`
(or the application's declared route paths), plus the immutable generation.
For a worker, also supply both `--worker-task` and `--assignment-digest` while
its exact assignment holds the active runtime lease. Read helper help first.
Do not supply resource IDs, database endpoints or complete URLs; those inputs
were removed without aliases.

The runtime observer discovers candidates matching either ownership label and
requires both labels on every candidate. It requires exactly one `web` and one
`db`, both running, one shared owned network, no web mounts, one named owned
database volume, exactly one loopback web publication and no published database
port. It derives the current web image from that container, independently of
historical owned images retained for cleanup. Ambiguous resources are failures;
never select the newest resource.

The actual `NetworkSettings.Ports` publication determines the HTTP origin.
Routes are bounded plain absolute paths: no full URL, double slash, dot segment,
query, fragment, encoded escape, backslash, whitespace or control character.
The API route remains `/api/v1/tasks`. Docker describes the inspected address
and port mapping in its [inspection reference](https://docs.docker.com/reference/cli/docker/inspect/)
and [port-publishing guide](https://docs.docker.com/engine/network/port-publishing/).

Each attempt writes an immutable `runtime-observation-v1` receipt before updating
lifecycle state. It records the exact selected target, mode, source operation,
time, named predicates and bounded boolean/count values. The retained summary
includes the receipt digest. Product predicates, inspection/transport failures,
admission failures and unexpected observer exceptions are distinct. Neither
exception text nor raw inspection, credentials, database contents or tracebacks
belong in diagnostics. Interrupted receipt-to-ledger publication is reconciled
by the lifecycle owner before registration or cleanup; acceptance rejects an
incomplete or tampered ledger.

The lifecycle owner bounds active state at 1 MiB, including retained summaries.
Shared Git and native-hook consumers use that same limit for active lifecycle
state. Their profile, configuration, marker and origin files keep the 64 KiB
limit. Size and integrity failures remain distinct from JSON decoding failures;
never truncate or remove observations to make native commands or cleanup pass.

Worker observations remain development evidence, including when the worktree
is clean. They never publish canonical inventory. Normal acceptance selects the
clean integration or promoted checkout and requires its matching build and
inspected web image. Failed observations never publish endpoints/resources.
A failed acceptance observation ends the trial; a later PASS cannot replace it.

Development failures remain immutable and `blocked_missing_evidence` until
existing classifier and successful recovery records bind the failed receipt
digest and target. Use the existing active-task failure, counted remediation and
fresh-worker recovery owners. The verifier reads their immutable classification,
dispatch, completion journal and revalidation cursor, retains digest references
to recovery evidence, and rejects changed references. It does not classify the
cause, create a second repair engine, or clear downstream revalidation. Include
the observation digest in the failure event's evidence digests. For this verifier,
complete remediation with a relative `evidence/` path to a bounded private JSON
object recording the original operation's verification. A prose-only completion
reference is insufficient to preserve digest-bound recovery evidence. Unknown causes
stay blocked; a later successful inspection alone is insufficient.

Before deletion, lifecycle state retains safe summaries. Before deleting raw files, an immutable digest-bound checkpoint outside the run
records the validated diagnostics and completed runtime cleanup. Partial filesystem
deletion resumes only against that exact checkpoint after confirming no owned
runtime reappeared; arbitrary missing receipts remain invalid. Successful and
interrupted cleanup preserve named failure predicates, expected/observed values,
categories, operations and receipt digests. Reports route inventory/admission
issues to inventory/build/lease investigation, inspection failures to the
Docker environment or observer, and browser failures to Playwright. A generic
environment-evidence gap does not establish a Docker failure: native worker and
phase evidence gaps use the phase owner and classifier route. Cleanup still
discovers all exactly owned historical images and resources.

Lifecycle v7 is incompatible with earlier active state and requires a fresh trial;
historical reports remain historical without migration. After the owned UAT
restart, refresh inventory through this same operation before the fresh
post-restart browser stage. Preserve the named volume and do not reset the
fixture between UAT stages.
