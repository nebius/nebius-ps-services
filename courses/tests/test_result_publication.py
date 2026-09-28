"""Exercise the official client wire payload and selected-comparison state machine."""

import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import prometheus_client
import pytest
from prometheus_client.parser import text_string_to_metric_families
from test_observability_integration import RECIPE, load, result


@pytest.fixture
def publisher(tmp_path, monkeypatch):
    module = load("publish_results")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    (tmp_path / "reference").mkdir()
    (tmp_path / "reference/observability.json").write_text(
        json.dumps({"course": "test-course", "labs": {"01_example": RECIPE}})
    )
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps(result(batch_size=1)))
    b.write_text(json.dumps(result(batch_size=2)))
    args = SimpleNamespace(
        lab="01_example",
        baseline=a,
        candidate=b,
        workspace="learner-01",
        gateway="http://localhost:9091",
        metrics_url="http://localhost:8429",
        expected_generation=0,
        readback_timeout=0.01,
    )
    requests = []
    original = prometheus_client.push_to_gateway

    def handler(url, method, timeout, headers, data):
        assert method == "PUT" and "/job/course_lab_results/" in url
        assert timeout == 5

        def send():
            requests.append((url, data.decode()))

        return send

    monkeypatch.setattr(
        prometheus_client,
        "push_to_gateway",
        lambda *a, **kw: original(*a, handler=handler, **kw),
    )
    monkeypatch.setattr(module, "readback", lambda *a: True)
    return module, args, requests


def samples(document):
    return [
        s for family in text_string_to_metric_families(document) for s in family.samples
    ]


def test_atomic_pair_put_and_repeatable_republication_after_cache_restart(publisher):
    module, args, requests = publisher
    assert module.publish(args) == 1
    rows = samples(requests[0][1])
    assert {r.labels["slot"] for r in rows if "slot" in r.labels} == {
        "baseline",
        "candidate",
    }
    assert len(requests) == 1
    args.expected_generation = 1
    # Treat the server cache as lost; the immutable artifacts and local selection remain.
    requests.clear()
    assert module.publish(args) == 1
    assert len(requests) == 1
    assert {
        r.labels["slot"] for r in samples(requests[0][1]) if "slot" in r.labels
    } == {"baseline", "candidate"}
    state = json.loads(
        next(
            (module.ROOT / "results/publications/learner-01").glob("*.json")
        ).read_text()
    )
    assert state["status"] == "confirmed" and state["generation"] == 1


def test_delayed_retry_cannot_replace_a_newer_selected_pair(publisher):
    module, args, requests = publisher
    module.publish(args)
    args.expected_generation = 1
    data = json.loads(args.candidate.read_text())
    data["measurements"]["elapsed_ms"] = 3
    args.candidate.write_text(json.dumps(data))
    module.publish(args)
    stale = copy.copy(args)
    stale.expected_generation = 1
    with pytest.raises(ValueError, match="current generation is 2"):
        module.publish(stale)
    assert len(requests) == 2


def test_concurrent_writers_serialize_and_only_one_wins(publisher, monkeypatch):
    module, args, requests = publisher
    entered = threading.Event()
    release = threading.Event()

    def readback(*a):
        entered.set()
        assert release.wait(2)
        return True

    monkeypatch.setattr(module, "readback", readback)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(module.publish, copy.copy(args))
        assert entered.wait(2)
        second = executor.submit(module.publish, copy.copy(args))
        release.set()
        assert first.result() == 1
        with pytest.raises(ValueError, match="Selection changed"):
            second.result()
    assert len(requests) == 1


def test_transport_failure_preserves_benchmarks_and_retry_identity(
    publisher, monkeypatch
):
    module, args, _requests = publisher
    original = prometheus_client.push_to_gateway

    def fail(*a, **k):
        raise OSError("transport unavailable")

    monkeypatch.setattr(prometheus_client, "push_to_gateway", fail)
    before = args.baseline.read_bytes(), args.candidate.read_bytes()
    with pytest.raises(RuntimeError, match="expected-generation 1"):
        module.publish(args)
    assert before == (args.baseline.read_bytes(), args.candidate.read_bytes())
    monkeypatch.setattr(prometheus_client, "push_to_gateway", original)
    args.expected_generation = 1
    assert module.publish(args) == 1


def test_failed_ingestion_is_not_benchmark_failure(publisher, monkeypatch):
    module, args, requests = publisher
    monkeypatch.setattr(module, "readback", lambda *a: False)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    with pytest.raises(RuntimeError, match="not confirmed"):
        module.publish(args)
    state = json.loads(
        next(
            (module.ROOT / "results/publications/learner-01").glob("*.json")
        ).read_text()
    )
    assert (
        state["generation"] == 1 and state["status"] == "pending" and len(requests) == 1
    )
    monkeypatch.setattr(module, "readback", lambda *a: True)
    args.expected_generation = 1
    assert module.publish(args) == 1


def test_freshness_and_generation_use_one_backend_evaluation(monkeypatch):
    module = load("publish_results")
    queries = []
    from contextlib import contextmanager
    from urllib.parse import parse_qs, urlsplit

    @contextmanager
    def urlopen(url, timeout):
        queries.append(parse_qs(urlsplit(url).query)["query"][0])
        yield SimpleNamespace(
            read=lambda n: json.dumps(
                {"status": "success", "data": {"result": [{"value": [9999, "7"]}]}}
            ).encode()
        )

    monkeypatch.setattr(module.urllib.request, "urlopen", urlopen)
    assert module.readback("http://localhost", {"lab": "01_example"}, 7, 1000)
    assert (
        len(queries) == 1
        and "== 7" in queries[0]
        and "timestamp(" in queries[0]
        and ">= 1000.000000" in queries[0]
    )


@pytest.mark.parametrize("duplicate", ["run_id", "worker_identity", "gpu_uuid"])
def test_setup_requires_two_distinct_worker_gpu_runs(duplicate):
    module = load("publish_results")
    r = copy.deepcopy(RECIPE)
    r["lab"] = "environment_readiness"
    left, right = result(), result()
    for x in (left, right):
        x.update(
            lab_id="environment_readiness",
            run_id="a",
            worker_identity="worker-a",
            gpu_uuid="GPU-a",
        )
    with pytest.raises(ValueError, match="distinct"):
        module.comparison_payload(left, right, r)
    right.update(run_id="b", worker_identity="worker-b", gpu_uuid="GPU-b")
    assert module.comparison_payload(left, right, r)
    right[duplicate] = left[duplicate]
    with pytest.raises(ValueError, match="distinct"):
        module.comparison_payload(left, right, r)


def test_equivalence_requires_matching_response_digests():
    module = load("publish_results")
    r = {**RECIPE, "paired_digests": True}
    left, right = result(), result()
    left["measurements"]["response_digests"] = ["a" * 64]
    right["measurements"]["response_digests"] = ["b" * 64]
    with pytest.raises(ValueError, match="greedy"):
        module.comparison_payload(left, right, r)


def test_nccl_offline_or_instrumented_log_cannot_be_accepted():
    module = load("publish_results")
    left, right = result(), result()
    left["measurements"]["acceptance_timing"] = False
    with pytest.raises(ValueError, match="excludes acceptance"):
        module.comparison_payload(left, right, RECIPE)
