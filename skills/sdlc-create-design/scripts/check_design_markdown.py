#!/usr/bin/env python3
"""Check a design candidate without changing its owner-managed Markdown bytes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess


START = "<!-- maintain-project-specs:design:start schema=maintain-project-specs/design-v2 -->"
END = "<!-- maintain-project-specs:design:end -->"
DIRECTIVE = "<!-- markdownlint-disable-next-line MD013 -->"
FEATURE = re.compile(
    r"<!-- FEATURE: (?:FEAT|TI-DES)-[0-9]{3,} "
    r"reqs=(?:REQ|TI-REQ)-[0-9]{3,}(?:,(?:REQ|TI-REQ)-[0-9]{3,})* "
    r"status=(?:draft|ready|blocked|stale|superseded) "
    r"delivery=(?:unassessed|not-started|implemented|verified) "
    r"priority=P[0-9]+ version=[0-9]+ -->"
)
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
MAX_BYTES = 1024 * 1024


class CheckError(RuntimeError):
    """A fixed diagnostic that never includes candidate or tool output."""


def projection(text: str) -> tuple[str, list[int], list[int]]:
    """Exempt only current-schema machine markers, retaining original line IDs."""
    original_lines = text.split("\n")
    lines = [line.removesuffix("\r") for line in original_lines]
    if lines.count(START) != 1 or lines.count(END) != 1:
        raise CheckError("CANONICAL_DESIGN_REQUIRED")
    first, last = lines.index(START), lines.index(END)
    if first >= last:
        raise CheckError("CANONICAL_DESIGN_REQUIRED")
    output, mapping, exempted = [], [], []
    fence = None
    for index, line in enumerate(lines):
        match = FENCE.match(line)
        if fence:
            if (
                match
                and match[1][0] == fence[0]
                and len(match[1]) >= len(fence)
                and not match[2].strip()
            ):
                fence = None
        elif match:
            fence = match[1]
        elif first <= index <= last and (line == START or FEATURE.fullmatch(line)):
            output.append(DIRECTIVE)
            mapping.append(index + 1)
            exempted.append(index + 1)
        output.append(original_lines[index])
        mapping.append(index + 1)
    if first + 1 not in exempted:
        raise CheckError("CANONICAL_DESIGN_REQUIRED")
    return "\n".join(output), mapping, exempted


def read_candidate(candidate: Path) -> bytes:
    descriptor = os.open(candidate, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as source:
        metadata = os.fstat(source.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_BYTES:
            raise CheckError("INVALID_CANDIDATE")
        data = source.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise CheckError("INVALID_CANDIDATE")
    return data


def check(candidate: Path, project_root: Path) -> dict:
    data = read_candidate(candidate)
    projected, mapping, exempted = projection(data.decode("utf-8"))
    executable = shutil.which("markdownlint")
    if executable is None:
        raise CheckError("MARKDOWNLINT_UNAVAILABLE")
    result = subprocess.run(
        [executable, "--json", "--stdin"],
        input=projected,
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    # markdownlint-cli emits its JSON diagnostics on stderr. Never relay its
    # source excerpts, fix suggestions, configuration errors or other raw output.
    if result.stdout.strip() or result.returncode not in {0, 1}:
        raise CheckError("MARKDOWNLINT_PROTOCOL_ERROR")
    try:
        issues = json.loads(result.stderr) if result.stderr.strip() else []
    except json.JSONDecodeError as error:
        raise CheckError("MARKDOWNLINT_PROTOCOL_ERROR") from error
    if not isinstance(issues, list) or bool(issues) != bool(result.returncode):
        raise CheckError("MARKDOWNLINT_PROTOCOL_ERROR")
    violations = []
    for issue in issues:
        if not isinstance(issue, dict):
            raise CheckError("MARKDOWNLINT_PROTOCOL_ERROR")
        line, names = issue.get("lineNumber"), issue.get("ruleNames")
        if (
            type(line) is not int
            or not 1 <= line <= len(mapping)
            or not isinstance(names, list)
            or not names
            or not isinstance(names[0], str)
            or re.fullmatch(r"MD[0-9]{3}", names[0]) is None
        ):
            raise CheckError("MARKDOWNLINT_PROTOCOL_ERROR")
        violations.append({"line": mapping[line - 1], "rule": names[0]})
    if read_candidate(candidate) != data:
        raise CheckError("CANDIDATE_CHANGED")
    return {
        "schema": "sdlc/design-markdown-check-v1",
        "status": "FAIL" if violations else "PASS",
        "candidate_sha256": hashlib.sha256(data).hexdigest(),
        "marker_line_exceptions": exempted,
        "violations": violations,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = check(args.candidate, args.project_root)
    except CheckError as error:
        report = {"status": "ERROR", "error": str(error)}
    except (OSError, UnicodeError, subprocess.TimeoutExpired):
        report = {"status": "ERROR", "error": "MARKDOWN_CHECK_UNAVAILABLE"}
    print(json.dumps(report, sort_keys=True))
    return {"PASS": 0, "FAIL": 1, "ERROR": 2}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
