"""Historical organization copies evidence and never adopts it as a new run."""

import hashlib
import json

import pytest
from test_course_preparation import setup, course


def historical(tmp_path):
    root = course(tmp_path)
    results = root / "results"
    results.mkdir(mode=0o700)
    original = results / "original.json"
    original.write_text(
        json.dumps(
            {
                "lab_id": "01_example",
                "experiment": {"slurm_job_id": 123},
                "profile": "small",
            }
        )
    )
    (results / "unknown.nsys-rep").write_bytes(b"old report")
    return root, original


def test_history_is_verified_copy_only_and_idempotent(tmp_path):
    root, original = historical(tmp_path)
    before = original.read_bytes()
    report = setup.organize_history(root)
    assert report["unresolved"] == ["unknown.nsys-rep"]
    (row,) = report["copied"]
    copy = root / "results" / row["copy"]
    assert copy.read_bytes() == original.read_bytes() == before
    assert row["sha256"] == hashlib.sha256(before).hexdigest()
    assert copy.stat().st_mode & 0o777 == 0o600
    assert setup.organize_history(root) == report
    assert (root / "results/unknown.nsys-rep").read_bytes() == b"old report"


@pytest.mark.parametrize("defect", ["collision", "symlink"])
def test_history_rejects_conflicting_destinations_without_changing_original(
    tmp_path, defect
):
    root, original = historical(tmp_path)
    before = original.read_bytes()
    destination = root / "results/history/01_example/123/original.json"
    for parent in reversed(destination.parents):
        if parent.is_relative_to(root / "results"):
            parent.mkdir(mode=0o700, exist_ok=True)
    if defect == "collision":
        destination.write_bytes(b"keep me")
    else:
        destination.symlink_to(original)
    with pytest.raises(ValueError):
        setup.organize_history(root)
    assert original.read_bytes() == before
    if defect == "collision":
        assert destination.read_bytes() == b"keep me"


def test_new_job_outputs_are_not_reorganized(tmp_path):
    root, _ = historical(tmp_path)
    path = root / "results/01_example/jobs/456/results/fresh.json"
    path.parent.mkdir(parents=True)
    path.write_text("new")
    result = setup.organize_history(root)
    assert all("456" not in row["source"] for row in result["copied"])
    assert path.read_text() == "new"


def test_unattributed_binary_history_is_not_read(tmp_path, monkeypatch):
    from pathlib import Path

    root, _ = historical(tmp_path)
    unknown = root / "results/unknown.nsys-rep"
    original_open = Path.open

    def guarded_open(path, *args, **kwargs):
        if path == unknown:
            raise AssertionError("Unattributed binary reports need no content read")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    assert setup.organize_history(root)["unresolved"] == ["unknown.nsys-rep"]


def test_attributed_history_streams_reports_and_verifies_copies(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from pathlib import Path

    root = course(tmp_path)
    source = root / "results/01_example/profiles/job-123-rank-0.nsys-rep"
    source.parent.mkdir(parents=True, mode=0o700)
    payload = b"native-report" * 200_000
    source.write_bytes(payload)
    original_open = Path.open
    reads = []

    class BoundedReader:
        def __init__(self, stream):
            self.stream = stream

        def read(self, size=-1):
            assert 0 < size <= 1024 * 1024, "History must use bounded reads"
            reads.append(size)
            return self.stream.read(size)

        def __getattr__(self, name):
            return getattr(self.stream, name)

    @contextmanager
    def guarded_open(path, mode="r", *args, **kwargs):
        with original_open(path, mode, *args, **kwargs) as stream:
            yield BoundedReader(stream) if path.suffix == ".nsys-rep" and "r" in mode else stream

    monkeypatch.setattr(Path, "open", guarded_open)
    report = setup.organize_history(root)
    assert setup.organize_history(root) == report
    (row,) = report["copied"]
    assert row["sha256"] == hashlib.sha256(payload).hexdigest()
    assert len(reads) > 2
    with original_open(source, "rb") as stream:
        assert stream.read() == payload
    with original_open(root / "results" / row["copy"], "rb") as stream:
        assert stream.read() == payload


def test_oversized_history_json_without_filename_identity_stays_unresolved(tmp_path, monkeypatch):
    from pathlib import Path

    root = course(tmp_path)
    results = root / "results"
    results.mkdir(mode=0o700)
    source = results / "oversized.json"
    with source.open("wb") as stream:
        stream.truncate(9 * 1024 * 1024)
    original_open = Path.open

    def guarded_open(path, *args, **kwargs):
        if path == source:
            raise AssertionError("Oversized metadata needs no content read")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    assert setup.organize_history(root) == {"schema": "course-history/v1", "copied": [], "unresolved": ["oversized.json"]}


def test_changed_metadata_cannot_attribute_a_copy_to_the_previous_job(tmp_path, monkeypatch):
    root, original = historical(tmp_path)
    digest = setup.history_digest
    changed = None

    def change_before_streaming(source, destination=None):
        nonlocal changed
        if source == original and changed is None:
            data = json.loads(original.read_text())
            data["experiment"]["slurm_job_id"] = 456
            changed = json.dumps(data).encode()
            original.write_bytes(changed)
        return digest(source, destination)

    monkeypatch.setattr(setup, "history_digest", change_before_streaming)
    with pytest.raises(ValueError, match="Evidence changed"):
        setup.organize_history(root)
    assert original.read_bytes() == changed
    assert not list((root / "results/history").glob("organization-*.json"))
