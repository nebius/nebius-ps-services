#!/usr/bin/env python3
"""Offline standard/host separation and typed YAML regression tests."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
SPEC = importlib.util.spec_from_file_location(
    "skill_validator", Path(__file__).with_name("validate-skill-structure.py")
)
assert SPEC and SPEC.loader
validator = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = validator
SPEC.loader.exec_module(validator)
from skill_frontmatter import frontmatter  # noqa: E402


class StandardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.skill = Path(self.temp.name) / "sample"
        self.skill.mkdir()

    def check(self, fields="", body="Do the requested task.", **kwargs):
        (self.skill / "SKILL.md").write_text(
            f"---\nname: sample\ndescription: Useful example\n{fields}---\n{body}\n"
        )
        return validator.validate_skill(self.skill, policy="agentskills", **kwargs)

    def test_minimum_passes_each_host_without_repo_sections(self):
        for agent in ("core", "codex", "claude"):
            with self.subTest(agent=agent):
                self.assertTrue(self.check(agent=agent).ok)
        result = validator.validate_skill(self.skill)
        self.assertFalse(result.ok)
        self.assertIn("SKILL.md is missing ## Learning Loop", result.failures)

    def test_optional_fields_and_multiline_description(self):
        result = self.check('license: MIT\ncompatibility: Requires Python\n'
                            'metadata:\n  version: "1"\nallowed-tools: Read Bash(git:*)\n')
        self.assertTrue(result.ok, result.failures)
        path = self.skill / "SKILL.md"
        path.write_text(path.read_text().replace("description: Useful example",
                                                "description: >-\n  Useful\n  example"))
        self.assertTrue(validator.validate_skill(self.skill, policy="agentskills").ok)

    def test_invalid_field_types_and_duplicate_keys(self):
        for fields in ('name: duplicate\n', 'description: [invalid]\n',
                       'license: 3\n', 'compatibility: ""\n',
                       'compatibility: ' + 'x' * 501 + '\n',
                       'metadata: {version: 1}\n', 'metadata: {true: value}\n',
                       'metadata: {a: one, a: two}\n', 'allowed-tools: [Read]\n',
                       'malformed: [\n', 'unexpected-extension: true\n',
                       'metadata: !!python/object:bad {}\n'):
            with self.subTest(fields=fields[:30]):
                result = self.check(fields)
                self.assertFalse(result.ok)

    def test_native_controls_are_preserved_and_conformity_is_separate(self):
        result = self.check('disable-model-invocation: true\nuser-invocable: false\n',
                            agent="claude")
        self.assertTrue(result.ok, result.failures)
        self.assertEqual(result.strict_conformity, "EXTENSIONS")
        self.assertEqual(result.extensions, ["disable-model-invocation", "user-invocable"])
        self.assertFalse(self.check('disable-model-invocation: "false"\n').ok)

    def test_standard_policy_does_not_enforce_sdlc_naming_conventions(self):
        self.skill = self.skill.rename(self.skill.with_name("sdlc-example"))
        (self.skill / "SKILL.md").write_text(
            "---\nname: sdlc-example\ndescription: An unrelated external skill\n---\nUse it.\n"
        )
        self.assertTrue(validator.validate_skill(self.skill, policy="agentskills",
                                                  agent="claude").ok)

    def test_standard_policy_ignores_optional_repo_eval_format(self):
        (self.skill / "evals").mkdir()
        (self.skill / "evals/trigger-prompts.csv").write_text("external format\n")
        self.assertTrue(self.check().ok)
        self.assertFalse(self.check(require_evals=True).ok)

    def test_unicode_standard_name_and_naming_boundaries(self):
        for name, valid in (("café", True), ("a--b", False), ("a-", False),
                            ("A", False), ("a" * 64, True), ("a" * 65, False)):
            with self.subTest(name=name):
                path = Path(self.temp.name) / name
                path.mkdir(exist_ok=True)
                (path / "SKILL.md").write_text(f"---\nname: {name}\ndescription: x\n---\nx\n")
                self.assertEqual(validator.validate_skill(path, policy="agentskills").ok, valid)

    def test_resource_symlinks_and_escape_fail(self):
        outside = Path(self.temp.name) / "outside.txt"
        outside.write_text("private fixture")
        (self.skill / "reference.txt").symlink_to(outside)
        for body in ("Read [file](reference.txt)", "Read [file](../outside.txt)"):
            self.assertFalse(self.check(body=body).ok)

    def test_markdown_titles_urls_and_extensionless_resources(self):
        (self.skill / "reference.md").write_text("Useful reference")
        for body in ('Read [Reference](reference.md "Reference title").',
                     'Read [upstream](https://example.com/scripts/check.py).'):
            result = self.check(body=body)
            self.assertTrue(result.ok, result.failures)
        self.assertFalse(self.check(body="Read [License](LICENSE).").ok)

    def test_standard_status_is_independent_of_invalid_extension(self):
        result = self.check('disable-model-invocation: "false"\n')
        self.assertFalse(result.ok)
        self.assertEqual(result.standard_fields, "PASS")

    def test_bad_resource_paths_do_not_abort_catalog(self):
        (self.skill / "loop.md").symlink_to("loop.md")
        for body in ("Read [file](bad%00name.md).", "Read [file](loop.md)."):
            with self.subTest(body=body):
                self.assertFalse(self.check(body=body).ok)
        good = Path(self.temp.name) / "valid"
        good.mkdir()
        (good / "SKILL.md").write_text("---\nname: valid\ndescription: x\n---\nx\n")
        result = subprocess.run([sys.executable, "-B", str(Path(validator.__file__)),
                                 "--policy", "agentskills", self.temp.name],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Validated 2 skill(s)", result.stdout)
        self.assertNotIn("Traceback", result.stderr)

    def test_yaml_aliases_and_merge_complexity_are_bounded(self):
        result = self.check('license: &value MIT\nmetadata: {license: *value}\n')
        self.assertTrue(result.ok, result.failures)
        result = self.check('metadata: &loop {<<: *loop}\n')
        self.assertFalse(result.ok)
        result = self.check('metadata:\n' + ''.join('  ' * n + 'x:\n' for n in range(1, 45)))
        self.assertFalse(result.ok)

    def test_yaml_merge_preserves_explicit_override_and_sequence_precedence(self):
        for fields, expected in (
            ('metadata:\n  <<: &base {author: original}\n  author: replacement\n',
             {"author": "replacement"}),
            ('metadata:\n  <<: [{author: first}, {author: second, license: MIT}]\n',
             {"author": "first", "license": "MIT"}),
            ('metadata: {<<: {=: first}, =: final}\n', {"=": "final"}),
        ):
            with self.subTest(fields=fields):
                result = self.check(fields)
                self.assertTrue(result.ok, result.failures)
                self.assertEqual(frontmatter(self.skill / "SKILL.md")["metadata"], expected)

    def test_yaml_merge_reused_alias_retains_overrides(self):
        result = self.check(
            'metadata: &derived\n  <<: {author: original}\n  author: replacement\n'
            'hooks:\n  first: *derived\n  second: {<<: *derived, author: final}\n'
        )
        self.assertTrue(result.ok, result.failures)
        data = frontmatter(self.skill / "SKILL.md")
        self.assertEqual(data["hooks"]["first"], {"author": "replacement"})
        self.assertEqual(data["hooks"]["second"], {"author": "final"})

    def test_yaml_merge_does_not_allow_duplicate_explicit_keys(self):
        for fields in (
            'metadata: {<<: {author: first, author: second}}\n',
            'metadata: {<<: {author: first}, author: second, author: third}\n',
            'metadata: {<<: {author: first}, <<: {license: MIT}}\n',
            'metadata: {=: first, "=": second}\n',
        ):
            with self.subTest(fields=fields):
                result = self.check(fields)
                self.assertFalse(result.ok)
                self.assertTrue(any("duplicate YAML mapping key" in failure
                                    for failure in result.failures), result.failures)

    def test_missing_yaml_diagnostic_and_help_without_dependencies(self):
        script = Path(__file__).with_name("validate-skill-structure.py")
        self.check()
        result = subprocess.run([sys.executable, "-B", "-S", str(script), str(self.skill)],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requirements.txt", result.stdout + result.stderr)
        result = subprocess.run([sys.executable, "-B", "-S", str(script), "--help"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
