#!/usr/bin/env python3
"""The explicit public Implement contract and its owned dependency are loadable."""
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent / 'implement-test/scripts'))
from verify_implement import contract  # noqa: E402 — verified runtime support precedes imports


class ImplementContractTests(unittest.TestCase):
    def test_public_interface_and_policy(self):
        self.assertEqual(contract(ROOT)[0], 'PASS')

    def test_shared_helpers_are_loadable(self):
        for name in ('workflow_intake.py', 'workflow_transition.py', 'workflow_dispatch.py'):
            value = subprocess.run([sys.executable, str(ROOT.parent / 'worktree/scripts' / name), '--help'],
                                   capture_output=True, text=True)
            self.assertEqual(value.returncode, 0, value.stderr)

    def test_retired_private_commands_fail_before_mutation(self):
        helper = ROOT.parent / 'worktree/scripts/workflow_intake.py'
        for action in ('workspace', 'run', 'integrate', 'queue-next', 'upgrade'):
            value = subprocess.run([sys.executable, str(helper), '--owner', 'implement', action],
                                   capture_output=True, text=True)
            self.assertEqual(value.returncode, 2)
            self.assertIn('invalid choice', value.stderr)


if __name__ == '__main__':
    unittest.main()
