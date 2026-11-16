"""Retention never upgrades partial evidence or deletes an unreadable report."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "worktree/scripts"))
import test_workflow_implement_statistics as fixture
import workflow_statistics as statistics
from workflow_state import read, write
from implement_statistics import archive, reports, require_complete, StatisticsError


class ReportingEvidenceTests(unittest.TestCase):
    setUp = fixture.ImplementStatisticsTests.setUp
    tearDown = fixture.ImplementStatisticsTests.tearDown
    settle = fixture.ImplementStatisticsTests.settle
    inventory = fixture.ImplementStatisticsTests.inventory

    def test_partial_report_survives_archive_without_qualifying_completion(self):
        self.inventory()
        initial = (self.run_dir / "statistics/initial.json").read_bytes()
        write(self.run_dir / "run.json", {**read(self.run_dir / "run.json"), "status": "cancelled"})
        statistics.record(self.run_dir, "cancel")
        destination = self.root / "retained"
        result = archive(self.home, destination)
        self.assertEqual(len(result["reports"]), 3)
        copies = list(destination.glob("*/initial.json"))
        self.assertEqual(len(copies), 1)
        self.assertEqual(copies[0].read_bytes(), initial)
        before = {p: p.read_bytes() for p in destination.rglob("*") if p.is_file()}
        archive(self.home, destination)
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        with self.assertRaises(StatisticsError):
            require_complete(self.run_dir)

    def test_earlier_terminal_report_cannot_replace_the_final_artifact(self):
        self.inventory()
        for status in ("blocked", "cancelled"):
            write(self.run_dir / "run.json", {**read(self.run_dir / "run.json"), "status": status})
            statistics.record(self.run_dir, status)
        current = read(self.run_dir / "statistics/current.json")
        final = self.run_dir / "statistics/terminal" / (current["observation_sha256"] + ".json")
        final.unlink()
        final.with_suffix(".md").unlink()
        self.assertEqual(len(list(final.parent.glob("*.json"))), 1)
        with self.assertRaisesRegex(StatisticsError, "Latest immutable terminal"):
            require_complete(self.run_dir)

    def test_modified_report_or_history_cannot_qualify_or_archive(self):
        self.inventory()
        path = self.run_dir / "statistics/current.json"
        original = read(path)
        write(path, {**original, "status": "done"})
        with self.assertRaises(StatisticsError):
            reports(self.run_dir)
        write(path, original)
        event = next((self.run_dir / "statistics/events").glob("*.json"))
        write(event, {**read(event), "sha256": "0" * 64})
        with self.assertRaises(RuntimeError):
            archive(self.home, self.root / "retained")

    def test_symlinked_retention_destination_is_rejected(self):
        self.inventory()
        target = self.root / "outside"
        target.mkdir()
        destination = self.root / "retained"
        destination.symlink_to(target, target_is_directory=True)
        with self.assertRaises(RuntimeError):
            archive(self.home, destination)
        self.assertEqual(list(target.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
