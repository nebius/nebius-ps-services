#!/usr/bin/env python3
"""Public SDLC entry and phase-ownership contract checks."""
from pathlib import Path
import re
import unittest

SKILLS = Path(__file__).resolve().parents[2]


class SdlcStartContractTests(unittest.TestCase):
    def text(self, path):
        return (SKILLS / path).read_text()

    def test_public_interface_is_mirrored(self):
        for path in ('sdlc-start/SKILL.md', 'sdlc-start/README.md'):
            content = self.text(path)
            for command in ('$sdlc-start', '$sdlc-start <objective>', '$sdlc-start continue [project-folder]'):
                self.assertIn(command, content)
            self.assertNotIn('$sdlc-start workspace init', content)
            self.assertNotIn('$sdlc-start run <', content)

    def test_metadata_stays_explicit_only(self):
        self.assertIn('allow_implicit_invocation: false', self.text('sdlc-start/agents/openai.yaml'))
        self.assertIn('disable-model-invocation: true', self.text('sdlc-start/SKILL.md'))

    def test_phase_consumers_read_shared_execution_contract(self):
        for folder in SKILLS.glob('sdlc-*'):
            if folder.name == 'sdlc-workflow-test':
                continue
            content = self.text(str(folder.relative_to(SKILLS) / 'SKILL.md'))
            self.assertIn('workflow-contract.md', content, folder.name)

    def test_no_capture_or_prompt_file_entry(self):
        self.assertFalse((SKILLS / 'prompt-session-intake').exists())
        self.assertFalse((SKILLS / 'sdlc-start/scripts/prompt_workspace.py').exists())
        for path in ('sdlc-start/SKILL.md', 'sdlc-start/README.md'):
            self.assertNotIn('prompt-ref-or-file', self.text(path))
        self.assertIn('raw', self.text('sdlc-start/SKILL.md'))

    def test_private_completion_helpers_do_not_expand_public_surface(self):
        content = self.text('sdlc-start/SKILL.md')
        lines = re.findall(r'(?m)^\$sdlc-start[^\n]*$', content)
        self.assertEqual(lines, ['$sdlc-start', '$sdlc-start <objective>', '$sdlc-start continue [project-folder]', '$sdlc-start -h', '$sdlc-start --help'])

    def test_post_uat_documents_preserve_promotion_boundary(self):
        for path in ('sdlc-start/SKILL.md', 'sdlc-update-documents/SKILL.md'):
            content = ' '.join(self.text(path).split())
            self.assertIn('post-promotion documentation assessment is read-only', content)
            self.assertIn('corrective run from the exact promoted commit', content)
            self.assertIn('DOCUMENTATION_DRIFT', content)

    def test_raw_commit_authorization_does_not_gate_private_sealing(self):
        content = ' '.join(self.text('sdlc-commit/SKILL.md').split())
        self.assertIn('For operator-visible raw `git commit` only', content)
        self.assertIn('private sealing and promotion', content)

    def test_managed_outer_handoff_is_exact_and_local(self):
        content = self.text('sdlc-start/SKILL.md')
        self.assertIn('$worktree integrate <generated-name>', content)
        self.assertIn('outer-integration-pending', content)
        self.assertIn('complete-outer-integration', content)

    def test_specification_ownership_is_shared(self):
        content = self.text('sdlc-start/SKILL.md')
        for term in ('maintain-project-specs', 'canonical', 'integration'):
            self.assertIn(term, content)
        self.assertIn('publish', self.text('worktree/references/workflow-contract.md'))


if __name__ == '__main__':
    unittest.main()
