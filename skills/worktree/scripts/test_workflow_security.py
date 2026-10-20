"""Sensitive material must not inherit a neighboring placeholder exemption."""

import unittest

from workflow_security import contains_sensitive


class SensitiveTextTests(unittest.TestCase):
    def test_placeholder_words_do_not_hide_other_values(self):
        synthetic = "A" * 20
        for text in (
            "example: password=" + synthetic,
            "token=placeholder; password=" + synthetic,
            "password=" + synthetic + " # redacted example",
            "example: https://service.internal/diagnostics",
            "example: -----BEGIN PRIVATE KEY-----",
        ):
            with self.subTest(case=text.split(":", 1)[0]):
                self.assertTrue(contains_sensitive(text))

    def test_placeholder_values_and_dynamic_lookups_remain_allowed(self):
        for text in (
            "token=placeholder",
            "password=not-a-secret",
            "secret='redacted'",
            "token = client.cookies['csrf']",
        ):
            with self.subTest(text=text):
                self.assertFalse(contains_sensitive(text))


if __name__ == "__main__":
    unittest.main()
