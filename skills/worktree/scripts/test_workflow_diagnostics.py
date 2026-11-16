"""Diagnostic helper lifecycle with synthetic enforced-host observations."""
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'sdlc-classify-failure/scripts'))
import test_workflow_execution as fixture
from test_repair_control import failure_payload
import repair_control
import workflow_diagnostics as d
import workflow_projects as capacity
from workflow_state import WorkflowError

POLICY = {'enforced': True, 'deadline_enforced': True, 'no_recursive_delegation': True,
          'scope': 'provided_evidence_only', 'tools': [], 'native_policy_identity': 'synthetic-policy'}


class DiagnosticTests(unittest.TestCase):
    tearDown = fixture.GitLifecycleTests.tearDown

    def setUp(self):
        fixture.GitLifecycleTests.setUp(self)
        destination = self.root / 'home/sdlc-runs/project/run-1'
        destination.parent.mkdir(parents=True)
        self.run_dir.rename(destination)
        self.run_dir = destination
        self.event = repair_control.record_failure_event(self.run_dir, failure_payload())['event']
        evidence = self.run_dir / 'evidence/FEAT-001'
        evidence.mkdir(parents=True, exist_ok=True)
        (evidence / 'failure.md').write_text('Observed accepted-boundary mismatch.\n')
        self.questions = [{'question': f'Inspect boundary {i}', 'evidence': ['evidence/FEAT-001/failure.md']}
                          for i in range(2)]
        self.now = 1000000000
        capacity.admit(self.run_dir)

    def reserve(self, restrictions=lambda: POLICY):
        return d.reserve(self.run_dir, self.event, self.questions, 'deterministic-coordinator',
                         restrictions=restrictions, remaining_seconds=120, clock=lambda: self.now)

    def findings(self, result):
        return [{'assignment_id': row['assignment_id'], 'native_handle': f'synthetic-{index}',
                 'findings': ['Observed boundary evidence.'], 'contradictions': ['Alternative remains.'],
                 'status': 'completed'} for index, row in enumerate(result['assignments'])]

    def reconcile(self, findings, terminal=True):
        return d.reconcile(self.run_dir, 'FEAT-001', self.event['event_id'], findings,
                           'deterministic-coordinator', observe_termination=lambda *_: terminal,
                           restrictions=lambda: POLICY, clock=lambda: self.now)

    def test_no_effective_tool_restrictions_falls_back_serially(self):
        for observer in (None, lambda: {'role': 'read-only'}, lambda: {**POLICY, 'tools': ['shell']}):
            self.assertEqual(self.reserve(observer)['status'], 'serial')
        self.assertFalse((self.run_dir / 'repairs/FEAT-001/helpers').exists())

    def test_terminal_observation_and_contradictions_precede_one_diagnosis(self):
        result = self.reserve()
        self.assertEqual(result['status'], 'reserved')
        self.assertEqual(result['assignments'][0]['deadline_ns'], self.now + 120000000000)
        with self.assertRaises(WorkflowError):
            d.require_reconciled(self.run_dir, 'FEAT-001', self.event['event_id'])
        findings = self.findings(result)
        with self.assertRaises(WorkflowError):
            self.reconcile(findings, False)
        self.now += 10000000000
        observed = self.reconcile(findings)
        self.assertTrue(observed['findings'][0]['contradictions'])
        self.assertFalse(observed['classification_authority'])
        d.require_reconciled(self.run_dir, 'FEAT-001', self.event['event_id'])
        self.assertEqual(self.reserve()['status'], 'serial')
        self.reconcile(findings)
        budget = repair_control._load_control(self.run_dir, 'FEAT-001')['active_blocker']
        self.assertEqual(budget['active_seconds'], 10)
        self.assertEqual(len(budget['experiments']), 1)

    def test_interrupted_accounting_cannot_admit_diagnosis(self):
        findings = self.findings(self.reserve())
        self.now += 5000000000
        with mock.patch.object(repair_control, 'record_experiment', side_effect=RuntimeError('interrupted')):
            with self.assertRaises(RuntimeError):
                self.reconcile(findings)
        with self.assertRaisesRegex(WorkflowError, 'accounting is incomplete'):
            d.require_reconciled(self.run_dir, 'FEAT-001', self.event['event_id'])
        self.reconcile(findings)
        d.require_reconciled(self.run_dir, 'FEAT-001', self.event['event_id'])

    def test_capacity_interruption_reuses_durable_identities(self):
        with mock.patch.object(capacity, 'reserve_resources', side_effect=RuntimeError('interrupted')):
            with self.assertRaises(RuntimeError):
                self.reserve()
        path = self.run_dir / 'repairs/FEAT-001/helpers' / self.event['event_id'] / 'assignments.json'
        before = path.read_bytes()
        result = self.reserve()
        self.assertEqual(result['status'], 'reserved')
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(len(result['reservations']), 2)
        self.reconcile(self.findings(result))

    def test_insufficient_capacity_returns_to_serial_diagnosis(self):
        held = capacity.reserve_resources(self.run_dir, 'workers', ['FEAT-001/WAVE-001/TASK-001'], ceiling=2)
        self.assertEqual(len(held), 1)
        self.assertEqual(self.reserve()['status'], 'serial')
        d.require_reconciled(self.run_dir, 'FEAT-001', self.event['event_id'])
        self.assertEqual(self.reserve()['status'], 'serial')

    def test_two_helpers_use_total_native_capacity_alongside_worker(self):
        from workflow_capacity import configure, observe
        configure(self.run_dir, 4, 8, 16, 'deterministic-coordinator')
        observe(self.run_dir, native_slots=4, evidence='synthetic capacity', session='deterministic-coordinator')
        capacity.reserve_resources(self.run_dir, 'workers', ['FEAT-001/WAVE-001/TASK-001'], ceiling=4)
        result = self.reserve()
        self.assertEqual(result['status'], 'reserved')
        self.assertEqual(len(result['assignments']), 2)
        self.reconcile(self.findings(result))

    def test_historical_event_cannot_charge_current_blocker(self):
        result = self.reserve()
        path = self.run_dir / 'repairs/FEAT-001/repair-control.json'
        from workflow_state import read, write
        value = read(path)
        value['current_event_id'] = 'f' * 64
        write(path, value)
        with self.assertRaises((WorkflowError, repair_control.RepairControlError)):
            self.reserve()
        with self.assertRaises((WorkflowError, repair_control.RepairControlError)):
            self.reconcile(self.findings(result))
        self.assertEqual(read(path)['active_blocker']['active_seconds'], 0)


if __name__ == '__main__':
    unittest.main()
