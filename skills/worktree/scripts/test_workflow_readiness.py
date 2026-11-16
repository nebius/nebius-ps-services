"""Readiness is owned, current and convergent, independent of checkpoints."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_workflow_execution as fixture
from test_workflow_fixture import seed_readiness, seed_validation, write_private
from workflow_readiness import inputs, issue, require
from workflow_state import WorkflowError
import workflow_execution as engine


class ReadinessTests(unittest.TestCase):
    setUp = fixture.GitLifecycleTests.setUp
    tearDown = fixture.GitLifecycleTests.tearDown
    prepare = fixture.GitLifecycleTests.prepare
    def test_empty_evidence_directories_do_not_block_readiness(self):
        seed_validation(self.plan)
        seed_readiness(self.run_dir, 'FEAT-001', self.plan)
        before = inputs(self.run_dir, 'FEAT-001', self.plan)
        for index in range(300):
            (self.run_dir / 'evidence/FEAT-001' / str(index)).mkdir(parents=True)
        self.assertEqual(inputs(self.run_dir, 'FEAT-001', self.plan), before)

    def test_missing_readiness_blocks_before_execution_effects(self):
        seed_validation(self.plan)
        with self.assertRaises(engine.ExecutionError) as error:
            engine.prepare_execution(self.run_dir, self.project, 'FEAT-001', self.plan, 2)
        self.assertEqual(error.exception.code, 'STEERING_PENDING')
        self.assertFalse((self.run_dir / 'execution').exists())
        self.assertFalse((self.run_dir / 'repairs').exists())

    def test_unchanged_steering_and_checkpoint_converge(self):
        seed_validation(self.plan)
        seed_readiness(self.run_dir, 'FEAT-001', self.plan)
        before = require(self.run_dir, 'FEAT-001', self.plan)
        write_private(self.run_dir / 'current-state.json', {'updated_at': 'later', 'phase': 'planning'})
        state = self.run_dir / 'steering/auto-steering.json'
        value = json.loads(state.read_text())
        value['updated_at'] = 'later'
        write_private(state, value)
        projection = inputs(self.run_dir, 'FEAT-001', self.plan)
        issued = issue(self.run_dir, 'FEAT-001', projection['inputs_sha256'],
                       'deterministic-coordinator', self.plan)
        self.assertEqual(before['receipt_sha256'], issued['receipt_sha256'])
        self.assertEqual(before, require(self.run_dir, 'FEAT-001', self.plan))

    def test_changed_context_stales_receipt_and_worker_admission(self):
        self.prepare()
        context = self.run_dir / 'context/FEAT-001.context.md'
        context.write_text('Changed decision evidence.\n')
        with self.assertRaises(engine.ExecutionError) as error:
            engine.prepare_wave(self.run_dir, 'FEAT-001', 'wave-001')
        self.assertEqual(error.exception.code, 'STEERING_PENDING')

    def test_document_metadata_clocks_converge_but_content_dates_remain_bound(self):
        from project_specs_lib.contracts import spec_markers
        source = self.project / 'docs/requirements.md'
        marker, end = spec_markers('requirements')
        source.write_text(marker + '\n---\ncreated_at: "2026-01-01"\nupdated_at: "2026-01-01"\n'
                          'status: active\n---\n' + source.read_text() + '\n' + end + '\n')
        seed_validation(self.plan)
        seed_readiness(self.run_dir, 'FEAT-001', self.plan)
        before = require(self.run_dir, 'FEAT-001', self.plan)
        source.write_text(source.read_text().replace('2026-01-01', '2026-11-13'))
        self.assertEqual(require(self.run_dir, 'FEAT-001', self.plan), before)
        source.write_text(source.read_text().replace('created_at: "2026-11-13"\n', '')
                          .replace('updated_at: "2026-11-13"\n', ''))
        self.assertEqual(require(self.run_dir, 'FEAT-001', self.plan), before)
        context = self.run_dir / 'context/FEAT-001.context.md'
        context.write_text('---\nupdated_at: "2026-01-01"\n---\nDecision deadline: 2026-12-01\n')
        seed_readiness(self.run_dir, 'FEAT-001', self.plan)
        before = require(self.run_dir, 'FEAT-001', self.plan)
        context.write_text(context.read_text().replace('2026-01-01', '2026-11-13'))
        self.assertEqual(require(self.run_dir, 'FEAT-001', self.plan), before)
        context.write_text(context.read_text().replace('2026-12-01', '2026-12-02'))
        with self.assertRaises(WorkflowError) as error:
            require(self.run_dir, 'FEAT-001', self.plan)
        self.assertEqual(error.exception.code, 'STEERING_PENDING')

    def test_metadata_like_content_is_decision_evidence(self):
        context = self.run_dir / 'context/FEAT-001.context.md'
        context.parent.mkdir(parents=True)
        context.write_text('# API example\n```yaml\nupdated_at: 2026-01-01\n```\n')
        seed_validation(self.plan)
        seed_readiness(self.run_dir, 'FEAT-001', self.plan)
        context.write_text(context.read_text().replace('2026-01-01', '2026-11-13'))
        with self.assertRaises(WorkflowError) as error:
            require(self.run_dir, 'FEAT-001', self.plan)
        self.assertEqual(error.exception.code, 'STEERING_PENDING')

    def test_json_schema_fields_and_unknown_record_timestamps_remain_bound(self):
        evidence = self.run_dir / 'evidence/FEAT-001/decision.json'
        for first, changed in (
            ({'properties': {'updated_at': {'type': 'string'}}},
             {'properties': {'updated_at': {'type': 'integer'}}}),
            ({'schema': 'workflow/unknown-data-v1', 'updated_at': 'first'},
             {'schema': 'workflow/unknown-data-v1', 'updated_at': 'second'}),
            ({'schema': 'agentic-sdlc/phase-result-v5', 'updated_at': {'type': 'string'}},
             {'schema': 'agentic-sdlc/phase-result-v5', 'updated_at': {'type': 'integer'}}),
        ):
            with self.subTest(first=first):
                write_private(evidence, first)
                seed_validation(self.plan)
                seed_readiness(self.run_dir, 'FEAT-001', self.plan)
                write_private(evidence, changed)
                with self.assertRaises(WorkflowError) as error:
                    require(self.run_dir, 'FEAT-001', self.plan)
                self.assertEqual(error.exception.code, 'STEERING_PENDING')

    def test_owned_json_metadata_converges_without_erasing_nested_decisions(self):
        evidence = self.run_dir / 'evidence/FEAT-001/decision.json'
        value = {'schema': 'agentic-sdlc/phase-result-v5', 'updated_at': 'first',
                 'assertions': {'example': {'updated_at': 'meaningful'}}}
        write_private(evidence, value)
        seed_validation(self.plan)
        seed_readiness(self.run_dir, 'FEAT-001', self.plan)
        before = require(self.run_dir, 'FEAT-001', self.plan)
        value['updated_at'] = 'second'
        write_private(evidence, value)
        self.assertEqual(require(self.run_dir, 'FEAT-001', self.plan), before)
        value['assertions']['example']['updated_at'] = 'changed decision'
        write_private(evidence, value)
        with self.assertRaises(WorkflowError) as error:
            require(self.run_dir, 'FEAT-001', self.plan)
        self.assertEqual(error.exception.code, 'STEERING_PENDING')

    def test_steering_entry_clock_converges_but_nested_content_is_bound(self):
        seed_validation(self.plan)
        seed_readiness(self.run_dir, 'FEAT-001', self.plan)
        state = self.run_dir / 'steering/auto-steering.json'
        value = json.loads(state.read_text())
        value['entries'][0]['received_at'] = 'later'
        write_private(state, value)
        require(self.run_dir, 'FEAT-001', self.plan)
        value['active_reminders'] = [{'text': 'Track API fields', 'example': {'updated_at': 'first'}}]
        write_private(state, value)
        projection = inputs(self.run_dir, 'FEAT-001', self.plan)
        issue(self.run_dir, 'FEAT-001', projection['inputs_sha256'], 'deterministic-coordinator', self.plan)
        value['active_reminders'][0]['example']['updated_at'] = 'second'
        write_private(state, value)
        with self.assertRaises(WorkflowError) as error:
            require(self.run_dir, 'FEAT-001', self.plan)
        self.assertEqual(error.exception.code, 'STEERING_PENDING')

    def test_unrelated_feature_context_is_not_current_context(self):
        seed_validation(self.plan)
        seed_readiness(self.run_dir, 'FEAT-001', self.plan)
        with self.assertRaises(WorkflowError) as error:
            inputs(self.run_dir, 'FEAT-002')
        self.assertEqual(error.exception.code, 'CONTEXT_PENDING')

    def test_plan_bound_receipt_is_shared_by_hook_and_first_preparation(self):
        import subprocess
        import os
        seed_readiness(self.run_dir, 'FEAT-001')
        self.assertEqual(require(self.run_dir, 'FEAT-001')['status'], 'ready')
        seed_validation(self.plan)
        seed_readiness(self.run_dir, 'FEAT-001', self.plan)
        checked = subprocess.run([sys.executable, '-B', str(Path(__file__).with_name('workflow_readiness.py')),
                                  '--run-dir', str(self.run_dir), '--execution', 'FEAT-001'],
                                 capture_output=True, text=True)
        self.assertEqual(checked.returncode, 0, checked.stdout)
        self.assertEqual(require(self.run_dir, 'FEAT-001'), require(self.run_dir, 'FEAT-001', self.plan))
        helper = Path(__file__).with_name('workflow_transition.py')
        returned = subprocess.run([sys.executable, '-B', str(helper), 'steering-inputs', '--run-dir',
                                   str(self.run_dir), '--execution', 'FEAT-001', '--plan', str(self.plan)],
                                  env={**os.environ, 'CODEX_THREAD_ID': 'deterministic-coordinator'},
                                  capture_output=True, text=True)
        self.assertEqual(returned.returncode, 0, returned.stdout + returned.stderr)
        response = json.loads(returned.stdout)['result']
        self.assertIn('ready_command', response)
        self.assertTrue(Path(response['working_directory']).is_dir())
        engine.prepare_execution(self.run_dir, self.project, 'FEAT-001', self.plan, 2)

    def test_corrupt_observational_timing_cannot_change_transition_outcome(self):
        from workflow_timing import measure
        for counters in ([], {'launch': {}}, {'launch': {'count': 'bad', 'failed': 0, 'total_ms': 0, 'max_ms': 0}}):
            write_private(self.run_dir / 'workflow-timing.json', {'schema': 'workflow/timing-v2', 'stages': counters})
            with measure(self.run_dir, 'launch'):
                pass
            with self.assertRaisesRegex(ValueError, 'original failure'):
                with measure(self.run_dir, 'launch'):
                    raise ValueError('original failure')

    def test_resolved_changes_and_volatile_ledger_fields_converge(self):
        seed_validation(self.plan)
        seed_readiness(self.run_dir, 'FEAT-001', self.plan)
        state = self.run_dir / 'steering/auto-steering.json'
        value = json.loads(state.read_text())
        value['entries'][0]['disposition'] = 'design-change'
        write_private(state, value)
        ledger = self.run_dir / 'STEERING.md'
        ledger.write_text('# Steering\n- Updated at: first\n- Current phase: design\n')
        projection = inputs(self.run_dir, 'FEAT-001', self.plan)
        first = issue(self.run_dir, 'FEAT-001', projection['inputs_sha256'],
                      'deterministic-coordinator', self.plan)
        ledger.write_text('# Steering\n- Updated at: second\n- Current phase: planning\n')
        self.assertEqual(require(self.run_dir, 'FEAT-001', self.plan)['receipt_sha256'], first['receipt_sha256'])

    def test_fabricated_pointer_without_owner_archive_is_rejected(self):
        seed_validation(self.plan)
        seed_readiness(self.run_dir, 'FEAT-001', self.plan)
        root = self.run_dir / 'steering/readiness/FEAT-001'
        value = json.loads((root / 'current.json').read_text())
        (root / (value['receipt_sha256'] + '.json')).unlink()
        with self.assertRaises(WorkflowError) as error:
            require(self.run_dir, 'FEAT-001', self.plan)
        self.assertEqual(error.exception.code, 'READINESS_INVALID')

    def test_foreign_issuer_and_stale_input_digest_rejected(self):
        seed_validation(self.plan)
        seed_readiness(self.run_dir, 'FEAT-001', self.plan)
        for identity, fingerprint in [('foreign', inputs(self.run_dir, 'FEAT-001', self.plan)['inputs_sha256']),
                                      ('deterministic-coordinator', '0' * 64)]:
            with self.subTest(identity=identity), self.assertRaises(WorkflowError):
                issue(self.run_dir, 'FEAT-001', fingerprint, identity, self.plan)


if __name__ == '__main__':
    unittest.main()
