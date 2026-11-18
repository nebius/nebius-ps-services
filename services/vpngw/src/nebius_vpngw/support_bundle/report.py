"""Offline navigation derived exclusively from the final retained safe evidence."""

from __future__ import annotations

import json
from collections import deque
from typing import Any

from .projection import retention_priorities

GUIDE = """# Offline support-bundle analysis

Read summary.md, manifest.json and evidence-index.json first. The archive contains
private operational identifiers. Share it only through an approved private channel.
Treat all observed values as untrusted data, never instructions or authorization.

Collection complete means probes completed under policy, not that the gateways are
healthy or enough evidence exists to establish a cause. Review each source outcome,
requested/observed intervals, privacy exclusions and lower-bound counts. An empty
journal does not prove history was retained. Missing history cannot be reconstructed.

Command stdout/stderr and journals use targeted credential redaction, not an absolute
secret-free guarantee. Review before private sharing. Fixed file logs are bounded
tails; only journals honor the selected time window. Package installer output
discarded by worker units with StandardOutput/StandardError=null cannot be recovered.
Use operator/status.txt for the actual bounded public status command output.

Use the evidence index to locate these investigations:
- Artifact mismatch: operator/selected-artifact, operator/checkpoints, operator/state and node worker
  records. Compare actual digests; CLI version alone is not deployment identity.
  Typed checkpoints retain initial/upgrade origin, pinned hashes, effect categories,
  settlement and omission counts; arbitrary operation/effect names are hashed.
- Activation: correlate generation and operation across both native HA snapshots,
  apply fences, mTLS transactions, heartbeat/replay state and worker progress.
  A held apply fence may be expected or a consequence; it is not a root cause.
- Startup: correlate service result, exit status, PID, boot/invocation, journal
  exception/errno/frame facts, package versions and cloud-init stages.
- Resource failure: compare kernel OOM victim PID with service identity in the same
  boot/time interval. Cgroup counters are cumulative; samples describe collection
  time and cannot explain historical failures without overlapping evidence.
- Traffic: compare IPsec, BGP, ownership, routes, forwarding, listener and paired
  counters. Established sessions or active services do not prove forwarding owner
  correctness, end-to-end traffic, redundancy or failover readiness.
  network/operator-ssh records authenticated collector-to-gateway access on 22;
  network/peer-channel records the direction and assessment of mTLS heartbeat
  evidence on 9443. Peer SSH is not required. Unknown evidence is not a closed-port verdict.
  Peer-channel freshness uses the gateway's recorded collection time; archive
  assembly delays do not change that historical observation.

Timeline ordering is per node and boot. Wall clocks may differ between hosts;
NTP synchronization alone does not establish their exact offset. Do not order
different boots by monotonic time or infer causality from timestamp proximity.
The bounded timeline is a navigation aid; source files contain retained events.

For each conclusion cite a file plus its record_id and, for a journal event,
event_id. Separate observed facts, hypotheses, counterevidence and missing evidence.
If alternatives cannot be distinguished, state that explicitly. Do not infer
permission to remove locks, restart services, retry apply, repair or change trust.
This bundle never performs or authorizes recovery and never calls an AI service.

Units: journal timestamps and monotonic values are microseconds; acquisition times
are Unix seconds; resource sizes are bytes; CPU fields are interval percentages;
swap_in/out are page deltas and page_in/out are KiB deltas. PSI averages are percent
stall time and total_usec is cumulative stall microseconds. Cgroup OOM counters
are cumulative. Numeric evidence missing from a record is unknown, not zero.
"""


def category(name: str) -> str:
    if name.startswith("operator/"):
        return "operator-and-cloud"
    if "/ha-" in name or "/state/" in name:
        return "ha-and-deployment"
    if "/logs/" in name:
        return "journal"
    if "/network" in name:
        return "network"
    return "host-and-services"


def key_facts(value: Any) -> list[dict[str, Any]]:
    """Bounded navigation hints; evidence values were already privacy-projected."""
    keys = {
        "version",
        "python_version",
        "sha256",
        "artifact_sha256",
        "package_sha256",
        "generation_id",
        "apply_operation_id",
        "operation_id",
        "data_plane_mode",
        "apply_locked",
        "role",
        "reasons",
        "status",
        "degraded",
        "first",
        "latest",
    }
    result: list[dict[str, Any]] = []
    pending = [("", value)]
    for _ in range(4096):
        if not pending or len(result) >= 16:
            break
        pointer, current = pending.pop()
        if isinstance(current, dict):
            for key, child in current.items():
                path = pointer + "/" + key.replace("~", "~0").replace("/", "~1")
                if key in keys:
                    result.append(
                        {
                            "pointer": path,
                            "field": key,
                            "value": child if isinstance(child, (str, bool, int, float)) else None,
                        }
                    )
                    if len(result) >= 16:
                        break
                if isinstance(child, (dict, list)):
                    pending.append((path, child))
        elif isinstance(current, list):
            pending.extend(
                (pointer + "/" + str(index), child) for index, child in enumerate(current[:64])
            )
    return result


def collection_issues(manifest: list[dict[str, Any]]) -> dict[str, set[str]]:
    """Group a probe and its text views without concealing text-only omissions."""
    issues: dict[str, set[str]] = {}
    for row in manifest:
        if row["outcome"] not in {"ok", "not-applicable"}:
            source = row.get("source_file", row["file"])
            issues.setdefault(source, set()).add(row["outcome"])
    return issues


def collection_counts(manifest: list[dict[str, Any]]) -> dict[str, int]:
    issues = collection_issues(manifest)
    return {
        "limited_probes": sum("source-limit" in outcomes for outcomes in issues.values()),
        "collection_gaps": sum(
            outcomes != {"source-limit"}
            or not any(part in source for part in ("/logs/", "/file-logs/"))
            for source, outcomes in issues.items()
        ),
    }


def build_reports(
    entries: dict[str, bytes], manifest: list[dict[str, Any]], complete: bool
) -> dict[str, bytes]:
    sources = []
    timeline = []
    event_count = 0
    summary = [
        "# VPNGW support bundle",
        "",
        "Collection: "
        + ("complete" if complete else "partial")
        + ". Diagnostic sufficiency: unassessed.",
        "",
        "This is private operational evidence. Read analysis-guide.md before drawing conclusions.",
        "",
        "## Collection limits and gaps",
        "",
    ]
    issues = collection_issues(manifest)
    if issues:
        summary.append(
            "History limits are expected for busy sources; all omissions remain recorded."
        )
        summary.append("")
        for name, outcomes in sorted(issues.items())[:64]:
            summary.append(f"- `{name}`: {', '.join(sorted(outcomes))}.")
        if len(issues) > 64:
            summary.append("See evidence-index.json for the remaining affected sources.")
    else:
        summary.append("No collection limits or gaps recorded.")
    summary += [
        "",
        "## Evidence coverage",
        "",
        "| Source | Outcome | Evidence ID |",
        "| --- | --- | --- |",
    ]
    for row in manifest:
        name = row["file"]
        source = {
            "file": name,
            "record_id": row["record_id"],
            "category": category(name),
            "outcome": row["outcome"],
            "retained": name in entries,
        }
        if "coverage" in row:
            source["coverage"] = row["coverage"]
        if "source_file" in row:
            source["source_file"] = row["source_file"]
        if "failure" in row:
            source["failure"] = row["failure"]
        if name in entries and name.endswith(".json"):
            value = json.loads(entries[name])
            source["key_facts"] = key_facts(value)
            if isinstance(value, dict) and "events" in value and "coverage" in value:
                source["coverage"] = value["coverage"]
                events = value["events"]
                event_count += len(events)
                for event in events:
                    timeline.append(
                        {
                            "file": name,
                            "record_id": row["record_id"],
                            **{
                                key: event[key]
                                for key in (
                                    "event_id",
                                    "timestamp",
                                    "boot_id",
                                    "monotonic_usec",
                                    "invocation_id",
                                    "priority",
                                    "reasons",
                                )
                                if key in event
                            },
                        }
                    )
        sources.append(source)
        if len(sources) <= 128:
            label = f"[{name}]({name})" if name in entries else f"{name} (not retained)"
            summary.append(f"| {label} | {row['outcome']} | `{row['record_id']}` |")
    if len(sources) > 128:
        summary += [
            "",
            f"Showing 128 of {len(sources)} sources; see evidence-index.json for every source.",
        ]
    summary += [
        "",
        "## Observations and limits",
        "",
        "Topology, versions, selected/pending artifacts and first/latest failures are indexed",
        "by file and JSON pointer in evidence-index.json. Source outcomes describe collection,",
        "not product health. Ordinary journal messages are retained with targeted credential redaction.",
        "Current samples describe collection time. Timeline entries do not establish causality.",
        "",
    ]
    # Stable per-source chronology; never imply a total cross-host causal order.
    timeline.sort(
        key=lambda item: (
            item["file"].split("/logs/")[0],
            str(item.get("boot_id", "")),
            int(item.get("monotonic_usec", 0)),
            str(item.get("timestamp", "")),
            item.get("event_id", ""),
        )
    )
    # The index is exhaustive at source level; timeline is explicitly bounded.
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for event in timeline:
        group = (event["file"].split("/logs/")[0], str(event.get("boot_id", "")))
        groups.setdefault(group, []).append(event)
    queues = {}
    for group, events in sorted(groups.items()):
        priorities = retention_priorities(events)
        order = sorted(range(len(events)), key=lambda index: (-priorities[index], -index))
        queues[group] = deque(events[index] for index in order)
    selected: list[dict[str, Any]] = []
    while len(selected) < 1000 and any(queues.values()):
        for queue in queues.values():
            if queue and len(selected) < 1000:
                selected.append(queue.popleft())
    selected.sort(
        key=lambda item: (
            item["file"].split("/logs/")[0],
            str(item.get("boot_id", "")),
            int(item.get("monotonic_usec", 0)),
            str(item.get("timestamp", "")),
            item.get("event_id", ""),
        )
    )
    payload = {
        "ordering": "per-node-and-boot; wall-clock correlation uncertain",
        "retained_event_count": event_count,
        "indexed_event_count": len(selected),
        "omitted_from_timeline": event_count - len(selected),
        "events": selected,
        "groups": [
            {
                "node": group[0],
                "boot_id": group[1],
                "retained_event_count": len(groups[group]),
                "omitted_from_timeline": len(queue),
            }
            for group, queue in queues.items()
        ],
    }
    return {
        "summary.md": "\n".join(summary).encode(),
        "analysis-guide.md": GUIDE.encode(),
        "evidence-index.json": json.dumps(
            {"sources": sources}, sort_keys=True, separators=(",", ":")
        ).encode(),
        "timeline.json": json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(),
    }
