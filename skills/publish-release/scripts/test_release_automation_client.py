"""Exercise enrolled release requests without project-local publisher wrappers."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CLIENT = Path(__file__).resolve().parents[2] / "merge-pr/scripts/automation_client.py"


class ReleaseAutomationClientTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="release-client-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.checkout = self.root / "checkout"
        self.checkout.mkdir()
        subprocess.run(["git", "init", "-q", str(self.checkout)], check=True)
        self.calls = self.root / "calls.jsonl"
        tools = self.root / "bin"
        tools.mkdir()
        fake = tools / "gh"
        fake.write_text(
            "#!/usr/bin/env python3\n"
            "import base64, json, os, sys\n"
            "from pathlib import Path\n"
            "args = sys.argv[1:]\n"
            "with Path(os.environ['CALL_LOG']).open('a') as log:\n"
            "    log.write(json.dumps(args) + '\\n')\n"
            "if args == ['api', 'repos/owner/repo']:\n"
            "    print(json.dumps({'full_name': 'owner/repo', 'default_branch': 'main'}))\n"
            "elif args == ['api', 'repos/owner/repo/contents/.github/ai-automation.json',"
            " '--method', 'GET', '-f', 'ref=main']:\n"
            "    config = {'schema_version': 1, 'authority': 'actions',"
            " 'workflows': {'release': 'release-owner.yml'},"
            " 'release_projects': ['vpngw', 'nebius-cxcli']}\n"
            "    print(json.dumps({'content': base64.b64encode("
            "json.dumps(config).encode()).decode()}))\n"
            "elif args[:2] == ['workflow', 'run']:\n"
            "    pass\n"
            "else:\n"
            "    raise SystemExit('Unexpected GitHub operation')\n"
        )
        fake.chmod(0o755)
        self.env = {
            **os.environ,
            "PATH": str(tools) + os.pathsep + os.environ["PATH"],
            "CALL_LOG": str(self.calls),
            "GH_REPO": "unrelated/repository",
        }

    def invoke(
        self, *args: str, cwd: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(CLIENT), "--repo", "owner/repo", *args],
            cwd=cwd or self.checkout,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )

    def observed(self) -> list[list[str]]:
        if not self.calls.exists():
            return []
        return [json.loads(line) for line in self.calls.read_text().splitlines()]

    def test_enrolled_projects_dispatch_exact_request_without_local_git_effects(
        self,
    ) -> None:
        for project in ("vpngw", "nebius-cxcli"):
            for tag in ("", "expected-v1.2.3"):
                with self.subTest(project=project, tag=tag):
                    result = self.invoke(
                        "release",
                        project,
                        "--pr",
                        "123",
                        "--completion-run",
                        "456",
                        "--tag",
                        tag,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(
                        self.observed()[-1],
                        [
                            "workflow",
                            "run",
                            "release-owner.yml",
                            "--repo",
                            "owner/repo",
                            "--ref",
                            "main",
                            "-f",
                            f"project={project}",
                            "-f",
                            "pr_number=123",
                            "-f",
                            "completion_run=456",
                            "-f",
                            f"tag={tag}",
                        ],
                    )
        refs = subprocess.check_output(
            ["git", "for-each-ref"], cwd=self.checkout, text=True
        )
        self.assertEqual(refs, "")
        status = subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=self.checkout, text=True
        )
        self.assertEqual(status, "")

    def test_explicit_repository_works_outside_checkout_with_inherited_override(
        self,
    ) -> None:
        result = self.invoke(
            "release", "vpngw", "--pr", "123", "--completion-run", "456", cwd=self.root
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.observed()
        self.assertEqual(calls[0], ["api", "repos/owner/repo"])
        self.assertEqual(calls[-1][calls[-1].index("--repo") + 1], "owner/repo")

    def test_invalid_or_unenrolled_release_never_dispatches(self) -> None:
        for project, number, completion in (
            ("vpngw", "0", "456"),
            ("vpngw", "123", "0"),
            ("vpngw", "123", "-1"),
            ("nebius-acc-management", "123", "456"),
        ):
            with self.subTest(project=project, number=number, completion=completion):
                result = self.invoke(
                    "release", project, "--pr", number, "--completion-run", completion
                )
                self.assertNotEqual(result.returncode, 0)
        self.assertFalse(
            any(call[:2] == ["workflow", "run"] for call in self.observed())
        )

    def test_retired_or_incomplete_commands_never_contact_github(self) -> None:
        for args in (
            ("release", "vpngw", "--prep", "1.2.3"),
            ("release", "vpngw", "--publish", "1.2.3"),
            ("release", "vpngw", "--pr", "123"),
        ):
            with self.subTest(args=args):
                self.assertNotEqual(self.invoke(*args).returncode, 0)
        self.assertEqual(self.observed(), [])


if __name__ == "__main__":
    unittest.main()
