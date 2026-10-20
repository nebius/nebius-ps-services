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

    def invoke_protocol(self, status):
        program = '''
import json,sys
for line in sys.stdin:
 e=json.loads(line); method=e.get('method'); result={}
 if 'id' not in e: continue
 if method=='config/read':result={'config':{}}
 if method=='thread/start':result={'thread':{'id':'root-native'}}
 print(json.dumps({'id':e['id'],'result':result}),flush=True)
 if method=='turn/start':
  print(json.dumps({'method':'turn/completed','params':{'threadId':'unrelated','turn':{'status':'completed'}}}),flush=True)
  print(json.dumps({'method':'turn/completed','params':{'threadId':'root-native','turn':{'status':STATUS}}}),flush=True)
'''.replace("STATUS", repr(status))
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
