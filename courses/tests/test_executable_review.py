"""CPU fault injection for lab numerics, training and offline validation."""

import ast
from contextlib import contextmanager
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from test_course_review_fixes import ROOT, load_lab


class CPUTorch:
    """Replace only allocation/device boundaries, retaining real tensor operations."""

    def __init__(self, torch):
        self.torch = torch
        self.cuda = SimpleNamespace(
            is_available=lambda: False,
            synchronize=lambda: None,
            empty_cache=lambda: None,
            memory_allocated=lambda: 0,
            reset_peak_memory_stats=lambda: None,
            max_memory_allocated=lambda: 0,
        )

    def __getattr__(self, name):
        value = getattr(self.torch, name)
        if name not in {"randn", "zeros"}:
            return value

        def allocate(*args, **kwargs):
            kwargs["device"] = "cpu"
            return value(*args, **kwargs)

        return allocate


@pytest.mark.parametrize("fixture", ["cancellation", 17, 1234])
def test_library_decision_accepts_independent_bf16_rounding(monkeypatch, fixture):
    torch = pytest.importorskip("torch")
    torch.manual_seed(fixture if isinstance(fixture, int) else 17)
    x = torch.randn(512, 512, dtype=torch.bfloat16)
    weight = torch.randn_like(x)
    bias = torch.randn(512, dtype=torch.bfloat16)
    if fixture == "cancellation":
        x.zero_()
        weight.zero_()
        bias.zero_()
        x[0, 0], weight[0, 0], bias[0] = 1.125, 3.625, -4
        assert torch.relu(x @ weight + bias)[0, 0].item() == 0.0625
        assert torch.relu(torch.addmm(bias, x, weight))[0, 0].item() == 0.078125
        assert not torch.allclose(
            torch.relu(x @ weight + bias),
            torch.relu(torch.addmm(bias, x, weight)),
            rtol=0.01,
            atol=0.01,
        )
    proxy = CPUTorch(torch)
    allocations = iter((x, bias))
    proxy.randn = lambda *a, **kw: next(allocations)
    proxy.randn_like = lambda _: weight
    timings = []

    def time_operation(_, operation, **kwargs):
        timings.append(operation())
        return [1.0, 1.1]

    records = []

    def write_result(*args, **kwargs):
        assert all(kwargs["correctness"].values())
        records.append(kwargs)

    with load_lab("gpu-optimizations/labs/16_library_first_decision.py") as lab:
        monkeypatch.setattr(lab, "load_torch", lambda: proxy)
        monkeypatch.setattr(lab, "require_h100", lambda _: {})
        monkeypatch.setattr(lab, "cuda_times_ms", time_operation)
        monkeypatch.setattr(lab, "write_result", write_result)
        monkeypatch.setattr(sys, "argv", ["lab", "--profile", "smoke"])
        lab.main()
    assert len(timings) == 2
    assert len(records) == 1
    record = records[0]
    assert record["correctness"] == {
        "composed_matches_reference": True,
        "library_addmm_matches_reference": True,
    }
    numerics = record["measurements"]["numerics"]
    assert numerics["reference_dtype"] == "float64"
    for errors in numerics["errors"].values():
        assert 0 <= errors["max_error_budget_fraction"] <= 1
    if fixture == "cancellation":
        assert numerics["errors"]["composed"]["max_abs_error"] == 0.015625
        assert numerics["errors"]["library_addmm"]["max_abs_error"] == 0


@pytest.mark.parametrize("target", ["composed", "library_addmm", "both"])
@pytest.mark.parametrize(
    "fault", ["offset", "zero", "nan", "inf", "shape", "dtype", "bias", "relu"]
)
def test_library_decision_rejects_corruption_before_timing(monkeypatch, target, fault):
    torch = pytest.importorskip("torch")
    # These rejections need cancellation, a negative preactivation and a
    # bias-only positive output; the acceptance tests retain smoke-sized inputs.
    x = torch.zeros(4, 4, dtype=torch.bfloat16)
    weight = torch.zeros_like(x)
    bias = torch.full((4,), 2, dtype=torch.bfloat16)
    x[0, 0], weight[0, 0], bias[0] = 1.125, 3.625, -4
    x[1, 1], weight[1, 1] = 1, -8
    proxy = CPUTorch(torch)
    allocations = iter((x, bias))
    proxy.randn = lambda *a, **kw: next(allocations)
    proxy.randn_like = lambda _: weight
    paths = iter(("composed", "library_addmm"))

    def relu(value):
        output = torch.relu(value)
        if value.dtype != torch.bfloat16:
            return output
        path = next(paths)
        if target not in {path, "both"}:
            return output
        if fault == "offset":
            return output + 1
        if fault == "zero":
            return torch.zeros_like(output)
        if fault in {"nan", "inf"}:
            output[0, 0] = float(fault)
            return output
        if fault == "shape":
            return output[:1]
        if fault == "dtype":
            return output.float()
        if fault == "bias":
            return torch.relu(x @ weight)
        return value  # Omit ReLU, retaining a negative preactivation.

    proxy.relu = relu
    with load_lab("gpu-optimizations/labs/16_library_first_decision.py") as lab:
        monkeypatch.setattr(lab, "load_torch", lambda: proxy)
        monkeypatch.setattr(lab, "require_h100", lambda _: {})
        monkeypatch.setattr(
            lab, "cuda_times_ms", lambda *a, **kw: pytest.fail("timed invalid output")
        )
        monkeypatch.setattr(
            lab,
            "write_result",
            lambda *a, **kw: pytest.fail("published invalid output"),
        )
        monkeypatch.setattr(sys, "argv", ["lab", "--profile", "smoke"])
        with pytest.raises(SystemExit, match="Invalid BF16|error budget exceeded"):
            lab.main()


def test_library_decision_rejects_nonfinite_reference():
    torch = pytest.importorskip("torch")
    with load_lab("gpu-optimizations/labs/16_library_first_decision.py") as lab:
        x = torch.full((2, 2), float("nan"), dtype=torch.bfloat16)
        weight = torch.ones_like(x)
        bias = torch.zeros(2, dtype=torch.bfloat16)
        with pytest.raises(SystemExit, match="Non-finite FP64 reference"):
            lab.reference_checks(torch, x, weight, bias, {"composed": weight})


@pytest.mark.parametrize("omit_update", [False, True])
def test_training_capstone_rejects_omitted_optimizer_step(monkeypatch, omit_update):
    torch = pytest.importorskip("torch")
    proxy = CPUTorch(torch)
    optimizers = []

    def optimizer(*args, **kwargs):
        result = torch.optim.SGD(*args, **kwargs)
        optimizers.append(result)
        if omit_update and len(optimizers) == 2:
            result.step = lambda: None
        return result

    proxy.optim = SimpleNamespace(SGD=optimizer)
    records = []
    with load_lab("llm-training/labs/31_training_capstone.py") as lab:
        monkeypatch.setattr(lab, "load_torch", lambda: proxy)
        monkeypatch.setattr(lab, "require_h100", lambda _: {})
        monkeypatch.setattr(lab, "cuda_times_ms", lambda *a, **kw: [1.0, 1.1])
        monkeypatch.setattr(lab, "write_result", lambda *a, **kw: records.append(kw))
        monkeypatch.setattr(sys, "argv", ["lab", "--variant-order", "baseline-first"])
        if omit_update:
            with pytest.raises(SystemExit, match="updates diverged"):
                lab.main()
            assert records == []
        else:
            lab.main()
            assert records[0]["correctness"]["full_update_close"] is True


@pytest.mark.parametrize("omitted_work", [None, "backward", "optimizer"])
def test_cuda_graph_acceptance_rejects_omitted_training_work(omitted_work):
    torch = pytest.importorskip("torch")
    path = ROOT / "llm-training/labs/27_fused_graph_trace.py"
    with load_lab(str(path.relative_to(ROOT))) as lab:
        main = next(
            n
            for n in ast.parse(path.read_text()).body
            if isinstance(n, ast.FunctionDef) and n.name == "main"
        )
        functions = [
            n
            for n in main.body
            if isinstance(n, ast.FunctionDef)
            and n.name in {"expression", "training_step"}
        ]
        gate = next(
            n
            for n in main.body
            if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "step_close" for t in n.targets)
        )
        torch.manual_seed(17)
        initial_weight = torch.randn(8, 8, dtype=torch.bfloat16) / 8**0.5
        initial_bias = torch.zeros(8, dtype=torch.bfloat16)
        state = dict(
            vars(lab),
            torch=torch,
            x=torch.randn(16, 8, dtype=torch.bfloat16),
            target=torch.randn(16, 8, dtype=torch.bfloat16),
            base_weight=initial_weight,
            base_bias=initial_bias,
        )
        exec(
            compile(ast.Module(body=functions, type_ignores=[]), str(path), "exec"),
            state,
        )
        for prefix in ("eager", "graph"):
            weight = initial_weight.clone().requires_grad_()
            bias = initial_bias.clone().requires_grad_()
            optimizer = torch.optim.SGD([weight, bias], lr=1e-3, foreach=False)
            if prefix == "graph" and omitted_work == "optimizer":
                optimizer.step = lambda: None
            if prefix == "graph" and omitted_work == "backward":
                loss = state["expression"](state["x"], weight, bias)
            else:
                loss = state["training_step"](weight, bias, optimizer)
            state[prefix + "_weight"] = weight
            state[prefix + "_bias"] = bias
            state["reference_loss" if prefix == "eager" else "graph_loss"] = loss
        exec(
            compile(ast.Module(body=[gate], type_ignores=[]), str(path), "exec"), state
        )
        assert state["step_close"] is (omitted_work is None)


@contextmanager
def cpu_cache_lab(monkeypatch):
    torch = pytest.importorskip("torch")
    proxy = CPUTorch(torch)
    linear = torch.nn.Linear

    def cpu_linear(*args, **kwargs):
        kwargs["device"] = "cpu"
        return linear(*args, **kwargs)

    proxy.nn = SimpleNamespace(Linear=cpu_linear, functional=torch.nn.functional)
    with load_lab("llm-inference/labs/08_kv_cache.py") as lab:
        # Keep the actual experiment body, with only its workload sizes reduced.
        tree = ast.parse((ROOT / "llm-inference/labs/08_kv_cache.py").read_text())
        main = next(
            n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main"
        )
        for node in ast.walk(main):
            if not isinstance(node, ast.Assign) or not isinstance(
                node.targets[0], ast.Tuple
            ):
                continue
            names = [getattr(item, "id", "") for item in node.targets[0].elts]
            if names == ["batch", "heads", "head_dim"]:
                node.value = ast.parse("1, 2, 4", mode="eval").body
            elif names == ["prompt", "generated"]:
                node.value = ast.parse("3, 2", mode="eval").body
        exec(
            compile(
                ast.fix_missing_locations(ast.Module(body=[main], type_ignores=[])),
                "cache_cpu",
                "exec",
            ),
            vars(lab),
        )
        monkeypatch.setattr(lab, "load_torch", lambda: proxy)
        monkeypatch.setattr(lab, "require_h100", lambda _: {})
        monkeypatch.setattr(sys, "argv", ["lab"])
        yield lab, torch


def test_kv_cache_validation_and_timing_do_not_build_backward_graphs(monkeypatch):
    with cpu_cache_lab(monkeypatch) as (lab, torch):
        saved = []
        timed = []
        records = []

        def timing(_, operation, **kwargs):
            output = operation()
            timed.append((torch.is_grad_enabled(), output.requires_grad))
            return [1.0, 1.1]

        monkeypatch.setattr(lab, "cuda_times_ms", timing)
        monkeypatch.setattr(lab, "write_result", lambda *a, **kw: records.append(kw))
        with torch.autograd.graph.saved_tensors_hooks(
            lambda t: saved.append(t) or t, lambda t: t
        ):
            lab.main()
        assert timed == [(False, False), (False, False)]
        assert saved == []
        assert all(records[0]["correctness"].values())


@pytest.mark.parametrize("call", [1, 2, 4, 6])
@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_kv_cache_rejects_nonfinite_prompt_and_decode(monkeypatch, call, bad):
    with cpu_cache_lab(monkeypatch) as (lab, torch):
        attention = torch.nn.functional.scaled_dot_product_attention
        calls = 0

        def injected(*args, **kwargs):
            nonlocal calls
            calls += 1
            output = attention(*args, **kwargs)
            return torch.full_like(output, bad) if calls == call else output

        monkeypatch.setattr(
            torch.nn.functional, "scaled_dot_product_attention", injected
        )
        monkeypatch.setattr(
            lab,
            "cuda_times_ms",
            lambda *a, **kw: pytest.fail("invalid output reached timing"),
        )
        monkeypatch.setattr(
            lab,
            "write_result",
            lambda *a, **kw: pytest.fail("invalid output was published"),
        )
        with pytest.raises(SystemExit, match="diverged"):
            lab.main()


def test_standalone_validation_leaves_no_temporary_bytecode(tmp_path):
    run = subprocess.run(
        [sys.executable, str(ROOT / "gpu-fundamentals/tools/validate_course.py")],
        env={**os.environ, "TMPDIR": str(tmp_path), "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    assert list(tmp_path.iterdir()) == []


def test_standalone_validation_still_rejects_invalid_python(monkeypatch):
    path = ROOT / "gpu-fundamentals/tools/validate_course.py"
    spec = importlib.util.spec_from_file_location("no_bytecode_validator", path)
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)
    read_bytes = Path.read_bytes

    def invalid_lab(path):
        if path.name == "00_cluster_preflight.py":
            return b"def invalid("
        return read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", invalid_lab)
    with pytest.raises(SyntaxError):
        validator.main()
