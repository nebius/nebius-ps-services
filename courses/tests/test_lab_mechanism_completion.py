"""Behavioral regressions for the five educational lab review findings."""

from __future__ import annotations

import math
import random
import subprocess
import sys

import pytest

from test_remaining_course_findings import COURSES, ROOT, load_pure_module


@pytest.mark.parametrize("input_grad", [False, True])
def test_training_flops_match_actual_autograd_gemms(input_grad: bool) -> None:
    torch = pytest.importorskip("torch")
    lab = load_pure_module("llm-training/labs/31_training_capstone.py")
    inputs = torch.randn(16, 8, requires_grad=input_grad)
    weight = torch.randn(8, 8, requires_grad=True)
    bias = torch.randn(8, requires_grad=True)
    with torch.profiler.profile(
        activities=[torch.profiler.ProfilerActivity.CPU], with_flops=True
    ) as profile:
        torch.addmm(bias, inputs, weight).square().mean().backward()
    gemms = [e for e in profile.key_averages() if e.key in {"aten::mm", "aten::addmm"}]
    assert sum(e.count for e in gemms) == 2 + int(input_grad)
    assert sum(e.flops for e in gemms) == lab.linear_step_flops(16, 8, input_grad)
    assert (inputs.grad is not None) == input_grad


def test_paged_kv_grows_recycles_and_rejects_atomically() -> None:
    lab = load_pure_module("llm-inference/labs/27_paged_kv.py")
    pool = lab.PagedKVPool(16, 4)
    pool.admit("a", 16)
    pool.admit("b", 17)
    first = pool.snapshot()["block_tables"]["a"]
    pool.grow("a", 17)
    assert pool.snapshot()["block_tables"]["a"] == first + [3]
    before = pool.snapshot()
    with pytest.raises(MemoryError):
        pool.grow("b", 33)
    assert pool.snapshot() == before
    with pytest.raises(MemoryError):
        pool.admit("c", 1)
    assert pool.snapshot() == before
    pool.release("a")
    pool.admit("c", 17)
    assert pool.snapshot()["block_tables"]["c"] == [0, 3]
    assert pool.snapshot()["block_tables"]["b"] == before["block_tables"]["b"]
    assert pool.snapshot()["accounting"]["internal_waste_tokens"] == 30
    pool.grow("c", 18)
    assert pool.snapshot()["block_tables"]["c"] == [0, 3]
    snapshot = pool.snapshot()
    snapshot["block_tables"]["b"].clear()
    assert pool.snapshot()["block_tables"]["b"] == [1, 2]
    for action in (
        lambda: pool.admit("b", 1),
        lambda: pool.grow("b", 1),
        lambda: pool.release("unknown"),
        lambda: pool.admit("negative", -1),
    ):
        with pytest.raises((ValueError, KeyError)):
            action()
    pool.check_invariants()


def test_paged_lifecycle_demo_asserts_real_reuse() -> None:
    lab = load_pure_module("llm-inference/labs/27_paged_kv.py")
    result = lab.lifecycle_demo(16, 4)
    assert result["checks"] and all(result["checks"].values())
    assert len(result["events"]) >= 7
    with pytest.raises(ValueError):
        lab.lifecycle_demo(16, 3)


def test_scheduler_full_prefill_is_not_a_large_chunk() -> None:
    lab = load_pure_module("llm-inference/labs/28_continuous_batching.py")
    full = lab.simulate([2048, 1], [2, 1], None, 256, [0, 1])
    chunked = lab.simulate([2048, 1], [2, 1], 64, 256, [0, 1])
    assert full["trace"][0]["prefill"] == {"0": 2048}
    assert full["trace"][0]["end"] == 8
    assert full["first_token_times"][1] > chunked["first_token_times"][1]
    for result in (full, chunked):
        assert result["scheduled_tokens"] == 2052
        assert result["completed_requests"] == 2
        for event in result["trace"]:
            assert event["end"] - event["start"] == math.ceil(event["work"] / 256)
    assert all(e["work"] <= 256 for e in chunked["trace"])
    assert all(v <= 64 for e in chunked["trace"] for v in e["prefill"].values())


@pytest.mark.parametrize("chunk", [None, 1, 64])
def test_scheduler_arrivals_and_completion_edges(chunk: int | None) -> None:
    lab = load_pure_module("llm-inference/labs/28_continuous_batching.py")
    result = lab.simulate([0, 1], [1, 1], chunk, 256, [0, 100])
    assert result["first_token_times"] == [1, 102]
    assert result["first_token_latencies"] == [1, 2]
    assert result["scheduled_tokens"] == 3
    assert lab.simulate([], [], chunk, 256)["completed_requests"] == 0
    for prompts, outputs, arrivals in [
        ([-1], [1], [0]),
        ([1], [0], [0]),
        ([1], [1], [-1]),
        ([1], [1], []),
    ]:
        with pytest.raises(ValueError):
            lab.simulate(prompts, outputs, chunk, 256, arrivals)


def test_lane_work_model_preserves_work_and_distinguishes_issue_cost() -> None:
    lab = load_pure_module("gpu-fundamentals/labs/11_scheduler_tail.py")
    mixed = lab.lane_work_model([20, 4] * 32)
    grouped = lab.lane_work_model([20] * 32 + [4] * 32)
    assert mixed["useful_lane_iterations"] == grouped["useful_lane_iterations"] == 768
    assert mixed["issued_warp_iterations"] == 40
    assert grouped["issued_warp_iterations"] == 24
    assert mixed["modeled_lane_utilization"] == 0.6
    assert grouped["modeled_lane_utilization"] == 1.0
    assert lab.lane_work_model([4])["modeled_lane_utilization"] == 1 / 32
    for bad in ([], [-1], [0]):
        with pytest.raises(ValueError):
            lab.lane_work_model(bad)


def test_cuda_pipeline_has_work_sweep_reference_and_partial_tiles() -> None:
    source = (ROOT / "custom-cuda-kernels/labs/08_async_pipeline.cu").read_text()
    assert "--work-iterations" in source
    assert "{0, 8, 32, 128}" in source
    assert "std::fma" in source
    assert "check_close(expected, serial_observed)" in source
    assert "check_close(expected, pipelined_observed)" in source
    assert "logical_flops_per_byte" in source
    assert "4099" in source
    assert "single_block_mechanism" in source
    cmake = (ROOT / "custom-cuda-kernels/CMakeLists.txt").read_text()
    assert "08_async_pipeline_zero_work" in cmake


@pytest.mark.parametrize("course", COURSES)
def test_worked_lab_guides_are_published_and_navigable(course: str) -> None:
    guide = (ROOT / course / "reference/lab-mechanisms.md").read_text()
    page = (ROOT / course / "index.html").read_text()
    assert len(guide.split()) >= 450
    assert 'href="#guide-reference-lab-mechanisms"' in page
    assert 'data-source="reference/lab-mechanisms.md"' in page
    assert 'id="guide-reference-lab-mechanisms"' in page
    assert "Lab mechanisms and evidence" in (ROOT / course / "README.md").read_text()
    if course != "gpu-optimizations":
        assert any(
            "(../lab-mechanisms.md)" in path.read_text()
            for path in (ROOT / course / "reference/labs").glob("*.md")
        )
        assert page.count('href="#guide-reference-lab-mechanisms"') >= 3


def test_scheduler_trace_conserves_per_request_work() -> None:
    lab = load_pure_module("llm-inference/labs/28_continuous_batching.py")
    rng = random.Random(42)
    for _ in range(30):
        prompts = [rng.randrange(0, 40) for _ in range(5)]
        outputs = [rng.randrange(1, 8) for _ in range(5)]
        arrivals = [rng.randrange(0, 10) for _ in range(5)]
        for chunk in (None, 3, 8):
            result = lab.simulate(prompts, outputs, chunk, 8, arrivals)
            prefill = [0] * 5
            decode = [0] * 5
            for event in result["trace"]:
                assert event["work"] > 0
                for key, count in event["prefill"].items():
                    index = int(key)
                    assert arrivals[index] <= event["start"]
                    assert not decode[index]
                    prefill[index] += count
                    if chunk is None:
                        assert count == prompts[index]
                    else:
                        assert count <= chunk
                for index in event["decode"]:
                    assert prefill[index] == prompts[index]
                    assert str(index) not in event["prefill"]
                    assert arrivals[index] <= event["start"]
                    decode[index] += 1
                if chunk is not None:
                    assert event["work"] <= 8
                elif event["work"] > 8:
                    assert len(event["prefill"]) == 1 and not event["decode"]
            assert prefill == prompts and decode == outputs
            assert all(
                end > arrival
                for end, arrival in zip(
                    result["completion_times"], arrivals, strict=True
                )
            )


def test_paged_allocator_zero_length_and_long_event_sequence() -> None:
    lab = load_pure_module("llm-inference/labs/27_paged_kv.py")
    pool = lab.PagedKVPool(4, 8)
    pool.admit("empty", 0)
    pool.grow("empty", 0)
    assert pool.snapshot()["free_block_ids"] == list(range(8))
    pool.release("empty")
    rng = random.Random(123)
    live: dict[str, int] = {}
    for index in range(100):
        before = pool.snapshot()
        action = rng.choice(["admit", "grow", "release"]) if live else "admit"
        name = rng.choice(list(live)) if live else str(index)
        try:
            if action == "admit":
                tokens = rng.randrange(0, 40)
                pool.admit(str(index), tokens)
                live[str(index)] = tokens
            elif action == "grow":
                tokens = live[name] + rng.randrange(0, 10)
                pool.grow(name, tokens)
                live[name] = tokens
            else:
                pool.release(name)
                del live[name]
        except MemoryError:
            assert pool.snapshot() == before
        pool.check_invariants()
        current = pool.snapshot()
        assert current["lengths"] == live
        assert (
            current["accounting"]["required_blocks"] + len(current["free_block_ids"])
            == 8
        )


@pytest.mark.parametrize("peak", ["nan", "inf", "0", "-1"])
def test_training_rejects_invalid_peak_before_gpu_startup(peak: str) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "llm-training/labs/31_training_capstone.py"),
            "--variant-order",
            "baseline-first",
            "--peak-tflops",
            peak,
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode != 0
    assert "finite and positive" in result.stderr
