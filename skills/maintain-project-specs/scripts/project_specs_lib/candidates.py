"""Pure candidate feedback from canonical spec validators, without publication."""
from __future__ import annotations

import re

from .contracts import ProjectSpecError, canonical_digest, digest, inspect_pair_bytes, spec_markers, _contains_unresolved_placeholder

HEADINGS = {
    "User Story", "Acceptance Criteria", "Negative Criteria", "Validation Method",
    "Test Method", "Evaluation Method", "Requirements Covered", "Context Evidence",
    "Design Details", "Selected Option", "Alternatives Considered", "Implementation Boundaries",
    "Test-First Success Criteria", "Validation Plan", "Test Plan", "Evaluation Plan",
    "Rollout And Rollback", "Done Definition", "Implementation Evidence", "Verification Evidence",
}
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
ROUTE = re.compile(
    r"\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)([ \t]+)"
    r"(/[A-Za-z0-9._~:/-]*<[A-Za-z][A-Za-z0-9_]*>[A-Za-z0-9._~:/<>-]*)"
    r"(?=[ \t.,;:)]|$)"
)
OPEN = re.compile(r"^<!-- (FEATURE|REQUIREMENT): ((?:FEAT|REQ|TI-DES|TI-REQ)-[0-9]{3,}) [^\n]+ -->$")
CLOSE = re.compile(r"^<!-- /(FEATURE|REQUIREMENT): (?:FEAT|REQ|TI-DES|TI-REQ)-[0-9]{3,} -->$")


def _prose(lines):
    """Yield only non-code lines; a different fence cannot close an open fence."""
    fence = None
    for index, line in enumerate(lines):
        match = FENCE.match(line)
        if fence:
            if match and match[1][0] == fence[0] and len(match[1]) >= len(fence) and not match[2].strip():
                fence = None
        elif match:
            fence = match[1]
        elif not line.startswith(('    ', '\t')):
            yield index, line


def _managed_prose(lines):
    """Human-owned envelopes and quoted schema examples are outside correction."""
    prose = list(_prose(lines))
    regions = []
    for kind in ('requirements', 'design'):
        start, end = spec_markers(kind)
        first = [index for index, line in prose if line.rstrip('\r\n') == start]
        last = [index for index, line in prose if line.rstrip('\r\n') == end]
        if len(first) == len(last) == 1 and first[0] < last[0]:
            regions.append((first[0], last[0]))
    if len(regions) != 1:
        return []
    first, last = regions[0]
    return [(index, line) for index, line in prose if first <= index <= last]


def _normalize(raw: bytes, changes: list | None = None) -> bytes:
    """Correct known syntax only; complete canonical validation proves eligibility.

    Never invent record IDs, mappings, acceptance content or closing locations
    inside prose. A missing record terminator can precede only the next record,
    outer section or managed-region end. Existing/mismatched terminators stay put.
    """
    lines = raw.decode("utf-8").splitlines(keepends=True)
    changes = [] if changes is None else changes
    prose = _managed_prose(lines)
    # Conservatively exclude documents containing raw HTML from route-format
    # correction. A method line inside an HTML block is not ordinary Markdown.
    route_context = all(
        not re.search(r'<[/A-Za-z!]', ROUTE.sub('', line))
        for _, line in prose if not line.lstrip().startswith('<!--') and '`' not in line
    )
    for index, line in prose:
        match = re.fullmatch(r"#{1,6}[ \t]+([^\n]+?)[ \t]*(\n?)", line)
        if match:
            title = match.group(1)
            level = 4 if title in HEADINGS else 3 if re.match(r"(?:REQ|FEAT|TI-REQ|TI-DES)-[0-9]{3,}: \S", title) else None
            if level:
                lines[index] = "#" * level + " " + title + match.group(2)
                if lines[index] != line:
                    changes.append({'kind': 'heading_structure', 'line': index + 1, 'level': level})
        # Bare literal API route parameters are code, not HTML elements. Do not
        # touch inline code, links, comments or ambiguous markup on the same line.
        if route_context and not any(token in line for token in ('`', '[', ']', '<!--')):
            formatted = ROUTE.sub(lambda match: match[1] + match[2] + '`' + match[3] + '`', lines[index])
            if formatted != lines[index]:
                changes.append({'kind': 'literal_route_format', 'line': index + 1})
            lines[index] = formatted
    prose = _managed_prose(lines)
    insertions = {}
    closings = {line.rstrip('\r\n') for _, line in prose if CLOSE.fullmatch(line.rstrip('\r\n'))}
    for position, (index, line) in enumerate(prose):
        marker = OPEN.fullmatch(line.rstrip('\r\n'))
        if marker is None:
            continue
        closing = f'<!-- /{marker[1]}: {marker[2]} -->'
        if closing in closings:
            continue
        for end, item in prose[position + 1:]:
            if CLOSE.fullmatch(item.rstrip('\r\n')):
                break  # A mismatched or duplicate record is not a missing terminator.
            if (OPEN.fullmatch(item.rstrip('\r\n')) or re.match(r'^#{1,2} ', item)
                    or re.fullmatch(r'<!-- maintain-project-specs:(?:requirements|design):end -->\r?\n?', item)):
                insertions[end] = closing + '\n\n'
                changes.append({'kind': 'record_terminator', 'before_line': end + 1, 'marker': closing})
                break
    for index in sorted(insertions, reverse=True):
        lines.insert(index, insertions[index])
    return "".join(lines).encode()


def _semantic(raw: bytes) -> bytes:
    """Normalize marker spacing, retaining marker position and all prose."""
    lines = _normalize(raw).decode().splitlines(keepends=True)
    prose = dict(_managed_prose(lines))
    result = []
    for index, line in enumerate(lines):
        if index in prose and CLOSE.fullmatch(line.rstrip('\r\n')):
            if result and result[-1].strip():
                result.append('\n')
            result.extend((line.rstrip('\r\n') + '\n', '\n'))
            continue
        if index in prose and not line.strip() and result and not result[-1].strip():
            continue
        result.append(line)
    return ''.join(result).encode()


def check_pair(requirements: bytes, design: bytes) -> dict:
    candidate = {"requirements": digest(requirements), "design": digest(design)}
    code = None
    try:
        inspect_pair_bytes(requirements, design)
    except ProjectSpecError as error:
        code = error.code
    corrections = {'requirements': [], 'design': []}
    try:
        normalized = (_normalize(requirements, corrections['requirements']),
                      _normalize(design, corrections['design']))
    except UnicodeError:
        normalized = None
    # Candidate formatting is an owner-issued rejection even when the semantic
    # parser accepts it. This runs before Markdown checks, never exempts a generic
    # linter exit, and provides the same feedback at check and submission time.
    if code is not None or normalized != (requirements, design):
        # Eligibility is proven by a known, semantics-preserving transformation
        # and a complete canonical validation, never inferred from error text.
        try:
            corrected = inspect_pair_bytes(*normalized) if normalized else None
        except ProjectSpecError:
            corrected = None
        eligible = (normalized is not None and normalized != (requirements, design)
                    and corrected is not None and corrected["status"] == "current"
                    and not any(_contains_unresolved_placeholder(match.group(1))
                                for match in re.finditer(
                                    r"(?ms)^#### (?:Acceptance Criteria|Negative Criteria)\s*\n(.*?)(?=^#### |^### |^## |\Z)",
                                    normalized[0].decode())))
        return {"schema": "maintain-project-specs/candidate-feedback-v1",
                "owner": "maintain-project-specs", "status": "rejected", "effects": "none",
                "candidate_sha256": canonical_digest(candidate), "code": code or "CANDIDATE_FORMAT",
                "correction_kind": "document_structure_format" if eligible else None,
                "corrections": corrections if eligible else {},
                "semantic_sha256": canonical_digest([digest(_semantic(raw)) for raw in normalized]) if eligible else None,
                "next_action": "Submit this rejected pair through the paired publisher to reserve bounded correction; "
                "then apply only the listed syntax corrections. Preserve all content."
                if eligible else "Use normal failure classification; no input-correction exemption."}
    return {"schema": "maintain-project-specs/candidate-feedback-v1",
            "owner": "maintain-project-specs", "status": "valid", "effects": "none",
            "candidate_sha256": canonical_digest(candidate), "code": None, "correction_kind": None,
            "corrections": {},
            "semantic_sha256": canonical_digest([digest(_semantic(raw)) for raw in (requirements, design)]),
            "next_action": "Publish through the paired owner transaction."}
