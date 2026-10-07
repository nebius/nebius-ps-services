"""Managed preparation reuse by isolated campaigns; no installers or scheduler."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from course_bootstrap import catalog as definitions, runtime
from course_bootstrap.state import State, atomic_json

ROOT = Path(__file__).resolve().parents[1]


def managed_fixture(root):
    """A real root-bound runtime with source inputs and a harmless executable."""
    root.mkdir(parents=True, mode=0o700)
    course = root / "example"
    for folder in ("labs", "slurm", "reference", "tools"):
        (course / folder).mkdir(parents=True)
    (course / "labs/01_example.py").write_text("print('fixture workload')\n")
    (course / "slurm/01_example.sbatch").write_text("#!/bin/sh\nexit 0\n")
    (course / "requirements.txt").write_text("fixture==1\n")
    (course / "reference/course.json").write_text(
        json.dumps({"slug": "example", "labs": [{"path": "labs/01_example.py"}]})
    )
    (course / "reference/runtime.json").write_text(
        json.dumps(
            {
                "schema": "course-runtime-bindings/v1",
                "course": "example",
                "labs": {"01_example": "sample"},
                "launchers": {"01_example.sbatch": "sample"},
            }
        )
    )
    catalog = {
        "schema": "course-runtime-catalog/v1",
        "components": {
            "sample": {"kind": "commands", "requirements": "example/requirements.txt"}
        },
        "runtimes": {"sample": {"group": "regular", "components": ["sample"]}},
    }
    for tools in (root / "tools", course / "tools"):
        tools.mkdir(exist_ok=True)
        shutil.copytree(
            ROOT / "tools/course_bootstrap",
            tools / "course_bootstrap",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        (tools / "course_bootstrap/catalog.json").write_text(json.dumps(catalog))
        shutil.copy2(ROOT / "tools/course_runtime.py", tools / "course_runtime.py")
        (tools / "regular-lab-setup.py").write_text(
            "raise AssertionError('never install')\n"
        )
    state = State(root)
    prefix = state.generation("sample", "fixture")
    (prefix / "python").write_text("fixture Python")
    component = state.commit("sample", "fixture", prefix, ["python"], {})
    record = {
        "schema": "course-runtime/v1",
        "id": "sample",
        "root": str(root),
        "fingerprint": definitions.fingerprint(
            catalog, root, {"example": course}, "sample"
        ),
        "status": "installed",
        "components": {"sample": component},
        "environment": {"COURSE_PYTHON": str(prefix / "python")},
    }
    atomic_json(state.runtimes / "sample.json", record)
    return catalog, record


@pytest.fixture
def bound_workspace(tmp_path, monkeypatch):
    prepared = tmp_path / "prepared"
    catalog, record = managed_fixture(prepared)
    monkeypatch.setattr(definitions, "load_catalog", lambda: catalog)
    workspace = tmp_path / "campaign/01_example/small/example"
    shutil.copytree(prepared / "example", workspace)
    workspace.chmod(0o700)
    atomic_json(
        workspace / ".course-runtime-source.json",
        {
            "schema": "course-runtime-source/v1",
            "root": str(prepared),
        },
    )
    return prepared, workspace, record


def test_bound_workspace_reuses_original_receipt_without_installing(
    bound_workspace, capsys
):
    prepared, workspace, record = bound_workspace
    receipt = prepared / ".runtime/runtimes/sample.json"
    before = receipt.read_bytes()
    assert runtime.load(workspace, "01_example.sbatch", launcher=True) == (
        prepared,
        record,
    )
    runtime.shell(workspace, "01_example.sbatch", launcher=True)
    assert str(prepared) in capsys.readouterr().out
    assert receipt.read_bytes() == before
    assert not (workspace / ".runtime").exists()


@pytest.mark.parametrize(
    "defect", ["input", "missing", "skipped", "fingerprint", "artifact"]
)
def test_bound_workspace_rejects_unprepared_runtime(bound_workspace, defect):
    prepared, workspace, record = bound_workspace
    receipt = prepared / ".runtime/runtimes/sample.json"
    if defect == "input":
        (workspace / "requirements.txt").write_text("fixture==2\n")
    elif defect == "missing":
        receipt.unlink()
    elif defect == "artifact":
        Path(record["environment"]["COURSE_PYTHON"]).unlink()
    else:
        record.update(
            {"status": "skipped", "reason": "hardware"}
            if defect == "skipped"
            else {"fingerprint": "stale"}
        )
        atomic_json(receipt, record)
    with pytest.raises(ValueError, match="regular-lab-setup.py"):
        runtime.load(workspace, "01_example.sbatch", launcher=True)


@pytest.mark.parametrize(
    "defect",
    ["mode", "schema", "relative", "traversal", "symlink", "root-symlink", "identity"],
)
def test_runtime_binding_fails_closed(bound_workspace, defect):
    prepared, workspace, _ = bound_workspace
    binding = workspace / ".course-runtime-source.json"
    value = json.loads(binding.read_text())
    if defect == "mode":
        binding.chmod(0o644)
    elif defect == "symlink":
        old = binding.with_name("owned.json")
        binding.rename(old)
        binding.symlink_to(old)
    elif defect == "identity":
        metadata = workspace / "reference/course.json"
        metadata.write_text(json.dumps({"slug": "other", "labs": []}))
    else:
        if defect == "schema":
            value["schema"] = "invalid"
        elif defect == "relative":
            value["root"] = "prepared"
        elif defect == "traversal":
            value["root"] = str(prepared / ".." / prepared.name)
        elif defect == "root-symlink":
            link = prepared.with_name("alias")
            link.symlink_to(prepared, target_is_directory=True)
            value["root"] = str(link)
        atomic_json(binding, value)
    with pytest.raises(ValueError):
        runtime.load(workspace, "01_example.sbatch", launcher=True)


def test_dry_run_prepares_actual_recipe_launchers_without_mutation(tmp_path):
    proc = subprocess.run(
        [
            sys.executable,
            "-B",
            str(ROOT / "skills/run-labs/scripts/run_labs.py"),
            "run",
            "--all-courses",
            "--workload",
            "small",
            "--dry-run",
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=True,
    )
    plan = json.loads(proc.stdout)["plan"]
    assert len(plan["units"]) == 110
    assert plan["execution_contract"] == "native-jobs/v2"
    assert not list(tmp_path.iterdir())
    for unit in plan["units"]:
        selected = {r["launcher"] for r in unit["preparation"]}
        actual = {
            Path(a).name
            for s in unit["stages"]
            for a in s.get("argv", [])
            if a.endswith(".sbatch")
        }
        assert selected == actual
    groups = {row["group"] for unit in plan["units"] for row in unit["preparation"]}
    assert groups == {
        "regular",
        "cuda",
        "communication",
        "serving",
        "transformer-engine",
    }
    engine = next(
        u for u in plan["units"] if u["key"] == "llm-inference:30_engine_profile"
    )
    optional = next(r for r in engine["preparation"] if r["optional"])
    assert optional["script"] == "serving-lab-setup.py"
    assert optional["arguments"] == ["--launcher", "30_engine_profile.trtllm.sbatch"]


def test_source_owned_adapter_uses_copy_while_installation_stays_original(
    bound_workspace,
):
    prepared, workspace, record = bound_workspace
    catalog = definitions.load_catalog()
    catalog["runtimes"]["sample"]["environment"] = {
        "COURSE_CONTAINER_RUNNER": "{course.example}/slurm/01_example.sbatch",
    }
    record["environment"]["COURSE_CONTAINER_RUNNER"] = str(
        prepared / "example/slurm/01_example.sbatch"
    )
    record["fingerprint"] = definitions.fingerprint(
        catalog, prepared, {"example": prepared / "example"}, "sample"
    )
    receipt = prepared / ".runtime/runtimes/sample.json"
    atomic_json(receipt, record)
    before = receipt.read_bytes()
    _, loaded = runtime.load(workspace, "01_example.sbatch", launcher=True)
    assert loaded["environment"]["COURSE_CONTAINER_RUNNER"] == str(
        workspace / "slurm/01_example.sbatch"
    )
    assert (
        loaded["environment"]["COURSE_PYTHON"] == record["environment"]["COURSE_PYTHON"]
    )
    assert receipt.read_bytes() == before


@pytest.mark.parametrize(
    "defect", ["copy-drift", "binding-drift", "workspace-symlink", "missing-runtime"]
)
def test_remote_sync_rechecks_existing_workspace(tmp_path, monkeypatch, defect):
    monkeypatch.syspath_prepend(str(ROOT / "skills/run-labs/scripts"))
    import prepare
    import catalog as campaign_catalog

    home = tmp_path / "home"
    prepared = home / "courses"
    managed_fixture(prepared)
    synchronized = home / "run-labs-fixture"
    shutil.copytree(prepared, synchronized, ignore=shutil.ignore_patterns(".runtime"))
    source = campaign_catalog.source_identity(synchronized, ["example"])
    query = {
        "destination": synchronized.name,
        "campaign": "fixture",
        "source": source,
        "source_sha256": "a" * 64,
        "units": [
            {
                "key": "example:01_example",
                "course": "example",
                "lab": "01_example",
                "profile": "small",
                "preparation": [{"launcher": "01_example.sbatch", "runtime": "sample"}],
            }
        ],
    }
    import os

    def invoke():
        return subprocess.run(
            [sys.executable, "-B", "-E", "-s", "-c", prepare.REMOTE],
            input=json.dumps(query),
            text=True,
            capture_output=True,
            env={**os.environ, "HOME": str(home)},
            timeout=30,
        )

    first = invoke()
    assert first.returncode == 0, first.stderr
    proof = json.loads(first.stdout)
    prepare.validate_proof(query, proof)
    workspace = Path(proof["units"][0]["remote_root"])
    sentinel = workspace / "results/retained.json"
    sentinel.parent.mkdir()
    sentinel.write_text("existing result\n")
    receipt = prepared / ".runtime/runtimes/sample.json"
    before = receipt.read_bytes()
    if defect == "copy-drift":
        (workspace / "labs/01_example.py").write_text("changed source\n")
    elif defect == "binding-drift":
        atomic_json(
            workspace / ".course-runtime-source.json",
            {"schema": "course-runtime-source/v1", "root": str(tmp_path)},
        )
    elif defect == "workspace-symlink":
        old = workspace.with_name("moved")
        workspace.rename(old)
        workspace.symlink_to(old, target_is_directory=True)
    else:
        receipt.unlink()
    result = invoke()
    assert result.returncode != 0
    assert sentinel.read_text() == "existing result\n"
    if defect == "missing-runtime":
        assert "regular-lab-setup.py" in json.loads(result.stdout)["error"]
        assert not receipt.exists()
    else:
        assert receipt.read_bytes() == before
    assert not (workspace / ".runtime").exists()


def test_preflight_requires_every_selected_runtime_proof(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "skills/run-labs/scripts"))
    import prepare

    unit = {
        "key": "example:01_example",
        "profile": "small",
        "course": "example",
        "preparation": [
            {"launcher": "01_example.sbatch", "runtime": "sample"},
            {"launcher": "01_example.optional.sbatch", "runtime": "optional"},
        ],
    }
    plan = {"source_sha256": "a" * 64, "units": [unit]}
    proof = {
        "source_sha256": "a" * 64,
        "units": [
            {
                "key": unit["key"],
                "profile": "small",
                "remote_root": "/campaign/small/example",
                "prepared_root": "/prepared",
                "runtimes": [
                    {
                        "launcher": "01_example.sbatch",
                        "runtime": "sample",
                        "fingerprint": "b" * 64,
                    }
                ],
            }
        ],
    }
    with pytest.raises(ValueError, match="proof is incomplete"):
        prepare.validate_proof(plan, proof)
    proof["units"][0]["runtimes"].append(
        {
            "launcher": "01_example.optional.sbatch",
            "runtime": "optional",
            "fingerprint": "c" * 64,
        }
    )
    prepare.validate_proof(plan, proof)


def test_copied_cuda_source_tree_invalidates_existing_build(bound_workspace):
    prepared, workspace, record = bound_workspace
    catalog = definitions.load_catalog()
    catalog["components"]["sample"].update(
        source_tree="example", source_files=["labs/01_example.py"]
    )
    catalog["runtimes"]["sample"]["group"] = "cuda"
    record["fingerprint"] = definitions.fingerprint(
        catalog, prepared, {"example": prepared / "example"}, "sample"
    )
    receipt = prepared / ".runtime/runtimes/sample.json"
    atomic_json(receipt, record)
    assert runtime.load(workspace, "01_example.sbatch", launcher=True)[1] == record
    (workspace / "labs/01_example.py").write_text("changed compiled input\n")
    with pytest.raises(
        ValueError, match="cuda-lab-setup.py --launcher 01_example.sbatch"
    ):
        runtime.load(workspace, "01_example.sbatch", launcher=True)


def test_old_execution_contract_is_rejected_before_source_or_remote_access(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "skills/run-labs/scripts"))
    import run_labs

    with pytest.raises(ValueError, match="predates managed preparation binding"):
        run_labs.verify_frozen({"plan": {"execution_contract": "native-jobs/v1"}})
