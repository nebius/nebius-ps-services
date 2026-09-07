"""Nonfinite fault controls for the labs' numerical acceptance boundaries."""

from __future__ import annotations

import ast
import math
import sys
from types import SimpleNamespace

import pytest

from test_course_review_fixes import ROOT, load_lab


def acceptance_segment(relative, after, before):
    """Execute the real acceptance statements, omitting GPU setup and timing."""
    tree = ast.parse((ROOT / relative).read_text())
    main = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    body = next(
        (node.body for node in main.body if isinstance(node, ast.Try)), main.body
    )

    def assigns(node, name):
        return isinstance(node, ast.Assign) and any(
            isinstance(child, ast.Name) and child.id == name
            for target in node.targets
            for child in ast.walk(target)
        )

    start = next(i for i, node in enumerate(body) if assigns(node, after)) + 1
    end = next(i for i, node in enumerate(body) if assigns(node, before))
    return compile(ast.Module(body=body[start:end], type_ignores=[]), relative, "exec")


@pytest.mark.parametrize("lab", ["02_sync_trap", "05_input_pipeline"])
@pytest.mark.parametrize("side", [0, 1])
@pytest.mark.parametrize(
    "fault", [float("nan"), float("inf"), float("-inf"), 2.0, None]
)
def test_scalar_equivalence_rejects_invalid_sums(lab, side, fault):
    values = [1.0, 1.0]
    if fault is not None:
        values[side] = fault
    if lab == "02_sync_trap":
        namespace = {"expected": values[0], "actual": values[1]}
        after = "actual"
    else:
        namespace = {
            "serial_sum": values[0],
            "worker_sum": values[1],
            "serial": {"all_batches_pinned": True},
            "worker": {"all_batches_pinned": True},
        }
        after = "worker"
    namespace["math"] = math
    segment = acceptance_segment(f"gpu-optimizations/labs/{lab}.py", after, "target")
    if fault is None:
        exec(segment, namespace)
        assert namespace["correct"] is True
    else:
        with pytest.raises(SystemExit, match="different"):
            exec(segment, namespace)


@pytest.mark.parametrize("parameter_index", [0, 1])
@pytest.mark.parametrize(
    "fault", [float("nan"), float("inf"), float("-inf"), 0.01, None]
)
def test_accumulation_checks_each_parameter_error(parameter_index, fault):
    torch = pytest.importorskip("torch")
    expected = [torch.ones(4), torch.ones(4)]
    gradients = [value.clone() for value in expected]
    if fault is not None:
        gradients[parameter_index][0] += fault
    relative = "llm-training/labs/02_gradient_accumulation.py"
    with load_lab(relative) as lab:
        namespace = dict(vars(lab))
        namespace.update(
            torch=torch,
            full_gradients=expected,
            reference=SimpleNamespace(
                parameters=lambda: [SimpleNamespace(grad=g) for g in gradients]
            ),
        )
        segment = acceptance_segment(relative, "micro_metrics", "target")
        if fault is None:
            exec(segment, namespace)
            assert namespace["sampled_gradient_error"] == 0
        else:
            with pytest.raises(SystemExit, match="[Gg]radient"):
                exec(segment, namespace)


@pytest.mark.parametrize("error_index", range(5))
@pytest.mark.parametrize(
    "fault", [float("nan"), float("inf"), float("-inf"), 0.1, None]
)
def test_tensor_parallel_rejects_each_error_through_rank_consensus(error_index, fault):
    torch = pytest.importorskip("torch")
    errors = [0.0] * 5
    if fault is not None:
        expected = torch.ones(4)
        observed = expected.clone()
        observed[0] += fault
        with load_lab("llm-training/labs/19_tensor_parallel_linear.py") as lab:
            errors[error_index] = lab.relative_l2(torch, observed, expected)
    votes = []
    minimum = object()

    def all_reduce(value, *, op):
        assert op is minimum
        votes.append(value.item())

    proxy = SimpleNamespace(
        tensor=lambda value, **kwargs: torch.tensor(value, dtype=torch.int32),
        int32=torch.int32,
        distributed=SimpleNamespace(
            all_reduce=all_reduce, ReduceOp=SimpleNamespace(MIN=minimum)
        ),
    )
    namespace = dict(
        zip(
            (
                "forward_error",
                "weight_gradient_error",
                "input_gradient_error",
                "update_error",
                "row_forward_error",
            ),
            errors,
            strict=True,
        )
    )
    namespace.update(math=math, torch=proxy, device="cpu")
    segment = acceptance_segment(
        "llm-training/labs/19_tensor_parallel_linear.py",
        "row_forward_error",
        "timed_input",
    )
    if fault is None:
        exec(segment, namespace)
        assert votes == [1]
    else:
        with pytest.raises(SystemExit, match="Tensor-parallel equivalence failed"):
            exec(segment, namespace)
        assert votes == [0], "every rank must reach rejection consensus before exit"


@pytest.mark.parametrize("prompt_index", range(4))
@pytest.mark.parametrize(
    "fault", [float("nan"), float("inf"), float("-inf"), 0.1, None]
)
def test_padding_main_rejects_invalid_prompt_before_publishing(
    monkeypatch, prompt_index, fault
):
    torch = pytest.importorskip("torch")

    class Batch(dict):
        def __getattr__(self, key):
            return self[key]

        def to(self, device):
            return self

    class Tokenizer:
        @classmethod
        def from_pretrained(cls, *args, **kwargs):
            return cls()

        def __call__(self, prompts, **kwargs):
            if isinstance(prompts, str):
                return SimpleNamespace(input_ids=[1] * prompts.count("Explain"))
            lengths = [prompt.count("Explain") for prompt in prompts]
            width = max(lengths)
            return Batch(
                input_ids=torch.ones((len(lengths), width), dtype=torch.long),
                attention_mask=torch.tensor(
                    [[1] * length + [0] * (width - length) for length in lengths]
                ),
            )

    class Model:
        @classmethod
        def from_pretrained(cls, *args, **kwargs):
            return cls()

        def to(self, device):
            return self

        def eval(self):
            return self

        def __call__(self, input_ids, attention_mask, **kwargs):
            logits = torch.ones((*input_ids.shape, 2))
            bucket_width = 4 if prompt_index < 2 else 32
            if fault is not None and input_ids.shape == (2, bucket_width):
                logits[prompt_index % 2] += fault
            return SimpleNamespace(logits=logits)

    monkeypatch.setitem(
        sys.modules,
        "transformers",
        SimpleNamespace(AutoTokenizer=Tokenizer, AutoModelForCausalLM=Model),
    )
    records = []
    with load_lab("llm-inference/labs/18_padding_bucketing.py") as lab:
        monkeypatch.setattr(lab, "load_torch", lambda: torch)
        monkeypatch.setattr(lab, "require_h100", lambda _: {})
        monkeypatch.setattr(lab, "seed_everything", lambda *args: None)
        monkeypatch.setattr(lab, "cuda_times_ms", lambda *args, **kwargs: [1.0])
        monkeypatch.setattr(
            lab, "write_result", lambda *args, **kwargs: records.append(kwargs)
        )
        monkeypatch.setattr(sys, "argv", ["lab", "--profile", "smoke"])
        if fault is None:
            lab.main()
            assert records[0]["correctness"]["equivalent_last_token_logits"] is True
            assert records[0]["measurements"]["maximum_last_logit_relative_l2"] == 0
        else:
            with pytest.raises(SystemExit, match="logits|logit norms"):
                lab.main()
            assert records == []
