"""Packet evidence shared by the native lifecycle tests and its parser regressions."""

from __future__ import annotations

import re

REPLY = re.compile(r"^\[(\d+\.\d+)\].*bytes from .*icmp_seq=(\d+)")
SUMMARY = re.compile(r"(\d+) packets transmitted, (\d+) received")


def packet_report(text: str) -> dict:
    replies = []
    seen = set()
    duplicates = 0
    summary = None
    for line in text.splitlines():
        match = REPLY.search(line)
        if match is not None:
            at, sequence = float(match[1]), int(match[2])
            if sequence in seen:
                duplicates += 1
                continue
            seen.add(sequence)
            replies.append((at, sequence))
        match = SUMMARY.search(line)
        if match is not None:
            summary = (int(match[1]), int(match[2]))
    replies.sort()
    largest_gap = 0.0
    gaps = []
    for previous, current in zip(replies, replies[1:], strict=False):
        largest_gap = max(largest_gap, current[0] - previous[0])
        if current[1] > previous[1] + 1:
            gaps.append(
                {
                    "last_reply_at": previous[0],
                    "next_reply_at": current[0],
                    "unanswered_between_replies": current[1] - previous[1] - 1,
                }
            )
    # Without the final transmitted count, trailing loss cannot be quantified.
    transmitted = summary[0] if summary is not None else None
    missing = None
    if transmitted is not None:
        missing = transmitted - len(seen)
        if missing < 0:
            raise ValueError("More unique replies than transmitted probes")
    return {
        "unique_replies": len(replies),
        "duplicates": duplicates,
        "transmitted": transmitted,
        "unanswered": missing,
        "maximum_reply_gap_seconds": largest_gap,
        "reply_gaps": gaps,
        "first_reply_at": replies[0][0] if replies else None,
        "last_reply_at": replies[-1][0] if replies else None,
        "replies": replies,
        "final_counters_available": summary is not None,
    }


def child_signatures(snapshot: dict, name: str) -> set[tuple]:
    signatures = set()
    for parent in snapshot["sas"].get(name, []):
        for child in parent["children"]:
            if child["state"] != "INSTALLED":
                continue
            signatures.add(
                tuple(
                    [parent["id"]]
                    + [
                        child[key]
                        for key in (
                            "uniqueid",
                            "reqid",
                            "spi-in",
                            "spi-out",
                            "if-id-in",
                            "if-id-out",
                        )
                    ]
                )
            )
    return signatures
