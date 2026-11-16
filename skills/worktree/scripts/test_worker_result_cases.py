"""The same receipt and dispatch contracts exercised by both real-Git fixtures.

All native identities and exit observations here are synthetic test inputs.
"""
from pathlib import Path
from datetime import datetime, timedelta, timezone
import json
from unittest import mock

import workflow_execution as engine
import workflow_reconcile as reconcile
from workflow_objectives import session_hash
from workflow_state import WorkflowError, read, write
from workflow_worker_contract import launch_contract
from workflow_worker_results import ingest_result, read_receipt, receipt_path, require_dispatchable


class WorkerResultContractCases:
    def test_shared_native_startup_headroom_and_exact_boundary(self):
        self.prepare()
        if read(self.run_dir / 'run.json')['owner'] == 'agentic-sdlc':
            engine.seal_tdd_base(self.run_dir, 'FEAT-001', 'test: startup base')
        assignments = engine.prepare_wave(self.run_dir, 'FEAT-001', 'WAVE-001')
        fixed = datetime(2026, 11, 3, tzinfo=timezone.utc)
        def observe(moment, operation, *args):
            stamp = {"utc": moment.isoformat(), "monotonic_ns": int(moment.timestamp() * 1_000_000_000),
                     "clock_id": "a" * 64, "scope": "machine-boot"}
            with mock.patch.object(engine, "_liveness_clock", return_value=stamp):
                return operation(*args, clock=lambda: moment)

        for assignment, elapsed in zip(assignments, (79, 299), strict=True):
            args = (self.run_dir, 'FEAT-001', 'WAVE-001', assignment['task_id'], assignment['assignment_digest'])
            launch = observe(fixed, engine.arm_task, *args)['launch']
            self.assertEqual(launch['health_policy']['startup_observation_seconds'], 300)
            self.assertIn('Long startup requests diagnosis', launch['prompt'])
            self.assertEqual(observe(fixed + timedelta(seconds=63), engine.watch_task, *args)['status'], 'PENDING_START')
            self.assertEqual(observe(fixed + timedelta(seconds=elapsed), engine.watch_task, *args)['status'], 'PENDING_START')
            # Replaying arm cannot renew the original dispatch window.
            observe(fixed + timedelta(seconds=elapsed), engine.arm_task, *args)
            observed = observe(fixed + timedelta(hours=120), engine.watch_task, *args)
            self.assertEqual(observed['status'], 'PENDING_START')
            self.assertEqual(observed['warning'], 'STARTUP_REVIEW')
            started = observe(fixed + timedelta(hours=120), engine.start_task, *args,
                              'synthetic-startup-' + assignment['task_id'], Path(assignment['scope_cwd']))
            self.assertEqual(started['health_policy']['duration_expiry'], 'disabled')
            task = read(engine.task_path(self.run_dir, 'FEAT-001', 'WAVE-001', assignment['task_id']))
            self.assertEqual(task['dispatched_at'], '2026-11-03T00:00:00Z')
            self.assertEqual(task['attempt'], 1)

    def result_fixture(self, *, start_peer=False):
        self.prepare()
        if read(self.run_dir / 'run.json')['owner'] == 'agentic-sdlc':
            engine.seal_tdd_base(self.run_dir, 'FEAT-001', 'test: receipt fixture base')
        assignments = engine.prepare_wave(self.run_dir, 'FEAT-001', 'WAVE-001')
        self.assertEqual(len(assignments), 2)
        first, peer = assignments
        self.result_start(first)
        if start_peer:
            self.result_start(peer)
        return first, peer

    def result_session(self):
        return getattr(self, 'session', 'deterministic-coordinator')

    def result_start(self, assignment):
        args = (self.run_dir, 'FEAT-001', 'WAVE-001', assignment['task_id'])
        armed = engine.arm_task(*args, assignment['assignment_digest'])
        native = 'receipt-native-stub-' + assignment['task_id']
        engine.start_task(*args, assignment['assignment_digest'], native, Path(assignment['scope_cwd']))
        return armed, native

    def result_payload(self, assignment, status='implemented'):
        gaps = []
        if status == 'replan_required':
            gaps = [{'kind': 'design', 'summary': 'Assigned interface cannot satisfy the declared behavior.',
                     'evidence': ['The required interface is outside this write claim.'],
                     'requirement_ids': ['REQ-001'], 'design_ids': ['FEAT-001']}]
        return {'task_id': assignment['task_id'], 'assignment_digest': assignment['assignment_digest'],
                'status': status, 'summary': 'Scoped work evidence', 'decisions': [], 'open_risks': [],
                'spec_gaps': gaps, 'validation': 'Synthetic validation observation',
                'review': 'Synthetic review observation'}

    def result_observation(self, assignment, *, session=None):
        state = read(engine.task_path(self.run_dir, 'FEAT-001', 'WAVE-001', assignment['task_id']))
        observer = session or self.result_session()
        path = (self.run_dir / 'execution/FEAT-001/terminations'
                / f"{assignment['task_id']}-{state['attempt']}-{session_hash(observer)}.json")
        value = {'schema': 'workflow/worker-termination-v1',
                 'assignment_digest': assignment['assignment_digest'],
                 'worker_session_hash': state['worker_session_hash'],
                 'observer_session_hash': session_hash(observer), 'status': 'terminated',
                 'native_handle': 'synthetic-native-handle', 'observation': 'Synthetic exit; not live proof'}
        if not path.exists():
            write(path, value, exclusive=True)
        else:
            self.assertEqual(read(path), value)
        return path

    def result_ingest(self, assignment, status='implemented', *, session=None, payload=None):
        observer = session or self.result_session()
        observation = self.result_observation(assignment, session=observer)
        value = ingest_result(self.run_dir, 'FEAT-001', 'WAVE-001', assignment['task_id'],
                              payload or self.result_payload(assignment, status), observation, observer)
        return Path(value['receipt_path']), observation, value

    def result_classify_blocked(self, receipt, *, proposed="IMPLEMENTATION_DEFECT"):
        if read(self.run_dir / 'run.json')['owner'] != 'agentic-sdlc':
            return None
        import sys
        scripts = Path(__file__).resolve().parents[2] / 'sdlc-classify-failure/scripts'
        if str(scripts) not in sys.path:
            sys.path.insert(0, str(scripts))
        import repair_control
        from test_repair_control import failure_payload
        payload = failure_payload(proposed=proposed)
        state_path = self.run_dir / 'current-state.json'
        state = read(state_path) if state_path.exists() else {}
        run = read(self.run_dir / 'run.json')
        state.update(run_id=run['run_id'], project_id=run['project_id'], current_execution='FEAT-001')
        state['fingerprint_ids'] = [key + ':' + value for key, value in payload['fingerprints'].items()]
        state['fingerprint_ids'] += ['context:sha256:' + '8' * 64, 'future-policy:sha256:' + '9' * 64]
        state.setdefault('checkpoint_id', 'checkpoint-0099')
        write(state_path, state)
        # Publish matching synthetic phase evidence, as the coordinator does.
        checkpoint = 'checkpoints/' + state['checkpoint_id'] + '.json'
        write(self.run_dir / checkpoint, state)
        write(self.run_dir / 'checkpoints/latest.json', {'path': checkpoint, 'checkpoint_id': state['checkpoint_id']})
        for key in ('execution_id', 'phase', 'execution_lifecycle', 'integration_commit', 'fingerprints'):
            payload.pop(key)
        with mock.patch('workflow_transition.runtime_session_identity', return_value=self.result_session()):
            event = repair_control.capture_worker_failure(
                self.run_dir, 'FEAT-001', 'WAVE-001', read(receipt)['worker_result']['task_id'],
                read(receipt)['receipt_sha256'], payload)['event']
        self.assertEqual(event['fingerprints'], repair_control._current_fingerprints(self.run_dir))
        classification = repair_control.classify_failure(
            self.run_dir, 'FEAT-001', event['event_id'], None)['classification']
        with mock.patch('workflow_transition.runtime_session_identity', return_value=self.result_session()):
            return repair_control.begin_remediation(
                self.run_dir, 'FEAT-001', classification['classification_id'], 'localized',
                'Repair the classified synthetic worker failure.', 'evidence/worker-failure.json',
            )['attempt']

    def result_write(self, assignment):
        target = Path(assignment['worktree']) / assignment['write_claims'][0]['path']
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('RESULT = 1\n')

    def result_finish(self, assignment, receipt, observation, *, session=None):
        return engine.finish_task(self.run_dir, 'FEAT-001', 'WAVE-001', assignment['task_id'],
                                  receipt, 'test: exact result receipt', termination=observation,
                                  session_identity=session or self.result_session())

    def test_shared_launch_contract_and_public_schema_identity(self):
        import workflow_transition
        first, _ = self.result_fixture()
        path = engine.assignment_path(self.run_dir, 'FEAT-001', 'WAVE-001', first['task_id'])
        launch = launch_contract(path, first)
        exported = workflow_transition.public_result({'launch': launch})['launch']
        self.assertEqual(exported, launch)
        self.assertEqual(launch['context_policy'], 'fresh-assignment-only')
        self.assertEqual(launch['scope_cwd'], first['scope_cwd'])
        schema = json.loads(Path(launch['output_schema_path']).read_text())
        self.assertIn('validation', schema['properties'])
        self.assertIn('review', schema['properties'])
        self.assertIn(json.dumps(first['worker_phases']), launch['prompt'])
        for item in launch['runtime_commands']:
            self.assertIn('runtime:' + item['resource'], first['conflict_domains'])
            self.assertEqual(item['acquire'][-1], item['resource'])
            self.assertIn(json.dumps(item), launch['prompt'])

    def test_shared_failure_handoff_in_initial_and_recovery_envelopes(self):
        first, _ = self.result_fixture()
        path = engine.assignment_path(self.run_dir, 'FEAT-001', 'WAVE-001', first['task_id'])
        for attempt in (None, 1):
            with self.subTest(recovery_attempt=attempt):
                prompt = launch_contract(path, first, recovery_attempt=attempt)['prompt']
                self.assertIn('Before repairing or rerunning an unexpected failed check', prompt)
                self.assertIn("the host's native approval mechanism", prompt)
                self.assertIn('requests do not themselves grant access', prompt)
                self.assertIn('policy/authority stop, never permission to bypass', prompt)
                self.assertIn('blocking review finding', prompt)
                self.assertIn('stop changes and return blocked to the coordinator', prompt)
                self.assertIn('declared before execution in the immutable assignment', prompt)
                self.assertIn('Never relabel an observed failure as expected', prompt)
                self.assertIn('Discover which instruction files exist before reading them', prompt)
                self.assertIn('do not include nonexistent paths in a read command', prompt)
                self.assertIn('authorized safe runtime teardown and lease release', prompt)
                self.assertIn('rather than presenting a reconstruction as contemporaneous', prompt)
                self.assertIn('target cadence of 30 seconds', prompt)
                self.assertIn('There is no duration-only attempt deadline', prompt)
                self.assertNotIn('at least every 30 seconds', prompt)

    def test_result_rejections_have_no_receipt_or_git_effect(self):
        first, _ = self.result_fixture()
        observation = self.result_observation(first)
        valid = self.result_payload(first)
        cases = [
            {**valid, 'task_id': 'TASK-999'}, {**valid, 'assignment_digest': 'f' * 64},
            {**valid, 'status': []}, {**valid, 'review': ''},
            {**valid, 'summary': 'token=' + 'A' * 32}, {**valid, 'extra': True},
            {**valid, 'status': 'replan_required'},
        ]
        state = read(engine.task_path(self.run_dir, 'FEAT-001', 'WAVE-001', first['task_id']))
        path = receipt_path(first, state['attempt'])
        before = engine.head(Path(first['worktree']))
        for value in cases:
            with self.subTest(value_shape=list(value)), self.assertRaises(engine.ExecutionError) as caught:
                ingest_result(self.run_dir, 'FEAT-001', 'WAVE-001', first['task_id'],
                              value, observation, self.result_session())
            self.assertEqual(caught.exception.code, 'WORKER_FAILED')
            self.assertNotIn('A' * 32, str(caught.exception))
            self.assertFalse(path.exists())
            self.assertEqual(engine.head(Path(first['worktree'])), before)
        self.assertEqual(read(engine.task_path(self.run_dir, 'FEAT-001', 'WAVE-001', first['task_id'])), state)

    def test_result_requires_independent_termination_and_current_coordinator(self):
        first, _ = self.result_fixture()
        observation = self.result_observation(first)
        original = read(observation)
        for field, value in [('worker_session_hash', 'e' * 64), ('status', 'running'),
                             ('observer_session_hash', 'e' * 64)]:
            write(observation, {**original, field: value})
            with self.assertRaises(engine.ExecutionError):
                ingest_result(self.run_dir, 'FEAT-001', 'WAVE-001', first['task_id'],
                              self.result_payload(first), observation, self.result_session())
            self.assertFalse(receipt_path(first, 1).exists())
        write(observation, original)
        with self.assertRaises(engine.ExecutionError):
            ingest_result(self.run_dir, 'FEAT-001', 'WAVE-001', first['task_id'],
                          self.result_payload(first), observation, 'unbound-coordinator')
        self.assertFalse(receipt_path(first, 1).exists())

    def test_result_exact_replay_finish_and_changed_replay_conflict(self):
        first, _ = self.result_fixture()
        self.result_write(first)
        observation = self.result_observation(first)
        before_head = engine.head(Path(first['worktree']))
        with self.assertRaises(engine.ExecutionError) as caught:
            self.result_finish(first, receipt_path(first, 1), observation)
        self.assertEqual(caught.exception.code, 'WORKER_RESULT_REQUIRED')
        self.assertEqual(engine.head(Path(first['worktree'])), before_head)
        path, observation, received = self.result_ingest(first)
        before = path.read_bytes()
        result = self.result_finish(first, path, observation)
        self.assertEqual(result['worker_result_receipt_sha256'], received['receipt_sha256'])
        self.assertEqual(result['summary'], self.result_payload(first)['summary'])
        replay = self.result_ingest(first)[2]
        self.assertEqual(replay, received)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.result_finish(first, path, observation), result)
        changed = {**self.result_payload(first), 'summary': 'Different result evidence'}
        with self.assertRaises(engine.ExecutionError) as caught:
            self.result_ingest(first, payload=changed)
        self.assertEqual(caught.exception.code, 'RESULT_CONFLICT')
        self.assertEqual(path.read_bytes(), before)

    def test_blocked_result_fences_new_dispatch_but_allows_peer_drain(self):
        first, peer = self.result_fixture(start_peer=True)
        path, observation, _ = self.result_ingest(first, 'blocked')
        before = path.read_bytes()
        peer_args = (self.run_dir, 'FEAT-001', 'WAVE-001', peer['task_id'])
        # A running same-worker replay is a drain operation, not a new dispatch.
        engine.start_task(*peer_args, peer['assignment_digest'],
                          'receipt-native-stub-' + peer['task_id'], Path(peer['scope_cwd']))
        for action in (engine.prepare_wave, engine.refill_wave):
            with self.assertRaises(engine.ExecutionError) as caught:
                action(self.run_dir, 'FEAT-001', 'WAVE-001')
            self.assertEqual(caught.exception.code, 'WORKER_BLOCKED')
        with self.assertRaises(engine.ExecutionError) as caught:
            self.result_finish(first, path, observation)
        self.assertEqual(caught.exception.code, 'WORKER_RESULT_INCOMPLETE')
        self.result_write(peer)
        peer_path, peer_observation, _ = self.result_ingest(peer)
        self.result_finish(peer, peer_path, peer_observation)
        engine.admit_task(*peer_args, peer_observation)
        with self.assertRaises(engine.ExecutionError):
            engine.integrate_wave(self.run_dir, 'FEAT-001', 'WAVE-001')
        with self.assertRaises(engine.ExecutionError):
            engine.refill_wave(self.run_dir, 'FEAT-001', 'WAVE-001')
        self.assertEqual(path.read_bytes(), before)

    def test_blocked_result_fences_armed_first_start(self):
        first, peer = self.result_fixture()
        args = (self.run_dir, 'FEAT-001', 'WAVE-001', peer['task_id'])
        engine.arm_task(*args, peer['assignment_digest'])
        self.result_ingest(first, 'blocked')
        for action in (
            lambda: engine.arm_task(*args, peer['assignment_digest']),
            lambda: engine.start_task(*args, peer['assignment_digest'], 'fresh-peer', Path(peer['scope_cwd'])),
        ):
            with self.assertRaises(engine.ExecutionError) as caught:
                action()
            self.assertEqual(caught.exception.code, 'WORKER_BLOCKED')
        self.assertEqual(read(engine.task_path(*args))['status'], 'assigned')

    def test_blocked_recovery_preserves_old_receipt_and_uses_common_envelope(self):
        first, _ = self.result_fixture()
        path, observation, _ = self.result_ingest(first, 'blocked')
        self.result_classify_blocked(path)
        before = path.read_bytes()
        next_native = 'authorized-recovered-worker'
        approval = reconcile.authorize_recovery(self.run_dir, 'FEAT-001', 'WAVE-001', first['task_id'],
                    observation, self.result_session(), session_hash(next_native))
        self.assertEqual(approval['launch']['recovery_attempt'], 1)
        self.assertIn('task-recover', approval['launch']['prompt'])
        self.assertEqual(approval['launch']['context_policy'], 'fresh-assignment-only')
        engine.recover_task(self.run_dir, 'FEAT-001', 'WAVE-001', first['task_id'], next_native,
                            Path(first['scope_cwd']), expected_attempt=1, confirmed_stopped=True)
        require_dispatchable(self.run_dir, 'FEAT-001')
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(receipt_path(first, 2).exists())
        self.result_write(first)
        new_path, new_observation, _ = self.result_ingest(first)
        self.assertNotEqual(new_path, path)
        with self.assertRaises(engine.ExecutionError):
            self.result_finish(first, path, new_observation)
        self.result_finish(first, new_path, new_observation)
        self.assertEqual(path.read_bytes(), before)

    def test_active_validation_recovery_observation_preserves_authority(self):
        first, _ = self.result_fixture()
        path, observation, _ = self.result_ingest(first, 'blocked')
        self.result_classify_blocked(path, proposed='VALIDATION_DEFECT')
        args = (self.run_dir, 'FEAT-001', 'WAVE-001', first['task_id'])
        native = 'validation-repair-observer-fixture'
        approval = reconcile.authorize_recovery(
            *args, observation, self.result_session(), session_hash(native))
        engine.recover_task(*args, native, Path(first['scope_cwd']),
                            expected_attempt=1, confirmed_stopped=True)
        if first['owner'] != 'agentic-sdlc':
            with self.assertRaises(engine.ExecutionError):
                reconcile.observe_worker_repair(*args)
            return
        approval_path = reconcile.recovery_path(
            self.run_dir, 'FEAT-001', first['task_id'], 1,
            session_hash(self.result_session()))
        watched = [path, observation, approval_path, engine.task_path(*args),
                   self.run_dir / 'repairs/FEAT-001/repair-control.json']
        before = {item: item.read_bytes() for item in watched}
        binding = reconcile.observe_worker_repair(*args)
        self.assertEqual(binding['assignment_digest'], first['assignment_digest'])
        self.assertEqual(binding['attempt'], 2)
        self.assertEqual(binding['worker_session_hash'], session_hash(native))
        self.assertEqual(binding['repair_authority'], approval['repair_authority'])
        self.assertEqual(binding['repair_authority']['classification'], 'VALIDATION_DEFECT')
        self.assertEqual(before, {item: item.read_bytes() for item in watched})
        original = read(approval_path)
        changes = {
            'assignment_digest': '0' * 64, 'attempt': 2,
            'prior_worker_session_hash': '1' * 64,
            'next_worker_session_hash': '2' * 64,
            'coordinator_session_hash': '3' * 64,
            'termination_path': str(self.run_dir / 'foreign.json'),
            'termination_sha256': '4' * 64,
            'worker_result_receipt_sha256': '5' * 64,
            'repair_authority': None,
        }
        for key, value in changes.items():
            with self.subTest(changed=key):
                write(approval_path, {**original, key: value})
                with self.assertRaises(engine.ExecutionError):
                    reconcile.observe_worker_repair(*args)
                write(approval_path, original)
        control_path = self.run_dir / 'repairs/FEAT-001/repair-control.json'
        control = read(control_path)
        write(control_path, {**control, 'status': 'completed'})
        with self.assertRaises(engine.ExecutionError):
            reconcile.observe_worker_repair(*args)
        write(control_path, control)
        self.assertEqual(before, {item: item.read_bytes() for item in watched})

    def test_worker_repair_observation_rejects_initial_attempt(self):
        first, _ = self.result_fixture()
        with self.assertRaises(engine.ExecutionError):
            reconcile.observe_worker_repair(
                self.run_dir, 'FEAT-001', 'WAVE-001', first['task_id'])

    def test_unclassified_blocked_recovery_requires_sdlc_authority_only(self):
        first, _ = self.result_fixture()
        path, observation, _ = self.result_ingest(first, 'blocked')
        before = path.read_bytes()
        args = (self.run_dir, 'FEAT-001', 'WAVE-001', first['task_id'])
        task_before = engine.task_path(*args).read_bytes()
        approval_path = reconcile.recovery_path(
            self.run_dir, 'FEAT-001', first['task_id'], 1, session_hash(self.result_session()))
        if first['owner'] == 'agentic-sdlc':
            with self.assertRaises(WorkflowError) as caught:
                reconcile.authorize_recovery(*args, observation, self.result_session(), session_hash('fresh'))
            self.assertEqual(caught.exception.code, 'REPAIR_NOT_AUTHORIZED')
            self.assertFalse(approval_path.exists())
        else:
            approval = reconcile.authorize_recovery(
                *args, observation, self.result_session(), session_hash('fresh'))
            self.assertIsNone(approval['repair_authority'])
            self.assertEqual(approval['worker_result_receipt_sha256'], read(path)['receipt_sha256'])
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(engine.task_path(*args).read_bytes(), task_before)

    def test_worker_failure_capture_rejects_stale_context_before_effects(self):
        first, _ = self.result_fixture()
        path, _, _ = self.result_ingest(first, 'blocked')
        attempt = self.result_classify_blocked(path)
        if attempt is None:
            # Implement keeps its independent recovery authority.
            self.assertFalse((self.run_dir / 'repairs').exists())
            return
        import repair_control as repair
        control = repair._load_control(self.run_dir, 'FEAT-001')
        event = repair._load_event(self.run_dir, 'FEAT-001', control['current_event_id'])
        state_path = self.run_dir / 'current-state.json'
        state = read(state_path)
        state['fingerprint_ids'].append('added-input:sha256:' + '7' * 64)
        write(state_path, state)
        before = {p: p.read_bytes() for p in (self.run_dir / 'repairs').rglob('*') if p.is_file()}
        with mock.patch('workflow_transition.runtime_session_identity', return_value=self.result_session()):
            diagnostic = {key: value for key, value in event.items() if key not in {
                'event_id', 'blocker_key', 'execution_id', 'phase', 'execution_lifecycle',
                'integration_commit', 'fingerprints'}}
            with self.assertRaises(repair.RepairControlError):
                repair.capture_worker_failure(self.run_dir, 'FEAT-001', 'WAVE-001', first['task_id'],
                                               read(path)['receipt_sha256'], diagnostic)
            with self.assertRaises(repair.RepairControlError):
                repair.begin_remediation(self.run_dir, 'FEAT-001', control['current_classification_id'],
                                         'localized', 'A new repair hypothesis.', 'evidence/new.json', 5)
            with self.assertRaises(repair.RepairControlError):
                repair.record_failure_event(self.run_dir, {**event, 'observed': 'Another stale observation.'})
            self.assertEqual(before, {p: p.read_bytes() for p in (self.run_dir / 'repairs').rglob('*') if p.is_file()})
            # Historical replay is idempotent despite subsequent checkpoint drift.
            self.assertFalse(repair.record_failure_event(self.run_dir, event)['created'])
            replay = repair.begin_remediation(self.run_dir, 'FEAT-001', control['current_classification_id'],
                                               'localized', attempt['hypothesis'], attempt['evidence_reference'])
            self.assertFalse(replay['created'])
            self.assertEqual(replay['control']['feature_dispatches'], control['feature_dispatches'])

    def test_worker_failure_capture_rejects_foreign_owner_receipt_and_head(self):
        first, _ = self.result_fixture()
        path, _, _ = self.result_ingest(first, 'blocked')
        if self.result_classify_blocked(path) is None:
            self.assertFalse((self.run_dir / 'repairs').exists())
            return
        import repair_control as repair
        control = repair._load_control(self.run_dir, 'FEAT-001')
        event = repair._load_event(self.run_dir, 'FEAT-001', control['current_event_id'])
        payload = {key: value for key, value in event.items() if key not in {
            'event_id', 'blocker_key', 'execution_id', 'phase', 'execution_lifecycle',
            'integration_commit', 'fingerprints', 'evidence_digests'}}
        payload['evidence_digests'] = {'evaluate': 'sha256:' + '1' * 64}
        digest = read(path)['receipt_sha256']
        before = {p: p.read_bytes() for p in (self.run_dir / 'repairs').rglob('*') if p.is_file()}
        for key, value in (('run_id', 'foreign-run'), ('project_id', 'foreign-project'),
                           ('current_execution', 'FEAT-002')):
            checkpoint = read(self.run_dir / 'current-state.json')
            write(self.run_dir / 'current-state.json', {**checkpoint, key: value})
            with mock.patch('workflow_transition.runtime_session_identity', return_value=self.result_session()), \
                    self.assertRaises(repair.RepairControlError):
                repair.capture_worker_failure(self.run_dir, 'FEAT-001', 'WAVE-001', first['task_id'], digest, payload)
            write(self.run_dir / 'current-state.json', checkpoint)
        for session, wave, task, expected in (
            ('foreign-coordinator', 'WAVE-001', first['task_id'], digest),
            (self.result_session(), 'WAVE-002', first['task_id'], digest),
            (self.result_session(), 'WAVE-001', 'TASK-999', digest),
            (self.result_session(), 'WAVE-001', first['task_id'], '0' * 64),
        ):
            with self.subTest(session=session, wave=wave, task=task), \
                    mock.patch('workflow_transition.runtime_session_identity', return_value=session), \
                    self.assertRaises(repair.RepairControlError):
                repair.capture_worker_failure(self.run_dir, 'FEAT-001', wave, task, expected, payload)
        coordinator = engine._load_coordinator(self.run_dir, 'FEAT-001')
        import subprocess
        subprocess.run(['git', '-C', coordinator['integration_worktree'], 'commit', '--allow-empty',
                        '-m', 'test: changed integration identity'], check=True, capture_output=True)
        with mock.patch('workflow_transition.runtime_session_identity', return_value=self.result_session()), \
                self.assertRaises(repair.RepairControlError):
            repair.capture_worker_failure(self.run_dir, 'FEAT-001', 'WAVE-001', first['task_id'], digest, payload)
        self.assertEqual(before, {p: p.read_bytes() for p in (self.run_dir / 'repairs').rglob('*') if p.is_file()})

    def test_recovery_rechecks_failure_arriving_after_interruption_authorization(self):
        first, _ = self.result_fixture()
        args = (self.run_dir, 'FEAT-001', 'WAVE-001', first['task_id'])
        observation = self.result_observation(first)
        reconcile.authorize_recovery(*args, observation, self.result_session(), session_hash('fresh'))
        receipt, _, _ = self.result_ingest(first, 'blocked')
        self.result_classify_blocked(receipt)
        before = engine.task_path(*args).read_bytes()
        with self.assertRaises(engine.ExecutionError) as caught:
            engine.recover_task(*args, 'fresh', Path(first['scope_cwd']),
                                expected_attempt=1, confirmed_stopped=True)
        self.assertEqual(caught.exception.code, 'REPAIR_NOT_AUTHORIZED')
        self.assertEqual(engine.task_path(*args).read_bytes(), before)

    def test_blocked_recovery_rechecks_completed_dispatch_before_transfer(self):
        first, _ = self.result_fixture()
        args = (self.run_dir, 'FEAT-001', 'WAVE-001', first['task_id'])
        receipt, observation, _ = self.result_ingest(first, 'blocked')
        attempt = self.result_classify_blocked(receipt)
        reconcile.authorize_recovery(*args, observation, self.result_session(), session_hash('fresh'))
        if attempt is None:
            engine.recover_task(*args, 'fresh', Path(first['scope_cwd']),
                                expected_attempt=1, confirmed_stopped=True)
            self.assertEqual(read(engine.task_path(*args))['attempt'], 2)
            self.assertFalse((self.run_dir / 'repairs').exists())
            return
        import repair_control
        repair_control.complete_remediation(self.run_dir, 'FEAT-001', attempt['dispatch_id'],
                                            'succeeded', 'evidence/synthetic-resolution.json')
        before = engine.task_path(*args).read_bytes()
        with self.assertRaises(engine.ExecutionError) as caught:
            engine.recover_task(*args, 'fresh', Path(first['scope_cwd']),
                                expected_attempt=1, confirmed_stopped=True)
        self.assertEqual(caught.exception.code, 'REPAIR_NOT_AUTHORIZED')
        self.assertEqual(engine.task_path(*args).read_bytes(), before)

    def test_second_blocked_attempt_requires_current_owner_authority(self):
        first, _ = self.result_fixture()
        args = (self.run_dir, 'FEAT-001', 'WAVE-001', first['task_id'])
        receipt, observation, _ = self.result_ingest(first, 'blocked')
        self.result_classify_blocked(receipt)
        reconcile.authorize_recovery(*args, observation, self.result_session(), session_hash('fresh'))
        engine.recover_task(*args, 'fresh', Path(first['scope_cwd']),
                            expected_attempt=1, confirmed_stopped=True)
        second, observation, _ = self.result_ingest(first, 'blocked')
        self.assertNotEqual(read(second)['receipt_sha256'], read(receipt)['receipt_sha256'])
        before = engine.task_path(*args).read_bytes()
        if first['owner'] == 'agentic-sdlc':
            with self.assertRaises(WorkflowError) as caught:
                reconcile.authorize_recovery(*args, observation, self.result_session(), session_hash('newer'))
            self.assertEqual(caught.exception.code, 'REPAIR_NOT_AUTHORIZED')
        else:
            approval = reconcile.authorize_recovery(
                *args, observation, self.result_session(), session_hash('newer'))
            self.assertIsNone(approval['repair_authority'])
            self.assertEqual(approval['worker_result_receipt_sha256'], read(second)['receipt_sha256'])
        self.assertEqual(engine.task_path(*args).read_bytes(), before)

    def test_recovery_rejects_legacy_authorization_without_binding(self):
        first, _ = self.result_fixture()
        args = (self.run_dir, 'FEAT-001', 'WAVE-001', first['task_id'])
        observation = self.result_observation(first)
        reconcile.authorize_recovery(*args, observation, self.result_session(), session_hash('fresh'))
        approval_path = reconcile.recovery_path(
            self.run_dir, 'FEAT-001', first['task_id'], 1, session_hash(self.result_session()))
        value = read(approval_path)
        value['schema'] = 'workflow/recovery-authorization-v1'
        del value['repair_authority'], value['worker_result_receipt_sha256']
        write(approval_path, value)
        before = engine.task_path(*args).read_bytes()
        with self.assertRaises(engine.ExecutionError) as caught:
            engine.recover_task(*args, 'fresh', Path(first['scope_cwd']),
                                expected_attempt=1, confirmed_stopped=True)
        self.assertEqual(caught.exception.code, 'OWNERSHIP_INVALID')
        self.assertEqual(engine.task_path(*args).read_bytes(), before)

    def test_replan_result_cannot_authorize_same_assignment_recovery(self):
        first, _ = self.result_fixture()
        path, observation, _ = self.result_ingest(first, 'replan_required')
        before = path.read_bytes()
        with self.assertRaises(WorkflowError) as caught:
            reconcile.authorize_recovery(self.run_dir, 'FEAT-001', 'WAVE-001', first['task_id'],
                observation, self.result_session(), session_hash('would-be-retry'))
        self.assertEqual(caught.exception.code, 'REPLAN_REQUIRED')
        with self.assertRaises(WorkflowError) as caught:
            require_dispatchable(self.run_dir, 'FEAT-001')
        self.assertEqual(caught.exception.code, 'WORKER_REPLAN_REQUIRED')
        self.assertEqual(path.read_bytes(), before)

    def test_blocked_recovery_requires_runtime_release_for_both_coordinators(self):
        import hashlib
        import workflow_resources as resources
        resource = 'recovery-' + hashlib.sha256(str(self.run_dir).encode()).hexdigest()[:24]
        lines = self.plan.read_text().splitlines()
        index = next(i for i, line in enumerate(lines) if line.startswith('- Conflict domains:'))
        lines[index] = '- Conflict domains: runtime:' + resource
        self.plan.write_text('\n'.join(lines) + '\n')
        first, _ = self.result_fixture()
        args = (self.run_dir, 'FEAT-001', 'WAVE-001', first['task_id'])
        assignment = engine.assignment_path(*args)
        assignment_before = assignment.read_bytes()
        home = self.run_dir.parents[2]
        lease = resources.acquire(home, assignment, resource,
                                  'receipt-native-stub-' + first['task_id'])
        self.addCleanup(Path(lease['lease_path']).unlink, missing_ok=True)
        receipt, observation, _ = self.result_ingest(first, 'blocked')
        self.result_classify_blocked(receipt)
        receipt_before = receipt.read_bytes()
        next_native = 'recovered-after-runtime-release'
        reconcile.authorize_recovery(*args, observation, self.result_session(), session_hash(next_native))
        state = engine.task_path(*args)
        state_before = state.read_bytes()
        with self.assertRaises(engine.ExecutionError) as caught:
            engine.recover_task(*args, next_native, Path(first['scope_cwd']),
                                expected_attempt=1, confirmed_stopped=True)
        self.assertEqual(caught.exception.code, 'RESOURCE_BUSY')
        self.assertEqual(state.read_bytes(), state_before)
        self.assertEqual(receipt.read_bytes(), receipt_before)
        resources.release(home, assignment, resource, self.result_session(),
                          termination=observation)
        engine.recover_task(*args, next_native, Path(first['scope_cwd']),
                            expected_attempt=1, confirmed_stopped=True)
        self.assertEqual(read(state)['attempt'], 2)
        self.assertEqual(read(state)['worker_session_hash'], session_hash(next_native))
        self.assertEqual(assignment.read_bytes(), assignment_before)
        self.assertEqual(receipt.read_bytes(), receipt_before)

    def test_historical_receipt_survives_coordinator_succession(self):
        first, _ = self.result_fixture()
        self.result_write(first)
        path, original_observation, original_receipt = self.result_ingest(first)
        before = path.read_bytes()
        # Isolate receipt semantics with a synthetic succession. Public continue/takeover
        # authorization is covered separately; this does not claim native takeover.
        run = read(self.run_dir / 'run.json')
        successor = 'synthetic-successor-coordinator'
        run['coordinator_session_sha256'] = session_hash(successor)
        write(self.run_dir / 'run.json', run)
        observation = self.result_observation(first, session=successor)
        self.result_finish(first, path, observation, session=successor)
        replay = ingest_result(self.run_dir, 'FEAT-001', 'WAVE-001', first['task_id'],
                               self.result_payload(first), original_observation, successor)
        self.assertEqual(replay, original_receipt)
        self.assertEqual(path.read_bytes(), before)
        state = read(engine.task_path(self.run_dir, 'FEAT-001', 'WAVE-001', first['task_id']))
        self.assertEqual(read_receipt(first, state)[1]['coordinator_session_hash'], session_hash(self.result_session()))

    def test_receipt_and_termination_drift_block_finish_before_git(self):
        first, _ = self.result_fixture()
        self.result_write(first)
        path, observation, _ = self.result_ingest(first)
        original, exit_original = read(path), read(observation)
        before = engine.head(Path(first['worktree']))
        write(path, {**original, 'attempt': 2})
        with self.assertRaises(engine.ExecutionError):
            self.result_finish(first, path, observation)
        write(path, original)
        write(observation, {**exit_original, 'observation': 'Different synthetic exit evidence'})
        with self.assertRaises(engine.ExecutionError):
            self.result_finish(first, path, observation)
        self.assertEqual(engine.head(Path(first['worktree'])), before)
        self.assertFalse(engine.result_path(self.run_dir, 'FEAT-001', 'WAVE-001', first['task_id']).exists())

    def test_recovery_authorization_succession_retains_prior_evidence(self):
        first, _ = self.result_fixture()
        path, observation, _ = self.result_ingest(first, 'blocked')
        self.result_classify_blocked(path)
        next_native = 'native-after-succession'
        original = reconcile.authorize_recovery(self.run_dir, 'FEAT-001', 'WAVE-001', first['task_id'],
                    observation, self.result_session(), session_hash(next_native))
        old_path = reconcile.recovery_path(self.run_dir, 'FEAT-001', first['task_id'], 1,
                                          session_hash(self.result_session()))
        old_bytes, result_bytes = old_path.read_bytes(), path.read_bytes()
        successor = 'synthetic-recovery-successor'
        run = read(self.run_dir / 'run.json')
        run['coordinator_session_sha256'] = session_hash(successor)
        write(self.run_dir / 'run.json', run)
        with self.assertRaises((FileNotFoundError, engine.ExecutionError)):
            engine.recover_task(self.run_dir, 'FEAT-001', 'WAVE-001', first['task_id'], next_native,
                                Path(first['scope_cwd']), expected_attempt=1, confirmed_stopped=True)
        current_observation = self.result_observation(first, session=successor)
        current = reconcile.authorize_recovery(self.run_dir, 'FEAT-001', 'WAVE-001', first['task_id'],
                    current_observation, successor, session_hash(next_native))
        self.assertNotEqual(original['coordinator_session_hash'], current['coordinator_session_hash'])
        engine.recover_task(self.run_dir, 'FEAT-001', 'WAVE-001', first['task_id'], next_native,
                            Path(first['scope_cwd']), expected_attempt=1, confirmed_stopped=True)
        self.assertEqual(old_path.read_bytes(), old_bytes)
        self.assertEqual(path.read_bytes(), result_bytes)
