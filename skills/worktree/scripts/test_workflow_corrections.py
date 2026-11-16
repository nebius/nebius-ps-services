"""Typed pre-effect correction does not borrow or reset repair allowances."""
from pathlib import Path
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_workflow_execution as fixture
from project_specs_lib.candidates import check_pair, _normalize
from workflow_corrections import submit, pause, spec_submission, _authoring_phase
from workflow_state import read, write, WorkflowError


class CorrectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run = Path(self.temp.name).resolve() / 'run'
        self.operation = {'owner': 'maintain-project-specs', 'phase': 'sdlc-create-design',
                          'execution_id': 'FEAT-001'}
        self.now = 1000000000

    def submit(self, design):
        req = fixture.REQUIREMENTS.encode()
        return submit(self.run, self.operation, {'requirements': req.decode(), 'design': design},
                      check_pair(req, design.encode()), clock=lambda: self.now)

    def test_structural_feedback_requires_complete_acceptance(self):
        bad = fixture.DESIGN.replace('#### Design Details', '## Design Details')
        feedback = check_pair(fixture.REQUIREMENTS.encode(), bad.encode())
        self.assertEqual(feedback['correction_kind'], 'document_structure_format')
        missing = fixture.REQUIREMENTS.replace('#### Acceptance Criteria', '#### Missing Criteria')
        feedback = check_pair(missing.encode(), bad.encode())
        self.assertIsNone(feedback['correction_kind'])

    def test_missing_terminator_and_route_format_share_the_same_allowance(self):
        design = fixture.DESIGN.replace('#### Design Details\n',
                                        '#### Design Details\n\nPATCH /api/tasks/<id> updates one task.\n')
        rejected = design.replace('<!-- /FEATURE: FEAT-001 -->\n', '')
        self.assertEqual(self.submit(rejected)['remaining_submissions'], 2)
        self.assertEqual(self.submit(design)['remaining_submissions'], 1)
        corrected = design.replace('PATCH /api/tasks/<id>', 'PATCH `/api/tasks/<id>`')
        self.assertEqual(self.submit(corrected)['status'], 'valid')
        self.assertFalse((self.run / 'repairs').exists())

    def test_record_structure_never_invents_acceptance_or_repairs_mismatched_ids(self):
        missing = fixture.DESIGN.replace('<!-- /FEATURE: FEAT-001 -->\n', '')
        for req, design in [
            (fixture.REQUIREMENTS.replace('#### Acceptance Criteria', '#### Absent'), missing),
            (fixture.REQUIREMENTS, fixture.DESIGN.replace('/FEATURE: FEAT-001', '/FEATURE: FEAT-002')),
        ]:
            self.assertIsNone(check_pair(req.encode(), design.encode())['correction_kind'])

    def test_correction_cannot_move_record_boundary_over_prose(self):
        close = '<!-- /FEATURE: FEAT-001 -->'
        design = fixture.DESIGN.replace(close, close + '\n\n## General\n\nKeep this outside.')
        self.assertEqual(self.submit(design.replace('#### Design Details', '## Design Details'))['status'],
                         'correction_pending')
        moved = design.replace(close + '\n', '').replace('<!-- maintain-project-specs:design:end -->',
                                                       close + '\n<!-- maintain-project-specs:design:end -->')
        self.assertEqual(self.submit(moved)['status'], 'classification_required')

    def test_correction_preserves_human_owned_envelopes_exactly(self):
        prefix = '## Design Details\n\n\nPATCH /outside/<id> is human-owned.\n\n'
        design = prefix + fixture.DESIGN
        rejected = design.replace('#### Design Details', '## Design Details')
        self.assertEqual(_normalize(rejected.encode()), design.encode())
        self.assertEqual(self.submit(rejected)['status'], 'correction_pending')
        changed = design.replace(prefix, prefix.replace('\n\n\n', '\n\n'))
        self.assertEqual(self.submit(changed)['status'], 'classification_required')

    def test_formatting_does_not_rewrite_code_examples_or_generic_html(self):
        for prose in ('<span>Example</span>', '<pre>\nPATCH /api/tasks/<id>\n</pre>',
                      '````text\n```\nPATCH /api/tasks/<id>\n````',
                      '`PATCH /api/tasks/<id>`'):
            design = fixture.DESIGN.replace('#### Design Details\n', '#### Design Details\n\n' + prose + '\n')
            self.assertEqual(_normalize(design.encode()), design.encode())
            self.assertIsNone(check_pair(fixture.REQUIREMENTS.encode(), design.encode())['correction_kind'])

    def phase_checkpoint(self, **updates):
        state = {'run_id': self.run.name, 'project_id': self.run.parent.name,
                 'checkpoint_id': 'checkpoint-0003', 'current_phase': 'sdlc-gather-context',
                 'last_successful_phase': 'sdlc-gather-context', 'next_recommended_skill': 'sdlc-create-design',
                 'status': 'running', 'repair': None, **updates}
        write(self.run / 'checkpoints/latest.json', {'checkpoint_id': state['checkpoint_id'],
                                                   'path': 'checkpoints/' + state['checkpoint_id'] + '.json'})
        write(self.run / 'checkpoints' / (state['checkpoint_id'] + '.json'), state)
        return state

    def test_native_completed_phase_checkpoint_binds_next_authoring_operation(self):
        state = self.phase_checkpoint()
        self.assertEqual(_authoring_phase(self.run, state), 'sdlc-create-design')
        initial = self.phase_checkpoint(current_phase='sdlc-start', last_successful_phase=None,
                                        next_recommended_skill='sdlc-create-requirements')
        self.assertEqual(_authoring_phase(self.run, initial), 'sdlc-create-requirements')
        finished = self.phase_checkpoint(current_phase='sdlc-create-design',
                                         last_successful_phase='sdlc-create-design',
                                         next_recommended_skill='sdlc-create-plan')
        self.assertIsNone(_authoring_phase(self.run, finished))

    def test_phase_checkpoint_drift_and_existing_classification_cannot_gain_exemption(self):
        state = self.phase_checkpoint()
        state['current_execution'] = 'FEAT-002'
        with self.assertRaises(WorkflowError):
            _authoring_phase(self.run, state)
        state = self.phase_checkpoint(current_phase='sdlc-classify-failure',
                                     last_successful_phase='sdlc-classify-failure',
                                     repair={'control': 'repairs/FEAT-001/repair-control.json'})
        self.assertIsNone(_authoring_phase(self.run, state))

    def test_two_corrections_do_not_renew_on_candidate_changes(self):
        for level, remaining in [('##', 2), ('#####', 1), ('######', 0)]:
            result = self.submit(fixture.DESIGN.replace('#### Design Details', level + ' Design Details'))
            self.assertEqual(result['remaining_submissions'], remaining)
        self.assertEqual(result['status'], 'classification_required')
        self.assertEqual(self.submit(fixture.DESIGN)['status'], 'classification_required')
        self.assertFalse((self.run / 'repairs').exists())
        state = read(next((self.run / 'input-corrections').glob('*.json')))
        self.assertEqual(len(state['candidates']), 3)

    def test_second_submission_can_succeed(self):
        self.submit(fixture.DESIGN.replace('#### Design Details', '## Design Details'))
        self.submit(fixture.DESIGN.replace('#### Design Details', '##### Design Details'))
        self.assertEqual(self.submit(fixture.DESIGN)['status'], 'valid')

    def test_semantic_change_and_expired_window_classify(self):
        self.submit(fixture.DESIGN.replace('#### Design Details', '## Design Details'))
        self.now += 300 * 1000000000
        self.assertEqual(self.submit(fixture.DESIGN)['status'], 'classification_required')

    def test_rejected_replay_expires_and_keeps_its_own_outcome(self):
        rejected = fixture.DESIGN.replace('#### Design Details', '## Design Details')
        self.submit(rejected)
        self.assertEqual(self.submit(fixture.DESIGN)['status'], 'valid')
        self.assertEqual(self.submit(rejected)['status'], 'correction_pending')
        self.assertFalse(self.submit(rejected)['status'] == 'valid')

    def test_expired_original_replay_requires_classification(self):
        rejected = fixture.DESIGN.replace('#### Design Details', '## Design Details')
        self.submit(rejected)
        self.now += 300 * 1000000000
        self.assertEqual(self.submit(rejected)['status'], 'classification_required')

    def test_explicit_pause_excludes_only_recorded_inactive_time(self):
        self.submit(fixture.DESIGN.replace('#### Design Details', '## Design Details'))
        self.now += 280 * 1000000000
        with mock.patch('workflow_objectives.authorize_coordinator'):
            pause(self.run, True, 'coordinator', clock=lambda: self.now)
            self.now += 600 * 1000000000
            pause(self.run, False, 'coordinator', clock=lambda: self.now)
        self.now += 19 * 1000000000
        self.assertEqual(self.submit(fixture.DESIGN)['status'], 'valid')

    def test_pause_without_lane_does_not_create_phase_evidence(self):
        with mock.patch('workflow_objectives.authorize_coordinator'):
            pause(self.run, False, 'coordinator', clock=lambda: self.now)
        self.assertFalse((self.run / 'input-corrections').exists())

    def test_uninitialized_phase_has_no_specification_correction_exemption(self):
        self.run.mkdir()
        bound = (self.run, {'status': 'accepted'}, {})
        with mock.patch('agent_runtime.native_session_id', return_value='coordinator'), \
                mock.patch('workflow_objectives.observe_binding', return_value=bound):
            for design in (fixture.DESIGN, fixture.DESIGN.replace('#### Design Details', '## Design Details')):
                self.assertIsNone(spec_submission(self.run, fixture.REQUIREMENTS.encode(), design.encode()))
        self.assertFalse((self.run / 'input-corrections').exists())

    def test_corrected_content_cannot_change_semantics(self):
        self.submit(fixture.DESIGN.replace('#### Design Details', '## Design Details'))
        changed = fixture.DESIGN.replace('#### Design Details\n', '#### Design Details\n\nNew behavior.\n')
        self.assertEqual(self.submit(changed)['status'], 'classification_required')

    def test_closed_lane_allows_valid_work_without_renewing_exemption(self):
        self.submit(fixture.DESIGN.replace('#### Design Details', '## Design Details'))
        self.submit(fixture.DESIGN)
        changed = fixture.DESIGN.replace('#### Design Details\n', '#### Design Details\n\nNew behavior.\n')
        self.assertEqual(self.submit(changed)['status'], 'valid')
        self.assertEqual(self.submit(changed.replace('#### Design Details', '## Design Details'))['status'],
                         'classification_required')


class ResultCorrectionTests(unittest.TestCase):
    setUp = fixture.GitLifecycleTests.setUp
    tearDown = fixture.GitLifecycleTests.tearDown
    prepare = fixture.GitLifecycleTests.prepare
    result_fixture = fixture.WorkerResultContractCases.result_fixture
    result_start = fixture.WorkerResultContractCases.result_start
    result_payload = fixture.WorkerResultContractCases.result_payload
    result_observation = fixture.WorkerResultContractCases.result_observation
    result_session = fixture.WorkerResultContractCases.result_session

    def test_paired_publication_reserves_handoff_correction_before_effects(self):
        from project_specs_lib.transaction import publish_spec_pair
        from project_specs_lib.contracts import digest as bytes_digest
        run, objective = read(self.run_dir / 'run.json'), read(self.run_dir / 'objective.json')
        state = {'run_id': self.run_dir.name, 'project_id': self.run_dir.parent.name,
                 'checkpoint_id': 'checkpoint-0003', 'current_phase': 'sdlc-gather-context',
                 'last_successful_phase': 'sdlc-gather-context', 'next_recommended_skill': 'sdlc-create-design',
                 'current_execution': 'FEAT-001', 'status': 'running', 'repair': None}
        write(self.run_dir / 'current-state.json', state)
        write(self.run_dir / 'checkpoints/checkpoint-0003.json', state)
        write(self.run_dir / 'checkpoints/latest.json', {'checkpoint_id': 'checkpoint-0003',
                                                       'path': 'checkpoints/checkpoint-0003.json'})
        req, original = fixture.REQUIREMENTS.encode(), fixture.DESIGN.encode()
        route = original.replace(b'#### Design Details\n', b'#### Design Details\n\nPATCH /tasks/<id> updates a task.\n')
        rejected = route.replace(b'<!-- /FEATURE: FEAT-001 -->\n', b'')

        def publish(candidate, operation):
            return publish_spec_pair(self.project, requirements_candidate=req, design_candidate=candidate,
                                     expected_git_head=self.base_head, expected_requirements_sha256=bytes_digest(req),
                                     expected_design_sha256=bytes_digest(original), operation_id=operation)

        with mock.patch('agent_runtime.native_session_id', return_value='coordinator'), \
                mock.patch('workflow_objectives.observe_binding', return_value=(self.run_dir, run, objective)), \
                mock.patch.dict(os.environ, {'CODEX_HOME': str(self.root / 'home')}):
            self.assertEqual(publish(rejected, 'first')['remaining_submissions'], 2)
            self.assertEqual((self.project / 'docs/design.md').read_bytes(), original)
            # Entering the already-recorded next phase cannot renew its allowance.
            state.update(current_phase='sdlc-create-design', checkpoint_id='checkpoint-0004')
            write(self.run_dir / 'current-state.json', state)
            write(self.run_dir / 'checkpoints/checkpoint-0004.json', state)
            write(self.run_dir / 'checkpoints/latest.json', {'checkpoint_id': 'checkpoint-0004',
                                                           'path': 'checkpoints/checkpoint-0004.json'})
            self.assertEqual(publish(route, 'different-operation')['remaining_submissions'], 1)
            corrected = route.replace(b'PATCH /tasks/<id>', b'PATCH `/tasks/<id>`')
            self.assertEqual(publish(corrected, 'final')['schema'], 'maintain-project-specs.transaction.v1')
            self.assertEqual((self.project / 'docs/design.md').read_bytes(), corrected)
        lanes = list((self.run_dir / 'input-corrections').glob('*.json'))
        self.assertEqual(len(lanes), 1)
        self.assertEqual(read(lanes[0])['submissions'], 2)
        self.assertFalse((self.run_dir / 'repairs').exists())

    def test_result_correction_requires_observed_termination_and_keeps_ingestion_owned(self):
        from workflow_worker_results import ingest_result, receipt_path
        from workflow_state import write
        first, _ = self.result_fixture()
        observation = self.result_observation(first)
        original = read(observation)
        candidate = self.result_payload(first)
        write(observation, {**original, 'status': 'running'})
        with self.assertRaises(fixture.ExecutionError):
            ingest_result(self.run_dir, 'FEAT-001', 'WAVE-001', first['task_id'],
                          {'result': candidate}, observation, self.result_session())
        self.assertFalse((self.run_dir / 'input-corrections').exists())
        write(observation, original)
        pending = ingest_result(self.run_dir, 'FEAT-001', 'WAVE-001', first['task_id'],
                                {'result': candidate}, observation, self.result_session())
        self.assertEqual(pending['status'], 'correction_pending')
        self.assertFalse(receipt_path(first, 1).exists())
        accepted = ingest_result(self.run_dir, 'FEAT-001', 'WAVE-001', first['task_id'],
                                 candidate, observation, self.result_session())
        self.assertTrue(Path(accepted['receipt_path']).exists())
        self.assertFalse((self.run_dir / 'repairs').exists())


if __name__ == '__main__':
    unittest.main()
