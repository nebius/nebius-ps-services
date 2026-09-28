"""Exact local-link declarations do not broaden active-resource permission."""

import json
import tempfile
import unittest
from pathlib import Path

from test_course_assets import CHECK, fixture


class CompanionLinks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.publication = Path(self.temp.name).resolve()
        self.root = self.publication / "course"
        self.root.mkdir()
        self.page = self.root / "index.html"
        self.sources = self.root / "sources.json"
        self.sources.write_text("[]")
        self.links = self.root / "links.json"
        (self.root / "results.zip").write_bytes(b"companion")
        (self.publication / "lab-guide.html").write_text('<h1 id="setup">Setup</h1>')

    def check_link(self, href, declared=None, *, root=True, tag="a"):
        self.page.write_text(
            fixture().replace(
                "</main>", f'<{tag} href="{href}">Download</{tag}></main>'
            )
        )
        self.links.write_text(json.dumps(declared if declared is not None else [href]))
        return CHECK.check(
            self.page,
            self.root,
            self.sources,
            self.links,
            self.publication if root else None,
        )

    def test_declared_download_and_shared_guide(self):
        for href in ("results.zip", "../lab-guide.html", "../lab-guide.html#setup"):
            with self.subTest(href=href):
                self.assertEqual(self.check_link(href), [])

    def test_no_manifest_does_not_authorize_local_links(self):
        self.check_link("results.zip")
        self.assertTrue(CHECK.check(self.page, self.root, self.sources))
        self.assertTrue(self.check_link("results.zip", []))

    def test_scoped_root_and_fragments(self):
        for href, root in [
            ("../lab-guide.html", False),
            ("../lab-guide.html#missing", True),
            ("results.zip#missing", True),
            ("missing.zip", True),
            ("../../outside", True),
        ]:
            with self.subTest(href=href):
                with self.assertRaises(ValueError):
                    self.check_link(href, root=root)

    def test_unsafe_and_duplicate_declarations(self):
        for href in (
            "https://example.com/file",
            "//example.com/file",
            "/results.zip",
            "javascript:alert(1)",
            "results.zip?q=x",
            "%2fetc/passwd",
            "a%5cb",
            "results.zip\n",
        ):
            with self.subTest(href=href):
                with self.assertRaises(ValueError):
                    self.check_link(href)
        with self.assertRaises(ValueError):
            self.check_link("results.zip", ["results.zip", "results.zip"])
        with self.assertRaises(ValueError):
            self.check_link("results.zip", {"results.zip": True})

    def test_symlinks_and_non_anchor_references_rejected(self):
        (self.root / "alias.zip").symlink_to(self.root / "results.zip")
        with self.assertRaises(ValueError):
            self.check_link("alias.zip")
        self.assertTrue(self.check_link("results.zip", tag="svg"))


if __name__ == "__main__":
    unittest.main()
