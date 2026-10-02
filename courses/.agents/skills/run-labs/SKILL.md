---
name: run-labs
description: "Run selected practical GPU course labs on a prepared Slurm cluster, verify results, capture real Grafana and Nsight views with headless Playwright, and replace student lab-results. Use for one or multiple labs, courses, all course labs, or resuming an evidence campaign; not course authoring, infrastructure installation, or production troubleshooting."
---

# Run Labs

## Help

For `$run-labs --help` or `$run-labs -h` (including native Claude forms), return
concise help and stop before
any workflow step. State the purpose and invocation policy. Show exact usage
for every public action. Describe each public action, positional
argument, and flag in one concise line, including `-h, --help`; say "No
additional public flags" when there are no others. Use only the documented
public interface. For internal or coordinator-only skills, state that boundary
and that no standalone public workflow action exists. After the selected
`SKILL.md` is loaded, help is report-only: do not call any additional tools,
inspect project state, or modify files, private state, Git, or external systems.
Never expose private helper actions or flags or treat help as workflow
authorization.

## Purpose and invocation

Execute the selected campaign through verified artifact publication. The Python
controller persists state and returns the next action; **you are its execution
agent**. Printing a plan or creating a campaign is not completion. Continue
through execution, independent verification, collection, browser review and
export, or report the exact missing prerequisite or failed acceptance gate.

Use explicit or natural-language requests to run course labs. Codex uses
`$run-labs`; Claude Code uses `/run-labs`. Both use the same source contract.

## Public interface

```text
$run-labs run (--lab COURSE:LAB ... | --course COURSE ... | --all-courses)
              [--workload small|large|both] [--courses-root PATH]
              [--environment PRIVATE_JSON] [--dry-run]
$run-labs status CAMPAIGN_DIRECTORY
$run-labs resume CAMPAIGN_DIRECTORY
$run-labs cancel CAMPAIGN_DIRECTORY
$run-labs --help
```

- `run`: start fresh runs; successful evidence replaces the selected current sets.
- `--lab`: course slug plus number or complete source stem; repeatable.
- `--course`: all executable labs in a practical course; repeatable. Combine with
  lab selectors; the union is deduplicated.
- `--all-courses`: all six practical courses; mutually exclusive with narrower
  selectors. Environment readiness and external duplicate links are excluded.
- `--workload`: `both` by default, or only `small` or `large`. These are workload
  presets, independent of GPU model. Qualification/modeling labs and the fixed
  server workloads named in recipe notes may have equal effective parameters;
  record that accurately. See [execution.md](references/execution.md).
- `--courses-root`: checkout containing course catalogs; defaults to the source
  checkout when this skill is used there. Supply it when installed elsewhere.
- `--environment`: private prepared-target JSON described in
  [environment.md](references/environment.md); required except for dry-run.
- `--dry-run`: return selected recipes without writes, sync, connections or jobs.
- `status`: read current progress only.
- `resume`: continue the same unchanged campaign, without repeating completed
  stages or resubmitting an uncertain dispatch.
- `cancel`: reconcile and cancel only this campaign's exact owned jobs; preserve
  prior published results and failed evidence, then release its claims.
- `-h`, `--help`: show help. No additional public flags.

The execution contract and per-job paths are documented in
[execution.md](references/execution.md). Native job files are authoritative;
there is no Python submission or profiling wrapper between the job and its workload.

## Workflow

1. Start in the local `courses/` checkout root on the user's workstation. Resolve
   the prepared environment from user context and existing private receipts.
   Read [environment.md](references/environment.md).
   Independently qualify existing prepared monitoring at runtime; setup discovery
   and new connection receipts remain a separate workflow. Preserve the selected
   local metrics routing and record every prerequisite observation before jobs.
   Never bake a deployment path, endpoint, credential or target into this skill.
   Ask only for missing information that cannot be found locally.

   For the user's interactive cluster login, run this from the workstation
   terminal, replacing the placeholder with the accepted Slurm login IP address:

   ```bash
   ./sync-labs.sh <slurm-login-ip-address>
   ```

   The script synchronizes the courses, then opens an SSH shell in `~/courses`
   on the login node. A bare IP uses `root`; use
   `user@<slurm-login-ip-address>` for another account. Keep the automated
   controller on the workstation: its stage helper uses `--sync-only` in step 3
   with existing SSH authentication and does not require this interactive shell.
   Do not perform this login/sync for help, status or dry-run requests, or repeat
   it merely because a campaign is resumed.
2. Invoke `python3 <skill>/scripts/run_labs.py` with the requested action/flags.
   Honor every recipe in `references/recipes.json`; never execute extracted
   Markdown or invent student-authored implementations. Recipes freeze guide
   variants, reference dependencies and launcher-owned repeated trials.
3. Read [execution.md](references/execution.md). Use the internal stage helper
   to sync through `sync-labs.sh`, bind the observed prepared prerequisites and
   execute one stage at a time. Keep one active allocation. Waiting for a job
   is normal; provide updates and poll at bounded intervals.
4. Check original correctness, allocation, effective parameters and all declared
   comparison invariants. Retain all capstone/server child trials. A failed
   correctness gate stops that lab; never edit source, reduce work, drop variants
   or retry until success. Collect originals locally and verify remote hashes.
5. Read [browser-evidence.md](references/browser-evidence.md). Import course
   dashboards through `nebius-cxcli grafana import` using the exact configured
   target and `--overwrite`. Publish selected unprofiled comparisons using the
   course publisher and stable workspace identity. Capture actual Grafana and
   applicable native Systems/Compute views with **headless Playwright**, inspect
   screenshots visually, and compare displayed content with independent numeric
   and native-report checks. Never substitute a generated chart or CLI screenshot.
   Use the reusable [evidence runner](references/evidence-runner.md) for prepared
   family adapters, persistent browser sessions and compact Grafana captures.
   Its visual-review gate requires actual image inspection before export.
6. Use [evidence.md](references/evidence.md) to record proof and export. Student
   output is PNG, CSV and JSON under
   `<course>/reference/lab-results/<lab>/<small|large>/`, plus one external course
   resources ZIP containing all Grafana dashboards and both result profiles. No explanatory README or result-analysis prose. Finish by
   regenerating course HTML links and checking course validators.
7. Report selected scope, observed completion and any blocker. Link the result
   folders. Separate static coverage from completed live runs. Do not claim all
   labs passed because all recipes exist.

## Recovery and ownership

A run authorizes its necessary sync, jobs, profiling, dashboard update, result
publication and replacement of owned evidence. For managed dashboards, reuse
that authorization after the identity and scope checks in
[browser-evidence.md](references/browser-evidence.md); ownership metadata alone
does not require another approval. Reuse prepared installations;
missing installations belong to shared README setup, not this workflow. Do not rerun the
profiling installer for discovery, provision resources, download new models,
change IAM, expose services or repair course source.

A run or resume also authorizes full recovery of its identified Grafana service,
Nsight Systems/Compute native tools and streamer/viewer services, and `nsys`/`ncu`
profiling runtimes, including necessary process/service restarts, browser-session
reconnection and recreation of task-owned SSH/loopback forwards. Repeat these as
often as needed to finish the selected evidence: there is **no fixed recovery
count** per report, service or campaign, and no repeated approval within existing
target authority. Remote service mutations require a confirmed non-production
target or exact live-action authorization for production/unconfirmed targets.
Preserve configuration, persistent storage, credentials, original evidence and
unrelated sessions, forwards, services and jobs. Connection recovery may escalate
to restarting the affected authorized deployment/service without asking again.

**Restart exited local port-forwards and continue.** Restoring already-authorized
loopback access to Grafana and the Nsight HTTP/TURN ports is local transport
recovery; it does not require separate remote-service restart approval. This
includes forwards that exited when a previous session of the same campaign
ended. "Local port-forwards exited" alone is a recoverable condition, not a
reason to stop or ask again. Follow
[local port-forward recovery](references/environment.md#local-port-forward-recovery)
to verify the target, keep the process alive and check the connection before
resuming evidence capture. Actual tool/access denials remain binding.

Before each recovery, inspect the current failure, preserve available evidence
and choose a scoped action. Bound each operation and readiness wait, inspect its
result, then continue or diagnose the remaining failure. Never stop solely
because a recovery count was reached or blindly loop an unchanged failing
action. Follow [service and profiler recovery](references/tool-recovery.md)
for ownership, restarts, visible browser responses and content revalidation.
If a diagnostic profiler job has already failed terminally, use the supported
[failed profiling unit recovery](references/execution.md#failed-profiling-unit-recovery)
path after fixing the tool failure; do not reset a failed stage or forge a job
receipt. Transient observational
requests retain their two retries; that request limit does not cap this recovery
workflow or subsequent readiness checks after an observed state change.

Submission intent is saved before sbatch; reconcile
its exact action name and receipt after a lost response. Absence from accounting
is not permission to resubmit. Frozen course-input or recipe drift requires a
new campaign. Skill-only updates preserve unchanged campaign inputs; record
the runner revision separately and reconcile any pending adapter effect first.

Stable filenames and per-lab/profile ownership manifests govern replacement.
Keep the previous complete public and private sets until the new set passes.
Recover a pending replacement journal before proceeding. Preserve unrelated
files and pre-existing historical archives. Never copy model caches or raw
infrastructure logs into student results. Keep credentials in existing stores
or process memory; do not save browser storage, HAR, traces or authentication
screenshots. Sanitize public projections independently and hash the actual
projected bytes.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

Preserve observed launcher ownership, trial cardinality, and native report
producer coverage in reviewed recipes. A lost submission response requires
read-only reconciliation even during cancellation.
