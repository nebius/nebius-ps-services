"""Bound the agent delta separately from clean changes inherited from the base."""

import difflib
import re

from .contracts import Blocked

MARKER = re.compile(r"^(?:<{7}|={7}|>{7}|\|{7})(?: |$)")


def conflict_basis(source):
    before = {}
    conflicts = set()
    for path in source.rglob("*"):
        if path.is_file() and not path.is_symlink():
            name = str(path.relative_to(source))
            before[name] = (path.read_bytes(), path.stat().st_mode & 0o777)
            if any(
                MARKER.match(line) for line in before[name][0].decode(errors="replace").splitlines()
            ):
                conflicts.add(name)
    if not conflicts:
        raise Blocked("CONFLICT_STRUCTURE_UNSUPPORTED")
    return before, conflicts


def conflict_scope(source, before, conflicts):
    after = {
        str(p.relative_to(source)): (p.read_bytes(), p.stat().st_mode & 0o777)
        for p in source.rglob("*")
        if p.is_file() and not p.is_symlink()
    }
    edited = [name for name in before.keys() | after.keys() if before.get(name) != after.get(name)]
    if not edited or len(edited) > 5:
        raise Blocked("CONFLICT_REPAIR_TOO_LARGE")
    changed_lines = 0
    for name in edited:
        if name not in conflicts and not any(part in {"test", "tests"} for part in name.split("/")):
            raise Blocked("CONFLICT_REPAIR_UNRELATED")
        if name not in after:
            raise Blocked("CONFLICT_REPAIR_DELETION")
        left = [
            s
            for s in before.get(name, (b"", 0))[0].decode(errors="replace").splitlines()
            if not MARKER.match(s)
        ]
        right = after[name][0].decode(errors="replace").splitlines()
        if any(MARKER.match(s) for s in right):
            raise Blocked("UNRESOLVED_CONFLICT_MARKERS")
        changed_lines += sum(1 for s in difflib.ndiff(left, right) if s.startswith(("+ ", "- ")))
    if changed_lines > 200:
        raise Blocked("CONFLICT_REPAIR_TOO_LARGE")
