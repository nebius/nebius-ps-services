"""A fixed public prompt workload with explicit client-visible streaming boundaries."""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import math
import statistics
import time
import urllib.request
from itertools import pairwise


def percentile(values, fraction):
    if not values:
        raise ValueError("No samples")
    return sorted(values)[
        min(len(values) - 1, max(0, math.ceil(len(values) * fraction) - 1))
    ]


def prompts(count, words):
    base = "Describe how a packet moves through a network. "
    return [
        (base * words)
        + f"\nGive a concise numbered explanation for request {n % 4}. /no_think"
        for n in range(count)
    ]


def request(url, prompt, output_tokens):
    body = {
        "model": "course-model",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": output_tokens,
        "min_tokens": output_tokens,
        "ignore_eos": True,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    req = urllib.request.Request(
        url + "/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    started_unix_ns = time.time_ns()
    started = time.perf_counter()
    arrivals, content, usage = [], [], None
    complete = False
    with urllib.request.urlopen(req, timeout=180) as response:
        for raw in response:
            if not raw.startswith(b"data: "):
                continue
            payload = raw[6:].strip()
            if payload == b"[DONE]":
                complete = True
                break
            item = json.loads(payload)
            if item.get("error"):
                raise ValueError("Serving returned an error event")
            if item.get("usage"):
                usage = item["usage"]
            for choice in item.get("choices", []):
                delta = choice.get("delta", {})
                text = (
                    delta.get("reasoning_content") or delta.get("reasoning") or ""
                ) + (delta.get("content") or "")
                if text:
                    arrivals.append(time.perf_counter())
                    content.append(text)
    if (
        not complete
        or not arrivals
        or not usage
        or usage.get("completion_tokens") != output_tokens
    ):
        raise ValueError(
            "Incomplete stream or changed output-token count; inspect min_tokens/ignore_eos support before comparison"
        )
    gaps = [(b - a) * 1000 for a, b in pairwise(arrivals)]
    request_ms = (time.perf_counter() - started) * 1000
    ended_unix_ns = time.time_ns()
    return {
        "request_start_unix_ns": started_unix_ns,
        "request_end_unix_ns": ended_unix_ns,
        "ttft_ms": (arrivals[0] - started) * 1000,
        "request_ms": request_ms,
        "mean_token_spacing_ms": (arrivals[-1] - arrivals[0])
        * 1000
        / (output_tokens - 1)
        if output_tokens > 1
        else 0,
        "chunk_gaps_ms": gaps,
        "completion_tokens": output_tokens,
        "prompt_tokens": usage["prompt_tokens"],
        "output_sha256": hashlib.sha256("".join(content).encode()).hexdigest(),
    }


def benchmark(url, *, count, words, output_tokens, concurrency, warmup, folder):
    corpus = prompts(count, words)
    for prompt in corpus[:warmup]:
        request(url, prompt, output_tokens)
    started_unix_ns = time.time_ns()
    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        rows = list(
            pool.map(lambda prompt: request(url, prompt, output_tokens), corpus)
        )
    elapsed = time.perf_counter() - started
    ended_unix_ns = time.time_ns()
    (folder / "requests.json").write_text(json.dumps(rows, indent=2) + "\n")
    (folder / "measurement-window.json").write_text(
        json.dumps(
            {
                "started_unix_ns": started_unix_ns,
                "ended_unix_ns": ended_unix_ns,
                "request_count": count,
                "warmup_count": warmup,
                "elapsed_seconds": elapsed,
                "latency_clock": "time.perf_counter",
            },
            indent=2,
        )
        + "\n"
    )
    gaps = [value for row in rows for value in row["chunk_gaps_ms"]]
    return {
        "request_count": count,
        "prompt_tokens_total": sum(row["prompt_tokens"] for row in rows),
        "output_tokens_total": count * output_tokens,
        "ttft_p50_ms": statistics.median(row["ttft_ms"] for row in rows),
        "ttft_p99_ms": percentile([row["ttft_ms"] for row in rows], 0.99),
        "mean_token_spacing_ms": statistics.mean(
            row["mean_token_spacing_ms"] for row in rows
        ),
        "chunk_gap_p99_ms": percentile(gaps, 0.99) if gaps else 0,
        "requests_per_second": count / elapsed,
        "tokens_per_second": count * output_tokens / elapsed,
        "elapsed_seconds": elapsed,
        "prompt_corpus_sha256": hashlib.sha256(json.dumps(corpus).encode()).hexdigest(),
        "output_signature": hashlib.sha256(
            json.dumps([row["output_sha256"] for row in rows]).encode()
        ).hexdigest(),
    }
