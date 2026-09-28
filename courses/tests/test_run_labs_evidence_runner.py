"""Resume and review boundaries for the reusable evidence runner."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/run-labs/scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location(
    "evidence_runner_tests", SCRIPTS / "evidence_runner.py"
)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
sys.path.remove(str(SCRIPTS))


def write(path, value):
    path.write_text(json.dumps(value))
    path.chmod(0o600)


def test_completed_adapter_reuses_exact_outputs_and_rejects_source_drift(tmp_path):
    output = tmp_path / "result.json"
    script = tmp_path / "verify.py"
    script.write_text(
        "from pathlib import Path\nimport sys\np=Path(sys.argv[1]);p.write_text('{}');p.chmod(0o600)\n"
    )
    spec = {
        "argv": [sys.executable, str(script), str(output)],
        "pins": {str(script): runner.digest(script)},
        "outputs": [str(output)],
    }
    journal = tmp_path / "adapter.json"
    runner.adapter(spec, journal)
    runner.adapter(
        spec, journal
    )  # A second execution would fail exclusive log creation.
    script.write_text("raise RuntimeError('drift')")
    with pytest.raises(ValueError, match="source changed"):
        runner.adapter(spec, journal)


def test_uncertain_adapter_is_never_replayed(tmp_path):
    script = tmp_path / "fail.py"
    script.write_text("raise SystemExit(1)")
    spec = {
        "argv": [sys.executable, str(script)],
        "pins": {str(script): runner.digest(script)},
        "outputs": [],
    }
    journal = tmp_path / "adapter.json"
    with pytest.raises(ValueError, match="failed"):
        runner.adapter(spec, journal)
    with pytest.raises(ValueError, match="Uncertain"):
        runner.adapter(spec, journal)


def test_publication_reuses_confirmation_but_never_repeats_uncertain_intent(
    tmp_path, monkeypatch
):
    receipt, intent = tmp_path / "confirmed.json", tmp_path / "intent.json"
    unit = {"lab": "01_example", "profile": "small"}
    item = {
        "confirmed": str(receipt),
        "intent": str(intent),
        "stages": ["baseline", "candidate"],
    }
    write(intent, {})
    with pytest.raises(ValueError, match="Uncertain publication"):
        runner.publication({}, unit, item, tmp_path / "journal.json")
    write(
        receipt,
        {
            **unit,
            "stages": item["stages"],
            "publication": {"status": "confirmed", "generation": 7},
        },
    )
    monkeypatch.setattr(
        runner,
        "adapter",
        lambda *_: pytest.fail("Confirmed publication must not be replayed"),
    )
    assert runner.publication({}, unit, item, tmp_path / "journal.json") == 7
    write(
        receipt,
        {
            **unit,
            "stages": ["wrong"],
            "publication": {"status": "confirmed", "generation": 7},
        },
    )
    with pytest.raises(ValueError, match="identity"):
        runner.publication({}, unit, item, tmp_path / "journal.json")


def test_status_is_read_only_and_finish_cannot_bypass_visual_gate(
    tmp_path, monkeypatch
):
    manifest = tmp_path / "manifest.json"
    write(manifest, {"schema": "run-labs-evidence-runner/v1"})
    unit = {"key": "test:01", "lab": "01", "course": "test", "profile": "small"}
    state = {"plan": {"source_sha256": "frozen"}}
    monkeypatch.setattr(
        runner, "context", lambda _: (tmp_path, state, {"kind": "browser"}, unit)
    )
    before = set(tmp_path.iterdir())
    assert runner.run(manifest, "status")["next_action"] == "browser"
    assert set(tmp_path.iterdir()) == before
    monkeypatch.setattr(
        runner,
        "assemble",
        lambda *_: (_ for _ in ()).throw(ValueError("Explicit visual review required")),
    )
    monkeypatch.setattr(
        runner.stage,
        "run",
        lambda *_: pytest.fail("No transition before visual review"),
    )
    with pytest.raises(ValueError, match="visual review"):
        runner.run(manifest, "finish")


def test_nixl_review_uses_mechanism_validator_instead_of_kernel_flag(
    tmp_path, monkeypatch
):
    native = {
        "stage": "systems-baseline",
        "tool": "systems",
        "job": 13,
        "producer": "rank-1",
        "path": "vendor.nsys-rep",
        "sha256": "a" * 64,
        "kernels": [],
        "nvtx": ["initiator", "nixl::registerMem", "nixl::postXferReq.write"],
        "nixl_transfer": {
            "role": "initiator",
            "buffer_bytes": 2**30,
            "buffer_device": 0,
            "registration_events": 1,
            "cuda_api_events": 482,
            "osrt_events": 100,
            "write_ranges": 550,
            "put_submissions": 550,
            "put_completions": 550,
            "put_bytes": 550 * 4096,
        },
    }
    checked, review_file = tmp_path / "checked.json", tmp_path / "review.json"
    write(
        checked,
        {
            "jobs": [{"stage": "systems-baseline", "job": 13, "results": []}],
            "native": [native],
        },
    )
    # Synthetic bytes test hash binding and review routing, never image content.
    shot = tmp_path / "view.png"
    shot.write_bytes(b"synthetic fixture")
    review = {
        "lab": "29_nixl_transfer",
        "profile": "small",
        "stage": "systems-baseline",
        "job": 13,
        "producer": "rank-1",
        "screenshot": shot.name,
        "screenshot_sha256": runner.digest(shot),
        "visual_review": True,
        "public_view_review": True,
        "numeric_match": True,
        "matching_kernel_or_nvtx": True,
    }
    write(review_file, review)
    unit = {
        "course": "advanced-gpu-communication",
        "lab": "29_nixl_transfer",
        "profile": "small",
        "stages": [
            {
                "id": "systems-baseline",
                "kind": "profile",
                "tool": "systems",
                "variant": "baseline",
                "dispatch": {"job": 13},
            }
        ],
    }
    state = {
        "id": "test",
        "environment": {"private_root": str(tmp_path)},
        "courses_root": str(tmp_path),
    }
    manifest = {
        "checked": str(checked),
        "hardware": {},
        "limitations": [],
        "grafana": [],
        "native_reviews": [str(review_file)],
    }
    validated = []
    monkeypatch.setattr(runner, "validate", lambda *args: validated.append(args[2]))
    with pytest.raises(ValueError, match="Native review is incomplete"):
        runner.assemble(manifest, state, unit)
    assert not validated
    write(review_file, {**review, "matching_nixl_transfer": True})
    result = runner.assemble(manifest, state, unit)
    assert result["screenshots"][0]["content_checks"] == {
        "matching_job": True,
        "native_cli_verified": True,
        "matching_nixl_transfer": True,
    }
    assert validated == [result]
    native["nixl_transfer"]["put_submissions"] = 549
    write(
        checked,
        {
            "jobs": [{"stage": "systems-baseline", "job": 13, "results": []}],
            "native": [native],
        },
    )
    with pytest.raises(ValueError, match="Native NIXL"):
        runner.assemble(manifest, state, unit)


def test_host_copy_review_requires_mechanism_proof_and_exact_image(
    tmp_path, monkeypatch
):
    from test_run_labs import host_copy_fixture

    unit, stage, native = host_copy_fixture()
    unit["stages"] = [stage]
    native.update(path="copy.nsys-rep", sha256="a" * 64)
    checked, review_file = tmp_path / "checked.json", tmp_path / "review.json"
    data = {
        "jobs": [{"stage": stage["id"], "job": 13, "results": []}],
        "native": [native],
    }
    write(checked, data)
    shot = tmp_path / "view.png"
    shot.write_bytes(b"synthetic hash fixture; not visual evidence")
    review = {
        "lab": unit["lab"],
        "profile": unit["profile"],
        "stage": stage["id"],
        "job": 13,
        "producer": "rank-0",
        "screenshot": shot.name,
        "screenshot_sha256": runner.digest(shot),
        "visual_review": True,
        "public_view_review": True,
        "numeric_match": True,
        "matching_kernel_or_nvtx": True,
    }
    write(review_file, review)
    state = {
        "id": "test",
        "environment": {"private_root": str(tmp_path)},
        "courses_root": str(tmp_path),
    }
    manifest = {
        "checked": str(checked),
        "hardware": {},
        "limitations": [],
        "grafana": [],
        "native_reviews": [str(review_file)],
    }
    validated = []
    monkeypatch.setattr(runner, "validate", lambda *args: validated.append(args[2]))
    with pytest.raises(ValueError, match="Native review is incomplete"):
        runner.assemble(manifest, state, unit)
    assert not validated
    write(review_file, {**review, "matching_host_copy": True})
    result = runner.assemble(manifest, state, unit)
    assert result["screenshots"][0]["content_checks"] == {
        "matching_job": True,
        "native_cli_verified": True,
        "matching_host_copy": True,
    }
    assert validated == [result]
    native["host_copy"]["modes"][0]["completions"] = 24
    write(checked, data)
    with pytest.raises(ValueError, match="Native host-copy"):
        runner.assemble(manifest, state, unit)
    native["host_copy"]["modes"][0]["completions"] = 25
    write(checked, data)
    shot.write_bytes(b"changed after visual approval")
    with pytest.raises(ValueError, match="[Hh]ash|[Cc]hecksum|[Ss]creenshot"):
        runner.assemble(manifest, state, unit)


def test_exact_action_guard_prevents_concurrent_export_from_submitting_next_unit(
    tmp_path, monkeypatch
):
    stages = runner.stage
    old = {"kind": "export", "unit": "course:01", "profile": "small", "id": "export"}
    new = {"kind": "execute", "unit": "course:02", "profile": "small", "id": "baseline"}
    monkeypatch.setattr(stages, "load", lambda _: {})
    monkeypatch.setattr(stages, "verify_frozen", lambda _: None)
    monkeypatch.setattr(stages, "next_action", lambda _: new)
    monkeypatch.setattr(
        stages,
        "ensure_claims",
        lambda *_: pytest.fail("No effect before exact-action guard"),
    )
    monkeypatch.setattr(
        stages, "request", lambda *_: pytest.fail("Must not dispatch next unit")
    )
    with pytest.raises(ValueError, match="action changed"):
        stages.run(tmp_path, "advance", expected_action=old)


def originals(left, right):
    return {
        slot: {
            "schema": "gpu-course-result/v1",
            "lab_id": "99_example",
            "profile": "small",
            "seed": 1,
            "environment": {},
            "correctness": {"passed": True},
            "measurements": measurements,
            "experiment": {
                "parameters": {},
                "instrumented": False,
                "started_unix_seconds": 1,
                "ended_unix_seconds": 2,
                "slurm_job_id": job,
            },
        }
        for slot, measurements, job in (("baseline", left, 1), ("candidate", right, 2))
    }


@pytest.mark.parametrize("fail_capture", [False, True])
def test_prepare_captures_each_generation_before_next_publication(
    tmp_path, monkeypatch, fail_capture
):
    config = tmp_path / "browser.json"
    write(config, {})
    publications = [
        {"confirmed": str(tmp_path / f"pair-{i}.json"), "stages": ["base", str(i)]}
        for i in (1, 2)
    ]
    captures = [
        {**item, "observation": str(tmp_path / f"capture-{i}.json"), "generation": i}
        for i, item in enumerate(publications, 1)
    ]
    manifest = tmp_path / "manifest.json"
    write(
        manifest,
        {
            "schema": "run-labs-evidence-runner/v1",
            "publications": publications,
            "grafana": captures,
            "node": sys.executable,
            "browser_config": str(config),
        },
    )
    unit = {"key": "test:01", "lab": "01", "course": "test", "profile": "small"}
    state = {"plan": {"source_sha256": "frozen"}}
    monkeypatch.setattr(
        runner, "context", lambda _: (tmp_path, state, {"kind": "browser"}, unit)
    )
    monkeypatch.setattr(runner, "validate_capture_inputs", lambda *_: None)
    events = []
    current_generation = 0

    def publish(_manifest, _unit, item, _journal):
        nonlocal current_generation
        current_generation = publications.index(item) + 1
        events.append(("publish", current_generation))

    def capture(spec, _journal):
        request = json.loads(Path(spec["argv"][-1]).read_text())
        assert request["generation"] == current_generation
        events.append(("capture", current_generation))
        if fail_capture:
            raise ValueError("Capture interrupted")

    monkeypatch.setattr(runner, "publication", publish)
    monkeypatch.setattr(runner, "adapter", capture)
    monkeypatch.setattr(
        runner,
        "grafana_request",
        lambda _m, _s, _u, item: {"generation": item["generation"]},
    )
    if fail_capture:
        with pytest.raises(ValueError, match="Capture interrupted"):
            runner.run(manifest, "prepare")
        assert events == [("publish", 1), ("capture", 1)]
    else:
        assert runner.run(manifest, "prepare")["gate"] == "visual-review"
        assert events == [
            ("publish", 1),
            ("capture", 1),
            ("publish", 2),
            ("capture", 2),
        ]


@pytest.mark.parametrize(
    "invalid", ["missing", "foreign", "stages", "duplicate", "empty"]
)
def test_publication_capture_bindings_fail_before_adapters(
    tmp_path, monkeypatch, invalid
):
    item = {"confirmed": "first.json", "stages": ["base", "candidate"]}
    manifest = {
        "schema": "run-labs-evidence-runner/v1",
        "publications": [item],
        "grafana": [dict(item)],
    }
    if invalid == "missing":
        manifest["grafana"] = []
    elif invalid == "foreign":
        manifest["grafana"][0]["confirmed"] = "foreign.json"
    elif invalid == "stages":
        manifest["grafana"][0]["stages"] = ["base", "other"]
    elif invalid == "duplicate":
        manifest["publications"].append(dict(item))
    else:
        manifest["publications"] = []
    filename = tmp_path / "manifest.json"
    write(filename, manifest)
    unit = {"key": "test:01", "lab": "01", "course": "test", "profile": "small"}
    state = {"plan": {"source_sha256": "frozen"}}
    monkeypatch.setattr(
        runner, "context", lambda _: (tmp_path, state, {"kind": "verify"}, unit)
    )
    monkeypatch.setattr(runner, "adapter", lambda *_: pytest.fail("No adapter effects"))
    with pytest.raises(ValueError, match="publication"):
        runner.run(filename, "prepare")


def test_multiple_captures_bind_to_their_publication_with_stable_indices():
    first = {"confirmed": "first.json", "stages": ["base", "one"]}
    second = {"confirmed": "second.json", "stages": ["base", "two"]}
    groups = runner.publication_captures(
        {"publications": [first, second], "grafana": [second, first, first]}
    )
    assert [[index for index, _ in group] for group in groups] == [[1, 2], [0]]


@pytest.mark.parametrize(
    "extra,labels",
    [
        ({"case_values": {"small": ["1024", "4096"]}}, ["1024", "4096"]),
        ({"case": "payload"}, ["payload-0", "payload-1"]),
    ],
)
def test_canonical_publisher_case_labels_keep_original_indices(extra, labels):
    api = runner.publisher(SCRIPTS.parents[2])
    metric = {"name": "speed", "path": "values.*", "unit": "s", **extra}
    rows = runner.metric_expectations(
        api,
        originals({"values": [1, 2]}, {"values": [3, 4]}),
        {"lab": "99_example", "metrics": [metric]},
    )[0]["expected"]
    assert [r["display_case"] for r in rows] == labels
    assert [r["case"] for r in rows] == ["0", "1"]


def test_optional_missing_null_and_asymmetric_metrics_keep_only_required_values():
    api = runner.publisher(SCRIPTS.parents[2])
    metric = {"name": "speed", "path": "value", "unit": "s", "optional": True}
    obs = {"lab": "99_example", "metrics": [metric]}
    assert runner.metric_expectations(api, originals({}, {"value": None}), obs) == []
    rows = runner.metric_expectations(api, originals({}, {"value": 3}), obs)[0][
        "expected"
    ]
    assert rows == [{"case": "", "display_case": "value", "values": {"candidate": 3}}]


def test_nested_cases_use_canonical_custom_prefix():
    api = runner.publisher(SCRIPTS.parents[2])
    metric = {"name": "speed", "path": "v.*.*", "unit": "s", "case": "payload"}
    rows = runner.metric_expectations(
        api,
        originals({"v": [[1, 2]]}, {"v": [[3, 4]]}),
        {"lab": "99_example", "metrics": [metric]},
    )[0]["expected"]
    assert [r["display_case"] for r in rows] == ["payload-0-0", "payload-0-1"]


@pytest.mark.parametrize(
    "clip",
    [
        None,
        {},
        {"x": -1, "y": 0, "width": 800, "height": 500},
        {"x": 0, "y": 0, "width": 639, "height": 500},
        {"x": 0, "y": 0, "width": 800, "height": 359},
        {"x": 1500, "y": 0, "width": 800, "height": 500},
        {"x": 0, "y": 900, "width": 800, "height": 500},
        {"x": False, "y": 0, "width": 800, "height": 500},
        {"x": float("nan"), "y": 0, "width": 800, "height": 500},
    ],
)
def test_zero_metric_clip_fails_before_verification_or_publication(
    tmp_path, monkeypatch, clip
):
    observability = tmp_path / "test/reference/observability.json"
    observability.parent.mkdir(parents=True)
    write(observability, {"labs": {"01": {"metrics": []}}})
    item = {"confirmed": "first.json", "stages": ["base", "candidate"]}
    manifest = tmp_path / "manifest.json"
    write(
        manifest,
        {
            "schema": "run-labs-evidence-runner/v1",
            "publications": [item],
            "grafana": [{**item, "overview_clip": clip}],
        },
    )
    unit = {"key": "test:01", "lab": "01", "course": "test", "profile": "small"}
    state = {"plan": {"source_sha256": "frozen"}, "courses_root": str(tmp_path)}
    monkeypatch.setattr(
        runner, "context", lambda _: (tmp_path, state, {"kind": "verify"}, unit)
    )
    monkeypatch.setattr(runner, "adapter", lambda *_: pytest.fail("No adapter effects"))
    monkeypatch.setattr(
        runner.stage, "run", lambda *_a, **_k: pytest.fail("No stage effects")
    )
    with pytest.raises(ValueError, match="overview_clip"):
        runner.run(manifest, "prepare")


def test_zero_metric_clip_accepts_native_viewport_bounds():
    runner.validate_overview_clip(
        {"overview_clip": {"x": 310, "y": 160, "width": 1500, "height": 530}}
    )
