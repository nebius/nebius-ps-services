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
    root, courses = catalog.discover(ROOT / "tools/regular-lab-setup.py")
    definitions = catalog.load_catalog()
    return (
        root,
        courses,
        definitions,
        {
            group: selection.resolve(group, courses, definitions)
            for group in selection.GROUPS
        },
    )


def test_complete_disjoint_partition_and_regular_dependency_boundary():
    _, _, definitions, groups = plans()
    assert {group: len(plan["labs"]) for group, plan in groups.items()} == dict(
        zip(selection.GROUPS, (79, 13, 10, 7, 1))
    )
    assert len({lab for plan in groups.values() for lab in plan["labs"]}) == 110
    regular = groups["regular"]["components"]
    assert "models-small" in regular
    assert not set(regular) & {
        "toolchain",
        "vllm",
        "aiperf",
        "training-te",
        "dynamo",
        "nixl",
        "bridge",
    }
    assert all(
        definitions["components"][name]["kind"] in {"python", "models"}
        for name in regular
    )
    assert all(
        not definitions["runtimes"][name].get("optional")
        for plan in groups.values()
        for name in plan["runtimes"]
    )


@pytest.mark.parametrize(
    "group,launcher,component",
    [
        ("cuda", "build_and_test.sbatch", "cuda-image"),
        ("serving", "30_engine_profile.trtllm.sbatch", "trtllm-image"),
        ("communication", "dynamo_disaggregated_preflight.sbatch", "dynamo-image"),
    ],
)
def test_container_variants_are_explicit(group, launcher, component):
    _, courses, definitions, _ = plans()
    plan = selection.resolve(group, courses, definitions, launcher=launcher)
    assert component in plan["components"]


def test_model_selection_and_shared_revision():
    _, courses, definitions, _ = plans()
    serving = selection.resolve("serving", courses, definitions, lab="10_vllm_offline")
    speculative = selection.resolve(
        "serving", courses, definitions, lab="33_speculative_engine_client"
    )
    assert "models-small" in serving["components"]
    assert "models-speculative" not in serving["components"]
    assert {"models-small", "models-speculative"} <= set(speculative["components"])
    audit = selection.resolve(
        "regular", courses, definitions, lab="16_model_artifact_audit"
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


@pytest.mark.parametrize("group", selection.GROUPS)
def test_plan_is_read_only_and_does_not_inspect_platform(tmp_path, group):
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            str(ROOT / "tools" / f"{group}-lab-setup.py"),
            "--plan",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["group"] == group
    assert not list(tmp_path.iterdir())
    if group != "regular":
        result = subprocess.run(
            [sys.executable, "-I", "-S", str(ROOT / "tools" / f"{group}-lab-setup.py")],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert "no installation performed" in result.stdout


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


def test_native_prerequisites_exclude_container_and_fabric_packages():
    _, _, definitions, groups = plans()
    packages = system.prerequisites(definitions, groups["regular"]["components"])
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
            tmp_path / "tools/regular-lab-setup.py",
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
    replacement = tmp_path / "tools/regular-lab-setup.py"
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
                        "tools/regular-lab-setup.py": {
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
                assert "COURSE_CONTAINER_RUNNER" not in source
                assert "VLLM_IMAGE_DIGEST" not in source
                assert "CUDA_IMAGE_DIGEST" not in source
                subprocess.run(
                    ["bash", "-n", str(course / "slurm" / launcher)], check=True
                )


def test_preparation_error_uses_actual_checkout_root(tmp_path):
    import shlex

    command = selection.command(
        "cuda", root=tmp_path / "standalone kit", launcher="01_vector_add.sbatch"
    )
    assert shlex.split(command) == [
        "python3.12",
        str(tmp_path / "standalone kit/tools/cuda-lab-setup.py"),
        "--launcher",
        "01_vector_add.sbatch",
    ]
