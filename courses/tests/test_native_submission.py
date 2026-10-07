"""Native dispatch owns exact paths and cleanup leaves other evidence alone."""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "native_remote", ROOT / "skills/run-labs/scripts/remote_job.py"
)
remote = importlib.util.module_from_spec(spec)
spec.loader.exec_module(remote)


def workspace(tmp_path):
    root = tmp_path.resolve() / "course space"
    root.mkdir()
    (root / "reference").mkdir()
    (root / "slurm").mkdir()
    (root / "reference/course.json").write_text(
        json.dumps({"labs": [{"path": "labs/01_example.py"}]})
    )
    (root / "slurm/01_example.sbatch").write_text("#!/bin/bash\n")
    (root / ".run-labs-workspace.json").write_text(
        json.dumps({"campaign": "fixture", "source_sha256": "a" * 64})
    )
    return root


def test_native_submission_preserves_argv_and_prepares_logs_before_scheduler(tmp_path):
    root = workspace(tmp_path)
    source = [
        "sbatch",
        "--nodes=2",
        "slurm/01_example.sbatch",
        "--workload",
        "large",
        "--note",
        "two words;$(literal)",
    ]
    argv = remote.native_submission(root, source, "rl-" + "a" * 24)
    assert argv[0:2] == ["sbatch", "--parsable"]
    assert argv[-len(source[1:]) :] == source[1:]
    assert f"--chdir={root}" in argv
    assert f"--output={root}/results/01_example/logs/%j.out" in argv
    for path in (root / "results").rglob("*"):
        assert path.is_dir() and path.stat().st_mode & 0o777 == 0o700
    assert not list((root / "results/01_example/jobs").iterdir())


@pytest.mark.parametrize(
    "defect", ["foreign", "traversal", "symlink", "public", "override", "old-wrapper"]
)
def test_invalid_submission_never_reaches_scheduler(tmp_path, defect):
    root = workspace(tmp_path)
    argv = ["sbatch", "slurm/01_example.sbatch"]
    if defect == "foreign":
        argv[1] = "slurm/02_foreign.sbatch"
    elif defect == "traversal":
        argv[1] = "slurm/../01_example.sbatch"
    elif defect == "symlink":
        (root / "results").symlink_to(tmp_path)
    elif defect == "public":
        (root / "results").mkdir(mode=0o755)
        (root / "results").chmod(0o755)
    elif defect == "override":
        argv.insert(1, "--output=/tmp/other")
    else:
        argv = ["python3", "tools/submit_lab.py"]
    with pytest.raises(ValueError):
        remote.native_submission(root, argv, "rl-" + "a" * 24)


def test_cleanup_removes_only_successful_dispatched_job(tmp_path, monkeypatch):
    root = workspace(tmp_path)
    paths = [
        "results/01_example/jobs/123/results/run.json",
        "results/01_example/logs/123.out",
        "results/01_example/logs/123.err",
        "results/01_example/jobs/456/results/other.json",
        "results/history/original.nsys-rep",
        "results/old.json",
    ]
    for name in paths:
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("keep evidence")
    request = {
        "root": str(root),
        "campaign": "fixture",
        "source_sha256": "a" * 64,
        "action": "cleanup",
        "lab": "01_example",
        "jobs": [{"job": 123, "name": "owned"}],
    }
    monkeypatch.setattr(
        remote, "job_rows", lambda *args: [{"state": "FAILED", "exit_code": "1:0"}]
    )
    with pytest.raises(ValueError, match="completed successfully"):
        remote.main(request)
    assert all((root / name).exists() for name in paths)
    monkeypatch.setattr(
        remote, "job_rows", lambda *args: [{"state": "COMPLETED", "exit_code": "0:0"}]
    )
    assert remote.main(request) == {"cleaned": True}
    assert all(not (root / name).exists() for name in paths[:3])
    assert all((root / name).read_text() == "keep evidence" for name in paths[3:])
