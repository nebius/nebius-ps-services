"""Copied courses must prepare the same lab paths without repository imports."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from course_bootstrap import catalog, selection
from sync_course_tools import COURSES

ROOT = Path(__file__).resolve().parents[1]


def copy_course(parent, name):
    destination = parent / name
    for folder in ("tools", "reference", "slurm"):
        shutil.copytree(
            ROOT / name / folder,
            destination / folder,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    return destination


@pytest.fixture(scope="module", params=COURSES)
def standalone_course(tmp_path_factory, request):
    return copy_course(tmp_path_factory.mktemp("standalone-course"), request.param)


@pytest.mark.parametrize("group", selection.GROUPS)
def test_isolated_course_supports_every_preparation_plan(
    standalone_course, group, tmp_path
):
    script = f"{group}-lab-setup.py"
    process = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-B",
            str(standalone_course / "tools" / script),
            "--plan",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert process.returncode == 0, process.stderr
    expected = selection.resolve(
        group,
        {standalone_course.name: ROOT / standalone_course.name},
        catalog.load_catalog(),
    )
    assert json.loads(process.stdout) == expected
    assert not list(tmp_path.iterdir())
    assert not (standalone_course / "results").exists()
    assert not (standalone_course / ".runtime").exists()


def test_standalone_setup_and_campaign_copy_share_publication_authority(
    standalone_course, tmp_path,
):
    standalone_course = copy_course(tmp_path / "installation", standalone_course.name)
    settings = standalone_course / ".course-environment.sh"
    settings.write_text(
        "export COURSE_WORKSPACE=learner\n"
        "export COURSE_PUSHGATEWAY=http://127.0.0.1:9091\n"
        "export COURSE_METRICS_URL=http://127.0.0.1:8428\n"
    )
    settings.chmod(0o600)
    campaign = tmp_path / "campaign" / standalone_course.name
    (campaign / "tools").mkdir(parents=True)
    (campaign / "tools/regular-lab-setup.py").touch()
    (campaign / "reference").mkdir()
    shutil.copy2(standalone_course / "reference/course.json", campaign / "reference/course.json")
    binding = campaign / ".course-runtime-source.json"
    binding.write_text(json.dumps({"schema": "course-runtime-source/v1", "root": str(standalone_course)}))
    binding.chmod(0o600)
    program = """
import sys
from pathlib import Path
root, campaign = map(Path, sys.argv[1:])
sys.path.insert(0, str(root / 'tools'))
from course_bootstrap import controller
controller.ubuntu = lambda: {}
controller.capacity = lambda: []
controller.install_packages = lambda *args, **kwargs: None
controller.setup(
    root / 'tools/regular-lab-setup.py',
    lambda **kwargs: (root / '.runtime').mkdir(mode=0o700, exist_ok=True),
    plan={'group': 'regular', 'runtimes': []},
)
from course_publication_state import authority
directory, identity = authority(root)
assert identity['binding']['root'] == str(root)
assert authority(campaign) == (directory, identity)
assert not (campaign / '.runtime').exists()
"""
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", program, str(standalone_course), str(campaign)],
        cwd=tmp_path, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "publication initialized" in result.stdout


@pytest.mark.parametrize("name", COURSES)
def test_copied_preparation_repairs_every_launcher_output_parent(tmp_path, name):
    copied = copy_course(tmp_path, name)
    metadata = json.loads((copied / "reference/course.json").read_text())
    labs = {Path(row["path"]).stem for row in metadata["labs"]}
    execution = json.loads((copied / "reference/execution.json").read_text())
    assert set(execution["labs"]) == labs
    historical = {}
    for lab in labs:
        for relative in ("results", f"results/{lab}", f"results/{lab}/logs"):
            path = copied / relative
            path.mkdir(exist_ok=True)
            path.chmod(0o755)
        log = copied / "results" / lab / "logs/previous.err"
        log.write_bytes(b"preserved earlier job output\n")
        historical[log] = (log.read_bytes(), log.stat().st_mode)

    # Invoke the copied internal directory owner without installing any runtimes.
    command = [
        sys.executable,
        "-I",
        "-S",
        "-B",
        "-c",
        (
            "import json,sys; from pathlib import Path; "
            "sys.path.insert(0, str(Path(sys.argv[1]) / 'tools')); "
            "from course_bootstrap.support import prepare; "
            "print(json.dumps(prepare(course_root=Path(sys.argv[1]))))"
        ),
        str(copied),
    ]
    for _ in range(2):
        process = subprocess.run(
            command,
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
        assert process.returncode == 0, process.stderr
        assert json.loads(process.stdout) == {name: len(labs)}

    assert {path.name for path in (copied / "results").iterdir()} == labs
    for lab in labs:
        source = (copied / "slurm" / f"{lab}.sbatch").read_text()
        assert f'--lab {lab} --launcher "$0" -- "$@"' in source
        for option, suffix in (("output", "out"), ("error", "err")):
            directive = f"#SBATCH --{option}=results/{lab}/logs/%j.{suffix}"
            assert directive in source.splitlines()
        for relative in (
            "results",
            f"results/{lab}",
            f"results/{lab}/logs",
            f"results/{lab}/jobs",
        ):
            path = copied / relative
            assert not path.is_symlink()
            assert path.stat().st_mode & 0o7777 == 0o700
            assert path.stat().st_uid == os.geteuid()
        assert not list((copied / "results" / lab / "jobs").iterdir())
    assert all(
        (path.read_bytes(), path.stat().st_mode) == before
        for path, before in historical.items()
    )


@pytest.mark.parametrize("defect", ["missing", "stale"])
def test_aggregate_validation_rejects_copied_helper_drift(tmp_path, defect):
    shutil.copytree(
        ROOT / "tools",
        tmp_path / "tools",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    generated = subprocess.run(
        [sys.executable, "-B", str(tmp_path / "tools/sync_course_tools.py")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert generated.returncode == 0, generated.stderr
    helper = tmp_path / "gpu-fundamentals/tools/course_execution.py"
    if defect == "missing":
        helper.unlink()
    else:
        helper.write_text("# Stale copied helper\n")
    process = subprocess.run(
        [sys.executable, "-B", str(tmp_path / "tools/validate_all_courses.py")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert process.returncode != 0
    assert (
        "Stale standalone helper: gpu-fundamentals/tools/course_execution.py"
        in process.stderr
    )
