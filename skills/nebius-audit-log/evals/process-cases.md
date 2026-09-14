# Supplemental Process Cases

The trigger CSV is the canonical routing authority. These cases define workflow
and report-quality expectations; they are not evidence of fresh model execution.

| Case | Required behavior | Failure control |
| --- | --- | --- |
| Resource deletion by another actor | Verify caller, query resource/MK8S/DELETE, retain first page, distinguish DONE/ERROR/STARTED and identify actor ID | Never substitute current caller or infer a human from a service account |
| Unknown actor and unknown resource | Obtain explicit tenant-wide scope with tenant, region, time and appropriate service/action filters | No implicit current-subject query or cross-region enumeration |
| Multiple user tenants | Select whoami mapping matching target tenant | Reject absent/duplicate mapping instead of taking first prefixed ID |
| Identity failure | Stop before audit; identify failing stage safely | A working profile name is not authentication proof |
| Audit permission denied | Distinguish denial from authentication and empty results; describe tenant audit viewer role | No role enumeration, credential repair or IAM changes |
| Region discovery denied/mismatched | Stop with unresolved region or mismatch | Never choose eu-north1 silently |
| Offline preview | No subprocesses; unresolved config/caller and not_checked access | No live identity/project calls or executable command containing literals |
| Partial pagination | Retain validated pages and pending token; report incomplete; preserve absolute window | No success/absence claim after page limit, failed page or cycle |
| Private filter values | Show field/operator metadata only in preview, results and errors | No raw literal or stderr leak, even with include-pii |
| Name request | Include only opted-in names with safe correlation fields | Never expose credential fields or request/response payloads |

## Static acceptance

The deterministic test suite covers ordered calls, synthetic permission errors,
region/identity mismatches, invalid inputs and responses, output/timeout limits,
continuation behavior, safe reporting and the offline preview boundary.

## Live acceptance boundary

When separately requested, use the exact tenant, region, resource/window and a
low page size with existing configured credentials. A successful empty request
proves read access for that query, not the absence of all related events.
Do not provision fixtures, change IAM or repair authentication as part of the
trial. Report unavailable negative-permission accounts as untested rather than
creating them or claiming mocked failures as live evidence.
