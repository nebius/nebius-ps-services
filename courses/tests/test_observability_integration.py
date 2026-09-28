"""Acceptance summaries, capture scope and dashboard correctness contracts."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def result(**parameters):
    return {
        "schema": "gpu-course-result/v1",
        "lab_id": "01_example",
        "profile": "small",
        "seed": 17,
        "environment": {"gpu_family": "NVIDIA H100"},
        "correctness": {"reference": True},
        "experiment": {
            "started_unix_seconds": 1,
            "ended_unix_seconds": 2,
            "slurm_job_id": 3,
            "instrumented": False,
            "parameters": parameters,
        },
        "measurements": {"elapsed_ms": 2},
    }


RECIPE = {
    "lab": "01_example",
    "tuning_parameters": ["batch_size"],
    "metrics": [{"name": "duration_seconds", "path": "elapsed_ms", "scale": 0.001}],
}


def test_explicit_mapping_converts_units_and_keeps_two_slots():
    rows = load("publish_results").comparison_payload(
        result(batch_size=1), result(batch_size=2), RECIPE
    )
    assert ("duration_seconds", "baseline", "value", 0.002) in rows
    assert ("duration_seconds", "candidate", "value", 0.002) in rows
    assert not any("run_id" in row[0] for row in rows)


@pytest.mark.parametrize("lab", ["03_training_readiness", "04_inference_readiness"])
def test_readiness_publication_uses_recorded_placement_invariants(lab):
    import copy
    import json

    recipe = json.loads(
        (ROOT / "advanced-gpu-communication/reference/observability.json").read_text()
    )["labs"][lab]
    baseline = result()
    baseline.update(
        lab_id=lab,
        measurements={"world_size": 2, "distinct_host_count": 2},
        correctness={"two_nodes": True, "nccl_all_reduce": True},
    )
    candidate = copy.deepcopy(baseline)
    candidate["experiment"]["slurm_job_id"] = 4
    publisher = load("publish_results")
    rows = publisher.comparison_payload(baseline, candidate, recipe)
    for field in ("world_size", "distinct_host_count"):
        assert (field, "baseline", "value", 2) in rows
        assert (field, "candidate", "value", 2) in rows
        changed = copy.deepcopy(candidate)
        changed["measurements"][field] = 1
        with pytest.raises(ValueError, match="workload invariant"):
            publisher.comparison_payload(baseline, changed, recipe)
        del changed["measurements"][field]
        with pytest.raises(ValueError, match="Missing declared measurement"):
            publisher.comparison_payload(baseline, changed, recipe)


@pytest.mark.parametrize(
    "defect",
    [
        "profile",
        "seed",
        "environment",
        "instrumented",
        "correctness",
        "missing",
        "nonfinite",
        "controls",
    ],
)
def test_invalid_acceptance_comparisons_fail(defect):
    left, right = result(), result()
    if defect in ("profile", "seed", "environment"):
        right[defect] = "different"
    elif defect == "instrumented":
        right["experiment"][defect] = True
    elif defect == "correctness":
        right[defect]["reference"] = False
    elif defect == "missing":
        right["measurements"].clear()
    elif defect == "nonfinite":
        right["measurements"]["elapsed_ms"] = float("nan")
    else:
        right["experiment"]["parameters"] = {"warmup": 9}
    with pytest.raises(ValueError):
        load("publish_results").comparison_payload(left, right, RECIPE)


def test_clean_callable_has_no_annotation_wrapper(monkeypatch):
    module = load("course_evidence")
    monkeypatch.delenv("COURSE_CAPTURE", raising=False)
    fn = lambda: 4
    assert module.annotated_operation(fn, "course_measure") is fn


def test_capture_is_bounded_and_disables_clock_changes(tmp_path):
    module = load("profile_lab")
    command = module.capture_command(
        "ncu", ["python3", "lab.py"], tmp_path / "capture", "course_measure", "vector"
    )
    assert command[command.index("--launch-count") + 1] == "1"
    assert command[command.index("--clock-control") + 1] == "none"
    assert command[command.index("--nvtx-include") + 1] == "course_measure/"


@pytest.mark.parametrize(
    "lab,region,kernel",
    [
        ("07_profile_workload", "projection", ".*"),
        ("15_tail_load_balance", "tail_measure", "uniform_tail_probe"),
        ("19_h2d_pipeline", "consume_batch", ".*"),
        ("20_d2h_pipeline", "produce_output", ".*(gemm|nvjet).*"),
    ],
)
def test_compute_command_selects_workload_region(
    tmp_path, monkeypatch, lab, region, kernel
):
    import json
    import shlex
    from types import SimpleNamespace

    module = load("profile_lab")
    inventory = json.loads(
        (ROOT / "gpu-optimizations/reference/observability.json").read_text()
    )
    recipe = inventory["labs"][lab]
    command = shlex.split(recipe["compute_command"])
    export = next(arg for arg in command if arg.startswith("--export="))
    environment = dict(item.split("=", 1) for item in export.split(",")[1:])
    assert environment["COURSE_PROFILE_RANGE"] == region
    monkeypatch.delenv("COURSE_PROFILE_KERNEL", raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("SLURM_JOB_ID", "41")
    monkeypatch.setenv("RANK", "0")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    (tmp_path / "reference").mkdir()
    (tmp_path / "reference/observability.json").write_text(json.dumps(inventory))
    lab_index = command.index(f"labs/{lab}.py")
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "profile_lab.py",
            "--lab",
            lab,
            "--",
            "python3",
            *command[lab_index:],
        ],
    )
    monkeypatch.setattr(module.shutil, "which", lambda name: "/prepared/" + name)
    observed = []

    def launch(argv, **kwargs):
        observed.append(argv)
        assert kwargs["env"]["COURSE_CAPTURE"] == "1"
        Path(argv[argv.index("--export") + 1]).with_suffix(".ncu-rep").write_bytes(
            b"test report"
        )
        return SimpleNamespace(wait=lambda **kwargs: 0)

    monkeypatch.setattr(module.subprocess, "Popen", launch)
    with pytest.raises(SystemExit) as stopped:
        module.main()
    assert stopped.value.code == 0
    assert len(observed) == 1
    captured = observed[0]
    assert captured[captured.index("--nvtx-include") + 1] == region + "/"
    assert captured[captured.index("--kernel-name") + 1] == "regex:" + kernel
    assert captured[captured.index("--launch-count") + 1] == "1"
    assert captured[captured.index("--clock-control") + 1] == "none"
    if lab == "07_profile_workload":
        assert "--external-only" in captured and "--export-trace" not in captured


def test_prefill_decode_workload_study_accepts_declared_token_change():
    import json

    recipe = json.loads(
        (ROOT / "llm-inference/reference/observability.json").read_text()
    )["labs"]["09_hf_prefill_decode"]

    def artifact(tokens):
        doc = result(new_tokens=tokens)
        doc["lab_id"] = recipe["lab"]
        doc["measurements"] = {
            "model": "test-model",
            "revision": "frozen-revision",
            "prompt_tokens": 32,
            "requested_new_tokens_per_request": tokens,
            "generated_new_tokens_per_request": tokens,
            "decode_steps_per_request": tokens - 1,
            "requests": 2,
            "prefill_timing": {"median_ms": 4},
            "decode_timing": {"median_ms": 2} if tokens > 1 else None,
        }
        return doc

    publisher = load("publish_results")
    baseline, candidate = artifact(1), artifact(8)
    rows = publisher.comparison_payload(baseline, candidate, recipe)
    assert ("decode_steps_per_request", "baseline", "value", 0) in rows
    assert ("decode_steps_per_request", "candidate", "value", 7) in rows
    assert not any(
        row[:2] == ("decode_timing_median_seconds", "baseline") for row in rows
    )
    for field, different in (("model", "another-model"), ("prompt_tokens", 64)):
        changed = artifact(8)
        changed["measurements"][field] = different
        with pytest.raises(ValueError, match="workload invariant"):
            publisher.comparison_payload(baseline, changed, recipe)
    for field in (
        "requested_new_tokens_per_request",
        "generated_new_tokens_per_request",
        "decode_steps_per_request",
    ):
        changed = artifact(1)
        changed["measurements"][field] += 1
        with pytest.raises(ValueError, match="workload invariant"):
            publisher.comparison_payload(baseline, changed, recipe)


def test_goodput_publication_accepts_text_variation_but_preserves_work(tmp_path):
    import copy
    import json

    from test_advanced_vendor_evidence import aiperf_fixture
    from test_course_review_fixes import load_lab

    recipe = json.loads(
        (ROOT / "advanced-gpu-communication/reference/observability.json").read_text()
    )["labs"]["34_serving_goodput"]
    _, outputs = aiperf_fixture(tmp_path)
    baseline = result(
        concurrency=8, ttft_slo_ms=1000, itl_slo_ms=50, implementation_sha256="a" * 64
    )
    baseline["lab_id"] = recipe["lab"]
    with load_lab("advanced-gpu-communication/labs/34_serving_goodput.py") as lab:
        baseline["measurements"] = lab.summarize(tmp_path, 2, output_tokens=4, seed=17)
        candidate = copy.deepcopy(baseline)
        candidate["experiment"]["parameters"]["concurrency"] = 16
        outputs["data"][0]["response_text"] = "Different generated wording"
        (tmp_path / "outputs.json").write_text(json.dumps(outputs))
        candidate["measurements"] = lab.summarize(tmp_path, 2, output_tokens=4, seed=17)
    assert (
        baseline["measurements"]["output_signature"]
        != candidate["measurements"]["output_signature"]
    )
    publisher = load("publish_results")
    assert publisher.comparison_payload(baseline, candidate, recipe)
    for field, value in (
        ("workload_sha256", "b" * 64),
        ("requested_output_tokens", 5),
        ("measurement_contract", "different-contract"),
        ("input_lengths", [8, 9]),
        ("output_lengths", [4, 3]),
        ("completed_requests", 1),
    ):
        changed = copy.deepcopy(candidate)
        changed["measurements"][field] = value
        with pytest.raises(ValueError, match="workload invariant"):
            publisher.comparison_payload(baseline, changed, recipe)
    for field in ("measurement_contract", "workload_sha256", "requested_output_tokens"):
        old_baseline, old_candidate = copy.deepcopy(baseline), copy.deepcopy(candidate)
        del old_baseline["measurements"][field]
        del old_candidate["measurements"][field]
        with pytest.raises(ValueError, match="Missing declared measurement"):
            publisher.comparison_payload(old_baseline, old_candidate, recipe)
    for field in ("ttft_slo_ms", "itl_slo_ms", "implementation_sha256"):
        changed = copy.deepcopy(candidate)
        changed["experiment"]["parameters"][field] = "changed"
        with pytest.raises(ValueError, match="undeclared controls"):
            publisher.comparison_payload(baseline, changed, recipe)
    changed = copy.deepcopy(candidate)
    changed["environment"]["model_revision"] = "another-model"
    with pytest.raises(ValueError, match="same environment"):
        publisher.comparison_payload(baseline, changed, recipe)


def test_selected_results_ignore_historical_dashboard_end_time():
    """Late-published summaries stay current; GPU/node traces keep the chosen window."""
    import json
    import re

    generator = load("build_observability")
    for course in generator.COURSES:
        inventory = json.loads(
            (ROOT / course / "reference/observability.json").read_text()
        )
        for recipe in [inventory["setup"], *inventory["labs"].values()]:
            board = generator.dashboard(course, recipe)
            for panel in board["panels"]:
                for target in panel.get("targets", []):
                    if "course_lab_" in target["expr"]:
                        assert target["instant"]
                        assert "@ now()" in target["expr"], (
                            course,
                            recipe["lab"],
                            panel["title"],
                        )
                        assert target["editorMode"] == "code"
                        if "_unix_seconds{" in target["expr"]:
                            assert re.fullmatch(
                                r"\(course_lab_(?:started|ended)_unix_seconds"
                                r"\{[^}]+\} @ now\(\)\) \* 1000",
                                target["expr"],
                            ), (
                                "Resolve the selected timestamp before converting to milliseconds"
                            )
                    else:
                        assert target["range"] and not target["instant"]
                        assert "@" not in target["expr"]


def test_result_units_do_not_format_case_labels_or_metadata():
    """Live numeric labels must not become durations or invalid timestamps."""
    import json

    generator = load("build_observability")
    inventory = json.loads(
        (ROOT / "gpu-fundamentals/reference/observability.json").read_text()
    )
    board = generator.dashboard(
        "gpu-fundamentals", inventory["labs"]["01_cpu_gpu_crossover"]
    )
    for panel in board["panels"]:
        if not panel["targets"][0].get("instant"):
            continue
        assert panel["fieldConfig"]["defaults"]["unit"] == "none"
        if panel["type"] == "barchart":
            overrides = {
                item["matcher"]["options"]: item["properties"]
                for item in panel["fieldConfig"]["overrides"]
            }
            assert {"id": "unit", "value": "string"} in overrides["case"]
            assert {"id": "unit", "value": "s"} in overrides["Value #A"]
        else:
            fields = panel["transformations"][0]["options"]["include"]["names"]
            assert fields == ["slot", "case", "Value", "Value #A"]
            if panel["title"] in ("Experiment start", "Experiment end"):
                assert panel["fieldConfig"]["overrides"][0]["properties"] == [
                    {"id": "unit", "value": "dateTimeAsIso"}
                ]


@pytest.mark.parametrize("defect", [None, "value", "case", "default"])
def test_dashboard_validator_checks_value_units_separately_from_labels(defect):
    import json

    course = "gpu-fundamentals"
    lab = "01_cpu_gpu_crossover"
    recipe = json.loads((ROOT / course / "reference/observability.json").read_text())[
        "labs"
    ][lab]
    board = load("build_observability").dashboard(course, recipe)
    panel = next(p for p in board["panels"] if p["type"] == "barchart")
    if defect == "default":
        panel["fieldConfig"]["defaults"]["unit"] = "s"
    elif defect:
        field = "case" if defect == "case" else "Value #A"
        for override in panel["fieldConfig"]["overrides"]:
            if override["matcher"]["options"] == field:
                for prop in override["properties"]:
                    if prop["id"] == "unit":
                        prop["value"] = "bytes"
    validator = load("validate_course_template")
    guide = (ROOT / course / "reference/labs" / (lab + ".md")).read_text()
    if defect:
        with pytest.raises(SystemExit, match="measurement panel or unit"):
            validator.validate_lab_evidence(recipe, guide, board, course)
    else:
        validator.validate_lab_evidence(recipe, guide, board, course)


def test_context_legends_identify_nodes_and_local_gpu_indices():
    """Node exporter has instance labels, not DCGM Hostname/gpu labels."""
    import json

    generator = load("build_observability")
    for course in generator.COURSES:
        inventory = json.loads(
            (ROOT / course / "reference/observability.json").read_text()
        )
        for recipe in [inventory["setup"], *inventory["labs"].values()]:
            board = generator.dashboard(course, recipe)
            node_targets = []
            for panel in board["panels"]:
                for target in panel.get("targets", []):
                    if "node_cpu_seconds_total" in target["expr"] or (
                        "node_memory_MemAvailable_bytes" in target["expr"]
                    ):
                        node_targets.append(target)
                        assert target["legendFormat"] == "{{instance}}", (
                            course,
                            recipe["lab"],
                            panel["title"],
                        )
                    elif "DCGM_FI_DEV_" in target["expr"]:
                        assert target["legendFormat"] == "{{Hostname}} GPU {{gpu}}"
            assert len(node_targets) == 2
