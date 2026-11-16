import copy
import hashlib
import json
import os
import sys
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import implement_browser as browser
from implement_errors import LifecycleError


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return hashlib.sha256(path.read_bytes()).hexdigest()


class BrowserEvidenceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.state = {
            "generation_id": "00000000-0000-0000-0000-000000000001",
            "promoted_head": "a" * 40,
            "web_port": 49152,
            "runtime_catalog": {"sha256": "b" * 64},
            "browser_instance": {
                **browser.support().initial_state(
                    "00000000-0000-0000-0000-000000000001"
                ),
                "status": "CLOSED",
            },
        }
        task = {"id": 7, "title": "Browser task", "completed": True}
        self.value = {
            "schema": browser.SCHEMA,
            "generation_id": self.state["generation_id"],
            "head": self.state["promoted_head"],
            "url": "http://127.0.0.1:49152",
            "catalog_sha256": "b" * 64,
            "status": "PASS",
            "cleanup": "PASS",
            "phases": [],
            "restart": {"started_monotonic_ns": 3, "finished_monotonic_ns": 4},
        }
        for i, phase in enumerate(("before-restart", "after-restart")):
            attempt = str(i) * 32
            expected = {
                "schema": browser.SCHEMA,
                "generation_id": self.state["generation_id"],
                "head": self.state["promoted_head"],
                "url": self.value["url"],
                "phase": phase,
                "attempt_id": attempt,
                "record": None if i == 0 else task,
            }
            observed = {
                **expected,
                "record": task,
                "status": "PASS",
                "cleanup": True,
                "browser_version": "fixture",
                "checks": ["frontend", "create", "update", "reload"]
                if i == 0
                else ["frontend", "reload"],
            }
            artifacts = {}
            for name in ("assertions.json", "trace.zip", "page.png"):
                path = self.root / "evidence/browser" / attempt / name
                digest = write(
                    path, observed if name == "assertions.json" else {"fixture": True}
                )
                artifacts[name] = {
                    "path": str(path.relative_to(self.root)),
                    "sha256": digest,
                }
            self.value["phases"].append(
                {
                    "input": expected,
                    "record": task,
                    "artifacts": artifacts,
                    "exit_code": 0,
                    "cleanup": "PASS",
                    "started_monotonic_ns": 1 + i * 4,
                    "finished_monotonic_ns": 2 + i * 4,
                    "correlation": {"api": True, "database": True, "record": task},
                }
            )
        self.save()

    def save(self):
        self.state["browser_evidence_sha256"] = write(
            self.root / "evidence/browser.json", self.value
        )

    def test_browser_launch_capability_is_partial_but_assertion_failure_is_not(self):
        from implement_errors import CapabilityUnavailableError

        for status, error in (
            ("PARTIAL", CapabilityUnavailableError),
            ("FAIL", LifecycleError),
        ):

            def launch(command, **kwargs):
                env = kwargs["env"]
                expected = json.loads(Path(env["IMPLEMENT_BROWSER_INPUT"]).read_text())
                write(
                    Path(env["IMPLEMENT_BROWSER_OUTPUT"]) / "assertions.json",
                    {
                        **expected,
                        "checks": [],
                        "cleanup": True,
                        "browser_version": "",
                        "status": status,
                    },
                )
                process = mock.Mock(pid=12345, returncode=1)
                for stream in ("stdout", "stderr"):
                    read_fd, write_fd = os.pipe()
                    os.close(write_fd)
                    setattr(process, stream, os.fdopen(read_fd, "rb"))
                return process

            with (
                self.subTest(status=status),
                mock.patch.object(browser.subprocess, "Popen", side_effect=launch),
                mock.patch.object(browser, "close_owned") as close,
            ):
                with self.assertRaises(error):
                    browser.run_phase(
                        self.state, self.root, "before-restart", None, mock.Mock()
                    )
                close.assert_called_once()

    def test_runner_output_is_bounded_without_changing_assertion_artifacts(self):
        bundle = self.root / "private/browser/bundle"
        bundle.mkdir(parents=True)
        real_popen = subprocess.Popen
        script = """import os,json,pathlib
for i in range(32):
 os.write(1,b'x'*65536)
 os.write(2,b'y'*65536)
p=pathlib.Path(os.environ['IMPLEMENT_BROWSER_OUTPUT'])
v=json.loads(pathlib.Path(os.environ['IMPLEMENT_BROWSER_INPUT']).read_text())
v.update(record={'id':1,'title':'fixture','completed':True}, checks=['frontend','create','update','reload'],cleanup=True,status='PASS',browser_version='fixture')
(p/'assertions.json').write_text(json.dumps(v))
(p/'trace.zip').write_bytes(b'trace')
(p/'page.png').write_bytes(b'page')
"""
        def launch(_argv, **kwargs):
            if _argv[0] != "node":
                return real_popen(_argv, **kwargs)
            return real_popen([sys.executable, "-I", "-c", script], **kwargs)
        with mock.patch.object(browser.subprocess, "Popen", side_effect=launch):
            result = browser.run_phase(self.state, self.root, "before-restart", None, lambda: None)
        output = self.root / Path(result["artifacts"]["assertions.json"]["path"]).parent
        value = json.loads((output / "runner-output.json").read_text())
        self.assertFalse((output / "runner.log").exists())
        self.assertLess((output / "runner-output.json").stat().st_size, 1024)
        self.assertEqual(value["stdout"]["bytes"], 32 * 65536)
        self.assertEqual(value["stderr"]["sha256"], hashlib.sha256(b'y' * 32 * 65536).hexdigest())
        self.assertTrue(all(row["complete"] for row in value.values()))
        self.assertEqual(result["cleanup"], "PASS")

    def test_api_correlation_allows_metadata_but_rejects_duplicates_and_wrong_types(
        self,
    ):
        task = {"id": 7, "title": "Browser task", "completed": True}
        database = mock.Mock(
            return_value={"task_id": 7, "title": task["title"], "completed": True}
        )
        with mock.patch.object(browser, "build_opener") as opener:
            response = opener.return_value.open.return_value.__enter__.return_value
            response.read.return_value = json.dumps(
                [{**task, "created_at": "fixture"}]
            ).encode()
            self.assertTrue(browser.correlate(self.state, task, database)["api"])
            for tasks in (
                [task, task],
                [{**task, "completed": 1}],
                [{**task, "title": 7}],
                [],
            ):
                response.read.return_value = json.dumps(tasks).encode()
                with self.assertRaises(LifecycleError):
                    browser.correlate(self.state, task, database)

    def test_complete_two_phase_evidence_and_changed_artifact(self):
        self.assertEqual(browser.validate(self.state, self.root)["status"], "PASS")
        path = self.root / self.value["phases"][0]["artifacts"]["trace.zip"]["path"]
        path.write_text("changed")
        with self.assertRaisesRegex(LifecycleError, "artifact"):
            browser.validate(self.state, self.root)

    def test_wrong_identity_failed_exit_and_cleanup_cannot_pass(self):
        original = copy.deepcopy(self.value)
        for field, value in [
            ("generation_id", "foreign"),
            ("head", "c" * 40),
            ("url", "http://example.invalid"),
            ("catalog_sha256", "d" * 64),
            ("cleanup", "FAIL"),
        ]:
            with self.subTest(field=field):
                self.value = {**original, field: value}
                self.save()
                with self.assertRaises(LifecycleError):
                    browser.validate(self.state, self.root)
        for field, value in [("exit_code", 1), ("cleanup", "FAIL")]:
            self.value = copy.deepcopy(original)
            self.value["phases"][0][field] = value
            self.save()
            with self.assertRaises(LifecycleError):
                browser.validate(self.state, self.root)

    def test_missing_evidence_symlink_and_prose_claim_do_not_qualify(self):
        path = self.root / "evidence/browser.json"
        path.unlink()
        with self.assertRaises(OSError):
            browser.validate(self.state, self.root)
        self.value["phases"] = []
        self.save()
        with self.assertRaises(LifecycleError):
            browser.validate(self.state, self.root)
        target = self.root / "other.json"
        path.rename(target)
        path.symlink_to(target)
        with self.assertRaises(LifecycleError):
            browser.validate(self.state, self.root)

    def test_restart_must_preserve_record_and_boundary(self):
        original = copy.deepcopy(self.value)
        self.value["phases"][1]["record"]["id"] = 8
        self.save()
        with self.assertRaises(LifecycleError):
            browser.validate(self.state, self.root)
        self.value = original
        self.value["restart"]["finished_monotonic_ns"] = 0
        self.save()
        with self.assertRaises(LifecycleError):
            browser.validate(self.state, self.root)


if __name__ == "__main__":
    unittest.main()
