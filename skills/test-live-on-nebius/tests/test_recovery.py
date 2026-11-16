"""Recovery effects tested with real process groups and fake SDK operations."""

import contextlib
import json
import os
import select
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "assets"))
import vm_trial
from live_state import Journal, StateError
from process_group import reconcile_owned, run_owned
from sdk_actions import Actions


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(
            dir=Path(tempfile.gettempdir()).resolve()
        )
        self.addCleanup(self.temp.cleanup)
        self.j = Journal.create(
            self.temp.name, {"project": "project-example", "engine": "sdk"}
        )

    def test_parent_exit_does_not_leave_descendant_writer(self):
        path = Path(self.temp.name) / "child.pid"
        command = [
            sys.executable,
            "-c",
            "import subprocess,sys; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); open(sys.argv[1],'w').write(str(p.pid))",
            str(path),
        ]
        with self.j.lock():
            result = run_owned(
                self.j,
                command,
                cwd=self.temp.name,
                env=os.environ.copy(),
                deadline=time.monotonic() + 10,
            )
        self.assertEqual(result["code"], 0)
        self.assertFalse(self.j.data["evidence"]["owned_process"]["active"])
        with self.assertRaises(ProcessLookupError):
            os.kill(int(path.read_text()), 0)

    def test_failed_delete_can_retry_but_uncertain_delete_cannot(self):
        j = self.j
        with j.lock():
            j.own(
                "vm", resource_id="vm", kind="vm", parent="project-example", owner="sdk"
            )
            j.phase("cleanup")
            j.intent("delete:vm", "old", deleting=True)
            observed = NS(
                metadata=NS(
                    id="vm",
                    parent_id="project-example",
                    labels={"live-test-run": j.data["run_id"]},
                )
            )
            with self.assertRaisesRegex(StateError, "RECONCILE"):
                Actions(j).delete("vm", Mock(), NS(id="vm"), observed)
            j.terminal("delete:vm", False)

            def submit(method, request, **kwargs):
                result = NS(operation_id="op", resource_id="vm")
                kwargs["on_accepted"](result)
                return result

            with patch("sdk.runtime.submit", side_effect=submit):
                Actions(j).delete("vm", Mock(), NS(id="vm"), observed)
            self.assertEqual(j.data["operations"]["delete:vm:2"]["status"], "succeeded")

    def test_cxcli_auth_environment_cannot_inherit_delegate(self):
        j = self.j
        j.data["plan"].update(
            cxcli_source="/source", source_identity={"cxcli_source_sha256": "hash"}
        )
        with (
            patch.object(vm_trial, "source_digest", return_value="hash"),
            patch("process_group.run_owned", return_value={"code": 0}) as run,
            patch.dict(
                os.environ,
                {
                    "CXCLI_NEBIUS_DELEGATE_ID": "foreign",
                    "NEBIUS_IAM_TOKEN": "sentinel",
                    "AWS_SECRET_ACCESS_KEY": "sentinel",
                },
            ),
        ):
            vm_trial.cxcli(j, ["validate", "config"], time.monotonic() + 10)
        env = run.call_args.kwargs["env"]
        self.assertNotIn("CXCLI_NEBIUS_DELEGATE_ID", env)
        self.assertNotIn("NEBIUS_IAM_TOKEN", env)
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", env)
        self.assertEqual(env["NEBIUS_PROFILE"], "codex-agent-project-example")

    def test_cxcli_nonzero_exit_retains_sanitized_supervisor_evidence(self):
        j = self.j
        source = Path(self.temp.name) / "cxcli"
        entrypoint = source / ".venv/bin/nebius-cxcli"
        entrypoint.parent.mkdir(parents=True)
        entrypoint.write_text(
            "#!/bin/sh\nprintf 'private diagnostic sentinel\\n'\nexit 7\n"
        )
        entrypoint.chmod(0o700)
        j = Journal.create(
            self.temp.name,
            {
                **j.data["plan"],
                "cxcli_source": str(source),
                "source_identity": {"cxcli_source_sha256": "hash"},
            },
        )
        with j.lock():
            with (
                patch.object(vm_trial, "source_digest", return_value="hash"),
                self.assertRaisesRegex(StateError, "CXCLI_COMMAND_FAILED"),
            ):
                vm_trial.cxcli(j, ["validate", "config"], time.monotonic() + 10)
            failure = j.data["evidence"]["cxcli_failure"]
            self.assertEqual(failure["action"], "validate")
            self.assertEqual(failure["exit"], 7)
            self.assertEqual(len(failure["captured_output_sha256"]), 64)
            self.assertFalse(j.data["evidence"]["owned_process"]["active"])
            self.assertNotIn("private diagnostic sentinel", j.path.read_text())

    def test_first_durable_intent_contains_complete_create_and_delete_descriptor(self):
        class SimulatedCrash(RuntimeError):
            pass

        for action in ("create", "delete"):
            with self.subTest(action=action):
                j = Journal.create(self.temp.name, self.j.data["plan"])
                with j.lock():
                    j.evidence("resource_baseline", {"vm": []})
                    observed = NS(
                        metadata=NS(
                            id="vm-id",
                            parent_id="project-example",
                            labels={"live-test-run": j.data["run_id"]},
                        )
                    )
                    if action == "delete":
                        j.own(
                            "vm",
                            resource_id="vm-id",
                            kind="vm",
                            parent="project-example",
                            owner="sdk",
                        )
                        j.phase("cleanup")
                    original_save = j.save

                    def crash_after_save(original_save=original_save):
                        original_save()
                        raise SimulatedCrash()

                    with (
                        patch.object(j, "save", side_effect=crash_after_save),
                        patch("sdk.runtime.submit") as submit,
                        self.assertRaises(SimulatedCrash),
                    ):
                        if action == "create":
                            Actions(j).create(
                                "vm", Mock(), NS(metadata=observed.metadata), kind="vm"
                            )
                        else:
                            Actions(j).delete("vm", Mock(), NS(id="vm-id"), observed)
                    submit.assert_not_called()
                restored = Journal(j.directory)
                record = restored.data["operations"][
                    "delete:vm" if action == "delete" else "vm"
                ]
                self.assertEqual(record["action"], action)
                self.assertEqual(record["kind"], "vm")
                self.assertEqual(record["status"], "intent")
                if action == "create":
                    self.assertEqual(record["parent"], "project-example")
                    self.assertEqual(record["dependencies"], [])
                else:
                    self.assertEqual(record["target_id"], "vm-id")
                    self.assertEqual(record["resource_key"], "vm")

    def test_native_config_drift_blocks_destroy_before_process(self):
        j = self.j
        j.data["plan"].update(
            cxcli_source="/source", source_identity={"cxcli_source_sha256": "hash"}
        )
        config = Path(self.temp.name) / "config.yaml"
        config.write_text("scope: original")
        j.data["evidence"]["native_inputs"] = vm_trial.native_digest(config)
        config.write_text("scope: changed")
        with (
            patch.object(vm_trial, "source_digest", return_value="hash"),
            patch("process_group.run_owned") as run,
            self.assertRaisesRegex(StateError, "NATIVE_INPUTS_CHANGED"),
        ):
            vm_trial.cxcli(j, ["destroy", str(config)], time.monotonic() + 10)
        run.assert_not_called()

    def test_lost_create_receipt_adopts_only_unique_scoped_resource_and_operation(self):
        j = self.j
        with j.lock():
            j.intent("vm", "hash")
            j.data["operations"]["vm"].update(
                action="create", kind="vm", parent="project-example", dependencies=[]
            )
            j.evidence("resource_baseline", {"vm": ["old"]})
            resource = NS(
                metadata=NS(
                    id="new",
                    parent_id="project-example",
                    labels={
                        "live-test-run": j.data["run_id"],
                        "live-test-intent": "vm",
                    },
                )
            )
            operation = Mock(id="operation", resource_id="new")
            operation.done.return_value = True
            operation.successful.return_value = True
            client = Mock()
            with (
                patch.object(
                    vm_trial, "context", return_value=contextlib.nullcontext()
                ),
                patch.object(
                    vm_trial,
                    "clients",
                    return_value={"vm": (client, Mock(), Mock(), Mock())},
                ),
                patch(
                    "sdk.runtime.collect_pages", side_effect=[[resource], [operation]]
                ),
                patch("sdk.runtime.rpc", return_value=operation),
            ):
                vm_trial.reconcile(j, time.monotonic() + 10)
            self.assertEqual(j.data["operations"]["vm"]["operation_id"], "operation")
            self.assertEqual(j.data["resources"]["vm"]["id"], "new")
            client.create.assert_not_called()

    def test_lost_delete_receipt_recovers_authoritative_absence_without_resubmit(self):
        from sdk.runtime import CloudError

        j = self.j
        with j.lock():
            j.own(
                "vm", resource_id="vm", kind="vm", parent="project-example", owner="sdk"
            )
            j.phase("cleanup")
            j.intent("delete:vm", "hash", deleting=True)
            j.data["operations"]["delete:vm"].update(
                action="delete", kind="vm", resource_key="vm", target_id="vm"
            )
            client = Mock()
            with (
                patch.object(
                    vm_trial, "context", return_value=contextlib.nullcontext()
                ),
                patch.object(
                    vm_trial,
                    "clients",
                    return_value={"vm": (client, Mock(), Mock(), Mock())},
                ),
                patch("sdk.runtime.rpc", side_effect=CloudError("NOT_FOUND")),
            ):
                vm_trial.reconcile(j, time.monotonic() + 10)
            client.delete.assert_not_called()
            j.finish_cleanup(writers_quiescent=True)
            self.assertTrue(j.data["cleanup_complete"])

    def test_parent_death_stops_supervisor_and_resume_proves_group_absence(self):
        scripts = str(Path(__file__).resolve().parents[1] / "scripts")
        code = (
            "import sys,os,time;sys.path.insert(0,sys.argv[1]);"
            "from live_state import Journal;from process_group import run_owned;"
            "j=Journal(sys.argv[2]);"
            'exec(\'with j.lock():\\n run_owned(j,[sys.executable,"-c","import time;time.sleep(30)"],cwd=sys.argv[2],env=os.environ.copy(),deadline=time.monotonic()+10)\')'
        )
        parent = subprocess.Popen(
            [sys.executable, "-B", "-c", code, scripts, str(self.j.directory)]
        )
        try:
            until = time.monotonic() + 5
            while time.monotonic() < until:
                data = json.loads(self.j.path.read_text())
                if data["evidence"].get("owned_process", {}).get("state") == "running":
                    break
                time.sleep(0.05)
            else:
                self.fail("supervisor did not start")
            parent.kill()
            parent.wait(timeout=5)
            recovered = Journal(self.j.directory)
            with recovered.lock():
                until = time.monotonic() + 5
                while True:
                    try:
                        reconcile_owned(recovered)
                        break
                    except StateError:
                        if time.monotonic() >= until:
                            raise
                        time.sleep(0.05)
                self.assertFalse(recovered.data["evidence"]["owned_process"]["active"])
        finally:
            if parent.poll() is None:
                parent.send_signal(signal.SIGTERM)
                parent.wait(timeout=15)

    def test_unreleased_launch_gate_cannot_start_a_cloud_writer(self):
        with self.j.lock():
            self.j.evidence(
                "owned_process", {"active": True, "state": "launch_pending"}
            )
            reconcile_owned(self.j)
            self.assertFalse(self.j.data["evidence"]["owned_process"]["active"])

    def test_delayed_group_absence_retains_identity_for_recovery(self):
        real_killpg = os.killpg

        def delay_probe(pid, sig):
            if sig:
                return real_killpg(pid, sig)
            return None

        with self.j.lock():
            with (
                patch("process_group.os.killpg", side_effect=delay_probe),
                self.assertRaisesRegex(StateError, "WRITER_NOT_PROVEN_QUIESCENT"),
            ):
                run_owned(
                    self.j,
                    [sys.executable, "-c", "pass"],
                    cwd=self.temp.name,
                    env=os.environ.copy(),
                    deadline=time.monotonic() + 10,
                )
            self.assertTrue(self.j.data["evidence"]["owned_process"]["active"])
            reconcile_owned(self.j)
            self.assertFalse(self.j.data["evidence"]["owned_process"]["active"])

    def test_parent_pipe_loss_after_exit_event_stops_anchor(self):
        read_status, write_status = os.pipe()
        read_gate, write_gate = os.pipe()
        script = Path(__file__).resolve().parents[1] / "scripts/process_group.py"
        child = subprocess.Popen(
            [
                sys.executable,
                "-B",
                str(script),
                str(write_status),
                str(read_gate),
                str(time.monotonic() + 30),
                json.dumps([sys.executable, "-c", "pass"]),
            ],
            pass_fds=(write_status, read_gate),
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        os.close(write_status)
        os.close(read_gate)
        try:
            self.assertTrue(select.select([read_status], [], [], 5)[0])
            self.assertEqual(json.loads(os.read(read_status, 4096)), {"event": "ready"})
            os.write(write_gate, b"G")
            self.assertTrue(select.select([read_status], [], [], 5)[0])
            self.assertEqual(json.loads(os.read(read_status, 4096))["event"], "exit")
            os.close(write_gate)
            write_gate = None
            child.wait(timeout=5)
            with self.assertRaises(ProcessLookupError):
                os.killpg(child.pid, 0)
        finally:
            if child.returncode is None:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=5)
            os.close(read_status)
            if write_gate is not None:
                os.close(write_gate)


if __name__ == "__main__":
    unittest.main()
