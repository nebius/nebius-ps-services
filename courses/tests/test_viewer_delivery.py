"""Report delivery uses explicit storage fixtures, never the developer's install."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import time

import pytest

import course_viewer
from course_execution import capabilities, select
from course_job import required_seconds, time_limit_seconds
import native_job_fixtures as fixtures

LAB = "01_cpu_gpu_crossover"


def document(root):
    return json.loads((root / f"results/{LAB}/jobs/123/result.json").read_text())


def patch_worker(monkeypatch, source):
    original = fixtures.viewer_fixture

    def patched(root):
        descriptor = original(root)
        path = root / "tools/course_viewer.py"
        path.write_text(
            path.read_text().replace(
                'if __name__ == "__main__":', source + '\nif __name__ == "__main__":'
            )
        )
        return descriptor

    monkeypatch.setattr(fixtures, "viewer_fixture", patched)


@pytest.fixture
def installed(tmp_path):
    (tmp_path / "tools").mkdir()
    descriptor = fixtures.viewer_fixture(tmp_path)
    spec = importlib.util.spec_from_file_location(
        "fixture_viewer", tmp_path / "tools/course_viewer.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, descriptor


def test_successful_job_exposes_exact_report_sets_and_preserves_private_originals(
    tmp_path,
):
    result = fixtures.run_job(tmp_path, "gpu-fundamentals", LAB, "all")
    assert result.returncode == 0, result.stderr
    job = document(tmp_path)
    assert job["viewer_delivery"]["status"] == "ready"
    for row in job["stages"]:
        delivery = row["viewer_delivery"]
        assert delivery["status"] == "ready"
        manifest = delivery["manifest"]
        destination = tmp_path / "viewer-reports" / manifest["run"] / row["id"]
        assert json.loads((destination / "manifest.json").read_text()) == manifest
        for original, report, viewer in zip(
            row["native_reports"],
            manifest["reports"],
            delivery["viewer_paths"],
            strict=True,
        ):
            private = tmp_path / f"results/{LAB}/jobs/123" / original
            public = destination / report["path"]
            assert private.read_bytes() == public.read_bytes()
            assert hashlib.sha256(public.read_bytes()).hexdigest() == report["sha256"]
            assert public.stat().st_mode & 0o777 == 0o644
            assert viewer in result.stdout
            assert str(tmp_path) not in json.dumps(manifest)
        assert destination.stat().st_mode & 0o777 == 0o755
        assert (tmp_path / f"results/{LAB}/jobs/123").stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize("error", ["ENOSPC", "EROFS"])
def test_delivery_failure_keeps_capture_success_and_runs_next_profiler(
    tmp_path, monkeypatch, error
):
    patch_worker(
        monkeypatch,
        """
import errno
_original_worker = worker
def worker(request):
    if request['action'] == 'publish' and request['profiler'] == 'nsys':
        def failed_write(*args):
            raise OSError(errno.FIXTURE_ERROR, 'fixture storage write failure')
        os.write = failed_write
    return _original_worker(request)
""".replace("FIXTURE_ERROR", error),
    )
    result = fixtures.run_job(tmp_path, "gpu-fundamentals", LAB, "all")
    assert result.returncode != 0
    job = document(tmp_path)
    assert job["status"] == "failed"
    assert [s["status"] for s in job["stages"]] == ["completed", "completed"]
    assert [s["exit_code"] for s in job["stages"]] == [0, 0]
    assert [s["viewer_delivery"]["status"] for s in job["stages"]] == [
        "failed",
        "ready",
    ]
    assert "publication" in job
    destination = tmp_path / "viewer-reports" / job["viewer_delivery"]["run"]
    assert not (destination / "nsys").exists()
    assert all(
        path.stat().st_mode & 0o777 == 0o700 for path in destination.glob(".staging-*")
    )


def test_lost_commit_acknowledgement_is_reconciled_by_independent_readback(
    tmp_path, monkeypatch
):
    patch_worker(
        monkeypatch,
        """
_original_worker, _original_emit = worker, emit
def worker(request):
    global fixture_action
    fixture_action = request['action']
    return _original_worker(request)
def emit(value):
    if fixture_action == 'publish' and value['status'] == 'ready':
        os._exit(7)
    return _original_emit(value)
""",
    )
    result = fixtures.run_job(tmp_path, "gpu-fundamentals", LAB, "nsys")
    assert result.returncode == 0, result.stderr
    assert document(tmp_path)["stages"][0]["viewer_delivery"]["status"] == "ready"


def test_unconfirmed_writer_stops_later_captures(tmp_path, monkeypatch):
    patch_worker(
        monkeypatch,
        """
def deliver(*args, **kwargs):
    return {'status': 'unconfirmed', 'quiescent': False, 'reason': 'fixture blocked writer'}
""",
    )
    result = fixtures.run_job(tmp_path, "gpu-fundamentals", LAB, "all")
    assert result.returncode != 0
    job = document(tmp_path)
    assert [s["status"] for s in job["stages"]] == ["completed", "unstarted"]
    assert job["viewer_delivery"]["status"] == "unconfirmed"
    assert len((tmp_path / "application-history.jsonl").read_text().splitlines()) == 1


@pytest.mark.parametrize("mode", ["none", "all"])
def test_normal_or_fully_skipped_job_needs_no_viewer_installation(
    tmp_path, monkeypatch, mode
):
    original = fixtures.viewer_fixture

    def missing(root):
        path = original(root)
        path.unlink()
        return path

    monkeypatch.setattr(fixtures, "viewer_fixture", missing)
    result = fixtures.run_job(
        tmp_path,
        "gpu-fundamentals",
        "10_compatibility_stack" if mode == "all" else LAB,
        "" if mode == "none" else mode,
    )
    assert result.returncode == 0, result.stderr


def test_missing_installation_stops_before_capture(tmp_path, monkeypatch):
    original = fixtures.viewer_fixture

    def missing(root):
        path = original(root)
        path.unlink()
        return path

    monkeypatch.setattr(fixtures, "viewer_fixture", missing)
    result = fixtures.run_job(tmp_path, "gpu-fundamentals", LAB, "all")
    assert result.returncode != 0
    assert not (tmp_path / "application-history.jsonl").exists()
    assert document(tmp_path)["viewer_delivery"]["status"] == "failed"


@pytest.mark.parametrize(
    "mutation",
    [
        "token",
        "mount",
        "type",
        "permissions",
        "symlink",
        "marker-mount",
        "installation",
    ],
)
def test_installed_binding_rejects_drift(installed, mutation, monkeypatch):
    module, descriptor = installed
    value = json.loads(descriptor.read_text())
    reports = Path(value["producer_path"])
    marker = reports / ".cxcli-nsight/binding.json"
    if mutation in {"token", "mount", "type", "installation"}:
        key, replacement = {
            "token": ("token", "2" * 64),
            "mount": ("mount_point", "/wrong"),
            "type": ("filesystem_type", "wrong"),
            "installation": ("installation", {}),
        }[mutation]
        value[key] = replacement
        descriptor.write_text(json.dumps(value))
    elif mutation == "permissions":
        reports.chmod(0o777)
    elif mutation == "symlink":
        data = marker.read_bytes()
        marker.unlink()
        replacement = reports / "other.json"
        replacement.write_bytes(data)
        marker.symlink_to(replacement)
    else:
        original = module.mount_identity
        count = 0

        def moved(fd):
            nonlocal count
            count += 1
            return {**original(fd), "id": str(count)}

        monkeypatch.setattr(module, "mount_identity", moved)
    with pytest.raises((ValueError, OSError)):
        module.binding()


def test_removed_mount_cannot_fall_back_to_an_ordinary_directory(
    installed, monkeypatch
):
    module, _ = installed
    monkeypatch.setattr(
        module,
        "mount_identity",
        lambda fd: {
            "id": "rootfs",
            "device": "rootfs",
            "point": "/",
            "type": "fixture",
        },
    )
    with pytest.raises(ValueError, match="installed mount"):
        module.binding()


def test_nested_reports_exact_retry_and_conflict(installed, tmp_path):
    module, _ = installed
    binding = module.preflight()
    stage = tmp_path / "stage"
    source = stage / "profiles/rank-0"
    source.mkdir(parents=True)
    report = source / "sample.nsys-rep"
    report.write_bytes(b"fixture native bytes")
    row = {
        "id": "nsys",
        "profiler": "nsys",
        "native_reports": ["stages/nsys/profiles/rank-0/sample.nsys-rep"],
    }
    kwargs = {"prepared": lambda manifest: None}
    first = module.deliver(stage, row, binding, "unique-run", **kwargs)
    assert first["status"] == "ready"
    second = module.deliver(stage, row, binding, "unique-run", **kwargs)
    assert second["status"] == "ready"
    destination = (
        Path(binding["producer_path"])
        / "unique-run/nsys/profiles/rank-0/sample.nsys-rep"
    )
    destination.write_bytes(b"conflicting existing contents")
    third = module.deliver(stage, row, binding, "unique-run", **kwargs)
    assert third["status"] != "ready"
    assert destination.read_bytes() == b"conflicting existing contents"


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "empty"])
def test_unsafe_native_report_never_exposes_a_stage(installed, tmp_path, kind):
    module, _ = installed
    binding = module.preflight()
    stage = tmp_path / "stage"
    stage.mkdir()
    report = stage / "sample.nsys-rep"
    target = tmp_path / "original"
    target.write_bytes(b"preserved original")
    if kind == "symlink":
        report.symlink_to(target)
    elif kind == "hardlink":
        os.link(target, report)
    elif kind == "fifo":
        os.mkfifo(report)
    else:
        report.touch()
    row = {
        "id": "nsys",
        "profiler": "nsys",
        "native_reports": ["stages/nsys/sample.nsys-rep"],
    }
    outcome = module.deliver(stage, row, binding, "unsafe-run", prepared=lambda _: None)
    assert outcome["status"] == "failed" and outcome["quiescent"]
    assert not (Path(binding["producer_path"]) / "unsafe-run/nsys").exists()
    assert target.read_bytes() == b"preserved original"


def test_cancellation_before_commit_never_exposes_a_stage(installed, tmp_path):
    module, _ = installed
    binding = module.preflight()
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "sample.nsys-rep").write_bytes(b"fixture native bytes")
    row = {
        "id": "nsys",
        "profiler": "nsys",
        "native_reports": ["stages/nsys/sample.nsys-rep"],
    }
    cancelled = False

    def prepared(manifest):
        nonlocal cancelled
        cancelled = True

    result = module.deliver(
        stage,
        row,
        binding,
        "cancelled-run",
        prepared=prepared,
        cancelled=lambda: cancelled,
    )
    assert result["status"] == "failed" and result["quiescent"]
    destination = Path(binding["producer_path"]) / "cancelled-run"
    assert not (destination / "nsys").exists()
    assert all(path.stat().st_mode & 0o777 == 0o700 for path in destination.iterdir())


@pytest.mark.parametrize(
    "name",
    ["../escape.nsys-rep", "/absolute.nsys-rep", "profiles/../../escape.nsys-rep"],
)
def test_report_paths_cannot_escape(name):
    with pytest.raises(ValueError):
        course_viewer.relative(name)


def test_publisher_timeout_is_bounded(installed):
    module, _ = installed
    path = Path(module.__file__)
    source = path.read_text().replace(
        "worker(json.loads(sys.stdin.buffer.readline(MAX_MESSAGE + 1)))",
        "time.sleep(30)",
    )
    path.write_text(source)
    started = time.monotonic()
    result = module.exchange(
        {"action": "validate", "large": "x" * (2 * 1024 * 1024)}, 0.2
    )
    assert result["status"] == "failed" and result["quiescent"]
    assert time.monotonic() - started < 3


def test_closed_admission_preserves_existing_jobs(installed):
    module, descriptor = installed
    value, root = module.binding()
    os.close(root)
    admission = Path(value["producer_path"]) / ".cxcli-nsight/admission.json"
    gate = json.loads(admission.read_text())
    gate["open"] = False
    admission.write_text(json.dumps(gate))
    with pytest.raises(ValueError, match="admission is closed"):
        module.binding()
    admitted, root = module.binding(value)
    os.close(root)
    assert admitted == value


def test_delivery_and_preflight_reservations_include_all_termination_bounds(
    monkeypatch,
):
    calls = []

    def exhausted(request, seconds, **kwargs):
        calls.append(seconds + course_viewer.TERMINATION_SECONDS)
        return {"status": "unconfirmed", "quiescent": True, "manifest": {"reports": []}}

    monkeypatch.setattr(course_viewer, "exchange", exhausted)
    row = {
        "status": "completed",
        "id": "nsys",
        "profiler": "nsys",
        "native_reports": [],
    }
    course_viewer.deliver(Path("/fixture"), row, {}, "fixture", prepared=lambda _: None)
    assert (
        sum(calls) <= course_viewer.DELIVERY_SECONDS + course_viewer.TERMINATION_SECONDS
    )
    assert (
        course_viewer.delivery_budget([row])
        >= sum(calls)
        + course_viewer.PREFLIGHT_SECONDS
        + course_viewer.TERMINATION_SECONDS
    )


def test_all_launchers_budget_the_maximum_valid_selection():
    count = 0
    for metadata in fixtures.ROOT.glob("*/reference/execution.json"):
        course = metadata.parents[1]
        for lab, spec in capabilities(course).items():
            count += 1
            source = (course / "slurm" / (lab + ".sbatch")).read_text()
            allocated = time_limit_seconds(
                re.search(r"^#SBATCH --time=(\S+)", source, re.MULTILINE)[1]
            )
            for variant in spec["variants"]:
                requested = {key: variant[key] for key in ("experiment", "profiler")}
                if spec["device_option"]:
                    requested["device"] = variant["device"]
                if variant["experiment"] == "sanitizer":
                    requested["sanitizer"] = "memcheck"
                assert allocated >= required_seconds(select(course, lab, requested))
                if variant["experiment"] != "sanitizer":
                    assert allocated >= required_seconds(
                        select(course, lab, {**requested, "profiler": "all"})
                    )
    assert count == 110
