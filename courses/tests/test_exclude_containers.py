"""Container opt-out preserves native preparation and committed image caches."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from course_bootstrap import catalog, controller, selection, system
from course_bootstrap.installer import Installer
from course_bootstrap.state import State
from test_unified_setup import setup_fixture as setup_fixture  # noqa: PLC0414

ROOT = Path(__file__).resolve().parents[1]


def test_catalog_exclusion_and_shared_native_dependencies():
    _, courses = catalog.discover(ROOT / "tools/lab-setup.py")
    definitions = catalog.load_catalog()
    full = selection.resolve(courses, definitions)
    plan = selection.resolve(courses, definitions, exclude="containers")
    assert set(plan["excluded_runtimes"]) == {
        "custom-cuda-kernels.container",
        "llm-inference.30_engine_profile.trtllm",
        "llm-inference.dynamo-preflight",
    }
    assert set(plan["runtimes"]) | set(plan["excluded_runtimes"]) == set(
        full["runtimes"]
    )
    assert not set(plan["runtimes"]) & set(plan["excluded_runtimes"])
    assert plan["courses"] == full["courses"]
    assert plan["selection"]["exclude"] == ["containers"]
    assert not {
        "cuda-image",
        "dynamo-image",
        "trtllm-image",
        "triton-repository",
    } & set(plan["components"])
    assert {"toolchain", "vllm", "models-small", "build-tools"} <= set(
        plan["components"]
    )
    assert "apptainer" not in system.prerequisites(definitions, plan["components"])
    assert full["excluded_runtimes"] == []


@pytest.mark.parametrize(
    "course",
    [
        "gpu-fundamentals",
        "gpu-optimizations",
        "custom-cuda-kernels",
        "llm-training",
        "llm-inference",
        "advanced-gpu-communication",
    ],
)
def test_standalone_plan_exclusion(course, tmp_path):
    target = tmp_path / course
    shutil.copytree(
        ROOT / course / "tools",
        target / "tools",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    shutil.copytree(ROOT / course / "slurm", target / "slurm")
    (target / "reference").mkdir()
    for name in ("course.json", "runtime.json", "execution.json"):
        shutil.copy2(ROOT / course / "reference" / name, target / "reference" / name)
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            str(target / "tools/lab-setup.py"),
            "--plan",
            "--exclude",
            "containers",
        ],
        cwd=tmp_path,
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    plan = json.loads(result.stdout)
    assert list(plan["courses"]) == [course]
    assert not any(name.endswith("-image") for name in plan["components"])
    assert not result.stderr
    assert sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*")) == before


@pytest.mark.parametrize(
    "arguments,expected",
    [
        (["--plan", "--exclude", "unknown"], "invalid choice"),
        (
            ["--exclude", "containers", "organize-history", "--course-root", "."],
            "Selection flags",
        ),
        (
            [
                "--plan",
                "--exclude",
                "containers",
                "--launcher",
                "build_and_test.sbatch",
            ],
            "excludes every selected runtime",
        ),
    ],
)
def test_invalid_or_empty_target_exclusion_is_write_free(arguments, expected, tmp_path):
    result = subprocess.run(
        [sys.executable, "-I", "-S", str(ROOT / "tools/lab-setup.py"), *arguments],
        cwd=tmp_path,
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0
    assert expected in result.stderr
    assert not result.stdout
    assert not list(tmp_path.iterdir())


def test_exclusion_keeps_native_target_and_removes_transitive_image_target(monkeypatch):
    import course_execution

    _, courses = catalog.discover(ROOT / "tools/lab-setup.py")
    definitions = catalog.load_catalog()
    native = selection.resolve(
        courses, definitions, lab="llm-inference/10_vllm_offline", exclude="containers"
    )
    assert "vllm" in native["components"]
    assert native["excluded_runtimes"] == []
    # The image is only a dependency of this runtime's top-level component.
    definitions["components"]["image-wrapper"] = {
        "kind": "commands",
        "depends": ["cuda-image"],
    }
    definitions["runtimes"]["wrapper"] = {"components": ["image-wrapper"]}
    monkeypatch.setattr(
        course_execution,
        "runtime_selections",
        lambda *a, **kw: [
            {"runtime": "wrapper"},
            {"runtime": "shared-tools"},
            {"runtime": "llm-inference.artifact-audit"},
        ],
    )
    mixed = selection.resolve(
        courses, definitions, lab="llm-inference/10_vllm_offline", exclude="containers"
    )
    assert mixed["excluded_runtimes"] == ["wrapper"]
    assert set(mixed["runtimes"]) == {"shared-tools", "llm-inference.artifact-audit"}
    assert not {"image-wrapper", "cuda-image"} & set(mixed["components"])


@pytest.mark.parametrize("existing_images", [False, True])
def test_repeated_exclusion_never_enters_worker_and_preserves_images(
    setup_fixture, monkeypatch, existing_images
):
    f = setup_fixture
    if existing_images:
        f.run(["native", "image-dependent"])
    state = State(f.root)
    image_state = {
        p: p.read_bytes()
        for directory in (state.records, state.runtimes, state.installs)
        for p in directory.rglob("*")
        if p.is_file() and "image" in str(p.relative_to(directory))
    }
    generations = sorted(
        p.relative_to(state.installs) for p in state.installs.glob("image*/*")
    )

    def forbidden(*args, **kwargs):
        pytest.fail("Excluded container preparation attempted")

    supervise = controller.supervise
    monkeypatch.setattr(controller, "supervise", forbidden)
    monkeypatch.setattr(Installer, "image", forbidden)
    plan = {
        "runtimes": ["native"],
        "excluded_runtimes": ["image-dependent"],
        "courses": {"fixture": ["native", "image-dependent"]},
    }
    for _ in range(2):
        controller.setup(f.root / "tools/lab-setup.py", lambda **kw: None, plan=plan)
        report = f.report()
        assert report["components"].keys() == {"native"}
        assert report["courses"]["fixture"]["excluded"] == 1
        assert (
            next(
                row for row in report["runtimes"] if row["runtime"] == "image-dependent"
            )["status"]
            == "excluded"
        )
        assert all(p.read_bytes() == data for p, data in image_state.items())
        assert (
            sorted(
                p.relative_to(state.installs) for p in state.installs.glob("image*/*")
            )
            == generations
        )
        monkeypatch.setattr(Installer, "install", forbidden)
    assert report["courses"]["fixture"]["reused"] == 1
    if existing_images:
        monkeypatch.setattr(controller, "supervise", supervise)
        for _ in range(2):
            f.run(["native", "image-dependent"])
            assert all(
                row["status"] == "reused" for row in f.report()["components"].values()
            )
            assert all(p.read_bytes() == data for p, data in image_state.items())
