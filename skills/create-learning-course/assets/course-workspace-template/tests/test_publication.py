"""Reusable packaging regression checks; add project renderer parity tests here."""

import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from publication import check_budget, results_zip, write_atomic


class PublicationContract(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / "results.csv").write_bytes(b"count,value\n1,2\n")

    def test_archive_preserves_explicit_members_only(self):
        (self.root / "unselected.txt").write_text("must stay outside archive")
        members = {"small/results.csv": "results.csv"}
        content = results_zip(self.root, members)
        self.assertEqual(content, results_zip(self.root, members))
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            self.assertEqual(archive.namelist(), list(members))
            self.assertEqual(
                archive.read("small/results.csv"),
                (self.root / "results.csv").read_bytes(),
            )

    def test_size_error_and_output_collision_do_not_write(self):
        output = self.root / "index.html"
        output.write_bytes(b"previous")
        before = (output.read_bytes(), output.stat().st_mtime_ns)
        for outputs, limit in [
            ({"index.html": b"new"}, 1),
            ({"a": b"x", "a/b": b"y"}, 1000),
        ]:
            with self.assertRaises(ValueError):
                check_budget(
                    self.root,
                    outputs,
                    max_file_bytes=limit,
                    max_site_bytes=1000,
                    inventory="directory",
                )
            self.assertEqual(before, (output.read_bytes(), output.stat().st_mtime_ns))

    def test_unchanged_output_is_not_rewritten(self):
        output = self.root / "index.html"
        write_atomic(output, b"complete publication")
        before = output.stat().st_mtime_ns
        write_atomic(output, b"complete publication")
        self.assertEqual(output.stat().st_mtime_ns, before)

    def test_virtual_member_paths_do_not_inspect_source_names(self):
        (self.root / "results").write_bytes(b"sample")
        (self.root / "alias").symlink_to(self.root / "results")
        data = results_zip(
            self.root, {"results/data.csv": "results", "alias/copy.csv": "results"}
        )
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertEqual(archive.read("results/data.csv"), b"sample")
        with self.assertRaises(ValueError):
            results_zip(self.root, {"safe.csv": "alias"})

    def test_empty_linked_publication_root_rejected(self):
        directory = self.root / "empty"
        directory.mkdir()
        linked = self.root / "link"
        linked.symlink_to(directory, target_is_directory=True)
        with self.assertRaises(ValueError):
            check_budget(
                linked, {}, max_file_bytes=10, max_site_bytes=10, inventory="directory"
            )

    def test_unsafe_archive_input_rejected(self):
        for members in [
            {"../escape": "results.csv"},
            {"result": "../escape"},
            {"result": "missing"},
        ]:
            with self.assertRaises(ValueError):
                results_zip(self.root, members)


if __name__ == "__main__":
    unittest.main()
