"""Offline engine capture must include measured work in the selected process."""

import os
import sys
from types import SimpleNamespace

import pytest
from test_course_review_fixes import load_lab


@pytest.mark.parametrize("capture", [False, True])
def test_offline_engine_process_and_measured_range(monkeypatch, capture):
    monkeypatch.setenv("COURSE_CAPTURE", "1" if capture else "0")
    monkeypatch.setenv("VLLM_ENABLE_V1_MULTIPROCESSING", "1")
    events = []

    class Engine:
        def __init__(self, **kwargs):
            events.append(("engine", os.environ["VLLM_ENABLE_V1_MULTIPROCESSING"]))

        def generate(self, prompts, sampling):
            events.append(("generate", len(prompts)))
            return [
                SimpleNamespace(
                    prompt_token_ids=[1, 2],
                    outputs=[SimpleNamespace(token_ids=[3], finish_reason="stop")],
                )
                for _ in prompts
            ]

    def annotate(operation, name):
        if not capture:
            return operation

        def wrapped(*args, **kwargs):
            events.append(("enter", name))
            result = operation(*args, **kwargs)
            events.append(("exit", name))
            return result

        return wrapped

    monkeypatch.setitem(
        sys.modules,
        "vllm",
        SimpleNamespace(LLM=Engine, SamplingParams=lambda **kwargs: kwargs),
    )
    with load_lab("llm-inference/labs/10_vllm_offline.py") as lab:
        monkeypatch.setattr(lab, "load_torch", lambda: object())
        monkeypatch.setattr(lab, "require_course_gpu", lambda _: {})
        monkeypatch.setattr(lab, "annotated_operation", annotate)
        records = []
        monkeypatch.setattr(lab, "write_result", lambda *a, **kw: records.append(kw))
        argv = ["lab", "--workload", "small", "--warmup", "1", "--iterations", "2"]
        monkeypatch.setattr(sys, "argv", argv + (["--in-process"] if capture else []))
        lab.main()
    assert events[0] == ("engine", "0" if capture else "1")
    assert events[1] == ("generate", 4)  # Warmup is outside the selected range.
    assert events.count(("generate", 4)) == 3
    assert events.count(("enter", "vllm_generate")) == (2 if capture else 0)
    assert events.count(("exit", "vllm_generate")) == (2 if capture else 0)
    assert records[0]["measurements"]["requests"] == 8
    assert all(records[0]["correctness"].values())


def test_offline_capture_rejects_child_process_before_engine_start(monkeypatch):
    monkeypatch.setenv("COURSE_CAPTURE", "1")
    monkeypatch.setattr(sys, "argv", ["lab", "--workload", "small"])
    with load_lab("llm-inference/labs/10_vllm_offline.py") as lab:
        monkeypatch.setattr(lab, "load_torch", lambda: pytest.fail("GPU setup ran"))
        with pytest.raises(SystemExit, match="--in-process"):
            lab.main()
