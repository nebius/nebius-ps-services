"""One-time local preparation owns scheduler log paths before submission."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "local_course_setup", ROOT / "tools/course_setup.py"
)
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


def course(root, name="example", labs=None, **extra):
    path = root / name
    (path / "reference").mkdir(parents=True)
    metadata = {
        "slug": name,
        "labs": labs if labs is not None else [{"path": "labs/01_example.py"}],
        **extra,
    }
    (path / "reference/course.json").write_text(json.dumps(metadata))
    return path


def test_catalog_prepares_every_declared_lab_and_preserves_results(tmp_path):
    expected = {}
    for metadata in ROOT.glob("*/reference/course.json"):
        name = metadata.parent.parent.name
        course(tmp_path, name, **json.loads(metadata.read_text()))
        if json.loads(metadata.read_text()).get("profile") not in ("text-only", "reference-only"):
            expected[name] = len(json.loads(metadata.read_text())["labs"])
    assert sum(expected.values()) == 110
    assert setup.prepare(courses_root=tmp_path) == expected
    result = tmp_path / "gpu-fundamentals/results/existing.json"
    result.write_text("preserved")
    assert setup.prepare(courses_root=tmp_path) == expected
    assert result.read_text() == "preserved"
    for name in expected:
        metadata = json.loads((tmp_path / name / "reference/course.json").read_text())
        for row in metadata["labs"]:
            for leaf in ("logs", "jobs"):
                path = tmp_path / name / "results" / Path(row["path"]).stem / leaf
                assert path.stat().st_mode & 0o777 == 0o700
                assert path.stat().st_uid == os.geteuid()
    assert not (tmp_path / "soperator/results").exists()


@pytest.mark.parametrize(
    "relative",
    [
        "results",
        "results/01_example",
        "results/01_example/logs",
        "results/01_example/jobs",
    ],
)
@pytest.mark.parametrize("defect", ["public", "file", "symlink", "dangling"])
def test_preparation_rejects_unsafe_existing_components(tmp_path, relative, defect):
    path = course(tmp_path)
    bad = path / relative
    for ancestor in reversed(bad.parents):
        if ancestor.is_relative_to(path) and ancestor != path:
            ancestor.mkdir(mode=0o700, exist_ok=True)
    if defect == "public":
        bad.mkdir(mode=0o755)
        bad.chmod(0o755)
    elif defect == "file":
        bad.write_text("keep")
    else:
        destination = tmp_path / "target"
        if defect == "symlink":
            destination.mkdir(mode=0o700)
        bad.symlink_to(destination)
    before = bad.lstat()
    with pytest.raises((ValueError, OSError)):
        setup.prepare(course_root=path)
    assert bad.lstat().st_mode == before.st_mode
    if defect == "file":
        assert bad.read_text() == "keep"
    if defect in {"symlink", "dangling"}:
        assert not (tmp_path / "target/logs").exists()


@pytest.mark.parametrize(
    "source",
    ["../outside.py", "labs/../outside.py", "/labs/01_bad.py", "labs/link/01_bad.py"],
)
def test_unsafe_inventory_is_rejected_before_writes(tmp_path, source):
    first = course(tmp_path, "a")
    course(tmp_path, "b", labs=[{"path": source}])
    with pytest.raises(ValueError, match="Unsafe"):
        setup.prepare(courses_root=tmp_path)
    assert not (first / "results").exists()


def test_foreign_ownership_is_not_repaired(tmp_path, monkeypatch):
    path = course(tmp_path)
    (path / "results").mkdir(mode=0o700)
    owner = os.geteuid()
    monkeypatch.setattr(setup.os, "geteuid", lambda: owner + 1)
    with pytest.raises(ValueError, match="owned"):
        setup.prepare(course_root=path)
    assert (path / "results").stat().st_uid == owner


def test_prepare_and_help_need_no_site_packages(tmp_path):
    path = course(tmp_path)
    for args in (
        ["--help"],
        ["prepare", "--help"],
        ["prepare", "--course-root", str(path)],
    ):
        result = subprocess.run(
            check=False,
            args=[
                sys.executable,
                "-I",
                "-S",
                str(ROOT / "tools/course_setup.py"),
                *args,
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
    assert (path / "results/01_example/logs").is_dir()


def test_metadata_symlink_is_rejected(tmp_path):
    path = course(tmp_path)
    metadata = path / "reference/course.json"
    saved = tmp_path / "inventory.json"
    metadata.rename(saved)
    metadata.symlink_to(saved)
    with pytest.raises(OSError):
        setup.prepare(course_root=path)
    assert not (path / "results").exists()


@pytest.mark.parametrize("document", ["[]", "null", '{"labs": []}'])
def test_malformed_metadata_fails_cleanly_before_any_write(tmp_path, document):
    good = course(tmp_path, "a")
    bad = course(tmp_path, "b")
    (bad / "reference/course.json").write_text(document)
    result = subprocess.run(
        check=False,
        args=[
            sys.executable,
            "-I",
            "-S",
            str(ROOT / "tools/course_setup.py"),
            "prepare",
            "--courses-root",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 2
    assert "Traceback" not in result.stderr
    assert not (good / "results").exists()


def test_fifo_metadata_cannot_hang_preparation(tmp_path):
    path = course(tmp_path)
    metadata = path / "reference/course.json"
    metadata.unlink()
    os.mkfifo(metadata)
    result = subprocess.run(
        check=False,
        args=[
            sys.executable,
            "-I",
            "-S",
            str(ROOT / "tools/course_setup.py"),
            "prepare",
            "--course-root",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 2
    assert "regular file" in result.stderr
    assert not (path / "results").exists()


def test_unsafe_later_course_prevents_partial_catalog_preparation(tmp_path):
    first = course(tmp_path, "a")
    last = course(tmp_path, "z")
    (last / "results").mkdir(mode=0o755)
    with pytest.raises(ValueError):
        setup.prepare(courses_root=tmp_path)
    assert not (first / "results").exists()
    assert not (tmp_path / ".runtime").exists()
