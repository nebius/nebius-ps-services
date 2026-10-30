#!/usr/bin/env python3
"""Hermetic bridge preparation, integrity and credential-boundary tests."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import tavily_bridge as bridge


def snapshot(root):
    return {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_mode)
            for p in root.rglob("*") if p.is_file()}


class TavilyBridgeTests(unittest.TestCase):
    def setUp(self):
        # Executable fixtures need trusted ancestry; Linux /tmp is world-writable.
        self.temp = tempfile.TemporaryDirectory(prefix="tavily-fixture-", dir=Path.home())
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.home = self.base / "native-home"
        self.home.mkdir(mode=0o700)
        self.node, self.npm = self.base / "node", self.base / "npm"
        for executable in (self.node, self.npm):
            executable.write_text("#!/bin/sh\nexit 91\n")
            executable.chmod(0o700)
        self.calls = []
        assets = self.base / "assets"
        assets.mkdir()
        for source in bridge.ASSETS.iterdir():
            (assets / source.name).write_bytes(source.read_bytes())
        self.fixture_source = b"// inert fixture, never launched\n"
        fixture_patch = json.loads((assets / "callback-readiness.patch.json").read_text())
        operation = fixture_patch["patches"][0]
        operation.update(sha256_before=bridge.digest(self.fixture_source),
                             sha256_after=bridge.digest(b"// patched fixture\n"),
                             before=self.fixture_source.decode(), after="// patched fixture\n")
        self.fixture_chunk = b"// inert chunk fixture\n"
        fixture_patch["patches"][1].update(sha256_before=bridge.digest(self.fixture_chunk),
            sha256_after=bridge.digest(b"// patched chunk fixture\n"),
            before=self.fixture_chunk.decode(), after="// patched chunk fixture\n")
        (assets / "callback-readiness.patch.json").write_text(json.dumps(fixture_patch))
        patcher = patch.object(bridge, "ASSETS", assets)
        patcher.start()
        self.addCleanup(patcher.stop)

    def runner(self, command, **options):
        self.calls.append((command, options))
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, "v26.12.1\n", "")
        target = options["cwd"] / "node_modules/mcp-remote/dist/proxy.js"
        target.parent.mkdir(parents=True)
        target.write_bytes(self.fixture_source)
        target.with_name("chunk-UZ6Y4QJH.js").write_bytes(self.fixture_chunk)
        return subprocess.CompletedProcess(command, 0, b"", b"")

    def install(self, agent="codex", port=37411):
        with patch.object(bridge.subprocess, "run", side_effect=self.runner):
            return bridge.prepare(self.home, agent, self.node, self.npm, port)

    def test_prepare_and_unchanged_rerun_are_scoped(self):
        native = self.home / "config.toml"
        native.write_text("# unrelated existing bytes\n")
        before = native.read_bytes()
        self.assertTrue(self.install())
        command = self.calls[-1][0]
        self.assertIn("--ignore-scripts", command)
        self.assertIn("--omit=dev", command)
        self.assertEqual(command[:2], [str(self.node), str(self.npm)])
        saved = snapshot(self.home)
        with patch.object(bridge.subprocess, "run", side_effect=AssertionError("must not execute")):
            self.assertFalse(bridge.prepare(self.home, "codex", self.node, self.npm, 37411))
            bridge.check(self.home, "codex")
        self.assertEqual(saved, snapshot(self.home))
        self.assertEqual(before, native.read_bytes())

    def test_check_never_reads_credentials_or_runs_processes(self):
        self.install()
        _, _, auth = bridge.paths(self.home)
        token = auth / "mcp-remote-v1/opaque_tokens.json"
        token.write_text('{"access_token":"sentinel-secret"}')
        token.chmod(0o600)
        original = bridge.read_regular
        def guarded(path, **options):
            self.assertFalse(path.is_relative_to(auth), "credential content was read")
            return original(path, **options)
        before = snapshot(self.home)
        with patch.object(bridge, "read_regular", side_effect=guarded), \
                patch.object(bridge.subprocess, "run", side_effect=AssertionError("must not execute")):
            bridge.check(self.home, "codex")
        self.assertEqual(before, snapshot(self.home))

    def test_install_failure_is_not_published_and_can_retry(self):
        def fail(command, **options):
            if command[-1] == "--version":
                return self.runner(command, **options)
            return subprocess.CompletedProcess(command, 1, b"sentinel-secret", b"sentinel-secret")
        with patch.object(bridge.subprocess, "run", side_effect=fail):
            with self.assertRaisesRegex(bridge.BridgeError, "dependency installation failed"):
                bridge.prepare(self.home, "codex", self.node, self.npm, 37411)
        root, runtime, _ = bridge.paths(self.home)
        self.assertFalse(runtime.exists())
        self.assertFalse((root / "prepare.lock").exists())
        self.assertEqual(list((root / "runtime").iterdir()), [])
        self.assertTrue(self.install())

    def test_vendor_patch_preimage_failure_never_publishes(self):
        self.fixture_source = b"// unexpected upstream artifact\n"
        with self.assertRaisesRegex(bridge.BridgeError, "patch preimage differs"):
            self.install()
        root, runtime, _ = bridge.paths(self.home)
        self.assertFalse(runtime.exists())
        self.assertEqual(list((root / "runtime").iterdir()), [])

    def test_missing_or_bad_second_patch_is_not_published(self):
        path = bridge.ASSETS / "callback-readiness.patch.json"
        original = json.loads(path.read_text())
        for defect in ("missing", "postimage"):
            candidate = json.loads(json.dumps(original))
            if defect == "missing":
                candidate["patches"].pop()
            else:
                candidate["patches"][1]["sha256_after"] = "0" * 64
            path.write_text(json.dumps(candidate))
            with self.subTest(defect=defect), self.assertRaises(bridge.BridgeError):
                self.install()
            root, runtime, _ = bridge.paths(self.home)
            self.assertFalse(runtime.exists())
            self.assertEqual(list((root / "runtime").iterdir()), [])

    def test_patch_revision_has_distinct_immutable_runtime(self):
        original = bridge.paths(self.home)[1]
        patch_path = bridge.ASSETS / "callback-readiness.patch.json"
        patch_path.write_bytes(patch_path.read_bytes() + b"\n")
        self.assertNotEqual(bridge.paths(self.home)[1], original)

    def test_unknown_partial_runtime_is_preserved(self):
        _, runtime, _ = bridge.paths(self.home)
        runtime.mkdir(parents=True)
        (runtime / "unmanaged").write_text("preserve")
        before = snapshot(self.home)
        with self.assertRaises((OSError, bridge.BridgeError)):
            self.install()
        self.assertEqual(before, snapshot(self.home))

    def test_existing_lock_does_not_admit_second_installer(self):
        root, _, _ = bridge.paths(self.home)
        for d in (self.home / "mcp", root, root / "runtime"):
            d.mkdir(mode=0o700)
        lock = root / "prepare.lock"
        lock.write_text("other installer")
        lock.chmod(0o600)
        with self.assertRaises(FileExistsError):
            self.install()
        self.assertEqual(lock.read_text(), "other installer")

    def test_custom_home_requires_port_before_any_write(self):
        with self.assertRaisesRegex(bridge.BridgeError, "custom agent homes"):
            bridge.prepare(self.home, "claude", self.node, self.npm)
        self.assertEqual(list(self.home.iterdir()), [])

    def test_symlinked_state_is_rejected_without_modifying_target(self):
        foreign = self.base / "preserve"
        foreign.mkdir()
        (self.home / "mcp").symlink_to(foreign)
        with self.assertRaises(bridge.BridgeError):
            self.install()
        self.assertEqual(list(foreign.iterdir()), [])

    def test_runtime_drift_is_not_reinstalled(self):
        self.install()
        _, runtime, _ = bridge.paths(self.home)
        (runtime / "node_modules/mcp-remote/dist/proxy.js").write_text("// user drift\n")
        before = snapshot(self.home)
        with self.assertRaisesRegex(bridge.BridgeError, "integrity differs"):
            self.install()
        self.assertEqual(before, snapshot(self.home))

    def test_selected_package_manager_executable_ancestry_is_distinct_from_state(self):
        package_dir = self.base / "package-manager"
        package_dir.mkdir(mode=0o775)
        package_dir.chmod(0o775)
        binary = package_dir / "node"
        binary.write_bytes(self.node.read_bytes())
        binary.chmod(0o755)
        self.assertEqual(bridge.executable(binary), str(binary))
        with self.assertRaisesRegex(bridge.BridgeError, "writable directory ancestry"):
            bridge.ancestors(package_dir)

    def test_executable_symlink_in_world_writable_parent_is_rejected(self):
        public = self.base / "public-bin"
        public.mkdir()
        public.chmod(0o777)
        link = public / "node"
        link.symlink_to(self.node)
        with patch.object(bridge.subprocess, "run", side_effect=self.runner):
            with self.assertRaisesRegex(bridge.BridgeError, "untrusted executable ancestry"):
                bridge.prepare(self.home, "codex", link, self.npm, 37411)
        self.assertEqual(self.calls, [])
        self.assertEqual(list(self.home.iterdir()), [])

    def test_selected_codex_bridge_must_be_enabled(self):
        self.install()
        actual = bridge.configuration(self.home, "codex")
        bridge.validate_registration(self.home, "codex", {**actual, "enabled": True})
        for enabled in (False, 0, 1, "true", None):
            candidate = {**actual, "enabled": enabled}
            with self.subTest(enabled=enabled), self.assertRaisesRegex(bridge.BridgeError, "disabled or invalid"):
                bridge.validate_registration(self.home, "codex", candidate)
            self.assertIs(candidate["enabled"], enabled)

    def test_insecure_or_symlinked_token_file_fails_without_read(self):
        self.install()
        _, _, auth = bridge.paths(self.home)
        token = auth / "mcp-remote-v1/token.json"
        token.write_text("sentinel-secret")
        token.chmod(0o644)
        with self.assertRaisesRegex(bridge.BridgeError, "credential file permissions"):
            bridge.check(self.home, "codex")
        token.unlink()
        token.symlink_to(self.node)
        with self.assertRaisesRegex(bridge.BridgeError, "credential file permissions"):
            bridge.check(self.home, "codex")

    def test_distinct_binding_and_private_stores(self):
        self.install()
        original_home = self.home
        original = bridge.configuration(self.home, "codex")
        self.home = self.base / "claude-home"
        self.home.mkdir(mode=0o700)
        self.install("claude", 37412)
        other = bridge.configuration(self.home, "claude")
        self.assertNotEqual(original["env"], other["env"])
        self.assertEqual(original["args"][2], "37411")
        self.assertEqual(other["args"][2], "37412")
        with self.assertRaisesRegex(bridge.BridgeError, "binding differs"):
            bridge.check(original_home, "claude")

    def test_exact_codex_registration_and_timeout_floor(self):
        self.install()
        actual = bridge.configuration(self.home, "codex")
        bridge.validate_registration(self.home, "codex", actual)
        for key, value in (("url", bridge.ENDPOINT), ("args", ["--debug"]),
                           ("env", {"MCP_REMOTE_CONFIG_DIR": "/other"}),
                           ("command", "node"), ("startup_timeout_sec", True),
                           ("tool_timeout_sec", 30), ("startup_timeout_sec", "360"),
                           ("tool_timeout_sec", "360")):
            with self.subTest(key=key), self.assertRaises(bridge.BridgeError):
                bridge.validate_registration(self.home, "codex", {**actual, key: value})
        bridge.validate_registration(self.home, "codex", {**actual, "tool_timeout_sec": 900})

    def test_claude_startup_and_effective_tool_limits(self):
        self.install("claude", 37412)
        actual = bridge.configuration(self.home, "claude")
        settings = {"env": {"MCP_TIMEOUT": "360000", "UNRELATED": "preserved"}}
        bridge.validate_registration(self.home, "claude", actual, settings)
        for limit in (None, "30000", True, "nan"):
            with self.subTest(limit=limit), self.assertRaises(bridge.BridgeError):
                bridge.validate_registration(self.home, "claude", actual, {"env": {"MCP_TIMEOUT": limit}})
        settings["env"]["MCP_TOOL_TIMEOUT"] = "30000"
        with self.assertRaises(bridge.BridgeError):
            bridge.validate_registration(self.home, "claude", actual, settings)
        bridge.validate_registration(self.home, "claude", {**actual, "timeout": 360000}, settings)
        with self.assertRaisesRegex(bridge.BridgeError, "must be numeric"):
            bridge.validate_registration(self.home, "claude", {**actual, "timeout": "360000"}, settings)
        self.assertEqual(settings["env"]["UNRELATED"], "preserved")

    def test_codex_checker_strict_bridge_function(self):
        self.install()
        spec = importlib.util.spec_from_file_location("codex_bridge_checker", Path(__file__).with_name("check-local-idempotency.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        failures = []
        module.check_tavily_bridge(self.home, {"mcp_servers": {"tavily": bridge.configuration(self.home, "codex")}}, failures)
        self.assertEqual(failures, [])
        module.check_tavily_bridge(self.home, {}, failures)
        self.assertEqual(len(failures), 1)

    def test_source_mirrors_and_pin(self):
        codex = Path(__file__).resolve().parents[1]
        claude = codex.with_name("config-claude")
        for name in ("scripts/tavily_bridge.py", "references/tavily-bridge.md", "assets/tavily-bridge/package.json", "assets/tavily-bridge/package-lock.json", "assets/tavily-bridge/callback-readiness.patch.json"):
            self.assertEqual((codex / name).read_bytes(), (claude / name).read_bytes())
        lock = json.loads((codex / "assets/tavily-bridge/package-lock.json").read_text())
        self.assertEqual(lock["packages"]["node_modules/mcp-remote"]["version"], "0.15.2")


if __name__ == "__main__":
    unittest.main()
