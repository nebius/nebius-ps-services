"""Keep request/profile correlation separate from monotonic latency timing."""

import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def client():
    path = (
        Path(__file__).resolve().parents[1]
        / "advanced-gpu-communication/labs/serving_client.py"
    )
    spec = importlib.util.spec_from_file_location("timestamp_serving_client", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_request_retains_epoch_bounds_without_changing_latency_clock(
    client, monkeypatch
):
    class Response:
        def __enter__(self):
            return iter(
                [
                    b'data: {"choices":[{"delta":{"content":"hello"}}]}\n',
                    b'data: {"choices":[{"delta":{"content":" world"}}]}\n',
                    b'data: {"usage":{"completion_tokens":2,"prompt_tokens":3}}\n',
                    b"data: [DONE]\n",
                ]
            )

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(client.urllib.request, "urlopen", lambda *a, **k: Response())
    monotonic = iter([10.0, 10.2, 10.4, 10.7])
    epoch = iter([1_000_000_000, 1_710_000_000])
    monkeypatch.setattr(client.time, "perf_counter", lambda: next(monotonic))
    monkeypatch.setattr(client.time, "time_ns", lambda: next(epoch))
    row = client.request("http://127.0.0.1:1234", "public fixture", 2)
    assert row["request_start_unix_ns"] == 1_000_000_000
    assert row["request_end_unix_ns"] == 1_710_000_000
    assert row["request_ms"] == pytest.approx(700)
    assert row["ttft_ms"] == pytest.approx(200)
    assert row["mean_token_spacing_ms"] == pytest.approx(200)
    assert row["chunk_gaps_ms"] == pytest.approx([200])


def test_measurement_window_excludes_warmup_and_retains_all_request_bounds(
    client, monkeypatch, tmp_path
):
    calls = []

    def request(url, prompt, output_tokens):
        calls.append(prompt)
        return {
            "request_start_unix_ns": 1100 + len(calls) * 10,
            "request_end_unix_ns": 1200 + len(calls) * 10,
            "ttft_ms": 1,
            "request_ms": 2,
            "mean_token_spacing_ms": 1,
            "chunk_gaps_ms": [1],
            "completion_tokens": 2,
            "prompt_tokens": 3,
            "output_sha256": "a" * 64,
        }

    monkeypatch.setattr(client, "request", request)
    epoch = iter([1000, 2000])
    monotonic = iter([10.0, 12.0])
    monkeypatch.setattr(client.time, "time_ns", lambda: next(epoch))
    monkeypatch.setattr(client.time, "perf_counter", lambda: next(monotonic))
    result = client.benchmark(
        "unused",
        count=4,
        words=1,
        output_tokens=2,
        concurrency=1,
        warmup=2,
        folder=tmp_path,
    )
    rows = json.loads((tmp_path / "requests.json").read_text())
    window = json.loads((tmp_path / "measurement-window.json").read_text())
    assert len(calls) == 6 and len(rows) == 4
    assert rows[0]["request_start_unix_ns"] == 1130
    assert window == {
        "started_unix_ns": 1000,
        "ended_unix_ns": 2000,
        "request_count": 4,
        "warmup_count": 2,
        "elapsed_seconds": 2.0,
        "latency_clock": "time.perf_counter",
    }
    assert all(
        window["started_unix_ns"]
        <= x["request_start_unix_ns"]
        <= x["request_end_unix_ns"]
        <= window["ended_unix_ns"]
        for x in rows
    )
    assert result["requests_per_second"] == 2
