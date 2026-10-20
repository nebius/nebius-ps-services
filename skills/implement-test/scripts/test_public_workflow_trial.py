#!/usr/bin/env python3
import unittest
import subprocess
import sys
import tempfile
import json
import tomllib
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock
import public_workflow_trial as trial
from public_workflow_trial import observe_event, concurrency


class ObservationTests(unittest.TestCase):
    def test_forced_cleanup_rejects_either_public_invocation(self):
        good = {
            'schema': 'implement-test/native-invocation-v2', 'exit_code': 0,
            'completed': True, 'timed_out': False, 'observer_complete': True,
            'cleanup_verified': True, 'forced_cleanup': False,
            'session_sha256': 'a' * 64,
        }
        run = Path('/disposable/run-test')
        for forced in ('initial', 'continuation'):
            values = {name: {**good, 'forced_cleanup': name == forced}
                      for name in ('initial', 'continuation')}
            receipt = {
                'schema': 'implement-test/public-invocations-v2', 'skill': 'implement',
                'run_id': run.name, 'objective_sha256': 'b' * 64,
                'intervened': False, 'entry': 'direct', 'rerun_status': 'ALREADY_COMPLETE',
                'delivery_sha256': trial.digest({}),
                **{name: {'path': name + '-invocation.json', 'sha256': trial.digest(value)}
                   for name, value in values.items()},
            }
            with mock.patch.object(trial, 'read', side_effect=[receipt, {}, values['initial'], values['continuation']]):
                with self.assertRaisesRegex(ValueError, 'native invocation did not complete'):
                    trial.validate_receipt(Path('/disposable/public-invocations.json'), run, 'b' * 64)

    def test_codex_launcher_allows_only_owned_git_and_private_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            project, home = root / 'project', root / 'private home'
            home.mkdir()
            command = trial.codex_command(project, home)
            self.assertNotIn('--sandbox', command)
            self.assertNotIn('--dangerously-bypass-approvals-and-sandbox', command)
            overrides = [command[index + 1] for index, part in enumerate(command) if part == '-c']
            config = tomllib.loads('\n'.join(overrides))
            self.assertEqual(config['default_permissions'], 'implement-test')
            profile = config['permissions']['implement-test']
            self.assertEqual(profile['extends'], ':workspace')
            self.assertEqual(profile['filesystem'], {str(home): 'write', str(project / '.git'): 'write'})
            (home / 'config.toml').write_text('sandbox_mode = "workspace-write"\n')
            with self.assertRaisesRegex(ValueError, 'legacy sandbox'):
                trial.codex_command(project, home)

    def test_effective_permissions_reject_overrides_from_other_layers(self):
        project, home = Path('/owned/project'), Path('/owned/home')
        config = {'default_permissions': 'implement-test', 'permissions': {
            'implement-test': {'extends': ':workspace',
                'filesystem': {str(home): 'write', str(project / '.git'): 'write'},
                'network': {'enabled': True}}}}
        trial.validate_codex_permissions(config, project, home)
        for legacy in ('read-only', 'workspace-write', 'danger-full-access'):
            with self.assertRaisesRegex(ValueError, 'legacy sandbox'):
                trial.validate_codex_permissions({**config, 'sandbox_mode': legacy}, project, home)
        config['permissions']['implement-test']['filesystem']['/unowned'] = 'write'
        with self.assertRaisesRegex(ValueError, 'scoped permission profile'):
            trial.validate_codex_permissions(config, project, home)

    def test_failed_native_commands_keep_only_allowlisted_diagnostics(self):
        event = {'type': 'item.completed', 'item': {
            'type': 'command_execution', 'exit_code': 1,
            'command': 'python3 /private/skill/workflow_intake.py accept --secret value',
            'aggregated_output': '{"code":"UNSAFE_STATE","error":"private secret"}\nOperation not permitted',
        }}
        observed = observe_event(event, 'codex')
        self.assertEqual(observed['diagnostics'], {
            'failed_commands': 1, 'intake_command_mentions': 1,
            'codes': ['PERMISSION_DENIED', 'UNSAFE_STATE'],
        })
        for sensitive in ('/private', 'secret', 'value', 'Operation not permitted'):
            self.assertNotIn(sensitive, str(observed))
        event['item']['aggregated_output'] = '{"code":"PRIVATE_CUSTOM_VALUE"}'
        self.assertEqual(observe_event(event, 'codex')['diagnostics']['codes'], [])

    def test_completed_unbound_native_session_is_precisely_failed(self):
        native = {'exit_code': 0, 'completed': True, 'timed_out': False,
                  'observer_complete': True, 'cleanup_verified': True,
                  'forced_cleanup': False}
        self.assertEqual(trial.initial_failure(native, None), {
            'status': 'FAIL', 'reason': 'public native invocation completed without an accepted objective',
        })
        self.assertIsNone(trial.initial_failure(native, ('run', {}, {})))
        self.assertEqual(trial.initial_failure({**native, 'observer_complete': False}, None)['status'], 'PARTIAL')

    def test_public_receipt_is_consumable_by_generation_lifecycle(self):
        # Exercise the real launcher/lifecycle filesystem boundary. Native
        # execution and semantic content are separate contracts, stubbed here.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            seed = root / 'seed'
            seed.mkdir()
            (seed / 'README.md').write_text('fixture\n')
            private = root / 'private'
            with mock.patch.object(trial.lifecycle, '_validate_runtime_preflight'):
                fixture = trial.lifecycle.prepare(private, seed)
            generation = fixture['generation_id']
            evidence = Path(fixture['evidence'])
            run_dir = Path(fixture['agent_home']) / 'implement/runs/test-run'
            coordinator = run_dir / 'execution/FEAT-001/coordinator.json'
            trial.write(coordinator, {'status': 'delivered'})
            objective = root / 'objective.md'
            objective.write_text('Build the fixture.')
            objective_state = {'revisions': [{'sha256': 'a' * 64}]}
            binding = (run_dir, {'status': 'done'}, objective_state)
            native = {
                'exit_code': 0, 'completed': True, 'observer_complete': True,
                'cleanup_verified': True, 'forced_cleanup': False,
            }
            output = StringIO()
            with (
                mock.patch.object(sys, 'argv', [
                    'public_workflow_trial.py', '--generation', generation,
                    '--objective', str(objective),
                ]),
                mock.patch.object(trial.lifecycle, 'default_root', return_value=private),
                mock.patch.object(trial, 'invoke', side_effect=[
                    (native, 'initial-session'), (native, 'fresh-session'),
                ]),
                mock.patch.object(trial, 'observe_binding', return_value=binding),
                mock.patch.object(trial, 'validate_receipt', return_value={}),
                redirect_stdout(output),
            ):
                self.assertEqual(trial.main(), 0)
            receipt = Path(json.loads(output.getvalue())['receipt'])
            manifest = evidence / 'live-results.json'
            head = subprocess.check_output(
                ['git', 'rev-parse', 'HEAD'], cwd=fixture['project'], text=True,
            ).strip()
            trial.write(manifest, {'status': 'PASS', 'project_head': head})
            scripts = Path(trial.__file__).resolve().parents[2] / 'worktree/scripts'
            with mock.patch('implement_semantics.validate_results', return_value=[]) as semantics:
                result = trial.lifecycle.validate_results(
                    private, generation, manifest, run_dir, receipt, scripts,
                )
            self.assertEqual(result['status'], 'RESULTS_VALID')
            self.assertEqual(receipt, evidence / 'public-invocations.json')
            self.assertEqual(semantics.call_args.kwargs['invocation_receipt'], receipt)
            self.assertFalse((run_dir / 'evidence').exists())
            for name in ('initial', 'continuation'):
                row = trial.read(receipt)[name]
                self.assertEqual(trial.digest(trial.read(evidence / row['path'])), row['sha256'])

    def native_stub(self, code, *, text='fixture', timeout=3):
        # Actual process/pipe tests with synthetic events, never native-agent proof.
        original = subprocess.Popen
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            with (mock.patch.object(trial.subprocess, 'Popen', side_effect=lambda command, **kwargs:
                                   original([sys.executable, '-c', code], **kwargs))):
                return trial.invoke_stream(root, root, 'claude', text, timeout)[0]

    def test_drains_buffered_completion_before_return(self):
        value = self.native_stub("import sys; sys.stdin.read(); print('{\"type\":\"result\",\"subtype\":\"success\",\"is_error\":false}')")
        self.assertTrue(value['completed'])
        self.assertTrue(value['observer_complete'])
        self.assertTrue(value['cleanup_verified'])
        self.assertFalse(value['forced_cleanup'])

    def test_blocked_stdin_is_bounded_and_cleanup_is_not_pass(self):
        value = self.native_stub('import time; time.sleep(30)', text='x'*128*1024, timeout=1)
        self.assertTrue(value['timed_out'])
        self.assertTrue(value['forced_cleanup'])
        self.assertIsInstance(value['cleanup_verified'], bool)
        self.assertLess(value['elapsed_ms'], 10000)

    @unittest.skipUnless(hasattr(__import__('os'), 'fork'), 'process-group case requires POSIX')
    def test_descendant_pipe_cannot_certify_clean_completion(self):
        value = self.native_stub("import os,time,sys; sys.stdin.read(); child=os.fork(); time.sleep(30) if child == 0 else None", timeout=1)
        self.assertTrue(value['timed_out'])
        self.assertTrue(value['forced_cleanup'])
        self.assertLess(value['elapsed_ms'], 10000)

    def test_slot_refill_requires_admission_and_surviving_peer(self):
        def sample(tasks, admitted, ready):
            return {'waves':[{'wave':'FEAT-001/WAVE-001','native_running':{task:'native-'+task for task in tasks},
                              'admitted':{task:'receipt-'+task for task in admitted},'ready':ready}]}
        before=sample(['one','peer'],[],['next'])
        admitted=sample(['peer'],['one'],['next'])
        refilled=sample(['peer','next'],['one'],[])
        self.assertEqual(concurrency([before,admitted,refilled]),(2,True))
        self.assertEqual(concurrency([before, sample(['peer','next'],[],[])]),(2,False))
        self.assertEqual(concurrency([sample(['one'],[],['next']), sample(['next'],['one'],[])]),(1,False))

    def test_refill_survives_termination_before_admission(self):
        def snap(running, admitted=(), ready=()):
            return {'waves': [{'wave': 'w', 'native_running': running,
                               'admitted': dict.fromkeys(admitted, 'receipt'), 'ready': list(ready)}]}
        before = snap({'one': 'n1', 'peer': 'n2'}, ready=['next'])
        terminated = snap({'peer': 'n2'}, ready=['next'])
        admitted = snap({'peer': 'n2'}, admitted=['one'])
        refilled = snap({'peer': 'n2', 'next': 'n3'}, admitted=['one'])
        self.assertEqual(concurrency([before, terminated, admitted, refilled]), (2, True))
        self.assertEqual(concurrency([before, terminated, terminated, admitted, refilled]), (2, True))
        # An interrupted/restarted peer cannot bridge the gap. Neither can a
        # replacement which started before the departed result was admitted.
        for gap in (snap({}), snap({'peer': 'different'}), snap({'peer': 'n2', 'next': 'n3'})):
            self.assertEqual(concurrency([before, terminated, gap, admitted, refilled]), (2, False))

    def test_restarted_peer_and_duplicate_native_sessions_cannot_prove_refill(self):
        def snap(running, admitted=(), ready=()):
            return {'waves':[{'wave':'w', 'native_running':running,
                              'admitted':dict.fromkeys(admitted,'receipt'), 'ready':list(ready)}]}
        before = snap({'one':'n1', 'peer':'n2'}, ready=['next'])
        restarted = snap({'peer':'n3', 'next':'n4'}, admitted=['one'])
        self.assertEqual(concurrency([before,restarted]), (2,False))
        with self.assertRaisesRegex(ValueError, 'one native worker'):
            concurrency([snap({'one':'n1','two':'n1'})])

    def test_agent_message_does_not_certify_completed_continuation(self):
        value = observe_event({'item':{'type':'agent_message','text':'ALREADY_COMPLETE was not reached'}}, 'codex')
        self.assertNotIn('already_complete', value)
        self.assertFalse(value['complete'])

    def test_non_native_task_declarations_cannot_prove_concurrency(self):
        self.assertEqual(observe_event({'tasks':[{'status':'running'}]},'codex')['children'],{})
        self.assertEqual(observe_event({'type':'assistant','parent_tool_use_id':'tool'},'claude')['children'],{})


if __name__ == '__main__':
    unittest.main()
