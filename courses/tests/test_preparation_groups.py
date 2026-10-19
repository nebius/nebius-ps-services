"""Preparation boundaries, preservation and optional native/container selection."""

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from course_bootstrap import catalog, controller, retirement, selection, system
from course_bootstrap.installer import Installer
from course_bootstrap.state import State

ROOT = Path(__file__).resolve().parents[1]


def plans():
    root, courses = catalog.discover(ROOT / "tools/lab-setup.py")
    definitions = catalog.load_catalog()
    return root, courses, definitions, selection.resolve(courses, definitions)


def test_complete_metadata_coverage_including_container_exercises():
    from course_execution import capabilities
    _, courses, definitions, plan = plans()
    labs, launchers = selection.inventory(courses, definitions)
    expected = {"shared-tools", *launchers.values()}
    for course in courses.values():
        expected.update(row["runtime"] for spec in capabilities(course).values() for row in spec["variants"])
    assert set(plan["labs"]) == set(labs)
    assert set(plan["runtimes"]) == expected
    assert set(plan["components"]) == set(catalog.order(definitions, [c for n in expected for c in definitions["runtimes"][n]["components"]]))
    assert {"cuda-image", "trtllm-image", "dynamo-image"} <= set(plan["components"])


@pytest.mark.parametrize(
    "group,launcher,component",
    [
        ("cuda", "build_and_test.sbatch", "cuda-image"),
        ("serving", "30_engine_profile.sbatch", "trtllm-image"),
        ("communication", "dynamo_disaggregated_preflight.sbatch", "dynamo-image"),
    ],
)
def test_container_variants_are_explicit(group, launcher, component):
    _, courses, definitions, _ = plans()
    plan = selection.resolve(courses, definitions, launcher=launcher, experiment="trtllm" if launcher == "30_engine_profile.sbatch" else "default")
    assert component in plan["components"]


def test_model_selection_and_shared_revision():
    _, courses, definitions, _ = plans()
    serving = selection.resolve(courses, definitions, lab="10_vllm_offline")
    speculative = selection.resolve(
        courses, definitions, lab="33_speculative_engine_client"
    )
    assert "models-small" in serving["components"]
    assert "models-speculative" not in serving["components"]
    assert {"models-small", "models-speculative"} <= set(speculative["components"])
    audit = selection.resolve(
        courses, definitions, lab="16_model_artifact_audit"
    )
    # Shared regular publication setup downloads its shared small model; the audit runtime itself does not.
    assert all(
        definitions["components"][name]["kind"] != "models"
        for name in catalog.order(
            definitions,
            definitions["runtimes"]["llm-inference.artifact-audit"]["components"],
        )
    )
    assert "models-dynamo" not in audit["components"]


@pytest.mark.parametrize("args", [("--plan",), ("--help",)])
def test_plan_is_read_only_and_does_not_inspect_platform(tmp_path, args):
    result = subprocess.run([sys.executable, "-I", "-S", str(ROOT / "tools/lab-setup.py"), *args], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert not result.stderr
    if args == ("--plan",):
        assert json.loads(result.stdout)["runtimes"]
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("args", [("--all",), ("--profiler", "none"), ("--experiment", "default"), ("--device", "cpu"), ("--sanitizer", "memcheck")])
def test_unsupported_or_untargeted_flags_fail_before_writes(tmp_path, args):
    result = subprocess.run([sys.executable, "-I", "-S", str(ROOT / "tools/lab-setup.py"), "--plan", *args], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode != 0
    assert not result.stdout
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("args", [("--profiler", "none"), ("--lab", "example"), ("--device", "cpu")])
def test_history_does_not_silently_ignore_selection_flags(tmp_path, args):
    result = subprocess.run(
        [sys.executable, "-I", "-S", str(ROOT / "tools/lab-setup.py"), *args,
         "organize-history", "--course-root", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "Selection flags apply to dependency preparation only" in result.stderr
    assert not list(tmp_path.iterdir())


def test_unrelated_definitions_do_not_invalidate_components_or_runtimes():
    root, courses, definitions, _ = plans()
    name = "llm-training.22_transformer_engine_fp8.ncu"
    before = catalog.fingerprint(definitions, root, courses, name)
    changed = copy.deepcopy(definitions)
    changed["components"]["vllm"]["packages"].append("unrelated==1")
    changed["runtimes"]["shared-tools"]["environment"]["UNRELATED"] = "1"
    assert catalog.fingerprint(changed, root, courses, name) == before
    changed["components"]["training-te"]["packages"].append("relevant==1")
    assert catalog.fingerprint(changed, root, courses, name) != before


def test_auxiliary_runtime_id_repair_hint_resolves(monkeypatch):
    from course_bootstrap import runtime
    import shlex

    def missing(_path):
        raise FileNotFoundError

    monkeypatch.setattr(runtime, "read_json", missing)
    course = ROOT / "llm-inference"
    with pytest.raises(ValueError, match="Prepared runtime is missing") as exc:
        runtime.load(course, "llm-inference.dynamo-preflight", runtime_id=True)
    arguments = shlex.split(str(exc.value).split("; run ", 1)[1])
    launcher = arguments[arguments.index("--launcher") + 1]
    assert launcher == "llm-inference/dynamo_disaggregated_preflight.sbatch"
    plan = selection.resolve({course.name: course}, catalog.load_catalog(), launcher=launcher)
    assert "llm-inference.dynamo-preflight" in plan["runtimes"]


def test_native_prerequisites_exclude_container_and_fabric_packages():
    _, courses, definitions, _ = plans()
    plan = selection.resolve(courses, definitions, lab="gpu-fundamentals/01_cpu_gpu_crossover")
    packages = system.prerequisites(definitions, plan["components"])
    assert not set(packages) & {
        "apptainer",
        "libpci-dev",
        "libgrpc++-dev",
        "build-essential",
    }


def test_regular_reconciliation_preserves_specialized_record_and_generation(
    tmp_path, monkeypatch
):
    definitions = {
        "components": {
            "shared": {
                "kind": "commands",
                "artifacts": ["ready"],
                "commands": [
                    {
                        "argv": [
                            sys.executable,
                            "-c",
                            "from pathlib import Path; Path('ready').write_text('ready')",
                        ]
                    }
                ],
            }
        },
        "runtimes": {"shared-tools": {"group": "regular", "components": ["shared"]}},
    }
    state = State(tmp_path)
    installer = Installer(tmp_path, {}, definitions, state)
    installer.install("shared")
    record = state.runtimes / "specialized.json"
    record.write_bytes(b"existing specialized runtime\n")
    record.chmod(0o600)
    before = (state.records / "shared.json").read_bytes()
    monkeypatch.setattr(controller.definitions, "discover", lambda _: (tmp_path, {}))
    monkeypatch.setattr(controller.definitions, "load_catalog", lambda: definitions)
    monkeypatch.setattr(controller, "ubuntu", lambda: {})
    monkeypatch.setattr(controller, "capacity", lambda: [])
    monkeypatch.setattr(controller, "install_packages", lambda *a, **kw: None)
    monkeypatch.setattr(
        controller.mount_root,
        "prepare",
        lambda: pytest.fail("native preparation entered a mount namespace"),
    )
    for _ in range(2):
        controller.setup(
            tmp_path / "tools/lab-setup.py",
            lambda **kw: None,
            plan={"group": "regular", "runtimes": ["shared-tools"]},
        )
    assert record.read_bytes() == b"existing specialized runtime\n"
    assert (state.records / "shared.json").read_bytes() == before
    assert len(list((state.installs / "shared").iterdir())) == 1


def test_retirement_preserves_unknown_files_symlinks_and_results(tmp_path, monkeypatch):
    definitions = tmp_path / "definitions"
    definitions.mkdir()
    (definitions / "retired.json").write_text(
        json.dumps({"course_setup.py": [hashlib.sha256(b"known").hexdigest()]})
    )
    monkeypatch.setattr(retirement, "__file__", str(definitions / "retirement.py"))
    (tmp_path / "tools").mkdir()
    old = tmp_path / "tools/course_setup.py"
    old.write_bytes(b"modified")
    assert retirement.retire(tmp_path, ["."])["preserved_modified"] == [
        "tools/course_setup.py"
    ]
    old.unlink()
    historical = tmp_path / "history"
    historical.write_bytes(b"known")
    old.symlink_to(historical)
    assert retirement.retire(tmp_path, ["."])["preserved_modified"]
    old.unlink()
    old.write_bytes(b"known")
    for mode in (0o666, 0o4755):
        old.chmod(mode)
        assert retirement.retire(tmp_path, ["."])["preserved_modified"]
        assert old.read_bytes() == b"known"
    old.chmod(0o644)
    alias = tmp_path / "retained-hardlink"
    alias.hardlink_to(old)
    assert retirement.retire(tmp_path, ["."])["preserved_modified"]
    assert alias.read_bytes() == b"known"
    alias.unlink()
    assert retirement.retire(tmp_path, ["."])["retired"] == ["tools/course_setup.py"]
    assert historical.read_bytes() == b"known"


def test_retirement_excludes_remote_only_courses(tmp_path, monkeypatch):
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools/retired.json").write_text(
        json.dumps({"course_setup.py": [hashlib.sha256(b"known").hexdigest()]})
    )
    monkeypatch.setattr(retirement, "__file__", str(tmp_path / "tools/retirement.py"))
    remote_only = tmp_path / "remote-only"
    (remote_only / "reference").mkdir(parents=True)
    (remote_only / "reference/course.json").write_text("{}")
    (remote_only / "tools").mkdir()
    old = remote_only / "tools/course_setup.py"
    old.write_bytes(b"known")
    assert retirement.retire(tmp_path, ["."])["retired"] == []
    assert old.read_bytes() == b"known"


def test_failed_source_verification_precedes_retirement(tmp_path, monkeypatch):
    import io
    import verify_source_sync

    (tmp_path / "tools").mkdir()
    old = tmp_path / "tools/course_setup.py"
    old.write_bytes(b"old")
    replacement = tmp_path / "tools/lab-setup.py"
    replacement.write_bytes(b"unexpected")
    monkeypatch.setattr(
        sys, "argv", ["verify_source_sync.py", "verify", "--root", str(tmp_path)]
    )
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps(
                {
                    "schema": "course-source-sync/v1",
                    "files": {
                        "tools/lab-setup.py": {
                            "sha256": "wrong",
                            "executable": False,
                        }
                    },
                }
            )
        ),
    )
    monkeypatch.setattr(
        verify_source_sync,
        "retire",
        lambda *a: pytest.fail("retired before source verification"),
    )
    with pytest.raises(ValueError, match="differs"):
        verify_source_sync.main()
    assert old.read_bytes() == b"old"


def test_native_serving_and_cuda_variants_have_no_container_dependency():
    _, courses, definitions, _ = plans()
    for course in courses.values():
        for launcher, runtime in catalog.bindings(course, definitions)[
            "launchers"
        ].items():
            spec = definitions["runtimes"][runtime]
            if spec["group"] in {"cuda", "serving"} and not spec.get("optional"):
                source = (course / "slurm" / launcher).read_text()
                if launcher.removesuffix(".sbatch") in json.loads((course / "reference/execution.json").read_text())["labs"]:
                    source = source.split("default_none_", 1)[1].split("\n      ;;", 1)[0]
                assert "COURSE_CONTAINER_RUNNER" not in source
                assert "VLLM_IMAGE_DIGEST" not in source
                assert "CUDA_IMAGE_DIGEST" not in source
                subprocess.run(
                    ["bash", "-n", str(course / "slurm" / launcher)], check=True
                )


def test_preparation_error_uses_actual_checkout_root(tmp_path):
    import shlex

    command = selection.command(
        root=tmp_path / "standalone kit", launcher="01_vector_add.sbatch"
    )
    assert shlex.split(command) == [
        "python3.12",
        str(tmp_path / "standalone kit/tools/lab-setup.py"),
        "--launcher",
        "01_vector_add.sbatch",
    ]
