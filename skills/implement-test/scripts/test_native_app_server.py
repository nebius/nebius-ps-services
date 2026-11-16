"""Protocol fixtures are adapter tests, never live native workflow proof."""
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import native_app_server as native
import public_workflow_trial as trial


class NativeAppServerTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "posix", "private native launches require POSIX")
    def test_native_child_creates_private_candidates_without_changing_parent_mask(self):
        program = '''
import json,pathlib,sys
request=json.loads(sys.stdin.readline())
pathlib.Path('candidate.json').write_text('{}\\n')
print(json.dumps({'id':request['id'],'result':{}}),flush=True)
sys.stdin.read()
'''
        prior = os.umask(0o022)
        try:
            with tempfile.TemporaryDirectory() as directory:
                server = native.NativeServer([sys.executable, '-c', program], Path(directory),
                                             None, lambda event: None)
                try:
                    server.request('initialize', {})
                    candidate = Path(directory).resolve() / 'candidate.json'
                    self.assertEqual(candidate.stat().st_mode & 0o777, 0o600)
                    from workflow_state import read
                    self.assertEqual(read(candidate), {})
                    self.assertEqual(os.umask(0o022), 0o022)
                finally:
                    server.close()
                self.assertTrue(server.cleanup_verified)
        finally:
            os.umask(prior)

    @staticmethod
    def spawn_event(status="failed", item_id="private-item"):
        return {"method": "item/completed", "params": {
            "threadId": "private-root", "item": {
                "type": "collabAgentToolCall", "tool": "spawnAgent", "id": item_id,
                "senderThreadId": "private-root", "receiverThreadIds": ["private-child"],
                "status": status, "prompt": "SECRET_PROMPT", "model": "SECRET_MODEL",
                "agentsStates": {"private-child": {"message": "SECRET_OUTPUT"}},
            }}}

    def test_launch_diagnostics_retain_rejection_and_prestart_child_without_content(self):
        trace = native.LaunchTrace()
        trace.observe(self.spawn_event(), "private-root", 12)
        trace.children({"private-child": "active"}, 20)
        value = trace.result(1, False)
        self.assertEqual([row["kind"] for row in value["events"]], ["spawn", "child"])
        self.assertEqual(value["events"][0]["status"], "failed")
        self.assertEqual(value["events"][0]["children_sha256"],
                         [value["events"][1]["child_sha256"]])
        self.assertTrue(value["absence_is_inconclusive"])
        self.assertFalse(value["observer_complete"])
        for secret in ("private-root", "private-child", "private-item", "SECRET_PROMPT",
                       "SECRET_MODEL", "SECRET_OUTPUT", "agentsStates", "prompt"):
            self.assertNotIn(secret, json.dumps(value))
        self.assertNotIn("task", json.dumps(value))  # No timing-derived task mapping.

    def test_unrelated_and_missing_spawn_observations_remain_inconclusive(self):
        trace = native.LaunchTrace()
        trace.observe(self.spawn_event(), "unrelated-root", 5)
        trace.observe(self.spawn_event(), None, 10)
        value = trace.result(0, True)
        self.assertEqual(value["events"], [])
        self.assertTrue(value["absence_is_inconclusive"])

    def test_launch_metadata_rejects_malformed_values_and_bounds_retention(self):
        trace = native.LaunchTrace()
        trace.observe({"method": []}, "private-root", 1)
        trace.observe(self.spawn_event(status=["SECRET_STATUS"]), "private-root", 2)
        trace.children({"private-child": "SECRET_STATUS"}, 3)
        self.assertEqual(trace.malformed, 3)
        for index in range(trace.LIMIT + 3):
            trace.observe(self.spawn_event(item_id=str(index)), "private-root", index)
        value = trace.result(2, False)
        self.assertEqual(len(value["events"]), trace.LIMIT)
        self.assertEqual(value["overflow_events"], 3)
        self.assertNotIn("SECRET_STATUS", json.dumps(value))

    def test_interactive_request_is_observed_but_never_approved(self):
        trace = native.LaunchTrace()
        server = native.NativeServer.__new__(native.NativeServer)
        server.pending = (json.dumps({"method": "SECRET_METHOD", "id": 3,
                                      "params": {"prompt": "SECRET_REQUEST"}}) + "\n").encode()
        server.notify = lambda event: trace.observe(event, "private-root", 1)
        server.send = mock.Mock()
        with self.assertRaisesRegex(ValueError, "unsupported interactive request"):
            server.receive(0)
        self.assertEqual(server.send.call_args.args[0]["error"]["code"], -32601)
        self.assertEqual(trace.result(0, False)["unsupported_interactions"], 1)
        self.assertNotIn("SECRET", json.dumps(trace.result(0, False)))

    @unittest.skipUnless(hasattr(os, "fork"), "process groups require POSIX")
    def test_cleanup_proves_descendant_exit_after_leader_exits(self):
        program = '''
import json,os,signal,sys,time
e=json.loads(sys.stdin.readline()); child=os.fork()
if child==0:
 signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(30); os._exit(0)
print(json.dumps({'id':e['id'],'result':{'child':child}}),flush=True)
sys.stdin.read()
'''
        with tempfile.TemporaryDirectory() as directory:
            server = native.NativeServer([sys.executable, "-c", program], Path(directory), None, lambda event: None)
            child = server.request("initialize", {})["child"]
            try:
                server.close()
                self.assertTrue(server.cleanup_verified)
                self.assertTrue(server.forced_cleanup)
                with self.assertRaises(ProcessLookupError):
                    os.kill(child, 0)
            finally:
                try:
                    os.kill(child, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_metadata_snapshot_has_no_subscription_or_history_effects(self):
        server = mock.Mock()
        server.request.side_effect = [
            {"data": ["root", "child", "other"], "nextCursor": None},
            {"thread": {"id": "child", "parentThreadId": "root", "status": {"type": "active"},
                        "preview": "private prompt"}},
            {"thread": {"id": "other", "parentThreadId": "different", "status": {"type": "active"}}},
        ]
        self.assertEqual(native.child_snapshot(server, "root", 100), {"child": "active"})
        self.assertEqual(server.request.call_args_list, [
            mock.call("thread/loaded/list", {"limit": 64}, deadline=100),
            mock.call("thread/read", {"threadId": "child", "includeTurns": False}, deadline=100),
            mock.call("thread/read", {"threadId": "other", "includeTurns": False}, deadline=100),
        ])

    def test_incomplete_inventory_and_unknown_status_fail_closed(self):
        server = mock.Mock()
        server.request.return_value = {"data": [], "nextCursor": "more"}
        with self.assertRaises(ValueError):
            native.child_snapshot(server, "root", 100)
        server.request.side_effect = [{"data": ["c"]}, {"thread": {
            "id": "c", "parentThreadId": "root", "status": {"type": "newStatus"}}}]
        with self.assertRaises(ValueError):
            native.child_snapshot(server, "root", 100)

    def test_snapshot_uses_one_deadline_for_all_children(self):
        program = """
import json,sys,time
for line in sys.stdin:
 e=json.loads(line); time.sleep(.12)
 r={'data':['a','b','c']} if e['method']=='thread/loaded/list' else {'thread':{'id':e['params']['threadId'],'parentThreadId':'root','status':{'type':'active'}}}
 print(json.dumps({'id':e['id'],'result':r}),flush=True)
"""
        with tempfile.TemporaryDirectory() as directory:
            server = native.NativeServer([sys.executable, "-c", program], Path(directory), None, lambda event: None)
            started = time.monotonic()
            try:
                with self.assertRaises(ValueError):
                    native.child_snapshot(server, "root", started + .2)
                self.assertLess(time.monotonic() - started, .4)
            finally:
                server.close()
            self.assertTrue(server.cleanup_verified)

    def invoke_protocol(self, status, events=()):
        program = '''
import json,sys
for line in sys.stdin:
 e=json.loads(line); method=e.get('method'); result={}
 if 'id' not in e: continue
 if method=='config/read':result={'config':{}}
 if method=='thread/start':result={'thread':{'id':'root-native'}}
 print(json.dumps({'id':e['id'],'result':result}),flush=True)
 if method=='turn/start':
  for event in EVENTS: print(json.dumps(event),flush=True)
  print(json.dumps({'method':'turn/completed','params':{'threadId':'unrelated','turn':{'status':'completed'}}}),flush=True)
  print(json.dumps({'method':'turn/completed','params':{'threadId':'root-native','turn':{'status':STATUS}}}),flush=True)
'''.replace("STATUS", repr(status)).replace("EVENTS", repr(events))
        original = subprocess.Popen
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (mock.patch.object(native.subprocess, "Popen", side_effect=lambda command, **kw:
                                    original([sys.executable, "-c", program], **kw)),
                  mock.patch.object(trial, "validate_codex_permissions")):
                return trial.invoke(root, root, "codex", "private objective", 5)[0]

    def test_completion_is_root_bound_and_graceful_shutdown_is_proven(self):
        value = self.invoke_protocol("completed")
        self.assertTrue(value["completed"])
        self.assertTrue(value["cleanup_verified"])
        self.assertFalse(value["forced_cleanup"])
        self.assertEqual(value["exit_code"], 0)
        self.assertNotIn("private objective", json.dumps(value))
        self.assertNotIn("root-native", json.dumps(value))

    def test_public_invocation_wires_bounded_spawn_diagnostics(self):
        event = self.spawn_event()
        event["params"]["threadId"] = "root-native"
        event["params"]["item"]["senderThreadId"] = "root-native"
        value = self.invoke_protocol("failed", [event])
        self.assertEqual(value["launch_diagnostics"]["events"][0]["status"], "failed")
        self.assertNotIn("SECRET", json.dumps(value))
        self.assertTrue(value["cleanup_verified"])

    def test_malformed_interactive_request_keeps_failed_native_receipt(self):
        value = self.invoke_protocol("completed", [{
            "id": 3, "method": "item/completed", "params": None,
            "private_payload": "SECRET_REQUEST",
        }])
        self.assertFalse(value["observer_complete"])
        self.assertEqual(value["launch_diagnostics"]["unsupported_interactions"], 1)
        self.assertTrue(value["cleanup_verified"])
        self.assertNotIn("SECRET", json.dumps(value))

    def test_failed_turn_exits_without_timeout_or_false_completion(self):
        value = self.invoke_protocol("failed")
        self.assertFalse(value["completed"])
        self.assertFalse(value["timed_out"])
        self.assertTrue(value["cleanup_verified"])
        self.assertLess(value["elapsed_ms"], 5000)

    def test_native_protocol_error_never_retains_server_payload(self):
        program = "import sys,json; e=json.loads(sys.stdin.readline()); print(json.dumps({'id':e['id'],'error':{'message':'secret payload'}}),flush=True); sys.stdin.read()"
        with tempfile.TemporaryDirectory() as directory:
            server = native.NativeServer([sys.executable, "-c", program], Path(directory), None, lambda event: None)
            try:
                with self.assertRaisesRegex(ValueError, "native request rejected: initialize") as error:
                    server.request("initialize", {})
                self.assertNotIn("secret", str(error.exception))
            finally:
                server.close()
            self.assertTrue(server.cleanup_verified)


if __name__ == "__main__":
    unittest.main()
