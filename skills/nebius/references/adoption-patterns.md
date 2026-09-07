# Reusable patterns and preservation ledger

Source adaptations are freshly written, generic and independent of donor
packages. Local source references describe provenance, not an import dependency.

| Pattern source | Adaptation | Boundary preserved |
| --- | --- | --- |
| vpngw nebius_pagination | Complete bounded lists and duplicate/cycle rejection | No partial list labeled complete |
| vpngw typed cloud errors | Structured RequestError classification | Permission/malformed response never becomes absence |
| cxcli nebius_api_helpers | Request and operation budgets; terminal checks | Operation submission is not completion |
| cxcli SDK auth | Native Config and owned client lifetime | No unscoped token fallback or environment mutation |
| cxcli MK8s preflight | Exact parent/region/shape checks and headroom guidance | Compatibility, quotas and capacity are distinct |
| vpngw route reconciliation | Exact identity, supported version preconditions, readback | Controller route ownership stays in vpngw |
| cxcli IAM issuance | Preserve partial-operation identity | No name-based privilege adoption or automatic credential deletion |
| cxcli storage/discovery | Identity-based attachments and bounded summaries | Protected Soperator lifecycle remains cxcli-owned |
| cxcli observability validation | Component readiness versus signal delivery | No ingestion claim from pod readiness |

## Instruction preservation

| Original block | Classification and destination |
| --- | --- |
| Project authority, secrets, effects, ownership | Always-needed SKILL guardrails; exact API contract in api-sdk.md |
| Detailed GPU/operator/preset/fabric rules | Conditional mk8s-compatibility.md and mk8s-gpu-setup.md, directly routed from SKILL |
| Pool trees, explicit/inherited CIDRs, route consumers | Conditional vpc-networking.md and route-inspection.md |
| Tenant/project quota aggregation and unknown coverage | Conditional quota-management.md plus inspector tests |
| SDK bootstrap, errors and waits | Deterministic sdk/runtime.py and behavioral tests |
| IAM/GPU/observability templates | Category assets; live effects labeled explicitly |
| cxcli catalog internals and host snapshot claims | Remove normative coupling; retain only generic verified principles |

The baseline entry point had 275 logical lines. The new entry point moves
conditional rules behind explicit routing while retaining authority and effect
boundaries. Comparable token/runtime quality measurements require a clean
model runner; line reduction is not evidence of runtime quality.
