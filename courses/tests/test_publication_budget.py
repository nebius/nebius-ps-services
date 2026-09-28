"""Whole-root hosting budgets and no-write failure boundaries."""

import subprocess
import sys
from pathlib import Path

import pytest

import course_archives
import publication
from course_builder import build


@pytest.fixture
def repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("ignored\n")
    (tmp_path / "tracked").write_bytes(b"12")
    subprocess.run(["git", "-C", str(tmp_path), "add", "tracked"], check=True)
    (tmp_path / "untracked").write_bytes(b"123")
    (tmp_path / "ignored").write_bytes(b"x" * 200)
    return tmp_path


def budget(root, outputs, file=100, site=1000):
    return publication.check_budget(
        root, outputs, max_file_bytes=file, max_site_bytes=site
    )


def test_full_git_inventory_overlays_planned_bytes_once(repo):
    (repo / "courses").mkdir()
    (repo / "courses/index.html").write_bytes(b"old")
    report = budget(repo, {"courses/index.html": b"new-long", "new.html": b"ab"})
    assert report["total_bytes"] == len(b"ignored\n") + 2 + 3 + 8 + 2
    assert "ignored" not in publication.git_sizes(repo)
    (repo / "tracked").unlink()
    assert "tracked" not in publication.git_sizes(repo)


def test_exact_limits_and_one_byte_over(repo):
    sizes = publication.git_sizes(repo)
    total = sum(sizes.values())
    assert budget(repo, {}, file=max(sizes.values()), site=total)["headroom_bytes"] == 0
    with pytest.raises(ValueError, match="File limit"):
        budget(repo, {"new": b"123456789"}, file=8)
    with pytest.raises(ValueError, match="Site limit"):
        budget(repo, {}, site=total - 1)


def test_outside_courses_file_counts(repo):
    (repo / "another-project").mkdir()
    (repo / "another-project/large").write_bytes(b"x" * 101)
    with pytest.raises(ValueError, match="another-project/large"):
        budget(repo, {"courses/index.html": b"ok"})


@pytest.mark.parametrize(
    "name", ["../escape", "/absolute", "a/../b", "a\\b", "./file", "a:stream"]
)
def test_unsafe_planned_paths(repo, name):
    with pytest.raises(ValueError):
        budget(repo, {name: b"bad"})


def test_linked_inventory_and_incomplete_git_fail(repo, tmp_path, monkeypatch):
    (repo / "alias").symlink_to(repo / "tracked")
    with pytest.raises(ValueError, match="Symlinked"):
        budget(repo, {})
    (repo / "alias").unlink()
    monkeypatch.setattr(
        publication.subprocess,
        "check_output",
        lambda *a, **k: (_ for _ in ()).throw(OSError("missing git")),
    )
    with pytest.raises(ValueError, match="Cannot inventory"):
        budget(repo, {})


def test_gitlinks_fail_instead_of_undercounting(repo):
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "update-index",
            "--add",
            "--cacheinfo",
            "160000," + "a" * 40 + ",dependency",
        ],
        check=True,
    )
    with pytest.raises(ValueError, match="Submodule"):
        budget(repo, {})


@pytest.mark.parametrize("check", [False, True])
def test_builder_budget_failure_occurs_before_writes(repo, monkeypatch, check):
    root = repo / "courses"
    root.mkdir()
    output = root / "index.html"
    output.write_bytes(b"previous")
    before = (output.read_bytes(), output.stat().st_mtime_ns)
    monkeypatch.setattr(build, "ROOT", root)
    monkeypatch.setattr(
        build, "plan_outputs", lambda selected: {output: b"new contents"}
    )
    monkeypatch.setattr(build, "MAX_FILE_BYTES", 8)
    monkeypatch.setattr(
        sys, "argv", ["build_course_html.py"] + (["--check"] if check else [])
    )
    with pytest.raises(SystemExit, match="File limit"):
        build.main()
    assert before == (output.read_bytes(), output.stat().st_mtime_ns)
    assert not list(root.glob(".course-*"))


def test_export_archive_cap_is_enforced(monkeypatch):
    monkeypatch.setattr(course_archives, "MAX_FILE_BYTES", 1)
    with pytest.raises(ValueError, match="archive exceeds"):
        course_archives.archive_bytes({"results.csv": (b"value\n1\n", 0o644)})


def test_template_helper_matches_reusable_source():
    template = (
        Path(__file__).resolve().parents[2]
        / "skills/create-learning-course/assets/course-workspace-template/tools/publication.py"
    )
    assert template.read_bytes() == Path(publication.__file__).read_bytes()


@pytest.mark.parametrize(
    "outputs", [{"new": b"x", "new/child": b"y"}, {"tracked/child": b"y"}]
)
def test_conflicting_output_tree_fails_preflight(repo, outputs):
    before = {
        p: (p.read_bytes(), p.stat().st_mtime_ns) for p in repo.iterdir() if p.is_file()
    }
    with pytest.raises(ValueError, match="conflict|Non-directory"):
        budget(repo, outputs)
    assert before == {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in before}


def test_empty_directory_inventory_rejects_linked_root(tmp_path):
    directory = tmp_path / "empty"
    directory.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(directory, target_is_directory=True)
    with pytest.raises(ValueError, match="Symlinked"):
        publication.check_budget(
            linked, {}, max_file_bytes=10, max_site_bytes=10, inventory="directory"
        )


def test_zip_member_names_are_independent_of_source_filesystem(tmp_path):
    import io
    import zipfile

    (tmp_path / "results").write_bytes(b"count,value\n1,2\n")
    (tmp_path / "alias").symlink_to(tmp_path / "results")
    members = {"results/data.csv": "results", "alias/copy.csv": "results"}
    content = publication.results_zip(tmp_path, members)
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        assert set(archive.namelist()) == set(members)
        assert archive.read("results/data.csv") == (tmp_path / "results").read_bytes()
    with pytest.raises(ValueError, match="Symlinked"):
        publication.results_zip(tmp_path, {"safe.csv": "alias"})
