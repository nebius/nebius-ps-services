"""Whole-root hosting budgets and no-write failure boundaries."""

import os
import select
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
    with pytest.raises(ValueError, match=r"File limit.*over by 0\.000001 MB"):
        budget(repo, {"new": b"123456789"}, file=8)
    with pytest.raises(ValueError, match=r"Site limit.*over by 0\.000001 MB"):
        budget(repo, {}, site=total - 1)


@pytest.mark.parametrize(
    ("total", "headroom", "expected_total", "expected_headroom"),
    [
        (691364502, 308635498, "691.36 MB", "308.64 MB"),
        (1000000000, 0, "1,000.00 MB", "0.00 MB"),
    ],
)
def test_publication_summary_uses_decimal_mb(
    monkeypatch, capsys, total, headroom, expected_total, expected_headroom
):
    largest = "courses/advanced-gpu-communication/reference/advanced-gpu-communication-lab-results.zip"
    report = {
        "total_bytes": total,
        "largest_path": largest,
        "largest_bytes": 100140877,
        "headroom_bytes": headroom,
        "max_file_bytes": 104857600,
        "max_site_bytes": 1000000000,
    }
    monkeypatch.setattr(build, "check_budget", lambda *args, **kwargs: report)
    assert build.publication_preflight({}) is report
    captured = capsys.readouterr()
    assert captured.out == (
        f"Publication estimate: {expected_total}; largest {largest} "
        f"(100.14 MB); site headroom {expected_headroom}; "
        "limits 104.86 MB/file, 1,000.00 MB/site\n"
    )
    assert captured.err == ""


def test_file_and_site_errors_show_sizes_and_precise_excess(repo, monkeypatch):
    monkeypatch.setattr(publication, "git_sizes", lambda _: {"large.zip": 104857601})
    with pytest.raises(ValueError) as error:
        budget(repo, {}, file=104857600, site=None)
    assert str(error.value) == (
        "File limit 104.86 MB exceeded: large.zip: 104.86 MB (over by 0.000001 MB)"
    )
    monkeypatch.setattr(publication, "git_sizes", lambda _: {"large.zip": 1000000001})
    with pytest.raises(ValueError) as error:
        budget(repo, {}, file=None, site=1000000000)
    assert str(error.value) == (
        "Site limit 1,000.00 MB exceeded: 1,000.00 MB (over by 0.000001 MB)"
    )


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
@pytest.mark.parametrize("failure", ["file", "site", "archive"])
def test_builder_budget_failure_occurs_before_writes(repo, monkeypatch, check, failure):
    root = repo / "courses"
    root.mkdir()
    output = root / "index.html"
    output.write_bytes(b"previous")
    before = (output.read_bytes(), output.stat().st_mtime_ns)
    monkeypatch.setattr(build, "ROOT", root)
    monkeypatch.setattr(
        build, "plan_outputs", lambda selected: {output: b"new contents"}
    )
    if failure == "file":
        monkeypatch.setattr(build, "MAX_FILE_BYTES", 8)
    elif failure == "site":
        monkeypatch.setattr(
            build,
            "check_budget",
            lambda root, outputs, **kwargs: budget(root, outputs, site=1),
        )
    else:
        monkeypatch.setattr(course_archives, "MAX_FILE_BYTES", 1)
        monkeypatch.setattr(
            build,
            "plan_outputs",
            lambda selected: {output: course_archives.archive_bytes({})},
        )
    monkeypatch.setattr(
        sys, "argv", ["build_course_html.py"] + (["--check"] if check else [])
    )
    with pytest.raises(SystemExit, match="File limit|Site limit|archive exceeds"):
        build.main()
    assert before == (output.read_bytes(), output.stat().st_mtime_ns)
    assert not list(root.glob(".course-*"))


def test_export_archive_cap_is_enforced(monkeypatch):
    monkeypatch.setattr(course_archives, "MAX_FILE_BYTES", 1)
    with pytest.raises(ValueError, match="archive exceeds"):
        course_archives.archive_bytes({"results.csv": (b"value\n1\n", 0o644)})


@pytest.mark.parametrize("assembler", ["course", "portable"])
def test_archive_exact_limit_and_one_byte_excess(tmp_path, monkeypatch, assembler):
    content = b"value\n1\n"
    (tmp_path / "results.csv").write_bytes(content)
    if assembler == "course":

        def assemble(limit):
            monkeypatch.setattr(course_archives, "MAX_FILE_BYTES", limit)
            return course_archives.archive_bytes({"results.csv": (content, 0o644)})
    else:

        def assemble(limit):
            return publication.results_zip(
                tmp_path, {"results.csv": "results.csv"}, max_file_bytes=limit
            )

    size = len(assemble(104857600))
    assert len(assemble(size)) == size
    with pytest.raises(ValueError) as error:
        assemble(size - 1)
    assert str(error.value) == (
        f"Results archive exceeds {(size - 1) / 1000000:,.2f} MB: "
        f"{size / 1000000:,.2f} MB (over by 0.000001 MB)"
    )


@pytest.mark.parametrize("check", [False, True])
@pytest.mark.parametrize("failure", ["file", "site", "archive"])
@pytest.mark.parametrize(
    ("stdout_tty", "stderr_tty", "term", "no_color", "colored"),
    [
        (False, True, "xterm", None, True),
        (True, False, "xterm", None, False),
        (False, True, "dumb", None, False),
        (False, True, "xterm", "", False),
        (False, True, "xterm", "1", False),
    ],
)
def test_builder_limit_diagnostics_respect_terminal_stream(
    repo, check, failure, stdout_tty, stderr_tty, term, no_color, colored
):
    program = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv.pop(1))
from course_builder import build
import course_archives
import publication
build.ROOT = Path(sys.argv.pop(1)) / 'courses'
failure = sys.argv.pop(1)
build.plan_outputs = lambda _: {build.ROOT / 'index.html': b'new contents'}
if failure == 'file':
    build.MAX_FILE_BYTES = 8
elif failure == 'site':
    build.check_budget = lambda root, outputs, **kwargs: publication.check_budget(
        root, outputs, max_file_bytes=None, max_site_bytes=1)
else:
    course_archives.MAX_FILE_BYTES = 1
    build.plan_outputs = lambda _: course_archives.archive_bytes({})
build.main()
"""
    env = dict(os.environ, TERM=term, PYTHONDONTWRITEBYTECODE="1")
    env.pop("NO_COLOR", None)
    if no_color is not None:
        env["NO_COLOR"] = no_color
    master, slave = os.openpty()
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                "-c",
                program,
                str(Path(publication.__file__).parent),
                str(repo),
                failure,
                *(["--check"] if check else []),
            ],
            stdout=slave if stdout_tty else subprocess.PIPE,
            stderr=slave if stderr_tty else subprocess.PIPE,
            env=env,
            timeout=15,
        )
        if stderr_tty:
            assert select.select([master], [], [], 5)[0], "missing terminal diagnostic"
            diagnostic = os.read(master, 65536).decode()
            assert result.stdout == b""
        else:
            diagnostic = result.stderr.decode()
        assert result.returncode == 1
        assert {
            "file": "File limit",
            "site": "Site limit",
            "archive": "archive exceeds",
        }[failure] in diagnostic
        assert " MB" in diagnostic and "over by " in diagnostic
        assert "Traceback" not in diagnostic
        assert "built " not in diagnostic and "current " not in diagnostic
        if colored:
            assert diagnostic.startswith("\033[31m")
            assert diagnostic.rstrip().endswith("\033[0m")
        else:
            assert "\033[" not in diagnostic
    finally:
        os.close(slave)
        os.close(master)


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
