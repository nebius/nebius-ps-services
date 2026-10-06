"""Truthful publication, complete dashboard assets, and opt-in capture regressions."""

import copy
import json
import subprocess
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_observability_integration import ROOT, load, result

COURSES = (
    "gpu-fundamentals",
    "gpu-optimizations",
    "llm-training",
    "llm-inference",
    "custom-cuda-kernels",
    "advanced-gpu-communication",
)


@pytest.mark.parametrize("case", ["sync", "launch", "memory", "compute"])
@pytest.mark.parametrize("mode", ["baseline", "optimized"])
def test_bottleneck_variants_keep_canonical_result_identity(
    monkeypatch, tmp_path, case, mode
):
    from test_course_review_fixes import load_lab

    with load_lab("gpu-optimizations/labs/14_profiler_bottlenecks.py") as lab:
        monkeypatch.setattr(
            "sys.argv",
            ["lab", "--case", case, "--mode", mode, "--output-dir", str(tmp_path)],
        )
        torch = SimpleNamespace(
            cuda=SimpleNamespace(
                synchronize=lambda: None,
                nvtx=SimpleNamespace(range=lambda _: nullcontext()),
            )
        )
        monkeypatch.setattr(lab, "load_torch", lambda: torch)
        monkeypatch.setattr(
            lab, "require_course_gpu", lambda _: {"gpu_family": "NVIDIA H200"}
        )
        monkeypatch.setattr(lab, "seed_everything", lambda *a: None)
        monkeypatch.setattr(lab, "cuda_times_ms", lambda *a, **kw: [1.0, 2.0, 3.0])
        monkeypatch.setattr(
            lab, "build_" + case + "_case", lambda *a: (lambda: 2.0, lambda: 2.0, {})
        )
        lab.main()
    document = json.loads(next(tmp_path.glob("*.json")).read_text())
    load("publish_results").validate_result(document, "14_profiler_bottlenecks")
    assert document["measurements"]["case"] == case
    assert document["measurements"]["mode"] == mode


def test_optimization_capstone_publishes_both_actual_timing_boundaries():
    recipe = json.loads(
        (ROOT / "gpu-optimizations/reference/observability.json").read_text()
    )["labs"]["09_capstone"]
    document = result()
    document["lab_id"] = "09_capstone"
    document["measurements"] = {
        "controlled_factors": {"elements": 1024},
        "trials": [
            {
                "eager": {"cuda_median_ms": 2, "wall_median_ms": 3},
                "compiled": {"cuda_median_ms": 1, "wall_median_ms": 2},
            }
        ],
        "median_observed_ratio_across_trials": 2,
    }
    rows = load("publish_results").comparison_payload(
        document, copy.deepcopy(document), recipe
    )
    values = {name: value for name, slot, case, value in rows if slot == "baseline"}
    assert values["trials_eager_cuda_median_seconds"] == 0.002
    assert values["trials_eager_wall_median_seconds"] == 0.003
    assert values["trials_compiled_cuda_median_seconds"] == 0.001
    assert values["trials_compiled_wall_median_seconds"] == 0.002


def test_source_edit_cannot_change_workload_or_case_identity():
    publisher = load("publish_results")
    recipe = {
        "lab": "01_example",
        "allow_source_change": True,
        "invariants": ["cases.*.elements", "dtype", "timing_scope"],
        "metrics": [{"name": "duration_seconds", "path": "elapsed_ms", "scale": 0.001}],
    }
    a = result(implementation_sha256="a")
    b = result(implementation_sha256="b")
    for document in (a, b):
        document["measurements"].update(
            cases=[{"elements": 1003}], dtype="float32", timing_scope="resident-kernel"
        )
    assert publisher.comparison_payload(a, b, recipe)
    for path, value in [
        ("cases", [{"elements": 10}]),
        ("dtype", "float16"),
        ("timing_scope", "enqueue-only"),
    ]:
        changed = copy.deepcopy(b)
        changed["measurements"][path] = value
        with pytest.raises(ValueError, match="workload invariant"):
            publisher.comparison_payload(a, changed, recipe)
    del b["measurements"]["dtype"]
    with pytest.raises(ValueError, match="Missing declared"):
        publisher.comparison_payload(a, b, recipe)


@pytest.mark.parametrize(
    "defect", ["schema", "completion", "failure", "duplicate", "case", "nonfinite"]
)
def test_cuda_never_synthesizes_success_for_incomplete_or_failed_output(
    monkeypatch, defect
):
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    adapter = load("cuda_result")
    document = "schema=gpu-course-result/v1\nlab_id=01_vector_add\nelements=1003\nkernel_median_ms=0.03\ncourse_checks=passed\n"
    assert adapter.parse_report(document, "01_vector_add")["elements"] == 1003
    if defect == "schema":
        document = document.replace("schema=gpu-course-result/v1\n", "")
    elif defect == "completion":
        document = document.replace("course_checks=passed\n", "")
    elif defect == "failure":
        document += "naive_correctness=failed\n"
    elif defect == "duplicate":
        document += "course_checks=passed\n"
    elif defect == "case":
        document += "case threads=128 median_ms=0.1\n"
    else:
        document = document.replace("0.03", "nan")
    with pytest.raises(ValueError):
        adapter.parse_report(document, "01_vector_add")


def test_cuda_all_six_resource_cases_required(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    adapter = load("cuda_result")
    header = "schema=gpu-course-result/v1\nlab_id=07_resource_sweep\nelements=4096\n"
    row = (
        "case threads=128 state_values=4 median_ms=0.1 p90_ms=0.2 correctness=passed\n"
    )
    assert (
        len(
            adapter.parse_report(
                header + row * 6 + "course_checks=passed\n", "07_resource_sweep"
            )["cases"]
        )
        == 6
    )
    cases = adapter.parse_report(
        header + row * 6 + "course_checks=passed\n", "07_resource_sweep"
    )["cases"]
    assert all(case["p90_ms"] == 0.2 and "_ms" not in case for case in cases)
    with pytest.raises(ValueError, match="six cases"):
        adapter.parse_report(
            header + row * 5 + "course_checks=passed\n", "07_resource_sweep"
        )


def test_all_dashboards_bind_exact_datasource_and_fit_kubernetes_configmaps():
    generator = load("build_observability")
    uids = set()
    labs = 0
    assets = 0
    for course in COURSES:
        root = ROOT / course
        metadata = json.loads((root / "reference/course.json").read_text())
        inventory = json.loads((root / "reference/observability.json").read_text())
        assert set(inventory["labs"]) == {
            Path(r["path"]).stem for r in metadata["labs"]
        }
        configmap = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": "course-dashboards"},
            "data": {},
        }
        for recipe in [inventory["setup"], *inventory["labs"].values()]:
            dashboard = generator.dashboard(course, recipe)
            path = root / "reference/grafana" / f"{recipe['lab']}.json"
            assert json.loads(path.read_text()) == dashboard
            assert dashboard["uid"] not in uids
            uids.add(dashboard["uid"])
            assets += 1
            assert len(dashboard["uid"]) <= 40
            assert len({p["id"] for p in dashboard["panels"]}) == len(
                dashboard["panels"]
            )
            for panel in dashboard["panels"]:
                if "targets" in panel:
                    assert panel["datasource"] == generator.DATASOURCE
                    assert (
                        panel["fieldConfig"]["defaults"]["noValue"]
                        == "No measured data"
                    )
                assert panel.get("description")
            variables = {item["name"]: item for item in dashboard["templating"]["list"]}
            gpu_panels = [
                target["expr"]
                for item in dashboard["panels"]
                for target in item.get("targets", [])
                if "DCGM_FI_DEV_" in target["expr"]
            ]
            if recipe.get("gpu_telemetry", True):
                # Soperator exports worker-local indices; UUID is absent from
                # the live exporter schema. Index 0 on two workers is two GPUs.
                assert variables["gpu_node"]["query"].endswith(", Hostname)")
                assert "${gpu_node:regex}" in variables["gpu"]["query"]
                assert variables["gpu"]["query"].endswith(", gpu)")
                assert gpu_panels
                for expression in gpu_panels:
                    assert 'Hostname=~"${gpu_node:regex}"' in expression
                    assert 'gpu=~"${gpu:regex}"' in expression
                    assert "UUID" not in expression
            else:
                assert not gpu_panels
                assert not {"gpu", "gpu_node"}.intersection(variables)
            configmap["data"][path.name] = path.read_text()
            if recipe["lab"] != "environment_readiness":
                labs += 1
                assert (
                    recipe["guided_comparison"]
                    and "the implementations or cases already"
                    not in recipe["tuning_control"]
                )
                assert "invariants" in recipe
                guide = (root / "reference/labs" / f"{recipe['lab']}.md").read_text()
                assert "../../../lab-guide.html#lab-preparation-scripts" in guide
                assert "[Lab Guide](../../../lab-guide.html#lab-preparation-scripts)" in guide
                assert "grafana import" not in guide
                assert "grafana validate" not in guide
                for obsolete in (
                    "--dashboard-json",
                    "--dashboard-folder",
                    "--attach",
                    "validate-dashboards",
                    'flux apply "$COURSE_GENERATED"',
                ):
                    assert obsolete not in guide
        # Kubernetes limits the UTF-8 data values, not escaped YAML transport.
        # Keep conservative headroom below the API's 1 MiB maximum.
        assert (
            sum(len(value.encode()) for value in configmap["data"].values()) < 1_000_000
        )
    assert labs == 110 and assets == 116


def test_setup_installs_profiling_before_monitoring_and_dashboard_import():
    guide = (ROOT / "README.md").read_text()
    ordered = (
        'nebius-cxcli deploy "$CLUSTER_CONFIG"',
        'nebius-cxcli soperator profiling install "$CLUSTER_CONFIG" --target "$CLUSTER_TARGET" --interactive',
        'nebius-cxcli grafana install --config "$CLUSTER_CONFIG" --target "$CLUSTER_TARGET" --pushgateway',
        "New → New folder",
        "export COURSE_GRAFANA_FOLDER_UID=",
        "nebius-cxcli grafana import ./reference/grafana --recursive",
        "labs/10_compatibility_stack.py --workload small",
        "tools/regular-lab-setup.py monitoring \\",
        "tools/verify_monitoring.py \\",
        '"$COURSE_PYTHON" tools/readiness.py',
    )
    positions = [guide.index(command) for command in ordered]
    assert positions == sorted(positions)
    assert "course-soperator-metrics" in guide
    assert "wizard" in guide
    assert "--attach" not in guide
    assert "--dashboard-json" not in guide


@pytest.mark.parametrize(
    "host,accepted", [("grafana.com", True), ("grafana.com.invalid", False)]
)
def test_grafana_official_reference_uses_exact_host(host, accepted):
    parser = load("validate_course_template").Parser()
    parser.feed(
        '<section id="official-references">'
        f'<a href="https://{host}/docs/">Grafana documentation</a></section>'
    )
    assert (not parser.errors) is accepted


@pytest.mark.parametrize("phase", ["probe", "profile"])
@pytest.mark.parametrize("variant", ["disabled", "enabled"])
def test_chunked_prefill_capture_brackets_each_request_campaign(phase, variant):
    launcher = (
        ROOT / "llm-inference/slurm/34_policy_equivalence_client.nsys.sbatch"
    ).read_text()
    trial = launcher.split("run_trial() {\n", 1)[1].split("\n}\n", 1)[0]
    # Execute the actual post-readiness campaign with only local Bash fixtures.
    campaign = trial.split("  if [[ ${ready} -ne 1 ]]", 1)[1]
    campaign = campaign.split("\n\n", 1)[1].rsplit("  stop_server", 1)[0]
    script = (
        r"""
set -euo pipefail
course_python=python_fixture
COURSE_AIPERF=runner_fixture
base_dir=fixture
model=fixture
revision=fixture
port=8000
trial=1
control_profile() { printf 'capture %s\n' "$1"; }
phase=$1
variant=$2
python_fixture() {
  if [[ $1 == tools/server_capture.py ]]; then
    printf 'capture %s\n' "$2"
  elif [[ $1 == labs/34_policy_equivalence_client.py ]]; then
    printf 'probe\n'
  else
    return 99
  fi
}
runner_fixture() {
  [[ $1 == profile ]]
  printf 'aiperf\n'
}
mkdir() { :; }
run_campaign() {
"""
        + campaign
        + "\n}\nrun_campaign\n"
    )
    result = subprocess.run(
        ["bash", "-c", script, "capture-test", phase, variant],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    workload = "probe" if phase == "probe" else "aiperf"
    assert result.stdout.splitlines() == ["capture start", workload, "capture stop"]


@pytest.mark.parametrize(
    "unit,label",
    [
        ("count:tokens/s", "tokens/s"),
        ("count:samples/s", "samples/s"),
        ("reqps", "requests/s"),
    ],
)
def test_job_inspection_uses_throughput_units_without_grafana_format_ids(
    tmp_path, monkeypatch, unit, label
):
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    inspector = load("inspect_results")
    monkeypatch.setattr(inspector, "ROOT", tmp_path)
    (tmp_path / "reference").mkdir()
    (tmp_path / "results/01_example/jobs/3/results").mkdir(parents=True)
    recipe = {
        "kind": "single_gpu",
        "metrics": [{"name": "throughput", "path": "throughput", "unit": unit}],
    }
    (tmp_path / "reference/observability.json").write_text(
        json.dumps({"labs": {"01_example": recipe}})
    )
    artifact = result()
    artifact["measurements"] = {"throughput": 1250000}
    (tmp_path / "results/01_example/jobs/3/results/completed.json").write_text(json.dumps(artifact))
    report = inspector.inspect("01_example", 3)[0]
    assert f"throughput [value]: 1.25e+06 {label}" in report
    assert "count:" not in report and "reqps" not in report


def test_job_inspection_exposes_actual_numbers_and_no_queued_job_success(
    tmp_path, monkeypatch
):
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    inspector = load("inspect_results")
    monkeypatch.setattr(inspector, "ROOT", tmp_path)
    (tmp_path / "reference").mkdir()
    (tmp_path / "results/01_example/jobs/3/results").mkdir(parents=True)
    from test_observability_integration import RECIPE

    recipe = {
        **RECIPE,
        "kind": "single_gpu",
        "metrics": [{**RECIPE["metrics"][0], "unit": "s"}],
    }
    (tmp_path / "reference/observability.json").write_text(
        json.dumps({"labs": {"01_example": recipe}})
    )
    (tmp_path / "results/01_example/jobs/3/results/completed.json").write_text(json.dumps(result()))
    assert inspector.inspect("01_example", 4) == []
    reports = inspector.inspect("01_example", 3)
    assert (
        len(reports) == 1 and "completed.json" in reports[0] and "0.002 s" in reports[0]
    )
    bad = result()
    bad["experiment"]["instrumented"] = True
    (tmp_path / "results/01_example/jobs/3/results/completed.json").write_text(json.dumps(bad))
    with pytest.raises(ValueError, match="unprofiled"):
        inspector.inspect("01_example", 3)


@pytest.mark.parametrize("candidate_loss", [0.2, float("nan"), "0.1"])
def test_numerical_equivalence_rejects_changed_or_invalid_losses(candidate_loss):
    publisher = load("publish_results")
    from test_observability_integration import RECIPE

    recipe = {
        **RECIPE,
        "equivalence": [{"path": "loss", "rtol": 0.001, "atol": 0.0001}],
    }
    baseline, candidate = result(), result()
    baseline["measurements"]["loss"] = 0.1
    candidate["measurements"]["loss"] = 0.10001
    assert publisher.comparison_payload(baseline, candidate, recipe)
    candidate["measurements"]["loss"] = candidate_loss
    with pytest.raises(ValueError, match="numerical equivalence"):
        publisher.comparison_payload(baseline, candidate, recipe)


def test_introductory_diagram_has_a_registered_home_without_renumbering_lessons():
    document = (ROOT / "gpu-performance-tools/index.html").read_text()
    assert document.count('id="detail-tools-measurement-loop"') == 1
    assert document.count('class="lesson"') == 5
    fundamentals = (ROOT / "gpu-fundamentals/index.html").read_text()
    assert fundamentals.count('class="lesson"') == 12
    assert 'id="using-gpu-performance-tools"' not in fundamentals


@pytest.mark.parametrize("relative", ["gpu-optimizations/labs/07_profile_workload.py", "llm-training/labs/30_training_profiler.py"])
@pytest.mark.parametrize("external,tool,expected", [(False, "none", True), (True, "none", False), (True, "nsys", True)])
def test_internal_profiler_results_record_actual_instrumentation(tmp_path, monkeypatch, relative, external, tool, expected):
    from unittest.mock import MagicMock
    from test_course_review_fixes import load_lab

    monkeypatch.setenv("COURSE_CAPTURE", "1" if tool == "nsys" else "0")
    monkeypatch.setenv("COURSE_PROFILE_TOOL", tool)
    monkeypatch.delenv("COURSE_JOB_DIR", raising=False)
    torch = MagicMock()
    torch.profiler.profile.return_value.__enter__.return_value.key_averages.return_value.table.return_value = "operator summary"
    with load_lab(relative) as lab:
        argv = ["lab", "--workload", "small", "--output-dir", str(tmp_path)]
        if external:
            argv.append("--external-only")
        monkeypatch.setattr("sys.argv", argv)
        monkeypatch.setattr(lab, "load_torch", lambda: torch)
        monkeypatch.setattr(lab, "require_course_gpu", lambda _: {"gpu_family": "NVIDIA H100"})
        monkeypatch.setattr(lab, "seed_everything", lambda *args: None)
        monkeypatch.setattr(lab, "annotated_operation", lambda fn, name: fn)
        if "training_profiler" in relative:
            monkeypatch.setattr(lab, "build_tiny_lm", lambda *a, **kw: MagicMock())
            monkeypatch.setattr(lab, "make_language_batch", lambda *a, **kw: (MagicMock(), MagicMock()))
        lab.main()
    document = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert document["experiment"]["instrumented"] is expected
    if not external:
        torch.profiler.profile.assert_called_once()
    else:
        torch.profiler.profile.assert_not_called()
