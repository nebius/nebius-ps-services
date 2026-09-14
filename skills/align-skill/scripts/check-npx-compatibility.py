#!/usr/bin/env python3
"""Check real skills CLI discovery/copy parity in disposable locations only."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from skill_frontmatter import FormatError, frontmatter

SKILLS_VERSION = "1.5.26"
AGENTS = {"codex": ".agents/skills", "claude-code": ".claude/skills"}
IGNORED_DIRS = {".git", "__pycache__", "__pypackages__", ".pytest_cache", ".ruff_cache"}
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
PORTABLE_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


class CheckError(ValueError):
    """Bounded diagnostic without source content or subprocess logs."""


class Unavailable(CheckError):
    """The required installer/toolchain could not be started."""


def payload(root: Path) -> dict:
    """Treat all non-cache source files as required, including metadata.json."""
    entries = {}
    total = 0
    if root.is_symlink() or not root.is_dir():
        raise CheckError("skill payload must be a non-symlink directory")
    for parent, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS)
        for name in [*dirs, *sorted(files)]:
            path = Path(parent) / name
            mode = path.lstat().st_mode
            rel = path.relative_to(root).as_posix()
            if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise CheckError("payload contains a symlink or special file")
            if len(entries) >= 50000:
                raise CheckError("payload exceeds 50000 entries")
            if stat.S_ISDIR(mode):
                entries[rel] = ("directory",)
                continue
            total += path.stat().st_size
            if total > 256 * 1024 * 1024:
                raise CheckError("payload exceeds 256 MiB")
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            entries[rel] = (digest.hexdigest(), mode & 0o111)
    return entries


def sources(target: Path) -> dict[str, Path]:
    if target.is_symlink() or not target.is_dir():
        raise CheckError("target must be a local non-symlink skill folder or catalog")
    if (target / "SKILL.md").exists():
        paths = [target]
    else:
        paths = sorted(p for p in target.iterdir() if p.is_dir() and (p / "SKILL.md").exists())
        if not paths and (target / "skills").is_dir():
            return sources(target / "skills")
    if not paths:
        raise CheckError("target contains no directly discoverable skills")
    found = {}
    for path in paths:
        data = frontmatter(path / "SKILL.md")
        name = data.get("name")
        if (not isinstance(name, str) or not PORTABLE_NAME.fullmatch(name)
                or len(name) > 64 or name != path.name):
            raise CheckError("name must match its folder without skills CLI normalization")
        if not isinstance(data.get("description"), str) or not data["description"].strip():
            raise CheckError("skills CLI requires a non-empty string description")
        if name in found:
            raise CheckError("duplicate skill name would be lost during discovery")
        found[name] = path
    return found


def environment(base: Path) -> dict[str, str]:
    env = {k: os.environ[k] for k in ("PATH", "SYSTEMROOT", "LANG", "LC_ALL") if k in os.environ}
    for name in ("home", "tmp", "cache", "config"):
        (base / name).mkdir()
    for name in ("user.npmrc", "global.npmrc"):
        (base / name).write_text("")
    env.update(HOME=str(base / "home"), USERPROFILE=str(base / "home"),
               XDG_CONFIG_HOME=str(base / "config"), TMPDIR=str(base / "tmp"),
               TMP=str(base / "tmp"), TEMP=str(base / "tmp"),
               CODEX_HOME=str(base / "home/.codex"),
               CLAUDE_CONFIG_DIR=str(base / "home/.claude"),
               npm_config_cache=str(base / "cache"),
               npm_config_userconfig=str(base / "user.npmrc"),
               npm_config_globalconfig=str(base / "global.npmrc"),
               npm_config_ignore_scripts="true", npm_config_audit="false", npm_config_fund="false",
               DISABLE_TELEMETRY="1", DO_NOT_TRACK="1", CI="1", NO_COLOR="1", FORCE_COLOR="0")
    return env


def run(command: list[str], cwd: Path, env: dict[str, str], timeout: int = 120) -> tuple[int, str]:
    # A file bounds parent memory; no package log or skill descriptions are emitted.
    with tempfile.TemporaryFile() as output:
        try:
            process = subprocess.Popen(command, cwd=cwd, env=env, stdout=output,
                                       stderr=subprocess.STDOUT, start_new_session=True)
        except OSError as exc:
            raise Unavailable("cannot start npx; install Node >=22.20.0 and npm") from exc
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise Unavailable("skills CLI timed out; check package access and toolchain") from exc
        if output.tell() > 8 * 1024 * 1024:
            raise CheckError("skills CLI output exceeds the check limit")
        output.seek(0)
        return process.returncode, ANSI.sub("", output.read().decode("utf-8", errors="replace"))


def listed_names(output: str) -> list[str]:
    return re.findall(r"^│ {4}([a-z0-9-]+)\s*$", output, flags=re.MULTILINE)


def assert_parity(expected: dict, destination: Path) -> None:
    if payload(destination) != expected:
        raise CheckError("installed payload differs: missing/extra resources, changed content or executable modes")


def check(targets: list[Path]) -> dict:
    if os.name != "posix":
        raise Unavailable("isolated process-group checks currently require POSIX")
    npx = shutil.which("npx")
    if not npx:
        raise Unavailable("npx unavailable; install Node >=22.20.0 and npm")
    groups = []
    all_names = set()
    for target in targets:
        group = sources(target)
        if all_names & group.keys():
            raise CheckError("duplicate skill names across selected targets")
        all_names.update(group)
        groups.append((target.resolve(), group, {name: payload(path) for name, path in group.items()}))
    sentinel_name = "unrelated-sentinel"
    while sentinel_name in all_names:
        sentinel_name += "-x"
    with tempfile.TemporaryDirectory(prefix="skills-npx-check-") as temp:
        base = Path(temp)
        env = environment(base)
        cli = [npx, "--yes", f"skills@{SKILLS_VERSION}"]
        code, output = run([*cli, "--version"], base, env, 180)
        if code or output.strip() != SKILLS_VERSION:
            raise Unavailable("pinned skills CLI unavailable; check Node version and npm registry access")
        home_before = payload(base / "home")
        for index, (target, group, expected) in enumerate(groups):
            project = base / f"project-{index}"
            project.mkdir()
            # Detect accidental mutation of unrelated config, hooks and skills.
            sentinels = [project / "unrelated.txt", project / ".codex/hooks.json",
                         project / ".claude/settings.json"]
            for rel in AGENTS.values():
                sentinels.append(project / rel / sentinel_name / "keep.txt")
            for path in sentinels:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}\n")
            before_list = payload(project)
            code, output = run([*cli, "add", str(target), "--list"], project, env)
            discovered = listed_names(output)
            if code or len(discovered) != len(group) or set(discovered) != set(group):
                raise CheckError("skills CLI discovery differs from the expected catalog")
            if payload(project) != before_list:
                raise CheckError("--list unexpectedly changed the project")
            for _ in range(2):
                code, _ = run([*cli, "add", str(target), "--skill", "*", "--agent", *AGENTS,
                               "--copy", "--yes"], project, env)
                if code:
                    raise CheckError("skills CLI installation failed; inspect local prerequisites separately")
                for name in group:
                    for relative in AGENTS.values():
                        assert_parity(expected[name], project / relative / name)
                for relative in AGENTS.values():
                    if {p.name for p in (project / relative).iterdir()} != set(group) | {sentinel_name}:
                        raise CheckError("installed skill set differs from the expected catalog")
                if any(path.is_symlink() or path.read_text() != "{}\n" for path in sentinels):
                    raise CheckError("installation changed unrelated files, hooks or settings")
                if payload(base / "home") != home_before:
                    raise CheckError("project installation unexpectedly changed the disposable home")
            for name, path in group.items():
                if payload(path) != expected[name]:
                    raise CheckError("source changed during verification; rerun against a stable source")
        return {"status": "PASS", "skills_version": SKILLS_VERSION, "skills": len(all_names),
                "agents": list(AGENTS), "discovery": "PASS", "copy_parity": "PASS",
                "repeat_install": "PASS", "isolation": "PASS", "runtime": "NOT_RUN",
                "scope": "local discovery and copied files; hooks and runtime require separate setup"}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("targets", nargs="+", type=Path, help="Local skill folders or catalogs (read-only sources).")
    args = parser.parse_args(argv)
    try:
        result = check([p.expanduser() for p in args.targets])
    except Unavailable as exc:
        print(json.dumps({"status": "UNAVAILABLE", "reason": str(exc)}))
        return 2
    except (CheckError, FormatError) as exc:
        print(json.dumps({"status": "FAIL", "reason": str(exc)}))
        return 1
    except OSError:
        print(json.dumps({"status": "FAIL", "reason": "cannot inspect disposable/source payload"}))
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
