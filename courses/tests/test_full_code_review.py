"""CPU regressions for the complete course executable review."""

from __future__ import annotations

import ast
import io
import json
import os
import re
import shlex
import subprocess
import sys
from types import SimpleNamespace

import pytest
from test_course_review_fixes import COURSES, ROOT, load_lab


def test_tooling_preflight_embedded_python_compiles():
    source = (ROOT / "gpu-optimizations/slurm/tooling_preflight.sbatch").read_text()
    line = next(line for line in source.splitlines() if " -c 'import torch;" in line)
    command = shlex.split(line)
    compile(command[command.index("-c") + 1], "tooling-preflight", "exec")


def test_operator_profiler_uses_averaged_device_events():
    tree = ast.parse(
        (ROOT / "gpu-fundamentals/labs/08_operator_to_kernels.py").read_text()
    )
    main = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    names = {"cuda_events", "top_cuda", "rows"}
    statements = [
        node
        for node in main.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id in names
            for target in node.targets
        )
    ]
    namespace = {
        "events": [
            SimpleNamespace(key="aten::mm", count=2, self_device_time_total=7.5),
            SimpleNamespace(key="cpu", count=1, self_device_time_total=0),
        ]
    }
    exec(
        compile(
            ast.Module(body=statements, type_ignores=[]), "profiler-summary", "exec"
        ),
        namespace,
    )
    assert namespace["rows"] == [
        {"event": "aten::mm", "calls": 2, "self_cuda_time_us": 7.5}
    ]


def trial_record(course, index):
    training = course == "llm-training"
    baseline, candidate = (
        ("baseline", "candidate") if training else ("materialized", "sdpa")
    )
    return {
        "schema": "gpu-course-result/v1",
        "lab_id": "31_training_capstone" if training else "32_inference_capstone",
        "profile": "smoke",
        "run_id": f"{index:012x}",
        "seed": index,
        "environment": {
            "gpu_family": "NVIDIA H100",
            "torch_version": "test",
            "cuda_version": "test",
        },
        "measurements": {
            "variant_order": "candidate-first" if index == 2 else "baseline-first",
            "shape": [512, 256],
            "isl": 256,
            "osl": 1,
            "concurrency": 1,
            "warmup": 3,
            "iterations": 10,
            "peak_tflops_reference": None,
            "timing": {baseline: {"median_ms": 2.0}, candidate: {"median_ms": 1.0}},
        },
        "correctness": {"full_update_close" if training else "output_allclose": True},
    }


def run_aggregator(course, trials, tmp_path, monkeypatch):
    paths = []
    for index, trial in enumerate(trials):
        path = tmp_path / f"trial-{index}.json"
        path.write_text(json.dumps(trial))
        paths.append(str(path))
    output = tmp_path / "summary.json"
    monkeypatch.setattr(
        sys, "argv", ["aggregate", "--input", *paths, "--output", str(output)]
    )
    with load_lab(f"{course}/tools/aggregate_capstone.py") as module:
        module.main()
    return output


@pytest.mark.parametrize("course", ("llm-training", "llm-inference"))
@pytest.mark.parametrize(
    "value", (float("nan"), float("inf"), float("-inf"), True, "2.0", 0, -1)
)
def test_capstone_rejects_invalid_timing(course, value, tmp_path, monkeypatch):
    trials = [trial_record(course, index) for index in (1, 2, 3)]
    key = "baseline" if course == "llm-training" else "materialized"
    for trial in trials:
        trial["measurements"]["timing"][key]["median_ms"] = value
    with pytest.raises(SystemExit, match="finite|numeric|positive"):
        run_aggregator(course, trials, tmp_path, monkeypatch)
    assert not (tmp_path / "summary.json").exists()


@pytest.mark.parametrize("course", ("llm-training", "llm-inference"))
def test_capstone_rejects_overflowing_ratio(course, tmp_path, monkeypatch):
    trials = [trial_record(course, index) for index in (1, 2, 3)]
    baseline, candidate = (
        ("baseline", "candidate")
        if course == "llm-training"
        else ("materialized", "sdpa")
    )
    for trial in trials:
        trial["measurements"]["timing"][baseline]["median_ms"] = 1e308
        trial["measurements"]["timing"][candidate]["median_ms"] = 1e-308
    with pytest.raises(SystemExit, match="finite"):
        run_aggregator(course, trials, tmp_path, monkeypatch)
    assert not (tmp_path / "summary.json").exists()


@pytest.mark.parametrize("course", ("llm-training", "llm-inference"))
@pytest.mark.parametrize(
    "field", ("profile", "environment", "warmup", "iterations", "workload")
)
def test_capstone_rejects_missing_shared_contract(course, field, tmp_path, monkeypatch):
    trials = [trial_record(course, index) for index in (1, 2, 3)]
    for trial in trials:
        if field in {"profile", "environment"}:
            del trial[field]
        else:
            key = (
                ("shape" if course == "llm-training" else "isl")
                if field == "workload"
                else field
            )
            del trial["measurements"][key]
    with pytest.raises(SystemExit, match="contract"):
        run_aggregator(course, trials, tmp_path, monkeypatch)
    assert not (tmp_path / "summary.json").exists()


@pytest.mark.parametrize("course", ("llm-training", "llm-inference"))
@pytest.mark.parametrize(
    "field,value",
    (
        ("profile", None),
        ("environment", {}),
        ("environment", {"gpu_family": "CPU"}),
        ("warmup", True),
        ("iterations", -1),
        ("workload", "256"),
    ),
)
def test_capstone_rejects_invalid_shared_contract(
    course, field, value, tmp_path, monkeypatch
):
    trials = [trial_record(course, index) for index in (1, 2, 3)]
    for trial in trials:
        if field in {"profile", "environment"}:
            trial[field] = value
        else:
            key = (
                ("shape" if course == "llm-training" else "isl")
                if field == "workload"
                else field
            )
            trial["measurements"][key] = value
    with pytest.raises(SystemExit, match="contract"):
        run_aggregator(course, trials, tmp_path, monkeypatch)
    assert not (tmp_path / "summary.json").exists()


@pytest.mark.parametrize(
    "case",
    (
        "valid",
        "garbage",
        "infinity",
        "missing",
        "duplicate",
        "pooled",
        "zero",
        "overflow",
    ),
)
def test_cuda_capstone_validates_each_trial(case, tmp_path):
    source = (
        ROOT / "custom-cuda-kernels/slurm/capstone_three_trials.sbatch"
    ).read_text()
    program = source.split('-v executable_sha256="${executable_sha256}" \'', 1)[
        1
    ].split('\' "${trial_logs[@]}"', 1)[0]
    records = [
        f"variant_order={'candidate-first' if index == 2 else 'baseline-first'}\nelements=1024\nbaseline_median_ms=2\ncandidate_median_ms=1\n"
        for index in (1, 2, 3)
    ]
    if case == "garbage":
        records = [value.replace("ms=2", "ms=2garbage") for value in records]
    elif case == "infinity":
        records = [value.replace("ms=2", "ms=1e999") for value in records]
    elif case == "missing":
        records[1] = records[1].replace("elements=1024\n", "")
    elif case == "duplicate":
        records[1] += "elements=1024\n"
    elif case == "pooled":
        records[0] += "baseline_median_ms=2\n"
        records[1] = records[1].replace("baseline_median_ms=2\n", "")
    elif case == "zero":
        records[1] = records[1].replace("ms=2", "ms=0")
    elif case == "overflow":
        records = [
            value.replace("ms=2", "ms=1e308").replace("ms=1\n", "ms=1e-308\n")
            for value in records
        ]
    paths = []
    for index, record in enumerate(records):
        path = tmp_path / f"trial-{index}.txt"
        path.write_text(record)
        paths.append(str(path))
    result = subprocess.run(
        ["awk", "-F=", program, *paths], capture_output=True, text=True, check=False
    )
    if case == "valid":
        assert result.returncode == 0, result.stderr
        assert "decision=candidate-for-scoped-keep" in result.stdout
    else:
        assert result.returncode != 0
        assert "decision=" not in result.stdout


@pytest.mark.parametrize(
    "launcher,model,revision",
    (
        ("aiperf.sbatch", "model", "revision"),
        ("vllm_chunked_prefill_ab.sbatch", "model", "revision"),
        ("vllm_speculative_ab.sbatch", "target_model", "target_revision"),
    ),
)
def test_aiperf_uses_server_tokenizer_revision(launcher, model, revision):
    source = (ROOT / "llm-inference/slurm" / launcher).read_text()
    command = re.search(r"aiperf profile[^\n]*(?:\\\n[^\n]*)*", source).group()
    result = subprocess.run(
        ["bash", "-c", 'aiperf() { printf "%s\\n" "$@"; }; ' + command],
        env={**os.environ, model: "example/model", revision: "a" * 40},
        capture_output=True,
        text=True,
        check=True,
    )
    arguments = result.stdout.splitlines()
    assert arguments[arguments.index("--tokenizer-revision") + 1] == "a" * 40


def test_training_profiler_honors_seed(monkeypatch):
    import torch

    class ModelReached(Exception):
        pass

    samples = []

    def build(*args, **kwargs):
        samples.append(torch.rand(4))
        raise ModelReached

    with load_lab("llm-training/labs/30_training_profiler.py") as module:
        monkeypatch.setattr(module, "load_torch", lambda: torch)
        monkeypatch.setattr(module, "require_h100", lambda torch: {})
        monkeypatch.setattr(module, "build_tiny_lm", build)
        with torch.random.fork_rng(devices=[]):
            for seed in (17, 17, 18):
                monkeypatch.setattr(sys, "argv", ["profiler", "--seed", str(seed)])
                with pytest.raises(ModelReached):
                    module.main()
    assert torch.equal(samples[0], samples[1])
    assert not torch.equal(samples[1], samples[2])


@pytest.mark.parametrize("ending", ("complete", "truncated", "error"))
def test_streaming_requires_successful_completion(ending, tmp_path, monkeypatch):
    content = 'data: {"choices":[{"text":"partial"}]}\n\n'
    if ending == "error":
        content += 'data: {"error":{"message":"engine failed"}}\n\n'
    if ending != "truncated":
        content += "data: [DONE]\n\n"
    output = tmp_path / "stream.json"
    with load_lab("llm-inference/labs/15_streaming_client.py") as module:
        monkeypatch.setattr(
            sys, "argv", ["stream", "--requests", "1", "--output", str(output)]
        )
        monkeypatch.setattr(
            module.urllib.request,
            "urlopen",
            lambda *args, **kwargs: io.BytesIO(content.encode()),
        )
        if ending == "complete":
            module.main()
            assert json.loads(output.read_text())["correctness"][
                "all_streams_produced_content"
            ]
        else:
            with pytest.raises(RuntimeError, match="stream|Stream"):
                module.main()
            assert not output.exists()


@pytest.mark.parametrize(
    "choice", ({}, {"text": ""}, {"text": None}, {"text": "response"})
)
def test_engine_profile_checks_generated_text(choice, tmp_path, monkeypatch):
    with load_lab("llm-inference/labs/30_engine_profile.py") as module:
        monkeypatch.setattr(
            sys,
            "argv",
            ["engine", "--model", "example/model", "--output-dir", str(tmp_path)],
        )
        monkeypatch.setattr(
            module,
            "request_json",
            lambda url, payload=None: {"data": []}
            if payload is None
            else {"choices": [choice]},
        )
        if choice.get("text"):
            module.main()
            assert list(tmp_path.glob("*.json"))
        else:
            with pytest.raises(SystemExit, match="generated text"):
                module.main()
            assert not list(tmp_path.glob("*.json"))


@pytest.mark.parametrize(
    "document",
    (
        '<a href="javascript:alert(1)">run</a>',
        '<a href="//example.com">link</a>',
        '<img src="https://example.com/pixel.png">',
        '<svg role="img" aria-labelledby="test"><use href="https://docs.nvidia.com/icons.svg#x"/></svg>',
        '<svg role="img" aria-labelledby="test"><image xlink:href="https://example.com/pixel.png"/></svg>',
        '<link rel="stylesheet" href="https://docs.nvidia.com/style.css">',
        '<a href="javascript:alert(1)" href="#safe">run</a>',
        '<img src="https://example.com/pixel.png" src="data:image/png;base64,AA==">',
        '<div style="background-image:url(https://example.com/pixel.png)">x</div>',
        '<style>@import "https://example.com/style.css";</style>',
        "<style>body { background: url(https://example.com/pixel.png); }</style>",
        '<meta http-equiv="refresh" content="0;url=https://example.com/">',
    ),
)
def test_publication_parser_rejects_active_or_remote_resources(document):
    with load_lab("tools/validate_course_template.py") as module:
        parser = module.Parser()
        parser.feed('<section id="official-references">' + document + "</section>")
        assert parser.errors


def test_publication_parser_preserves_inline_resources():
    with load_lab("tools/validate_course_template.py") as module:
        parser = module.Parser()
        parser.feed(
            '<link rel="icon" href="data:,">'
            '<a href="#lesson">lesson</a><img src="data:image/png;base64,AA=="><svg role="img" aria-labelledby="test"><use href="#shape"/></svg><section id="official-references"><a href="https://docs.nvidia.com/">reference</a></section>'
        )
        assert not parser.errors


def test_inline_code_preserves_literal_markdown():
    with load_lab("tools/build_course_html.py") as module:
        assert (
            module.inline("`x ** 2` and `y ** 2`")
            == "<code>x ** 2</code> and <code>y ** 2</code>"
        )
        assert (
            module.inline("`[x](https://example.com)` and **bold**")
            == "<code>[x](https://example.com)</code> and <strong>bold</strong>"
        )
        assert module.inline("[`x`](#x)") == '<a href="#x"><code>x</code></a>'


@pytest.mark.parametrize("course", COURSES)
def test_reviewed_validator_matches_canonical_template(course):
    assert (ROOT / course / "tools/validate_course.py").read_bytes() == (
        ROOT / "tools/validate_course_template.py"
    ).read_bytes()
