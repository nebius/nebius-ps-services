"""Evaluator-owned accounting for bounded native streams; never agent self-report.

Missing identity, lineage, terminal evidence or usage cannot qualify efficiency.
Native adapters normalize evidence; the reducer accepts disjoint coverage totals
or one inclusive total, never sums overlapping parent/child usage.
"""

from __future__ import annotations

import json

COORDINATION = {"Agent", "Task", "TaskOutput", "TaskStop"}
CODEX_ACTIONS = {"command_execution", "mcp_tool_call", "web_search", "file_change"}
TERMINAL = {"completed", "failed", "cancelled"}


def natural(value):
    return type(value) is int and value >= 0


def token_total(usage, fields):
    if not isinstance(usage, dict) or any(not natural(usage.get(k)) for k in fields):
        return None
    return sum(usage[k] for k in fields)


def aggregate(participants, actions, totals, *, lineage_complete, stream_complete):
    """Reduce normalized evaluator evidence, retaining incomplete observed counts."""
    reasons = set()
    agents = {}
    for participant in participants:
        identity = participant.get("id")
        if not isinstance(identity, str) or not identity:
            reasons.add("missing participant identity")
            continue
        if identity in agents and agents[identity] != participant:
            reasons.add("conflicting participant identity")
        agents[identity] = participant
    roots = [p for p in agents.values() if p.get("parent") is None]
    if len(roots) != 1 or not lineage_complete:
        reasons.add("incomplete descendant lineage")
    for identity, participant in agents.items():
        chain = {identity}
        parent = participant.get("parent")
        while parent is not None:
            if parent not in agents or parent in chain:
                reasons.add("invalid descendant lineage")
                break
            chain.add(parent)
            parent = agents[parent].get("parent")
        if (
            participant.get("status") not in TERMINAL
            or participant.get("trace_complete") is not True
        ):
            reasons.add("incomplete participant trace or outcome")
    if not stream_complete:
        reasons.add("incomplete native stream")

    seen = {}
    accepted = []
    deliveries = 0
    for action in actions:
        identity, call = action.get("agent"), action.get("id")
        if identity not in agents:
            reasons.add("unknown action participant")
        if action.get("kind") not in {"diagnostic", "coordination"}:
            reasons.add("unknown action kind")
        if not isinstance(call, str) or not call:
            reasons.add("missing native action identity")
        else:
            key = (identity, call)
            if key in seen:
                deliveries += 1
                if seen[key] != action:
                    reasons.add("conflicting native action delivery")
                continue
            seen[key] = action
        accepted.append(action)
    diagnostic = [a for a in accepted if a.get("kind") == "diagnostic"]
    coordination = [a for a in accepted if a.get("kind") == "coordination"]

    coverage = set()
    tokens = 0
    usage_valid = bool(totals)
    total_ids = {}
    for total in totals:
        identity = total.get("id")
        if not isinstance(identity, str) or not identity:
            usage_valid = False
        elif identity in total_ids:
            if total_ids[identity] != total:
                usage_valid = False
            continue
        total_ids[identity] = total
        covered = total.get("covers")
        if (
            not isinstance(covered, list)
            or not covered
            or any(not isinstance(v, str) for v in covered)
        ):
            usage_valid = False
            continue
        members = set(covered)
        if (
            len(members) != len(covered)
            or coverage & members
            or not members <= agents.keys()
        ):
            usage_valid = False
        coverage.update(members)
        if not natural(total.get("tokens")):
            usage_valid = False
        else:
            tokens += total["tokens"]
    if coverage != agents.keys() or not usage_valid:
        reasons.add("missing or overlapping aggregate usage")
        tokens = None
    signatures = [a.get("signature") for a in diagnostic]
    return {
        "diagnostic_actions": len(diagnostic),
        "duplicate_actions": len(signatures) - len(set(signatures)),
        "duplicate_deliveries": deliveries,
        "coordination_actions": len(coordination),
        "coordination_tokens": None,  # Native totals do not attribute reasoning to coordination.
        "aggregate_tokens": tokens,
        "accounting_complete": not reasons,
        "accounting_gaps": sorted(reasons),
        "participant_outcomes": {
            key: row.get("status") for key, row in sorted(agents.items())
        },
    }


def native_accounting(events, *, host, stream_complete=True):
    """Normalize only documented CLI evidence. Codex descendants remain a gap.

    Claude forwards child tool events by parent_tool_use_id; result.modelUsage is
    inclusive. Its result.usage excludes descendants and must not be added.
    Codex one-shot trials require one unambiguous terminal usage record;
    cached_input_tokens is already included in input_tokens. Undocumented child
    exports are deliberately not inferred.
    """
    root = next(
        (e.get("thread_id") for e in events if e.get("type") == "thread.started"), None
    )
    if host == "claude":
        root = next(
            (
                e.get("session_id")
                for e in events
                if e.get("session_id") and not e.get("parent_tool_use_id")
            ),
            None,
        )
    identity_known = isinstance(root, str) and bool(root)
    root = root if identity_known else "unidentified-root"
    participants = {
        root: {
            "id": root,
            "parent": None,
            "status": "running",
            "trace_complete": stream_complete,
        }
    }
    actions, totals, usage = [], [], None
    lineage = (
        identity_known and host == "claude"
    )  # Codex CLI child coverage is not documented.
    seen_messages = set()
    child_messages = set()
    child_results = {}
    native_calls = {}
    finished_calls = {}
    tasks = {}
    narrative_receipts = set()
    result_seen = False
    for index, event in enumerate(events):
        kind = event.get("type")
        if host == "codex":
            item = event.get("item") or {}
            if kind == "item.completed" and isinstance(item, dict):
                item_kind = item.get("type")
                if item_kind in CODEX_ACTIONS:
                    actions.append(
                        {
                            "agent": root,
                            "id": item.get("id"),
                            "kind": "diagnostic",
                            "signature": json.dumps(
                                {
                                    k: item[k]
                                    for k in (
                                        "type",
                                        "command",
                                        "server",
                                        "tool",
                                        "arguments",
                                        "query",
                                    )
                                    if k in item
                                },
                                sort_keys=True,
                            ),
                        }
                    )
                if item_kind not in CODEX_ACTIONS | {
                    "agent_message",
                    "reasoning",
                    "todo_list",
                }:
                    # Includes collaboration and unknown future tools.
                    lineage = False
            if kind == "turn.completed":
                participants[root]["status"] = "completed"
                usage = event.get("usage")
                totals.append(
                    {
                        "id": f"turn-{index}",
                        "covers": [root],
                        "tokens": token_total(usage, ("input_tokens", "output_tokens")),
                    }
                )
            elif kind == "turn.failed":
                participants[root]["status"] = "failed"
            continue

        parent_call = event.get("parent_tool_use_id")
        native_agent = event.get("agent_id")
        if native_agent:
            task = tasks.get(native_agent)
            if not task or task["call"] != parent_call:
                lineage = False
        agent = parent_call or root
        if parent_call:
            child_messages.add(parent_call)
        message = event.get("message")
        if isinstance(message, dict):
            narrative = [
                block
                for block in message.get("content", [])
                if isinstance(block, dict)
                and block.get("type") in {"text", "thinking", "redacted_thinking"}
            ]
            if narrative:
                receipt = (
                    agent,
                    message.get("id"),
                    json.dumps(narrative, sort_keys=True),
                )
                if receipt not in narrative_receipts and (
                    result_seen or agent in child_results
                ):
                    lineage = False
                narrative_receipts.add(receipt)
            for block in message.get("content", []):
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    call = block.get("id")
                    signature = json.dumps(
                        {k: block[k] for k in ("name", "input") if k in block},
                        sort_keys=True,
                    )
                    key = (agent, call)
                    if key not in native_calls and (
                        result_seen or agent in child_results
                    ):
                        lineage = False
                    if key in native_calls and native_calls[key] != signature:
                        lineage = False
                    native_calls[key] = signature
                    actions.append(
                        {
                            "agent": agent,
                            "id": call,
                            "kind": "coordination"
                            if block.get("name") in COORDINATION
                            else "diagnostic",
                            "signature": signature,
                        }
                    )
                    if block.get("name") in {"Agent", "Task"} and isinstance(call, str):
                        participant = {
                            "id": call,
                            "parent": agent,
                            "status": "running",
                            "trace_complete": stream_complete,
                        }
                        if (
                            call in participants
                            and participants[call]["parent"] != agent
                        ):
                            lineage = False
                        participants[call] = participant
                        if isinstance(block.get("input"), dict) and block["input"].get(
                            "run_in_background"
                        ):
                            lineage = False
                elif block.get("type") == "tool_result":
                    call = block.get("tool_use_id")
                    key = (agent, call)
                    fingerprint = json.dumps(block, sort_keys=True)
                    if key not in native_calls or (
                        result_seen and key not in finished_calls
                    ):
                        lineage = False
                    if key in finished_calls and finished_calls[key] != fingerprint:
                        lineage = False
                    finished_calls[key] = fingerprint
                    child_results[call] = (
                        "failed" if block.get("is_error") else "completed"
                    )
        if kind == "system" and event.get("subtype") == "task_started":
            task_id, task_type = event.get("task_id"), event.get("task_type")
            task = {
                "call": event.get("tool_use_id"),
                "run": event.get("run_id"),
                "type": task_type,
            }
            if not isinstance(task_id, str) or not task_id:
                lineage = False
            elif task_id in tasks and tasks[task_id] != task:
                lineage = False  # Resumption needs separate per-run reconciliation.
            else:
                tasks[task_id] = task
            if task_type == "local_agent":
                if (
                    task["call"] not in participants
                    or event.get("is_backgrounded") is not False
                ):
                    lineage = False
            elif task_type != "local_bash":
                lineage = False
        elif kind == "system" and str(event.get("subtype", "")).startswith("task_"):
            task = tasks.get(event.get("task_id"))
            if not task or (
                task["type"] == "local_agent"
                and event.get("subtype") != "task_progress"
            ):
                # Background/update/notification formats remain explicitly unqualified.
                lineage = False
        if kind == "result" and not parent_call:
            # Repeated delivery of the same final result is harmless; conflicting results are not.
            fingerprint = json.dumps(
                {k: v for k, v in event.items() if k != "_observed_seconds"},
                sort_keys=True,
                default=str,
            )
            if result_seen and fingerprint not in seen_messages:
                lineage = False
            seen_messages.add(fingerprint)
            result_seen = True
            participants[root]["status"] = (
                "failed" if event.get("is_error") else "completed"
            )
            usage = event.get("modelUsage")
    if host == "codex" and totals:
        # CLI terminal records lack a native usage-event ID. Multiple receipts
        # cannot be distinguished from duplicate delivery in this one-shot trial.
        values = [t["tokens"] for t in totals]
        totals = [
            {
                "id": "root-turn",
                "covers": [root],
                "tokens": values[0]
                if len(values) == 1 and natural(values[0])
                else None,
            }
        ]
    elif host == "claude":
        if native_calls.keys() != finished_calls.keys():
            lineage = False
        for child, participant in participants.items():
            if child != root:
                participant["status"] = child_results.get(child, "running")
                participant["trace_complete"] = (
                    stream_complete and child in child_messages
                )
        if child_messages - participants.keys():
            lineage = False
        values = (
            [
                token_total(
                    v,
                    (
                        "inputTokens",
                        "outputTokens",
                        "cacheReadInputTokens",
                        "cacheCreationInputTokens",
                    ),
                )
                for v in usage.values()
            ]
            if isinstance(usage, dict)
            else []
        )
        totals = [
            {
                "id": "inclusive-model-usage",
                "covers": list(participants),
                "tokens": sum(values)
                if values and all(natural(v) for v in values)
                else None,
            }
        ]
    else:
        lineage = False
    result = aggregate(
        list(participants.values()),
        actions,
        totals,
        lineage_complete=lineage,
        stream_complete=stream_complete,
    )
    result["usage"] = usage
    result["observed_tokens"] = result["aggregate_tokens"]
    if not result["accounting_complete"]:
        result["aggregate_tokens"] = None
    if host == "codex":
        result["coordination_actions"] = None  # CLI descendant protocol is unqualified.
    return result
