#!/usr/bin/env python3
"""Both-host exact retirement and customized-resource preservation."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from retire_workflow_capture import RETIRED, retire


class RetirementTests(unittest.TestCase):
    def fixture(self, root, host):
        home, source = root / host, root / "source"
        for name in ("implement", "implement-test", "worktree"):
            (source / name).mkdir(parents=True, exist_ok=True)
            (source / name / "SKILL.md").write_text("# fixture\n")
        (home / "hooks").mkdir(parents=True)
        (home / ".install-hooks-state").mkdir()
        rows = []
        for name in RETIRED:
            path = home / "hooks" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# unchanged owned payload\n")
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
            rows.append(
                "\t".join(
                    (
                        name,
                        "local:" + str(source / "prompt-session-intake/assets/hooks"),
                        name,
                        sha,
                        sha,
                    )
                )
            )
        provenance = home / ".install-hooks-state/hook-files.tsv"
        provenance.write_text("\n".join(sorted(rows)) + "\n")
        handler = {
            "type": "command",
            "timeout": 10,
            "command": f'python3 "{home}/hooks/hook_runtime.py" --agent {host} --hook prompt_session_intake.py',
        }
        if host == "codex":
            handler["statusMessage"] = "Staging bound-session prompt intent"
        foreign = {
            "matcher": "custom",
            "hooks": [{"type": "command", "command": "echo preserve"}],
        }
        value = {
            "hooks": {
                "UserPromptSubmit": [{"hooks": [handler]}, foreign],
                "Stop": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": "python3 stop_lifecycle_arbiter.py",
                            }
                        ]
                    }
                ],
            },
            "custom_setting": True,
        }
        settings = home / ("hooks.json" if host == "codex" else "settings.json")
        settings.write_text(json.dumps(value))
        private = home / "task-implementer/history/retained.json"
        private.parent.mkdir(parents=True)
        private.write_text("private history\n")
        tree = home / "sdlc-runs/project/run-old/worktrees/worker/retained.txt"
        tree.parent.mkdir(parents=True)
        tree.write_text("uncommitted work\n")
        return home, source, settings, private, tree

    def test_exact_retirement_is_idempotent_and_preserves_other_resources(self):
        for host in ("codex", "claude"):
            with self.subTest(host=host), tempfile.TemporaryDirectory() as temp:
                home, source, settings, private, tree = self.fixture(
                    Path(temp).resolve(), host
                )
                before = settings.read_bytes()
                self.assertEqual(
                    retire(home, source, host, preflight=True)["retired_entries"], 1
                )
                self.assertEqual(settings.read_bytes(), before)
                report = retire(home, source, host)
                self.assertEqual(report["retired_files"], len(RETIRED))
                value = json.loads(settings.read_text())
                self.assertEqual(len(value["hooks"]["UserPromptSubmit"]), 1)
                self.assertTrue(value["custom_setting"])
                self.assertEqual(len(value["hooks"]["Stop"]), 1)
                self.assertEqual(private.read_text(), "private history\n")
                self.assertEqual(tree.read_text(), "uncommitted work\n")
                after = settings.read_bytes()
                self.assertEqual(retire(home, source, host)["retired_files"], 0)
                self.assertEqual(settings.read_bytes(), after)

    def test_customized_registration_or_payload_preserves_capture_bundle(self):
        for host in ("codex", "claude"):
            for variant in (
                "flags",
                "mixed",
                "metadata",
                "foreign_owner",
                "changed_file",
                "symlink",
            ):
                with (
                    self.subTest(host=host, variant=variant),
                    tempfile.TemporaryDirectory() as temp,
                ):
                    home, source, settings, _private, _tree = self.fixture(
                        Path(temp).resolve(), host
                    )
                    value = json.loads(settings.read_text())
                    entry = value["hooks"]["UserPromptSubmit"][0]
                    if variant == "flags":
                        entry["hooks"][0]["command"] += " --custom"
                    if variant == "mixed":
                        entry["hooks"].append(
                            {"type": "command", "command": "echo custom"}
                        )
                    if variant == "metadata":
                        entry["matcher"] = "custom"
                    if variant == "foreign_owner":
                        p = home / ".install-hooks-state/hook-files.tsv"
                        p.write_text(p.read_text().replace("local:", "foreign:"))
                    path = home / "hooks/prompt_session_intake.py"
                    if variant == "changed_file":
                        path.write_text("# user customization\n")
                    if variant == "symlink":
                        backing = home / "custom.py"
                        path.rename(backing)
                        path.symlink_to(backing)
                    settings.write_text(json.dumps(value))
                    before = settings.read_bytes()
                    with self.assertRaisesRegex(ValueError, "owner reconciliation"):
                        retire(home, source, host)
                    self.assertEqual(settings.read_bytes(), before)
                    self.assertTrue(
                        all((home / "hooks" / name).exists() for name in RETIRED)
                    )

    def test_exact_legacy_direct_codex_command_is_retired(self):
        with tempfile.TemporaryDirectory() as temp:
            home, source, settings, _private, _tree = self.fixture(
                Path(temp).resolve(), "codex"
            )
            value = json.loads(settings.read_text())
            value["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"] = (
                'SKILLS_AGENT=codex python3 "${CODEX_HOME:-$HOME/.codex}/hooks/prompt_session_intake.py"'
            )
            settings.write_text(json.dumps(value))
            self.assertEqual(retire(home, source, "codex")["retired_entries"], 1)

    def test_customized_dependency_preserves_entire_bundle_and_registration(self):
        for host in ("codex", "claude"):
            for name in RETIRED:
                with (
                    self.subTest(host=host, dependency=name),
                    tempfile.TemporaryDirectory() as temp,
                ):
                    home, source, settings, _private, _tree = self.fixture(
                        Path(temp).resolve(), host
                    )
                    (home / "hooks" / name).write_text("# user-owned customization\n")
                    provenance = home / ".install-hooks-state/hook-files.tsv"
                    before = settings.read_bytes(), provenance.read_bytes()
                    for preflight in (True, False):
                        with self.assertRaisesRegex(ValueError, "owner reconciliation"):
                            retire(home, source, host, preflight=preflight)
                        self.assertEqual(
                            (settings.read_bytes(), provenance.read_bytes()), before
                        )
                        self.assertTrue(
                            all((home / "hooks" / item).exists() for item in RETIRED)
                        )

    def test_missing_provenance_blocks_registered_capture_before_runtime_replacement(
        self,
    ):
        for host in ("codex", "claude"):
            with self.subTest(host=host), tempfile.TemporaryDirectory() as temp:
                home, source, settings, _private, _tree = self.fixture(
                    Path(temp).resolve(), host
                )
                (home / ".install-hooks-state/hook-files.tsv").unlink()
                before = settings.read_bytes()
                with self.assertRaisesRegex(ValueError, "owner reconciliation"):
                    retire(home, source, host, preflight=True)
                self.assertEqual(settings.read_bytes(), before)

    def test_unregistered_custom_payload_is_preserved_without_blocking(self):
        with tempfile.TemporaryDirectory() as temp:
            home, source, settings, _private, _tree = self.fixture(
                Path(temp).resolve(), "codex"
            )
            value = json.loads(settings.read_text())
            value["hooks"]["UserPromptSubmit"].pop(0)
            settings.write_text(json.dumps(value))
            (home / "hooks/prompt_session_intake.py").write_text("# custom\n")
            report = retire(home, source, "codex", preflight=True)
            self.assertEqual(report["retired_files"], 0)
            self.assertEqual(set(report["preserved"]), RETIRED)

    def test_existing_settings_without_hooks_allow_first_install(self):
        for host in ("codex", "claude"):
            with self.subTest(host=host), tempfile.TemporaryDirectory() as temp:
                home, source, settings, _private, _tree = self.fixture(
                    Path(temp).resolve(), host
                )
                (home / ".install-hooks-state/hook-files.tsv").unlink()
                settings.write_text(json.dumps({"model": "fixture"}))
                before = settings.read_bytes()
                self.assertEqual(
                    retire(home, source, host, preflight=True)["retired_files"], 0
                )
                self.assertEqual(settings.read_bytes(), before)

    def test_catalog_install_blocks_before_replacing_custom_capture_runtime(self):
        source = Path(__file__).resolve().parents[2]
        for host in ("codex", "claude"):
            with self.subTest(host=host), tempfile.TemporaryDirectory() as temp:
                base = Path(temp).resolve()
                home, fixture_source, settings, _private, _tree = self.fixture(
                    base, host
                )
                provenance = home / ".install-hooks-state/hook-files.tsv"
                provenance.write_text(
                    provenance.read_text().replace(str(fixture_source), str(source))
                )
                value = json.loads(settings.read_text())
                value["hooks"].pop("Stop")
                value["hooks"]["UserPromptSubmit"][0]["matcher"] = "custom"
                settings.write_text(json.dumps(value))
                runtime = home / "hooks/hook_runtime.py"
                runtime.write_text("# retained installed runtime\n")
                before = (
                    settings.read_bytes(),
                    provenance.read_bytes(),
                    runtime.read_bytes(),
                )
                destination = base / "installed-skills"
                result = subprocess.run(
                    [
                        "bash",
                        str(source / "install-skills.sh"),
                        "--agent",
                        host,
                        str(source),
                        str(destination),
                    ],
                    cwd=source,
                    env={
                        **os.environ,
                        "HOME": str(base / "home"),
                        "CODEX_HOME": str(base / "codex"),
                        "CLAUDE_CONFIG_DIR": str(base / "claude"),
                        "PYTHONDONTWRITEBYTECODE": "1",
                        "NO_COLOR": "1",
                    },
                    capture_output=True,
                    text=True,
                    timeout=90,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("owner reconciliation", result.stdout + result.stderr)
                self.assertFalse(destination.exists())
                self.assertEqual(
                    (
                        settings.read_bytes(),
                        provenance.read_bytes(),
                        runtime.read_bytes(),
                    ),
                    before,
                )


if __name__ == "__main__":
    unittest.main()
