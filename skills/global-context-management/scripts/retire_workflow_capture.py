#!/usr/bin/env python3
"""Retire only unchanged, provenance-owned workflow capture hooks.

This does not inspect or remove private workflow histories or Git worktrees.
Retained registrations block runtime replacement until owner reconciliation.
Unregistered customized payloads are preserved.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import stat
import tempfile


RETIRED = frozenset(
    {
        "prompt_session_event.py",
        "prompt_session_intake.py",
        "prompt_session_result.py",
        "prompt_session_state.py",
        "prompt_session_storage.py",
        "stop_prompt_session_intake.py",
        "tests/test_prompt_session_intake.py",
    }
)


def regular(path: Path):
    value = path.lstat()
    if (
        not stat.S_ISREG(value.st_mode)
        or value.st_uid != os.getuid()
        or value.st_nlink != 1
        or value.st_mode & 0o022
    ):
        raise ValueError("retirement requires unchanged owned regular files")
    return value


def _known_entry(entry, event, home, agent):
    if (
        event != "UserPromptSubmit"
        or not isinstance(entry, dict)
        or set(entry) != {"hooks"}
    ):
        return False
    handlers = entry["hooks"]
    if (
        not isinstance(handlers, list)
        or len(handlers) != 1
        or not isinstance(handlers[0], dict)
    ):
        return False
    handler = dict(handlers[0])
    command = handler.pop("command", None)
    metadata = {"type": "command", "timeout": 10}
    if agent == "codex":
        metadata["statusMessage"] = "Staging bound-session prompt intent"
    if handler != metadata or not isinstance(command, str):
        return False
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    adapter = [
        "python3",
        str(home / "hooks/hook_runtime.py"),
        "--agent",
        agent,
        "--hook",
        "prompt_session_intake.py",
    ]
    direct = [
        f"SKILLS_AGENT={agent}",
        "python3",
        str(home / "hooks/prompt_session_intake.py"),
    ]
    variable = (
        "${CODEX_HOME:-$HOME/.codex}"
        if agent == "codex"
        else "${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
    )
    variable_direct = [
        f"SKILLS_AGENT={agent}",
        "python3",
        variable + "/hooks/prompt_session_intake.py",
    ]
    return tokens in (adapter, direct, variable_direct)


def _references_retired(entry):
    return any(name in json.dumps(entry, sort_keys=True) for name in RETIRED)


def _atomic(path, data, mode):
    descriptor, temporary = tempfile.mkstemp(
        prefix="." + path.name + ".", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def retire(home: Path, source: Path, agent: str, *, preflight=False):
    home, source = home.absolute(), source.absolute()
    # Only the complete replacement catalog can retire this subsystem.
    if not all(
        (source / name / "SKILL.md").is_file()
        for name in ("implement", "implement-test", "worktree")
    ):
        return {"retired_files": 0, "retired_entries": 0, "preserved": []}
    for root in (home, home / "hooks", home / ".install-hooks-state"):
        if root.exists() and (
            root != root.resolve()
            or not root.is_dir()
            or root.stat().st_uid != os.getuid()
            or root.stat().st_mode & 0o022
        ):
            raise ValueError("retirement destination ancestry is unsafe")
    provenance = home / ".install-hooks-state/hook-files.tsv"
    settings = home / ("hooks.json" if agent == "codex" else "settings.json")
    if not settings.exists():
        return {
            "retired_files": 0,
            "retired_entries": 0,
            "preserved": sorted(
                name
                for name in RETIRED
                if (home / "hooks" / name).exists()
                or (home / "hooks" / name).is_symlink()
            ),
        }
    settings_mode = stat.S_IMODE(regular(settings).st_mode)
    before_settings = settings.read_bytes()
    value = json.loads(before_settings)
    hooks = value.get("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("hook registrations are invalid")
    for entries in hooks.values():
        if not isinstance(entries, list):
            raise ValueError("hook registration event must be a list")
    registered = any(
        _references_retired(entry) for entries in hooks.values() for entry in entries
    )
    if not provenance.exists():
        if registered:
            raise ValueError(
                "retained capture registrations require owner reconciliation "
                "before runtime replacement"
            )
        return {
            "retired_files": 0,
            "retired_entries": 0,
            "preserved": sorted(
                name
                for name in RETIRED
                if (home / "hooks" / name).exists()
                or (home / "hooks" / name).is_symlink()
            ),
        }
    regular(provenance)
    before_provenance = provenance.read_bytes()
    rows = before_provenance.decode().splitlines()
    expected_owner = "local:" + str(source / "prompt-session-intake/assets/hooks")
    eligible = {}
    for row in rows:
        fields = row.split("\t")
        if len(fields) != 5:
            continue
        name, owner, relative, source_sha, target_sha = fields
        if name not in RETIRED or owner != expected_owner or relative != name:
            continue
        path = home / "hooks" / name
        if not path.exists() and not path.is_symlink():
            continue
        try:
            for parent in path.parents:
                if parent == home / "hooks":
                    break
                metadata = parent.lstat()
                if (
                    not stat.S_ISDIR(metadata.st_mode)
                    or metadata.st_uid != os.getuid()
                    or metadata.st_mode & 0o022
                ):
                    raise ValueError("retired payload ancestry is unsafe")
            regular(path)
        except (OSError, ValueError):
            continue
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != target_sha:
            continue
        eligible[name] = (path, data)
    matches = []
    custom = False
    for event, entries in hooks.items():
        if not isinstance(entries, list):
            raise ValueError("hook registration event must be a list")
        for entry in entries:
            if not _references_retired(entry):
                continue
            if (
                _known_entry(entry, event, home, agent)
                and "prompt_session_intake.py" in eligible
            ):
                matches.append((event, entry))
            else:
                custom = True
    present = {
        name
        for name in RETIRED
        if (home / "hooks" / name).exists() or (home / "hooks" / name).is_symlink()
    }
    if custom or present - eligible.keys():
        if registered:
            raise ValueError(
                "retained capture registrations require owner reconciliation "
                "before runtime replacement"
            )
        return {"retired_files": 0, "retired_entries": 0, "preserved": sorted(present)}
    for event, entry in matches:
        hooks[event].remove(entry)
    if preflight:
        return {
            "retired_files": len(eligible),
            "retired_entries": len(matches),
            "preserved": sorted(present - eligible.keys()),
        }
    # Compare complete observed files before publishing registration removal.
    if (
        settings.read_bytes() != before_settings
        or provenance.read_bytes() != before_provenance
    ):
        raise ValueError("retirement state changed; rerun installation")
    for path, expected in eligible.values():
        regular(path)
        if path.read_bytes() != expected:
            raise ValueError("retirement payload changed; rerun installation")
    if matches:
        _atomic(
            settings,
            (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode(),
            settings_mode,
        )
    removed = set()
    for name, (path, expected) in eligible.items():
        regular(path)
        if path.read_bytes() != expected:
            continue
        path.unlink()
        removed.add(name)
    kept_rows = [row for row in rows if row.split("\t", 1)[0] not in removed]
    if removed:
        _atomic(
            provenance,
            ("\n".join(kept_rows) + ("\n" if kept_rows else "")).encode(),
            stat.S_IMODE(provenance.stat().st_mode),
        )
    return {
        "retired_files": len(removed),
        "retired_entries": len(matches),
        "preserved": sorted(present - removed),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--agent", choices=("codex", "claude"), required=True)
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args(argv)
    try:
        print(
            json.dumps(
                retire(args.home, args.source, args.agent, preflight=args.preflight),
                sort_keys=True,
            )
        )
    except (OSError, ValueError) as error:
        print(json.dumps({"status": "blocked", "error": str(error)}))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
