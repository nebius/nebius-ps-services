#!/usr/bin/env python3
from test_implement_catalog import successful_verification

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
    def test_frozen_guard_blocks_initial_and_continuation_launch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            with mock.patch.object(trial, "invoke") as invoke:
                with self.assertRaisesRegex(ValueError, "catalog drift"):
                    trial.run_project(root, root, "codex", "objective", 10, root / "first",
                                      verify_frozen=mock.Mock(side_effect=ValueError("catalog drift")))
                invoke.assert_not_called()
            binding = (root / "run", {"status": "done"}, {"revisions": [{"sha256": "a" * 64}]})
            with mock.patch.object(trial, "invoke", return_value=({}, "session")) as invoke, \
                    mock.patch.object(trial, "observe_binding", return_value=binding), \
                    mock.patch.object(trial, "initial_failure", return_value=None), \
                    mock.patch.object(trial, "read", return_value={}):
                guard = mock.Mock(side_effect=[None, ValueError("catalog drift")])
                with self.assertRaisesRegex(ValueError, "catalog drift"):
                    trial.run_project(root, root, "codex", "objective", 10, root / "second", verify_frozen=guard)
                self.assertEqual(invoke.call_count, 1)
                self.assertEqual(guard.call_count, 2)

    def test_failed_native_project_is_not_downgraded_by_incomplete_peer(self):
        self.assertEqual(trial.combined_outcome([{"status": "FAIL"}, {"status": "PARTIAL"}]), "FAIL")
        self.assertEqual(trial.combined_outcome([{"status": "PASS"}, {"status": "PARTIAL"}]), "PARTIAL")
        self.assertEqual(trial.combined_outcome([{"status": "PASS"}, {"status": "PASS"}]), "PASS")
    def test_multi_project_acceptance_requires_typed_capacity_and_all_cli_cases(self):
        import workflow_automatic
        import implement_statistics
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home, repo, evidence = root / "home", root / "repo", root / "evidence"
            runs = [home / "implement" / name / ("run-" + name) for name in ("a", "b")]
            for run in runs:
                run.mkdir(parents=True)
            sample = {"shared_admitted": list(map(str, runs)), "shared_workers": 2,
                      "shared_worktrees": 16}
            documents = {}
            for index, folder in enumerate((evidence, evidence / "project-b")):
                documents[folder / "public-invocations.json"] = {"run_id": runs[index].name}
                documents[folder / "initial-invocation.json"] = {
                    "session_sha256": str(index) * 64, "started_monotonic_ns": 1,
                    "finished_monotonic_ns": 10, "samples": [sample]}

            def context(run):
                scope = "project-a" if run == runs[0] else "project-b"
                return ({"logical_project_root": str(repo / scope),
                         "git_common_dir": str(repo / ".git"), "automatic_outer": True},
                        {"revisions": [{"sha256": "a" * 64}]})

            with mock.patch.object(trial, "read", side_effect=documents.__getitem__), mock.patch.object(
                trial, "validate_run", side_effect=context
            ), mock.patch.object(trial, "validate_receipt"), mock.patch.object(
                workflow_automatic, "verify_delivery"
            ), mock.patch.object(implement_statistics, "require_complete") as reporting, mock.patch.object(
                trial.subprocess, "run"
            ) as command:
                for invalid in (None, True, -1, 5):
                    sample["shared_workers"] = invalid
                    with self.assertRaisesRegex(ValueError, "capacity proof"):
                        trial.validate_projects(evidence, repo, home)
                command.assert_not_called()
                sample["shared_workers"] = 2
                command.side_effect = [subprocess.CompletedProcess([], 0, value, "")
                                       for value in ("hello world\n", "\n", "one two three\n")]
                self.assertEqual(trial.validate_projects(evidence, repo, home)["status"], "PASS")
                self.assertEqual(command.call_count, 3)
                self.assertEqual(reporting.call_args_list[-2:], [mock.call(run) for run in runs])
                command.reset_mock()
                reporting.side_effect = [None, implement_statistics.StatisticsError("missing reporting")]
                with self.assertRaisesRegex(implement_statistics.StatisticsError, "missing reporting"):
                    trial.validate_projects(evidence, repo, home)
                command.assert_not_called()

    def test_shared_capacity_requires_all_three_typed_bounds(self):
        valid = {"shared_admitted": ["a", "b", "c", "d"], "shared_workers": 4,
                 "shared_worktrees": 16}
        # Retained trees still count while no workers are active.
        trial.validate_shared_capacity([valid, {**valid, "shared_workers": 0}], ["a", "b"])
        for field, invalids in {
            "shared_admitted": [None, True, "ab", ["a", "b", "c", "d", "e"],
                                ["a", "b", "a"], ["a", "b", None], ["a", "b", ""]],
            "shared_workers": [None, True, -1, 5, 1.0, "1"],
            "shared_worktrees": [None, True, -1, 17, 1.0, "1"],
        }.items():
            for invalid in invalids:
                with self.subTest(field=field, invalid=invalid), self.assertRaisesRegex(ValueError, "capacity proof"):
                    trial.validate_shared_capacity([{**valid, field: invalid}], ["a", "b"])
            missing = {key: value for key, value in valid.items() if key != field}
            with self.assertRaisesRegex(ValueError, "capacity proof"):
                trial.validate_shared_capacity([missing], ["a", "b"])
        for samples in ([], None, [None], [{**valid, "shared_admitted": ["a"]}]):
            with self.assertRaisesRegex(ValueError, "capacity proof"):
                trial.validate_shared_capacity(samples, ["a", "b"])

    def test_sampler_counts_retained_trees_in_waiting_objectives(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary).resolve()
            run = home / "implement/project/run-test"
            trial.write(home / "workflow-capacity/reservations.json", {"runs": {
                "a": {"admitted": True, "workers": ["worker"], "worktrees": ["one"]},
                "b": {"admitted": True, "workers": [], "worktrees": ["two", "three"]}}})
            with mock.patch.object(trial, "observe_binding", return_value=(
                run, {}, {"revisions": [{"sha256": "a" * 64}]}
            )):
                observed = trial.sample(home, "session", home / "repo", {}, 1)
            self.assertEqual(observed["shared_worktrees"], 3)
            self.assertEqual(observed["shared_workers"], 1)
            self.assertEqual(observed["shared_admitted"], ["a", "b"])

    def test_forced_cleanup_rejects_either_public_invocation(self):
        good = {
            'schema': 'implement-test/native-invocation-v3', 'exit_code': 0,
            'completed': True, 'timed_out': False, 'observer_complete': True,
            'cleanup_verified': True, 'forced_cleanup': False,
            'session_sha256': 'a' * 64,
        }
        run = Path('/disposable/run-test')
        for forced in ('initial', 'continuation'):
            values = {name: {**good, 'forced_cleanup': name == forced}
                      for name in ('initial', 'continuation')}
            receipt = {
                'schema': 'implement-test/public-invocations-v3', 'skill': 'implement',
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
            project.mkdir()
            subprocess.run(['git', 'init', '-q', str(project)], check=True)
            command = trial.codex_command(project, home)
            self.assertNotIn('--sandbox', command)
            self.assertNotIn('--dangerously-bypass-approvals-and-sandbox', command)
            overrides = [command[index + 1] for index, part in enumerate(command) if part == '-c']
            config = tomllib.loads('\n'.join(overrides))
            self.assertEqual(config['default_permissions'], 'implement-test')
            profile = config['permissions']['implement-test']
            self.assertEqual(profile['extends'], ':workspace')
            self.assertEqual(profile['filesystem'], {str(path): 'write' for path in (home, project, project / '.git', root / 'project-worktrees')})
            (home / 'config.toml').write_text('sandbox_mode = "workspace-write"\n')
            with self.assertRaisesRegex(ValueError, 'legacy sandbox'):
                trial.codex_command(project, home)

    def test_effective_permissions_reject_overrides_from_other_layers(self):
        project, home = Path('/owned/project'), Path('/owned/home')
        roots = (home, project, project / '.git', Path('/owned/project-worktrees'))
        self.enterContext(mock.patch.object(trial, 'permission_roots', return_value=roots))
        config = {'features': {'hooks': True}, 'default_permissions': 'implement-test', 'permissions': {
            'implement-test': {'extends': ':workspace',
                'filesystem': {str(path): 'write' for path in roots},
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
        self.assertEqual(trial.initial_failure({**native, 'observer_complete': False, 'exit_code': 1}, None)['status'], 'FAIL')

    def test_public_receipt_is_consumable_by_generation_lifecycle(self):
        # Exercise the real launcher/lifecycle filesystem boundary. Native
        # execution and semantic content are separate contracts, stubbed here.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            seed = root / 'seed'
            seed.mkdir()
            (seed / 'README.md').write_text('fixture\n')
            private = root / 'private'
            with mock.patch.object(trial.lifecycle, "_validate_runtime_preflight"), mock.patch.object(
                    trial.lifecycle.catalog, "preflight", return_value={
                        "schema": "implement-test/preflight-v2", "status": "PASS",
                        "catalog": trial.lifecycle.catalog.snapshot(), "stages_sha256": "a" * 64, "verification": successful_verification(), "verification_sha256": trial.lifecycle.catalog.digest(successful_verification())}):
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
                mock.patch.object(trial.lifecycle.catalog, 'runtime_hooks', return_value={'fixture': True}),
                mock.patch('agent_runtime.installed_skills_dir', return_value=trial.lifecycle.catalog.anchor()),
                mock.patch.object(trial, 'invoke', side_effect=lambda *args: (native, 'fixture-session')) as invoked,
                mock.patch.object(trial, 'validate_projects', return_value={'status': 'PASS'}),
                mock.patch.object(trial, 'observe_binding', return_value=binding),
                mock.patch.object(trial, 'validate_receipt', return_value={}),
                redirect_stdout(output),
            ):
                self.assertEqual(trial.main(), 0)
            requests = [call.args[3] for call in invoked.call_args_list]
            initial = [text for text in requests if not text.startswith('$implement continue ')]
            self.assertEqual(len(initial), 2)
            self.assertTrue(all(text.endswith(trial.OUTER_MEMORY_HANDOFF) for text in initial))
            continuations = [text for text in requests if text.startswith('$implement continue ')]
            self.assertEqual(len(continuations), 2)
            self.assertTrue(all(trial.OUTER_MEMORY_HANDOFF not in text for text in continuations))
            receipt = Path(json.loads(output.getvalue())['receipt'])
            manifest = evidence / 'live-results.json'
            head = subprocess.check_output(
                ['git', 'rev-parse', 'HEAD'], cwd=fixture['project'], text=True,
            ).strip()
            trial.write(manifest, {'status': 'PASS', 'project_head': head})
            scripts = Path(trial.__file__).resolve().parents[2] / 'worktree/scripts'
            with mock.patch('implement_semantics.validate_results', return_value=[]) as semantics, mock.patch('implement_browser.validate') as browser, mock.patch.object(trial.lifecycle.catalog, 'runtime_hooks', return_value={'fixture': True}):
                result = trial.lifecycle.validate_results(
                    private, generation, manifest, run_dir, receipt, scripts,
                )
            self.assertEqual(result['status'], 'RESULTS_VALID')
            browser.assert_called_once()
            self.assertEqual(receipt, evidence / 'public-invocations.json')
            self.assertEqual(semantics.call_args.kwargs['invocation_receipt'], receipt)
            self.assertFalse((run_dir / 'evidence').exists())
            for name in ('initial', 'continuation'):
                row = trial.read(receipt)[name]
                self.assertEqual(trial.digest(trial.read(evidence / row['path'])), row['sha256'])

    def native_stub(self, code, *, text='fixture', observation_interval=3):
        # Actual process/pipe tests with synthetic events, never native-agent proof.
        original = subprocess.Popen
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            with (mock.patch.object(trial.subprocess, 'Popen', side_effect=lambda command, **kwargs:
                                   original([sys.executable, '-c', code], **kwargs))):
                return trial.invoke_stream(root, root, 'claude', text, observation_interval)[0]

    def test_drains_buffered_completion_before_return(self):
        value = self.native_stub("import sys; sys.stdin.read(); print('{\"type\":\"result\",\"subtype\":\"success\",\"is_error\":false}')")
        self.assertTrue(value['completed'])
        self.assertTrue(value['observer_complete'])
        self.assertTrue(value['cleanup_verified'])
        self.assertFalse(value['forced_cleanup'])

    def test_slow_stdin_and_silence_continue_across_observation_intervals(self):
        value = self.native_stub("import time,sys; time.sleep(.2); sys.stdin.read(); print('{\"type\":\"result\",\"subtype\":\"success\",\"is_error\":false}')",
                                 text='x'*128*1024, observation_interval=.05)
        self.assertFalse(value['timed_out'])
        self.assertFalse(value['forced_cleanup'])
        self.assertTrue(value['completed'])
        self.assertTrue(value['cleanup_verified'])
        self.assertGreaterEqual(value['elapsed_ms'], 200)

    @unittest.skipUnless(hasattr(__import__('os'), 'fork'), 'process-group case requires POSIX')
    def test_descendant_pipe_cannot_certify_clean_completion(self):
        value = self.native_stub("import os,time,sys; sys.stdin.read(); child=os.fork(); time.sleep(30) if child == 0 else None", observation_interval=.05)
        self.assertFalse(value['timed_out'])
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

    def test_recovered_peer_can_survive_the_actual_admission_release(self):
        def snap(running, admitted=(), ready=('next',)):
            return {'waves': [{'wave': 'w', 'native_running': running,
                               'admitted': dict.fromkeys(admitted, 'receipt'), 'ready': list(ready)}]}
        # Native departure does not release the occupied slot. Recovery can
        # finish before admission, with its fresh peer spanning the release.
        history = [snap({'one': 'n1', 'peer': 'n2'}), snap({'one': 'n1'}),
                   snap({}), snap({'peer': 'n3'}),
                   snap({'peer': 'n3'}, admitted=['one']),
                   snap({'peer': 'n3', 'next': 'n4'}, admitted=['one'], ready=[])]
        self.assertEqual(concurrency(history), (2, True))
        self.assertEqual(concurrency(history[:3] + [history[3]] * 3 + history[4:]), (2, True))

    def test_admission_release_needs_a_prior_peer_and_later_replacement(self):
        def snap(running, admitted=(), ready=('next',)):
            return {'waves': [{'wave': 'w', 'native_running': running,
                               'admitted': dict.fromkeys(admitted, 'receipt'), 'ready': list(ready)}]}
        before = snap({'one': 'n1', 'peer': 'n2'})
        departed = snap({'peer': 'n2'})
        admitted = snap({'peer': 'n2'}, admitted=['one'])
        refilled = snap({'peer': 'n2', 'next': 'n4'}, admitted=['one'], ready=[])
        cases = {
            'peer starts at admission': [before, snap({}), admitted, refilled],
            'replacement starts at admission': [before, departed, refilled],
            'peer disappears after admission': [before, departed, admitted,
                                                snap({}, admitted=['one']), refilled],
            'peer restarts after admission': [before, departed, admitted,
                                             snap({'peer': 'n3'}, admitted=['one']),
                                             snap({'peer': 'n3', 'next': 'n4'}, admitted=['one'])],
            'replacement starts before admission': [before,
                snap({'peer': 'n2', 'next': 'n4'}), admitted, refilled],
        }
        for name, history in cases.items():
            with self.subTest(name=name):
                self.assertEqual(concurrency(history), (2, False))

    def test_agent_message_does_not_certify_completed_continuation(self):
        value = observe_event({'item':{'type':'agent_message','text':'ALREADY_COMPLETE was not reached'}}, 'codex')
        self.assertNotIn('already_complete', value)
        self.assertFalse(value['complete'])

    def test_non_native_task_declarations_cannot_prove_concurrency(self):
        self.assertEqual(observe_event({'tasks':[{'status':'running'}]},'codex')['children'],{})
        self.assertEqual(observe_event({'type':'assistant','parent_tool_use_id':'tool'},'claude')['children'],{})


if __name__ == '__main__':
    unittest.main()
