#!/usr/bin/env python3
"""Closed runtime imports and no-follow filesystem boundaries in private fixtures."""
from __future__ import annotations

import ast
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP = ROOT / "global-context-management/assets/trusted_runtime_bootstrap.py.template"
SUPPORT = ROOT / "global-context-management/scripts"
FILES = ("trusted_runtime.py", "agent_runtime.py", "hook_runtime.py", "task_state_permissions.py")


class TrustedRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="trusted-runtime-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.catalog = self.base / "catalog"
        self.support = self.catalog / "global-context-management/scripts"
        self.support.mkdir(parents=True)
        for name in FILES:
            shutil.copyfile(SUPPORT / name, self.support / name)
        self.anchor = self.catalog / "commit/scripts/fixture.py"
        self.anchor.parent.mkdir(parents=True)
        self.anchor.write_text("# reviewed fixture entrypoint\n")
        self.marker = self.base / "executed"

    def run_group(self, *, group="runtime", before="", after="", env=None, anchor=None,
                  declared="commit/scripts/fixture.py", source_only=False):
        script = ("from pathlib import Path\nimport os, sys, types\n"
                  + "exec(compile(Path(" + repr(str(BOOTSTRAP)) + ").read_bytes(), 'bootstrap', 'exec'))\n"
                  + before + "\n"
                  + "result = _load_skill_support(" + repr(group) + ", "
                  + repr(str(anchor or self.anchor)) + ", " + repr(declared)
                  + ", source_only=" + repr(source_only) + ")\n" + after)
        clean = {k: v for k, v in os.environ.items()
                 if k not in {"CODEX_THREAD_ID", "SKILLS_AGENT", "CODEX_HOME", "CLAUDE_CONFIG_DIR", "PYTHONPATH"}}
        clean.update({"PYTHONDONTWRITEBYTECODE": "1", "CODEX_HOME": str(self.base / "codex"),
                      "CLAUDE_CONFIG_DIR": str(self.base / "claude")})
        clean.update(env or {})
        return subprocess.run([sys.executable, "-B", "-c", script], cwd=self.base,
                              env=clean, capture_output=True, text=True, timeout=15)

    def assert_rejected(self, **kwargs):
        result = self.run_group(**kwargs)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertFalse(self.marker.exists(), result.stderr)

    def test_catalog_flat_and_individual_install_on_each_host(self):
        for agent in ("codex", "claude"):
            with self.subTest(agent=agent, layout="catalog"):
                result = self.run_group(env={"SKILLS_AGENT": agent})
                self.assertEqual(result.returncode, 0, result.stderr)
            home = self.base / agent
            flat = home / "hooks"
            flat.mkdir(parents=True)
            for name in FILES:
                shutil.copyfile(SUPPORT / name, flat / name)
            for anchor in (flat / "fixture.py", self.base / "individual/scripts/fixture.py"):
                with self.subTest(agent=agent, anchor=anchor):
                    result = self.run_group(anchor=anchor, env={"SKILLS_AGENT": agent})
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_ancestor_planting_and_ambient_module_are_not_selected(self):
        poison = self.base / "global-context-management/scripts"
        poison.mkdir(parents=True)
        (poison / "agent_runtime.py").write_text(f"open({str(self.marker)!r}, 'w').close()\n")
        result = self.run_group(env={"PYTHONPATH": str(poison)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.marker.exists())

    def test_unsafe_sources_fail_before_execution(self):
        for filename in ("trusted_runtime.py", "agent_runtime.py"):
            for kind in ("writable", "symlink", "hardlink", "fifo", "directory", "oversized"):
                with self.subTest(file=filename, kind=kind):
                    target = self.support / filename
                    original = target.read_bytes()
                    target.unlink()
                    linked = self.base / "linked.py"
                    if kind == "symlink":
                        linked.write_bytes(original)
                        target.symlink_to(linked)
                    elif kind == "hardlink":
                        linked.write_bytes(original)
                        os.link(linked, target)
                    elif kind == "fifo":
                        os.mkfifo(target)
                    elif kind == "directory":
                        target.mkdir()
                    else:
                        target.write_bytes(b"#" * 1048577 if kind == "oversized" else original)
                        if kind == "writable":
                            target.chmod(0o666)
                    self.assert_rejected()
                    target.rmdir() if target.is_dir() else target.unlink()
                    linked.unlink(missing_ok=True)
                    target.write_bytes(original)

    def test_writable_ancestor_and_user_symlink_rejected(self):
        self.catalog.chmod(0o777)
        self.assert_rejected()
        self.catalog.chmod(0o700)
        alias = self.base / "alias"
        alias.symlink_to(self.catalog, target_is_directory=True)
        self.assert_rejected(anchor=alias / "commit/scripts/fixture.py")

    def test_incomplete_present_bundle_cannot_use_another_home(self):
        fallback = self.base / "codex/hooks"
        fallback.mkdir(parents=True)
        for name in FILES:
            shutil.copyfile(SUPPORT / name, fallback / name)
        (self.support / "agent_runtime.py").unlink()
        self.assert_rejected()
        (self.support / "trusted_runtime.py").unlink()
        self.assert_rejected()

    def test_unknown_cache_even_with_matching_filename_is_rejected(self):
        for name in ("agent_runtime", "_skills_trusted_runtime"):
            before = (f"module = types.ModuleType({name!r})\n"
                      f"module.__file__ = {str(self.support / (name + '.py'))!r}\n"
                      f"sys.modules[{name!r}] = module\n")
            self.assert_rejected(before=before)
        self.assert_rejected(before="sys.modules['_skills_trusted_runtime'] = None\n")

    def test_cached_bytecode_does_not_replace_validated_source(self):
        import py_compile
        target = self.support / "agent_runtime.py"
        original = target.read_bytes()
        target.write_text(f"open({str(self.marker)!r}, 'w').close()\n")
        py_compile.compile(str(target), doraise=True)
        target.write_bytes(original)
        result = self.run_group()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.marker.exists())

    def test_wrong_payload_owner_is_rejected(self):
        before = ("from unittest.mock import patch\n"
                  "original_fstat = os.fstat\n"
                  "def foreign_owner(fd):\n"
                  "    original = original_fstat(fd)\n"
                  "    import stat\n"
                  "    if stat.S_ISREG(original.st_mode):\n"
                  "        values = {key: getattr(original, key) for key in dir(original) if key.startswith('st_')}\n"
                  "        values['st_uid'] += 1\n"
                  "        return types.SimpleNamespace(**values)\n"
                  "    return original\n"
                  "patcher = patch('os.fstat', side_effect=foreign_owner)\npatcher.start()\n")
        self.assert_rejected(before=before)

    def test_verified_cache_reused_without_sys_path_changes(self):
        result = self.run_group(before="original_path = sys.path[:]\n", after=(
            f"again = _load_skill_support('runtime', {str(self.anchor)!r}, 'commit/scripts/fixture.py')\n"
            "assert again['agent_runtime'] is result['agent_runtime']\n"
            "assert sys.path == original_path\n"))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_changed_during_read_is_rejected(self):
        target = self.support / "agent_runtime.py"
        before = ("from unittest.mock import patch\n"
                  f"target = Path({str(target)!r})\n"
                  "inode = target.stat().st_ino\noriginal_read = os.read\nchanged = False\n"
                  "def replaced(fd, count):\n"
                  "    global changed\n    result = original_read(fd, count)\n"
                  "    if os.fstat(fd).st_ino == inode and not changed:\n"
                  "        changed = True\n        target.write_text('# replaced during validation\\n')\n"
                  "    return result\n"
                  "patcher = patch('os.read', side_effect=replaced)\npatcher.start()\n")
        self.assert_rejected(before=before)

    def test_source_only_audit_does_not_load_runtime_or_installed_code(self):
        (self.support / "agent_runtime.py").write_text(f"open({str(self.marker)!r}, 'w').close()\n")
        result = self.run_group(group="permissions", source_only=True,
                                after="assert 'agent_runtime' not in sys.modules\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.marker.exists())
        self.assert_rejected(group="permissions", source_only=True, anchor=self.base / "elsewhere.py")

    def test_capture_whole_group_and_rollback_failed_initialization(self):
        (self.support / "agent_runtime.py").write_text(f"open({str(self.marker)!r}, 'w').close()\n")
        (self.support / "hook_runtime.py").chmod(0o666)
        self.assert_rejected(group="projector")
        shutil.copyfile(SUPPORT / "agent_runtime.py", self.support / "agent_runtime.py")
        (self.support / "hook_runtime.py").chmod(0o644)
        (self.support / "hook_runtime.py").write_text("raise RuntimeError('fixture failure')\n")
        before = (f"try:\n    _load_skill_support('projector', {str(self.anchor)!r}, 'commit/scripts/fixture.py')\n"
                  "except RuntimeError:\n    pass\n"
                  "assert 'hook_runtime' not in sys.modules\nassert 'agent_runtime' not in sys.modules\n")
        result = self.run_group(before=before)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_transitive_groups_and_declared_local_edges(self):
        namespace = {}
        exec(compile(BOOTSTRAP.read_bytes(), str(BOOTSTRAP), "exec"), namespace)
        # Inspect registry as data; its top-level code has no external effects.
        registry = {}
        exec(compile((SUPPORT / "trusted_runtime.py").read_bytes(), "registry", "exec"), registry)
        sources, groups = registry["_SOURCES"], registry["_GROUPS"]
        for name, relative in sources.items():
            destination = self.catalog / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, destination)
        for group, members in groups.items():
            with self.subTest(group=group):
                result = self.run_group(group=group)
                self.assertEqual(result.returncode, 0, result.stderr)
                for index, name in enumerate(members):
                    tree = ast.parse((ROOT / sources[name]).read_text())
                    for node in ast.walk(tree):
                        if isinstance(node, ast.ImportFrom) and node.module:
                            imported = (name.rpartition('.')[0] + '.' + node.module
                                        if node.level else node.module)
                            if imported in sources:
                                self.assertIn(imported, members[:index], (group, name, imported))

    def test_all_embedded_bootstraps_match_canonical(self):
        canonical = BOOTSTRAP.read_text().strip()
        count = 0
        for path in ROOT.rglob('*'):
            if not path.is_file() or not path.name.endswith(('.py', '.py.template', '.sh')):
                continue
            if path == BOOTSTRAP or path == Path(__file__) or '__pycache__' in path.parts:
                continue
            text = path.read_text()
            while '# BEGIN shared runtime bootstrap' in text:
                _, text = text.split('# BEGIN shared runtime bootstrap', 1)
                block, text = text.split('# END shared runtime bootstrap', 1)
                self.assertEqual('# BEGIN shared runtime bootstrap' + block
                                 + '# END shared runtime bootstrap', canonical, str(path.relative_to(ROOT)))
                count += 1
        self.assertGreaterEqual(count, 33)


if __name__ == '__main__':
    unittest.main()
