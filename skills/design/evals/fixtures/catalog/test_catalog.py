"""Check the implemented baseline used by the synthetic evaluation."""

import unittest

from consumer import export_ids
from service import list_items


class CatalogTests(unittest.TestCase):
    def test_consumer_reads_envelope(self):
        self.assertEqual(export_ids(["first", "second"]), ["first", "second"])

    def test_empty_page(self):
        self.assertEqual(list_items([])["items"], [])
        self.assertIsNone(list_items([])["next_cursor"])
