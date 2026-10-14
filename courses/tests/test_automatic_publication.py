"""Automatic publication validates originals and preserves ordering on failure."""

import copy
import json
from pathlib import Path
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

import course_publication as publication
import course_publication_state as state
import course_publication_evidence as evidence
from course_execution import select
from test_observability_integration import RECIPE, result

ROOT = Path(__file__).resolve().parents[1]
LAB = "01_example"


@pytest.fixture
def installation(tmp_path, monkeypatch):
    course = tmp_path / "test-course"
    course.mkdir()
    (course / ".runtime").mkdir(mode=0o700)
    (course / "reference").mkdir()
    binding = {
        "root": str(course),
        "workspace": "learner",
        "gateway": "http://localhost:9091",
        "metrics_url": "http://localhost:8429",
    }
    monkeypatch.setattr(state, "settings", lambda _: (course, binding))
    from course_bootstrap import runtime

    monkeypatch.setattr(
        runtime,
        "load",
        lambda *a, **kw: (
            course,
            {"environment": {"COURSE_PUBLISH_PYTHON": sys.executable}},
        ),
    )
    variant = {
        "key": "default_none_cuda",
        "experiment": "default",
        "profiler": "none",
        "device": "cuda",
        "runtime": "fixture",
        "scope": {"nodes": 1, "tasks": 1, "gpus_per_task": 1, "cpus_per_task": 1},
    }
    (course / "reference/execution.json").write_text(
        json.dumps(
            {
                "schema": "course-execution/v1",
                "course": course.name,
                "labs": {LAB: {"device_option": False, "variants": [variant]}},
            }
        )
    )
    recipe = copy.deepcopy(RECIPE)
    recipe.update(
        kind="single_gpu",
        publication={
            "policies": {
                variant["key"]: {
                    "kind": "single-original",
                    "result_lab": LAB,
                    "view": "latest",
                }
            },
            "controls": ["batch_size"],
        },
    )
    state.atomic(
        course / "reference/observability.json",
        {"course": course.name, "labs": {LAB: recipe}},
    )
    identity = state.initialize(course)
    sent = []
    monkeypatch.setattr(
        publication,
        "prepare_push",
        lambda candidate, rows, ident, generation: lambda: sent.append(
            (copy.deepcopy(candidate), list(rows), generation)
        ),
    )
    monkeypatch.setattr(publication, "visible", lambda *args: True)

    def job(number=101, *, status="completed", mutate=None):
        requested = {
            "workload": "small",
            "experiment": "default",
            "profiler": "none",
            "device": None,
            "sanitizer": None,
            "program_arguments": [],
        }
        stages = select(course, LAB, requested)
        root = course / "results" / LAB / "jobs" / str(number)
        (root / "stages/normal/results").mkdir(parents=True)
        path = root / "result.json"
        document = {
            "schema": "course-job-result/v1",
            "course": course.name,
            "lab_id": LAB,
            "job_id": str(number),
            "requested": requested,
            "allocation": {},
            "status": "incomplete",
            "started_at": "2026-01-01T00:00:00+00:00",
            "finished_at": "2026-01-01T00:00:02+00:00",
            "stages": stages,
        }
        state.atomic(path, document)
        document["publication"] = state.register(course, path)
        original = result(batch_size=1)
        original["experiment"]["slurm_job_id"] = number
        original["run_id"] = str(number)
        if mutate:
            mutate(original)
        source = root / "stages/normal/results/original.json"
        state.atomic(source, original)
        stages[0].update(
            status=status,
            measurements=[{"path": str(source.relative_to(root))}],
            native_reports=[],
        )
        document["status"] = status
        state.atomic(path, document)
        return path

    return course, identity, sent, job


def test_explicit_policy_covers_every_declared_variant():
    counts = {
        path.parent.parent.name: evidence.validate_policies(path.parent.parent)
        for path in ROOT.glob("*/reference/execution.json")
    }
    assert counts == {
        "advanced-gpu-communication": 69,
        "custom-cuda-kernels": 50,
        "gpu-fundamentals": 28,
        "gpu-optimizations": 41,
        "llm-inference": 52,
        "llm-training": 46,
    }


def test_initialized_identity_is_reused_and_missing_journal_is_not_reset(installation):
    course, identity, _, _ = installation
    assert state.initialize(course) == identity
    (course / ".runtime/publications/sequence.json").unlink()
    with pytest.raises(ValueError):
        state.initialize(course)


def test_job_publishes_original_and_preserves_bytes(installation):
    course, _, sent, job = installation
    path = job()
    original = next(path.parent.glob("stages/*/results/*.json"))
    before = original.read_bytes()
    assert publication.publish_job(course, path)["status"] == "confirmed"
    assert {entry[0]["channel"] for entry in sent} == {"latest", "diagnostic"}
    latest = next(row for row in sent if row[0]["channel"] == "latest")
    assert ("duration_seconds", "result", "0", "value", 0.002) in latest[1]
    assert ("control_batch_size", "result", "0", "value", 1) in latest[1]
    assert original.read_bytes() == before
    assert "run_id" not in latest[0]["labels"]
    assert publication.publish_job(course, path)["status"] == "confirmed"
    assert len(sent) == 2  # Confirmed job retry never issues another PUT.


def test_older_success_cannot_replace_newer_success(installation):
    course, _, sent, job = installation
    older, newer = job(101), job(102)
    publication.publish_job(course, newer)
    assert publication.publish_job(course, older)["status"] == "superseded"
    assert len(sent) == 2
    assert all(row[0]["job_id"] == 102 for row in sent)


def test_newer_failed_job_preserves_clean_result(installation):
    course, _, sent, job = installation
    publication.publish_job(course, job(101))
    publication.publish_job(course, job(102, status="failed"))
    assert [row[0]["job_id"] for row in sent if row[0]["channel"] == "latest"] == [101]
    assert ("stage_status", "status", "0", "failed", 1) in sent[-1][1]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(profile="large"),
        lambda value: value["experiment"].update(slurm_job_id=777),
        lambda value: value["experiment"].update(instrumented=True),
        lambda value: value["experiment"].update(rank=1),
        lambda value: value["correctness"].update(reference=False),
        lambda value: value["measurements"].update(acceptance_timing=False),
    ],
)
def test_invalid_original_cannot_publish_clean_metrics(installation, mutate):
    course, _, sent, job = installation
    assert publication.publish_job(course, job(mutate=mutate))["status"] == "failed"
    assert all(row[0]["channel"] == "diagnostic" for row in sent)


def test_changed_original_rejected_on_retry(installation, monkeypatch):
    course, _, sent, job = installation
    path = job()
    monkeypatch.setattr(publication, "visible", lambda *args: False)
    publication.publish_job(course, path, timeout=0)
    original = next(path.parent.glob("stages/*/results/*.json"))
    data = json.loads(original.read_text())
    data["measurements"]["elapsed_ms"] = 999
    state.atomic(original, data)
    assert publication.publish_job(course, path, timeout=0)["status"] == "failed"
    assert len(sent) == 2


def test_ambiguous_put_pauses_new_writes_and_resume_selects_newest(
    installation, monkeypatch
):
    course, _, sent, job = installation
    first, second = job(101), job(102)
    good_push = publication.prepare_push

    def ambiguous(candidate, *args):
        if candidate["channel"] == "latest":

            def timeout():
                raise TimeoutError("response lost")

            return timeout
        return good_push(candidate, *args)

    monkeypatch.setattr(publication, "prepare_push", ambiguous)
    one = publication.publish_job(course, first)
    paused = next(row for row in one["groups"] if row["status"] == "paused")
    monkeypatch.setattr(publication, "prepare_push", good_push)
    assert publication.publish_job(course, second)["status"] == "paused"
    assert not [row for row in sent if row[0]["channel"] == "latest"]
    with pytest.raises(ValueError, match="explicit quiescence"):
        publication.resume(course, paused["key"], paused["generation"], False)
    with pytest.raises(ValueError, match="generation"):
        publication.resume(course, paused["key"], 0, True)
    assert (
        publication.resume(course, paused["key"], paused["generation"], True)["status"]
        == "confirmed"
    )
    assert sent[-1][0]["job_id"] == 102
    assert publication.publish_job(course, first)["status"] == "superseded"


def test_acknowledged_put_with_delayed_visibility_does_not_pause(
    installation, monkeypatch
):
    course, _, sent, job = installation
    monkeypatch.setattr(publication, "visible", lambda *args: False)
    assert (
        publication.publish_job(course, job(101), timeout=0)["status"] == "unconfirmed"
    )
    monkeypatch.setattr(publication, "visible", lambda *args: True)
    assert publication.publish_job(course, job(102))["status"] == "confirmed"
    assert len(sent) == 4


def test_missing_known_group_state_cannot_reset_generation(installation):
    course, _, sent, job = installation
    path = job()
    outcome = publication.publish_job(course, path)
    for group in outcome["groups"]:
        (course / ".runtime/publications/groups" / (group["key"] + ".json")).unlink()
    assert publication.publish_job(course, path)["status"] == "failed"
    assert len(sent) == 2


def test_counter_cannot_overwrite_registered_run(installation):
    course, identity, _, job = installation
    job()
    state.atomic(
        course / ".runtime/publications/sequence.json",
        {"publisher_id": identity["publisher_id"], "sequence": 0},
    )
    with pytest.raises(ValueError, match="reuse"):
        job(102)


def test_supervisor_kills_stalled_publisher_without_losing_registration(tmp_path):
    runner = tmp_path / "stall"
    runner.write_text("#!/bin/sh\nexec sleep 30\n")
    runner.chmod(0o700)
    state = {
        "status": "registered",
        "registration": {"python": str(runner), "sequence": 4},
    }
    started = time.monotonic()
    outcome = publication.supervise(
        tmp_path, tmp_path / "result.json", state, timeout=0.1
    )
    assert time.monotonic() - started < 3
    assert (
        outcome["status"] == "failed"
        and outcome["registration"] == state["registration"]
    )


def test_auto_opt_out_has_no_subprocess(tmp_path, monkeypatch):
    monkeypatch.setenv("COURSE_AUTO_PUBLISH", "0")
    monkeypatch.setattr(
        publication.subprocess,
        "Popen",
        lambda *a, **k: pytest.fail("must not start a publisher"),
    )
    assert publication.supervise(
        tmp_path, tmp_path / "result.json", register_only=True
    ) == {"status": "disabled"}


def test_new_offer_survives_an_older_inflight_ambiguous_write(
    installation, monkeypatch
):
    course, identity, sent, job = installation
    paths = [job(101), job(102)]
    directory, _ = state.authority(course)
    choices = []
    for path in paths:
        snapshot_path, snapshot = evidence.freeze(course, path, identity, directory)
        choices.append(
            next(
                row
                for row in evidence.candidates(
                    course, snapshot_path, snapshot, identity
                )
                if row["channel"] == "latest"
            )
        )
    entered, release = threading.Event(), threading.Event()
    good_push = publication.prepare_push

    def prepare(candidate, *args):
        if candidate["job_id"] == 101:

            def delayed():
                entered.set()
                assert release.wait(3)
                raise TimeoutError("ambiguous old write")

            return delayed
        return good_push(candidate, *args)

    monkeypatch.setattr(publication, "prepare_push", prepare)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(publication.offer, course, directory, identity, choices[0])
        assert entered.wait(3)
        assert (
            publication.offer(course, directory, identity, choices[1])["status"]
            == "queued"
        )
        release.set()
        paused = first.result(timeout=3)
    assert paused["status"] == "paused"
    publication.resume(course, paused["key"], paused["generation"], True)
    assert sent[-1][0]["job_id"] == 102


def test_local_client_failure_does_not_require_quiescence_recovery(
    installation, monkeypatch
):
    course, _, sent, job = installation
    path = job()
    good = publication.prepare_push

    def missing(*args):
        raise ImportError("not installed")

    monkeypatch.setattr(publication, "prepare_push", missing)
    assert publication.publish_job(course, path)["status"] == "failed"
    monkeypatch.setattr(publication, "prepare_push", good)
    assert publication.publish_job(course, path)["status"] == "confirmed"
    assert len(sent) == 2


@pytest.mark.parametrize(
    "defect", [None, "missing", "duplicate", "wrong-order", "same-run"]
)
def test_three_trials_require_complete_explicit_ordinals(installation, defect):
    course, identity, _, job = installation
    path = job()
    document = state.read(path)
    stage = document["stages"][0]
    source = next(path.parent.glob("stages/*/results/*.json"))
    value = state.read(source)
    stage["measurements"] = []
    for trial in (1, 2, 3):
        item = copy.deepcopy(value)
        item["run_id"] = "same" if defect == "same-run" else str(trial)
        item["seed"] = trial
        item["experiment"]["trial_index"] = 1 if defect == "duplicate" else trial
        item["measurements"]["variant_order"] = (
            "candidate-first" if trial == 2 else "baseline-first"
        )
        if defect == "wrong-order":
            item["measurements"]["variant_order"] = "candidate-first"
        target = source.with_name(f"trial-{trial}.json")
        state.atomic(target, item)
        if defect != "missing" or trial != 3:
            stage["measurements"].append({"path": str(target.relative_to(path.parent))})
    policy = {
        "kind": "three-trials",
        "result_lab": LAB,
        "view": "latest",
        "trials": [1, 2, 3],
    }
    snapshot = {
        "job": document,
        "registration": document["publication"]["registration"],
    }
    if defect:
        with pytest.raises(ValueError):
            evidence.measurement_rows(course, snapshot, stage, policy)
    else:
        rows = evidence.measurement_rows(course, snapshot, stage, policy)
        assert {row[2] for row in rows} == {"1", "2", "3"}


@pytest.mark.parametrize(
    "defect", [None, "digest", "missing", "duplicate", "runtime", "seed"]
)
@pytest.mark.parametrize("auxiliary", [False, True])
def test_paired_probes_validate_every_trial_without_unique_run_ids(
    installation, defect, auxiliary
):
    course, _, _, job = installation
    path = job()
    document = state.read(path)
    stage = document["stages"][0]
    source = next(path.parent.glob("stages/*/results/*.json"))
    value = state.read(source)
    stage["measurements"] = []
    recipe_path = course / "reference/observability.json"
    recipes = state.read(recipe_path)
    recipes["labs"][LAB].update(paired_digests=True, tuning_parameters=["variant"])
    state.atomic(recipe_path, recipes)
    for trial in (1, 2, 3):
        for role in ("disabled", "enabled"):
            item = copy.deepcopy(value)
            item["experiment"]["trial_index"] = 1 if defect == "duplicate" else trial
            item["experiment"]["parameters"]["variant"] = role
            item["measurements"].update(variant=role, response_digests=["a" * 64])
            if defect == "digest" and role == "enabled" and trial == 3:
                item["measurements"]["response_digests"] = ["b" * 64]
            if defect == "runtime" and trial == 3:
                item["environment"]["runtime_identity"] = "changed"
            if defect == "seed" and trial == 3:
                item["seed"] = 99
            target = source.with_name(f"{role}-{trial}.json")
            state.atomic(target, item)
            if defect != "missing" or trial != 3:
                stage["measurements"].append(
                    {"path": str(target.relative_to(path.parent))}
                )
    policy = {
        "kind": "paired-probe-trials",
        "result_lab": LAB,
        "view": "latest",
        "trials": [1, 2, 3],
        "roles": ["disabled", "enabled"],
    }
    if auxiliary:
        probe = "02_probe"
        recipes["labs"][probe] = {**copy.deepcopy(recipes["labs"][LAB]), "lab": probe}
        recipes["labs"][LAB]["paired_digests"] = False
        state.atomic(recipe_path, recipes)
        for entry in list(stage["measurements"]):
            original = path.parent / entry["path"]
            item = state.read(original)
            item["lab_id"] = probe
            item["experiment"]["parameters"]["workload"] = "prefix-cache"
            target = original.with_name("probe-" + original.name)
            state.atomic(target, item)
            stage["measurements"].append({"path": str(target.relative_to(path.parent))})
            primary = state.read(original)
            primary["measurements"].pop("response_digests")
            state.atomic(original, primary)
        policy.update(
            kind="paired-performance-trials",
            probe_lab=probe,
            probe_workload="prefix-cache",
        )
    snapshot = {
        "job": document,
        "registration": document["publication"]["registration"],
    }
    if defect:
        with pytest.raises(ValueError):
            evidence.measurement_rows(course, snapshot, stage, policy)
    else:
        rows = evidence.measurement_rows(course, snapshot, stage, policy)
        assert {(row[1], row[2]) for row in rows} == {
            (role, str(trial)) for role in policy["roles"] for trial in policy["trials"]
        }


def test_missing_offer_never_allows_older_run_to_replace_newer(installation):
    course, _, sent, job = installation
    older, newer = job(101), job(102)
    assert publication.publish_job(course, newer)["status"] == "confirmed"
    for path in (course / ".runtime/publications/offers").glob("*.json"):
        path.unlink()
    assert publication.publish_job(course, older)["status"] == "failed"
    assert len(sent) == 2


def test_short_registration_contention_preserves_both_jobs(installation):
    course, _, _, job = installation
    path = job()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with state.locked(course / ".runtime/publications/sequence.lock"):
            future = pool.submit(state.register, course, path)
            time.sleep(0.05)
            assert not future.done()
        registered = future.result(timeout=2)
    assert registered["registration"]["sequence"] == 2


def test_resume_uses_newest_offer_when_superseded_originals_are_unavailable(
    installation, monkeypatch
):
    course, identity, sent, job = installation
    older, newer = job(101), job(102)
    directory, _ = state.authority(course)

    def selected(path):
        frozen_path, snapshot = evidence.freeze(course, path, identity, directory)
        return next(
            c
            for c in evidence.candidates(course, frozen_path, snapshot, identity)
            if c["channel"] == "latest"
        )

    first, second = selected(older), selected(newer)
    send = publication.prepare_push

    def fail(*args):
        def write():
            raise OSError("ambiguous fixture PUT")

        return write

    monkeypatch.setattr(publication, "prepare_push", fail)
    outcome = publication.offer(course, directory, identity, first)
    assert outcome["status"] == "paused"
    publication.save_offer(course, directory, second)
    next(older.parent.glob("stages/*/results/*.json")).unlink()
    monkeypatch.setattr(publication, "prepare_push", send)
    assert (
        publication.resume(course, first["key"], outcome["generation"], True)["status"]
        == "confirmed"
    )
    assert sent[0][0]["job_id"] == 102


@pytest.mark.parametrize("diagnostic", [False, True])
def test_status_only_and_intrinsic_diagnostic_recipes(installation, diagnostic):
    course, _, sent, job = installation
    recipe_path = course / "reference/observability.json"
    recipes = state.read(recipe_path)
    recipe = recipes["labs"][LAB]
    if diagnostic:
        recipe["kind"] = "diagnostic"
        recipe["publication"]["policies"]["default_none_cuda"].update(
            kind="single-diagnostic", view="diagnostic"
        )
    else:
        recipe["metrics"] = []
        recipe["publication"]["controls"] = []
    state.atomic(recipe_path, recipes)
    path = job(mutate=lambda value: value["experiment"].update(instrumented=diagnostic))
    assert publication.publish_job(course, path)["status"] == "confirmed"
    if diagnostic:
        assert len(sent) == 1 and sent[0][0]["channel"] == "diagnostic"
        assert ("duration_seconds", "result", "0", "value", 0.002) in sent[0][1]
    else:
        latest = next(row for row in sent if row[0]["channel"] == "latest")
        assert {row[0] for row in latest[1]} == {
            "started_unix_seconds",
            "ended_unix_seconds",
            "slurm_job_id",
            "correctness",
        }


def test_original_from_another_stage_is_rejected(installation):
    course, _, sent, job = installation
    path = job()
    document = state.read(path)
    source = next(path.parent.glob("stages/*/results/*.json"))
    target = path.parent / "stages/other/original.json"
    target.parent.mkdir()
    source.rename(target)
    document["stages"][0]["measurements"] = [
        {"path": str(target.relative_to(path.parent))}
    ]
    state.atomic(path, document)
    assert publication.publish_job(course, path)["status"] == "failed"
    assert {row[0]["channel"] for row in sent} == {"diagnostic"}


@pytest.mark.parametrize("transport_failure", [False, True])
def test_native_runner_registers_before_stage_and_publishes_after(
    installation, monkeypatch, transport_failure
):
    import course_job

    course, _, sent, _ = installation
    execution = state.read(course / "reference/execution.json")
    execution["labs"][LAB]["variants"][0].update(timeout_seconds=10, teardown_seconds=1)
    state.atomic(course / "reference/execution.json", execution)
    for relative in (
        "results",
        f"results/{LAB}",
        f"results/{LAB}/logs",
        f"results/{LAB}/jobs",
    ):
        (course / relative).mkdir(mode=0o700)
    value = result(batch_size=1)
    value["experiment"]["slurm_job_id"] = 123
    state.atomic(course / "input.json", value)
    launcher = course / "fixture.sbatch"
    launcher.write_text(
        'course_stage() { cp input.json "$COURSE_RESULTS_DIR/original.json"; }\n'
    )
    monkeypatch.chdir(course)
    monkeypatch.setenv("SLURM_JOB_ID", "123")
    monkeypatch.setattr(course_job, "scheduler_budget", lambda *args: None)
    phases = []

    def lifecycle(root, path, previous=None, *, register_only=False, **kwargs):
        document = state.read(path)
        phases.append(document["status"])
        if register_only:
            assert not list(path.parent.glob("stages/*/results/*.json"))
            return state.register(root, path)
        assert document["status"] == "completed"
        assert document["publication"]["registration"] == previous["registration"]
        if transport_failure:
            raise OSError("fixture transport unavailable")
        return publication.publish_job(root, path)

    monkeypatch.setattr(publication, "supervise", lifecycle)
    assert course_job.run(LAB, launcher, ["--workload", "small"]) == 0
    document = state.read(course / "results" / LAB / "jobs/123/result.json")
    assert phases == ["incomplete", "completed"]
    assert document["status"] == "completed"
    assert document["publication"]["status"] == (
        "failed" if transport_failure else "confirmed"
    )
    assert len(sent) == (0 if transport_failure else 2)


def test_official_put_has_bounded_identity_units_and_three_metadata_values(
    installation, monkeypatch
):
    import importlib
    import prometheus_client
    from prometheus_client.parser import text_string_to_metric_families

    course, identity, _, job = installation
    path = job()
    directory, _ = state.authority(course)
    frozen, snapshot = evidence.freeze(course, path, identity, directory)
    candidate = next(
        row
        for row in evidence.candidates(course, frozen, snapshot, identity)
        if row["channel"] == "latest"
    )
    requests = []
    original = prometheus_client.push_to_gateway

    def handler(url, method, timeout, headers, data):
        assert method == "PUT" and "/job/course_lab_latest/" in url and timeout == 5
        return lambda: requests.append(data.decode())

    monkeypatch.setattr(
        prometheus_client,
        "push_to_gateway",
        lambda *a, **kw: original(*a, handler=handler, **kw),
    )
    # The fixture mocks the transport factory. Load an independent module to
    # exercise the real official client without modifying its module globals.
    spec = importlib.util.spec_from_file_location(
        "wire_publication", ROOT / "tools/course_publication.py"
    )
    wire = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wire)
    send = wire.prepare_push(
        candidate, evidence.payload(course, candidate), identity, 8
    )
    assert not requests  # No request before the caller's durable sending journal.
    send()
    samples = [
        s
        for family in text_string_to_metric_families(requests[0])
        for s in family.samples
    ]
    timing = next(s for s in samples if s.name == "course_latest_duration_seconds")
    assert timing.value == 0.002 and timing.labels["measurement"] == "duration_seconds"
    assert {s.name for s in samples} >= {
        "course_latest_job_id",
        "course_latest_generation",
        "course_latest_ended_at_seconds",
    }
