"""Behavioral checks for run-labs selection, job safety and evidence replacement."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/run-labs/scripts"
sys.path.insert(0, str(SCRIPTS))


def module(name):
    spec = importlib.util.spec_from_file_location(
        "runlabs_" + name, SCRIPTS / (name + ".py")
    )
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


skill_common = module("run_labs_common")
catalog = module("catalog")
transaction = module("transaction")
evidence = module("evidence")
sys.path.remove(str(SCRIPTS))


def prepare_bundle_sources(course):
    """Public archive fixtures include the same source owner and catalog as export."""
    import shutil

    (course.parent / "tools").mkdir(exist_ok=True)
    shutil.copy2(ROOT / "tools/course_archives.py", course.parent / "tools/course_archives.py")
    manifests = list((course / "reference/lab-results").glob("*/*/manifest.json"))
    labs = sorted({p.parent.parent.name for p in manifests})
    skill_common.write(course / "reference/course.json", {
        "slug": course.name,
        "labs": [{"path": f"labs/{lab}.py", "dashboard": f"reference/grafana/{lab}.json"} for lab in labs],
    }, private=False)
    for lab in ["environment_readiness", *labs]:
        path = course / "reference/grafana" / f"{lab}.json"
        if not path.exists():
            skill_common.write(path, {"uid": lab}, private=False)


@pytest.mark.parametrize("layout", ["skills", ".agents/skills", ".claude/skills"])
def test_course_root_from_project_skill_installation(tmp_path, monkeypatch, layout):
    sys.path.insert(0, str(SCRIPTS))
    try:
        controller = module("run_labs")
    finally:
        sys.path.remove(str(SCRIPTS))
    root = tmp_path / "course checkout"
    metadata = root / "gpu-fundamentals/reference/course.json"
    metadata.parent.mkdir(parents=True)
    metadata.write_text("{}")
    (root / "sync-labs.sh").write_text("#!/bin/sh\n")
    monkeypatch.setattr(
        controller, "__file__", str(root / layout / "run-labs/scripts/run_labs.py")
    )
    monkeypatch.chdir(tmp_path)
    assert controller.courses_root() == root
    assert controller.courses_root(tmp_path) == tmp_path


def test_external_skill_requires_explicit_course_checkout(tmp_path, monkeypatch):
    sys.path.insert(0, str(SCRIPTS))
    try:
        controller = module("run_labs")
    finally:
        sys.path.remove(str(SCRIPTS))
    monkeypatch.setattr(controller, "__file__", str(tmp_path / "run_labs.py"))
    with pytest.raises(ValueError, match="supply --courses-root"):
        controller.courses_root()


def test_full_catalog_and_union():
    rows = catalog.catalog(ROOT)
    assert len(rows) == 110
    assert len({r["course"] for r in rows.values()}) == 6
    choice = catalog.select(
        rows, ["gpu-fundamentals"], ["gpu-fundamentals:01", "llm-training:02"], False
    )
    assert len(choice) == 12
    assert len(catalog.select(rows, [], [], True)) == 110
    with pytest.raises(ValueError):
        catalog.select(rows, ["soperator"], [], False)
    with pytest.raises(ValueError):
        catalog.select(rows, ["llm-training"], [], True)
    with pytest.raises(ValueError):
        catalog.select(rows, [], ["00"], False)


def routing_idle_fixture():
    unit = {"course": "advanced-gpu-communication", "lab": "33_dynamo_routing"}
    stage = {"tool": "systems", "variant": "candidate", "dispatch": {"job": 1}}
    proof = {
        "schema": "run-labs-routing-idle/v1",
        "router": "kv",
        "job": 1,
        "producer": "rank-1",
        "peer_producer": "rank-0",
        "selected_requests": 0,
        "completed_requests": 0,
        "session_requests": 69,
        "peer_selected_requests": 69,
        "peer_completed_requests": 69,
        "kernel_events": 0,
        "inference_nvtx_events": 0,
        "gpu_devices": list(range(8)),
        "profiler_starts": [
            {"pid": 100 + i, "timestamp_ns": 1000 + i} for i in range(8)
        ],
        "profiler_stops": [
            {"pid": 100 + i, "timestamp_ns": 2000 + i} for i in range(7)
        ],
        "capture_start_acknowledged": True,
        "capture_stop_acknowledged": True,
    }
    idle = {
        "tool": "systems",
        "job": 1,
        "producer": "rank-1",
        "kernels": [],
        "nvtx": ["NCCL Init 0"],
        "routing_idle": proof,
    }
    active = {
        "tool": "systems",
        "job": 1,
        "producer": "rank-0",
        "kernels": ["matmul"],
        "nvtx": ["execute_context_2(512)_generation_1(1)"],
        "routing_activity": {"selected_requests": 69, "completed_requests": 69},
    }
    return unit, stage, idle, active


def test_routing_idle_requires_profiled_idle_and_verified_active_peer():
    unit, stage, idle, active = routing_idle_fixture()
    assert evidence.verify_native_content(unit, stage, idle) == "matching_routing_idle"
    evidence.verify_routing_producer_coverage(unit, stage, [idle, active])
    with pytest.raises(ValueError):
        evidence.verify_routing_producer_coverage(unit, stage, [idle])
    active["routing_activity"]["completed_requests"] = 68
    with pytest.raises(ValueError):
        evidence.verify_routing_producer_coverage(unit, stage, [idle, active])
    active["routing_activity"]["completed_requests"] = 69
    active["kernels"] = []
    with pytest.raises(ValueError):
        evidence.verify_routing_producer_coverage(unit, stage, [idle, active])


@pytest.mark.parametrize(
    "field,value",
    [
        ("selected_requests", 1),
        ("completed_requests", 1),
        ("kernel_events", 1),
        ("inference_nvtx_events", 1),
        ("session_requests", 68),
        ("peer_selected_requests", 68),
        ("peer_completed_requests", 68),
        ("selected_requests", False),
        ("job", 2),
        ("router", "round-robin"),
        ("producer", "rank-0"),
        ("peer_producer", "rank-1"),
        ("gpu_devices", list(range(7))),
        ("gpu_devices", [0] * 8),
        ("profiler_starts", []),
        ("profiler_starts", [{"pid": 1, "timestamp_ns": 1}] * 8),
        ("profiler_stops", []),
        ("profiler_stops", [{"pid": 999, "timestamp_ns": 2000}]),
        ("profiler_stops", [{"pid": 100, "timestamp_ns": 500}]),
        ("capture_start_acknowledged", False),
        ("capture_stop_acknowledged", False),
    ],
)
def test_routing_idle_rejects_incomplete_or_conflicting_proof(field, value):
    unit, stage, idle, _ = routing_idle_fixture()
    idle["routing_idle"][field] = value
    with pytest.raises(ValueError):
        evidence.verify_native_content(unit, stage, idle)


def test_routing_idle_does_not_relax_other_native_requirements():
    unit, stage, idle, _ = routing_idle_fixture()
    for changed_unit, changed_stage in [
        ({**unit, "lab": "34_serving_goodput"}, stage),
        (unit, {**stage, "variant": "baseline"}),
        (unit, {**stage, "tool": "compute"}),
    ]:
        with pytest.raises(ValueError):
            evidence.verify_native_content(changed_unit, changed_stage, idle)
    idle["nvtx"] = ["execute_context_1(256)_generation_0(0)"]
    with pytest.raises(ValueError):
        evidence.verify_native_content(unit, stage, idle)
    idle["nvtx"] = []
    idle.pop("routing_idle")
    with pytest.raises(ValueError):
        evidence.verify_native_content(unit, stage, idle)


def test_routing_idle_browser_requires_active_peer(tmp_path):
    import shutil
    import struct

    unit, stage, idle, active = routing_idle_fixture()
    unit.update(
        profile="small",
        recipe={
            "comparisons": [],
            "repetitions": 1,
            "profilers": {"systems": ["capture"]},
        },
        stages=[stage],
    )
    stage.update(id="systems-candidate", kind="profile", expected_reports=2)
    root, raw = tmp_path / "courses", tmp_path / "raw"
    raw.mkdir(mode=0o700)
    ref = root / unit["course"] / "reference"
    (ref / "grafana").mkdir(parents=True)
    (root / "tools").mkdir()
    shutil.copy2(ROOT / "tools/publish_results.py", root / "tools/publish_results.py")
    skill_common.write(
        ref / "observability.json",
        {"labs": {unit["lab"]: {"metrics": []}}},
        private=False,
    )
    skill_common.write(
        ref / "grafana" / (unit["lab"] + ".json"), {"uid": "course-test"}, private=False
    )
    inventory = []
    for report in (idle, active):
        name = report["producer"]
        report.update(
            stage=stage["id"],
            path=name + ".nsys-rep",
            proof=name + ".json",
            native_cli_verified=True,
            warnings=[],
        )
        for field in ("path", "proof"):
            file = raw / report[field]
            file.write_bytes(b"synthetic native fixture")
            checksum = skill_common.digest(file)
            report["sha256" if field == "path" else "proof_sha256"] = checksum
            inventory.append(
                {"path": file.name, "sha256": checksum, "size": file.stat().st_size}
            )
    skill_common.write(
        raw / "inventory.json", {"schema": "run-labs-inventory/v1", "files": inventory}
    )
    (raw / "view.png").write_bytes(
        b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", 1920, 1080)
    )
    common = {
        "path": "view.png",
        "sha256": skill_common.digest(raw / "view.png"),
        "browser": "playwright-headless",
        "visually_reviewed": True,
        "public_view_reviewed": True,
    }
    grafana = dict(
        common,
        tool="grafana",
        dashboard_uid="course-test",
        profile="small",
        generation=1,
        numeric_checks=[],
    )

    def shot(report, check):
        return dict(
            common,
            tool="systems",
            report=report["path"],
            report_sha256=report["sha256"],
            content_checks={
                "matching_job": True,
                "native_cli_verified": True,
                check: True,
            },
        )

    receipt = {
        "schema": "run-labs-evidence/v1",
        "lab": unit["lab"],
        "profile": "small",
        "results": {},
        "native_reports": [idle, active],
        "screenshots": [grafana, shot(active, "matching_kernel_or_nvtx")],
    }
    evidence.validate(unit, raw, receipt, root)
    receipt["screenshots"][-1] = shot(idle, "matching_routing_idle")
    with pytest.raises(ValueError, match="representative native screenshot"):
        evidence.validate(unit, raw, receipt, root)
    receipt["screenshots"][-1] = shot(active, "matching_kernel_or_nvtx")
    active["routing_activity"]["completed_requests"] = 68
    with pytest.raises(ValueError, match="peer lacks complete inference"):
        evidence.validate(unit, raw, receipt, root)


@pytest.mark.parametrize("profile,size", [("small", 4096), ("large", 4 * 2**20)])
@pytest.mark.parametrize("role", ["initiator", "target"])
def test_nixl_native_transfer_without_cuda_kernels(profile, size, role):
    import copy

    unit = {
        "course": "advanced-gpu-communication",
        "lab": "29_nixl_transfer",
        "profile": profile,
    }
    stage = {"tool": "systems", "variant": "baseline"}
    proof = {
        "role": role,
        "buffer_bytes": 2**30,
        "buffer_device": 0,
        "cuda_api_events": 482,
        "osrt_events": 100,
        "registration_events": 1,
    }
    if role == "initiator":
        proof.update(
            write_ranges=550,
            put_submissions=550,
            put_completions=550,
            put_bytes=550 * size,
        )
        mechanism = "nixl::postXferReq.write"
    else:
        proof.update(
            notification_polls=1000,
            notification_end_ns=2000,
            validation_copy={
                "bytes": size,
                "device": 0,
                "direction": "device-to-host",
                "start_ns": 2001,
                "end_ns": 2002,
            },
        )
        mechanism = "nixl::getNotifs"
    report = {
        "tool": "systems",
        "producer": "rank-1",
        "kernels": [],
        "nvtx": [role, "nixl::registerMem", mechanism],
        "nixl_transfer": proof,
    }
    assert (
        evidence.verify_native_content(unit, stage, report) == "matching_nixl_transfer"
    )
    # Field corruption cannot turn initialization or a partial capture into proof.
    mutations = [
        ("buffer_bytes", 4096),
        ("buffer_device", 1),
        ("cuda_api_events", True),
        ("osrt_events", 0),
        ("registration_events", 0),
        ("role", "unknown"),
    ]
    mutations += (
        [
            ("put_bytes", size),
            ("put_submissions", 549),
            ("put_completions", 0),
            ("write_ranges", 549),
        ]
        if role == "initiator"
        else [
            ("notification_polls", 0),
            ("notification_end_ns", 2002),
            ("validation_copy", {**proof["validation_copy"], "bytes": size + 1}),
            (
                "validation_copy",
                {**proof["validation_copy"], "direction": "host-to-device"},
            ),
        ]
    )
    for field, value in mutations:
        broken = copy.deepcopy(report)
        broken["nixl_transfer"][field] = value
        with pytest.raises(ValueError, match="Native NIXL"):
            evidence.verify_native_content(unit, stage, broken)
    for field, value in [
        ("nvtx", [role, "nixl::registerMem"]),
        ("producer", "rank-8"),
        ("tool", "compute"),
        ("nixl_transfer", None),
    ]:
        with pytest.raises(ValueError, match="Native"):
            evidence.verify_native_content(unit, stage, {**report, field: value})
    with pytest.raises(ValueError, match="Native"):
        evidence.verify_native_content(
            {**unit, "lab": "28_nic_selection"}, stage, report
        )


def host_copy_fixture(profile="small", variant="baseline"):
    size = {"baseline": 64, "candidate": 128}[variant] * 2**20
    unit = {
        "course": "gpu-fundamentals",
        "lab": "03_transfer_and_pinning",
        "profile": profile,
    }
    stage = {
        "id": "systems-" + variant,
        "kind": "profile",
        "tool": "systems",
        "variant": variant,
        "expected_reports": 1,
        "dispatch": {"job": 13},
        "argv": [
            "labs/03_transfer_and_pinning.py",
            "--profile",
            profile,
            "--size-mib",
            str(size // 2**20),
        ],
    }

    def group(start, count, memory, direction, blocking):
        return {
            "host_memory": memory,
            "direction": direction,
            "submissions": count,
            "completions": count,
            "correlated_copies": count,
            "segments": count,
            "bytes": count * size,
            "first_submission_ns": start,
            "last_submission_end_ns": start + 60,
            "first_copy_start_ns": start + 1,
            "last_copy_end_ns": start + 70,
            "device_synchronizations": 21 if count == 25 else 0,
            "stream_synchronizations": count if blocking else 0,
        }

    modes = [
        {"mode": name, **group(100 + i * 100, 25, memory, "host-to-device", blocking)}
        for i, (name, memory, blocking) in enumerate(
            [
                ("pageable_blocking", "Pageable", True),
                ("pageable_nonblocking", "Pageable", False),
                ("pinned_blocking", "Pinned", True),
                ("pinned_nonblocking", "Pinned", False),
            ]
        )
    ]
    report = {
        "tool": "systems",
        "stage": stage["id"],
        "job": 13,
        "producer": "rank-0",
        "kernels": [],
        "nvtx": ["lab_workload"],
        "host_copy": {
            "schema": "run-labs-host-copy/v1",
            "job": 13,
            "producer": "rank-0",
            "buffer_bytes": size,
            "device": 0,
            "context_id": 1,
            "stream_id": 7,
            "global_pid": 3 * 0x1000000,
            "global_tid": 3 * 0x1000000 + 7,
            "workload": {"name": "lab_workload", "start_ns": 1, "end_ns": 1000},
            "modes": modes,
            "validation_copy": group(500, 1, "Pageable", "device-to-host", True),
        },
    }
    return unit, stage, report


@pytest.mark.parametrize("profile", ["small", "large"])
@pytest.mark.parametrize("variant", ["baseline", "candidate"])
@pytest.mark.parametrize("kernels", [[], ["incidental_initialization"]])
def test_host_copy_uses_complete_transfer_proof(profile, variant, kernels):
    unit, stage, report = host_copy_fixture(profile, variant)
    report["kernels"] = kernels
    # Segmented activity is valid when its logical copy counts and bytes agree.
    report["host_copy"]["modes"][0]["segments"] = 50
    assert evidence.verify_native_content(unit, stage, report) == "matching_host_copy"
    report.pop("host_copy")
    with pytest.raises(ValueError, match="Native host-copy"):
        evidence.verify_native_content(unit, stage, report)


@pytest.mark.parametrize(
    "field,value",
    [
        ("submissions", 24),
        ("completions", 24),
        ("correlated_copies", 24),
        ("segments", 24),
        ("bytes", 64 * 2**20),
        ("submissions", True),
        ("direction", "device-to-host"),
        ("host_memory", "Pinned"),
        ("first_submission_ns", 0),
        ("first_copy_start_ns", 99),
        ("last_copy_end_ns", 1001),
        ("last_submission_end_ns", 99),
        ("device_synchronizations", 20),
        ("stream_synchronizations", 0),
    ],
)
def test_host_copy_rejects_partial_or_conflicting_modes(field, value):
    unit, stage, report = host_copy_fixture()
    report["kernels"] = ["incidental_initialization"]
    report["host_copy"]["modes"][0][field] = value
    with pytest.raises(ValueError, match="Native host-copy"):
        evidence.verify_native_content(unit, stage, report)


def test_host_copy_rejects_identity_scope_and_readback_changes():
    import copy

    unit, stage, report = host_copy_fixture()
    report["kernels"] = ["incidental_initialization"]
    for field, value in [
        ("job", 14),
        ("producer", "rank-1"),
        ("buffer_bytes", True),
        ("device", True),
        ("device", 1),
        ("context_id", 0),
        ("stream_id", -1),
        ("global_pid", 0),
        ("global_pid", 4 * 0x1000000),
        ("global_tid", True),
        ("schema", "wrong"),
        ("validation_copy", None),
        ("modes", report["host_copy"]["modes"][:3]),
    ]:
        broken = copy.deepcopy(report)
        broken["host_copy"][field] = value
        with pytest.raises(ValueError, match="Native host-copy"):
            evidence.verify_native_content(unit, stage, broken)
    for field, value in [
        ("bytes", 1),
        ("direction", "host-to-device"),
        ("first_submission_ns", 450),
        ("completions", 0),
    ]:
        broken = copy.deepcopy(report)
        broken["host_copy"]["validation_copy"][field] = value
        with pytest.raises(ValueError, match="Native host-copy"):
            evidence.verify_native_content(unit, stage, broken)
    for changed_unit, changed_stage, changed_report in [
        ({**unit, "lab": "04_layout"}, stage, report),
        (unit, {**stage, "tool": "compute"}, {**report, "tool": "compute"}),
        (unit, {**stage, "dispatch": {"job": 14}}, report),
        (unit, {**stage, "variant": "candidate"}, report),
        (unit, {**stage, "argv": stage["argv"] + ["--size-mib", "64"]}, report),
        (unit, {**stage, "argv": stage["argv"] + ["--warmup", "4"]}, report),
        ({**unit, "profile": "large"}, stage, report),
        (unit, stage, {**report, "job": True}),
        (unit, stage, {**report, "nvtx": ["initialization"]}),
    ]:
        with pytest.raises(ValueError, match="Native host-copy"):
            evidence.verify_native_content(changed_unit, changed_stage, changed_report)
    report["host_copy"]["modes"].reverse()
    with pytest.raises(ValueError, match="Native host-copy"):
        evidence.verify_native_content(unit, stage, report)


def test_host_copy_export_retains_report_proof_and_visual_gates(tmp_path):
    import copy
    import shutil
    import struct

    unit, baseline, first = host_copy_fixture()
    _, candidate, second = host_copy_fixture(variant="candidate")
    candidate["dispatch"]["job"] = second["job"] = second["host_copy"]["job"] = 14
    unit.update(
        stages=[baseline, candidate],
        recipe={
            "comparisons": [],
            "repetitions": 1,
            "profilers": {"systems": ["capture"]},
        },
    )
    root, raw = tmp_path / "courses", tmp_path / "raw"
    raw.mkdir(mode=0o700)
    ref = root / unit["course"] / "reference"
    (ref / "grafana").mkdir(parents=True)
    (root / "tools").mkdir()
    shutil.copy2(ROOT / "tools/publish_results.py", root / "tools/publish_results.py")
    skill_common.write(
        ref / "observability.json",
        {"labs": {unit["lab"]: {"metrics": []}}},
        private=False,
    )
    skill_common.write(
        ref / "grafana" / (unit["lab"] + ".json"), {"uid": "course-test"}, private=False
    )
    inventory = []
    for report in (first, second):
        report.update(
            path=report["stage"] + ".nsys-rep",
            proof=report["stage"] + ".json",
            native_cli_verified=True,
            warnings=[],
        )
        for field in ("path", "proof"):
            file = raw / report[field]
            file.write_bytes(b"synthetic native fixture")
            checksum = skill_common.digest(file)
            report["sha256" if field == "path" else "proof_sha256"] = checksum
            inventory.append(
                {"path": file.name, "sha256": checksum, "size": file.stat().st_size}
            )
    skill_common.write(
        raw / "inventory.json", {"schema": "run-labs-inventory/v1", "files": inventory}
    )
    # Synthetic headers exercise structural/hash gates, not real visual acceptance.
    image = raw / "view.png"
    image.write_bytes(
        b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", 1920, 1080)
    )
    common = {
        "path": image.name,
        "sha256": skill_common.digest(image),
        "browser": "playwright-headless",
        "visually_reviewed": True,
        "public_view_reviewed": True,
    }
    shots = [
        {
            **common,
            "tool": "grafana",
            "dashboard_uid": "course-test",
            "profile": "small",
            "generation": 1,
            "numeric_checks": [],
        }
    ]
    for report in (first, second):
        shots.append(
            {
                **common,
                "tool": "systems",
                "report": report["path"],
                "report_sha256": report["sha256"],
                "content_checks": {
                    "matching_job": True,
                    "native_cli_verified": True,
                    "matching_host_copy": True,
                },
            }
        )
    receipt = {
        "schema": "run-labs-evidence/v1",
        "lab": unit["lab"],
        "profile": "small",
        "results": {},
        "native_reports": [first, second],
        "screenshots": shots,
    }
    evidence.validate(unit, raw, receipt, root)
    for mutation in ("proof", "report", "review", "missing-config", "image-hash"):
        broken = copy.deepcopy(receipt)
        if mutation == "proof":
            broken["native_reports"][0]["proof_sha256"] = "0" * 64
        elif mutation == "report":
            broken["native_reports"][0]["sha256"] = "0" * 64
        elif mutation == "review":
            checks = broken["screenshots"][1]["content_checks"]
            checks.pop("matching_host_copy")
            checks["matching_kernel_or_nvtx"] = True
        elif mutation == "missing-config":
            broken["screenshots"].pop()
        else:
            broken["screenshots"][1]["sha256"] = "0" * 64
        with pytest.raises(ValueError):
            evidence.validate(unit, raw, broken, root)
    image.write_bytes(b"not a PNG")
    for shot in receipt["screenshots"]:
        shot["sha256"] = skill_common.digest(image)
    with pytest.raises(ValueError, match="Invalid screenshot PNG"):
        evidence.validate(unit, raw, receipt, root)


def test_profiles_do_not_mutate_recipe_and_references_stay_separate():
    recipes = catalog.catalog(ROOT)
    row = recipes["advanced-gpu-communication:34_serving_goodput"]
    old = json.dumps(row, sort_keys=True)
    large = catalog.actions(row, "large", {"MODEL_PATH": "/prepared/model"})
    small = catalog.actions(row, "small", {"MODEL_PATH": "/prepared/model"})
    for stages, profile in [(large, "large"), (small, "small")]:
        for stage in stages:
            if "--profile" in stage.get("argv", []):
                assert stage["argv"][stage["argv"].index("--profile") + 1] == profile
    assert json.dumps(row, sort_keys=True) == old
    ref = catalog.actions(
        recipes["advanced-gpu-communication:30_megatron_overlap"], "large", {}
    )
    assert ref[0]["kind"] == "dependency"
    assert "--reference-only" in ref[0]["argv"]


@pytest.mark.parametrize("profile", ("small", "large"))
def test_vllm_offline_diagnostics_keep_process_and_memory_scope(profile):
    recipe = catalog.catalog(ROOT)["llm-inference:10_vllm_offline"]
    original = json.dumps(recipe, sort_keys=True)
    stages = catalog.actions(recipe, profile, {})
    for stage in stages:
        argv = stage.get("argv", [])
        assert ("--in-process" in argv) == (stage["kind"] == "profile")
        memory = stage["environment"].get("SBATCH_MEM_PER_NODE")
        assert memory == ("262144" if stage["id"] == "compute-baseline" else None)
        assert stage["environment"]["COURSE_WORKLOAD_PROFILE"] == profile
        if argv:
            assert argv[:2] == ["python3", "tools/submit_lab.py"]
            assert argv[argv.index("--profile") + 1] == profile
    assert json.dumps(recipe, sort_keys=True) == original


@pytest.mark.parametrize("profile", ("small", "large"))
def test_recipe_stage_memory_overrides_defaults_without_leaking(profile):
    recipe = catalog.catalog(ROOT)["llm-inference:10_vllm_offline"]
    prepared = {"SBATCH_MEM_PER_NODE": "65536"}
    stages = catalog.actions(recipe, profile, prepared)
    for stage in stages:
        expected = "262144" if stage["id"] == "compute-baseline" else "65536"
        assert stage["environment"]["SBATCH_MEM_PER_NODE"] == expected
    stages[0]["environment"]["SBATCH_MEM_PER_NODE"] = "1"
    assert stages[1]["environment"]["SBATCH_MEM_PER_NODE"] == "65536"
    assert prepared == {"SBATCH_MEM_PER_NODE": "65536"}


@pytest.mark.parametrize(
    "environment",
    [
        None,
        [],
        "SBATCH_MEM_PER_NODE=262144",
        {"SBATCH_MEM_PER_NODE": 262144},
        {"SBATCH_MEM_PER_NODE": True},
        *(
            {"SBATCH_MEM_PER_NODE": value}
            for value in (
                "",
                "0",
                "-1",
                "+1",
                "256G",
                "1.5",
                " 1",
                "1\n",
                "01",
                "${MEMORY}",
            )
        ),
        {"SBATCH_ACCOUNT": "other"},
        {"COURSE_CAPTURE": "1"},
        {"SLURM_JOB_ID": "1"},
        {"API_KEY": "not-a-secret"},
    ],
)
def test_recipe_stage_environment_rejects_unreviewed_values(environment):
    recipe = catalog.catalog(ROOT)["llm-inference:10_vllm_offline"]
    recipe["profiling_runs"][0]["environment"] = environment
    with pytest.raises(ValueError, match="Recipe stage environment"):
        catalog.actions(recipe, "small", {})


@pytest.mark.parametrize("profile", ("small", "large"))
@pytest.mark.parametrize(
    "lab,variants",
    [
        ("09_capstone", ("baseline", "repeat")),
        ("16_library_first_decision", ("baseline", "repeat")),
        ("19_h2d_pipeline", ("serial", "pipeline", "slot-control")),
    ],
)
def test_optimization_acceptance_keeps_three_independent_jobs_per_variant(
    profile, lab, variants
):
    recipe = catalog.catalog(ROOT)[f"gpu-optimizations:{lab}"]
    stages = catalog.actions(recipe, profile, {})
    executions = [stage for stage in stages if stage["kind"] == "execute"]
    assert [stage["id"] for stage in executions] == [
        f"{variant}-trial{trial}"
        for trial, order in [
            (1, variants),
            (2, tuple(reversed(variants))),
            (3, variants),
        ]
        for variant in order
    ]
    for variant in variants:
        trials = [stage for stage in executions if stage["variant"] == variant]
        assert len(trials) == 3
        assert all(stage["argv"] == trials[0]["argv"] for stage in trials)
        assert all(
            stage["argv"][stage["argv"].index("--profile") + 1] == profile
            for stage in trials
        )
    assert recipe["result_count"] == 1
    assert len({stage["id"] for stage in stages}) == len(stages)


@pytest.mark.parametrize("profile", ("small", "large"))
def test_engine_qualification_keeps_distinct_equivalent_runs_per_protocol(profile):
    recipe = catalog.catalog(ROOT)["llm-inference:30_engine_profile"]
    stages = catalog.actions(recipe, profile, {})
    executions = [stage for stage in stages if stage["kind"] == "execute"]
    assert len(executions) == 4
    assert recipe["comparisons"] == [
        ["openai", "openai-repeat"],
        ["triton", "triton-repeat"],
    ]
    by_variant = {stage["variant"]: stage for stage in executions}
    for protocol, launcher in (
        ("openai", "slurm/openai_engine.sbatch"),
        ("triton", "slurm/trtllm_triton.sbatch"),
    ):
        baseline = by_variant[protocol]
        repeat = by_variant[protocol + "-repeat"]
        assert baseline["id"] != repeat["id"]
        assert baseline["argv"] == repeat["argv"]
        assert launcher in baseline["argv"]
        assert baseline["environment"] == repeat["environment"]
        assert baseline["environment"]["COURSE_WORKLOAD_PROFILE"] == profile
    assert not any(stage["kind"] == "profile" for stage in stages)
    assert recipe["result_count"] == 1


@pytest.mark.parametrize("profile", ("small", "large"))
def test_inference_basics_repeats_each_configuration_and_profiles_only_cuda(profile):
    recipe = catalog.catalog(ROOT)["llm-inference:35_inference_basics"]
    stages = catalog.actions(recipe, profile, {})
    executions = [stage for stage in stages if stage["kind"] == "execute"]
    assert len(executions) == 6
    configurations = ("cuda", "cpu", "cpu-one-token")
    assert recipe["comparisons"] == [[name, name + "-repeat"] for name in configurations]
    by_variant = {stage["variant"]: stage for stage in executions}
    for name in configurations:
        baseline = by_variant[name]
        repeat = by_variant[name + "-repeat"]
        assert baseline["id"] != repeat["id"]
        assert baseline["argv"] == repeat["argv"]
        assert baseline["environment"] == repeat["environment"]
        assert baseline["argv"][baseline["argv"].index("--profile") + 1] == profile
        assert baseline["argv"][baseline["argv"].index("--device") + 1] == (
            "cuda" if name == "cuda" else "cpu"
        )
        assert ("--max-new-tokens" in baseline["argv"]) == (name == "cpu-one-token")
        if name == "cpu-one-token":
            assert baseline["argv"][baseline["argv"].index("--max-new-tokens") + 1] == "1"
    captures = [stage for stage in stages if stage["kind"] == "profile"]
    assert len(captures) == 1
    capture = captures[0]
    assert (capture["id"], capture["tool"], capture["variant"]) == (
        "systems-cuda", "systems", "cuda"
    )
    assert capture["argv"][capture["argv"].index("--device") + 1] == "cuda"
    assert capture["argv"][capture["argv"].index("--profile") + 1] == profile
    assert capture["expected_reports"] == 1
    assert recipe["profilers"]["systems"] == recipe["profiling_runs"][0]["argv"]
    assert recipe["profilers"]["compute"] is None


@pytest.mark.parametrize("profile", ("small", "large"))
def test_profile_workload_keeps_internal_and_external_profilers_separate(profile):
    recipe = catalog.catalog(ROOT)["gpu-optimizations:07_profile_workload"]
    stages = catalog.actions(recipe, profile, {})
    executions = [stage for stage in stages if stage["kind"] == "execute"]
    captures = [stage for stage in stages if stage["kind"] == "profile"]
    assert len(executions) == len(captures) == 2
    for stage in executions:
        assert "--export-trace" in stage["argv"]
        assert "--external-only" not in stage["argv"]
    assert {stage["tool"] for stage in captures} == {"systems", "compute"}
    for stage in captures:
        assert "--external-only" in stage["argv"]
        assert "--export-trace" not in stage["argv"]
        assert stage["argv"][stage["argv"].index("--profile") + 1] == profile
        export = next(arg for arg in stage["argv"] if arg.startswith("--export="))
        if stage["tool"] == "compute":
            assert "COURSE_PROFILE_RANGE=projection" in export.split(",")
        else:
            assert not any("COURSE_PROFILE_RANGE=" in arg for arg in stage["argv"])
        template = list(recipe["profilers"][stage["tool"]])
        template[template.index("--profile") + 1] = profile
        assert stage["argv"] == template


@pytest.mark.parametrize("profile", ("small", "large"))
def test_tail_compute_uses_measured_probe_in_both_profiles(profile):
    recipe = catalog.catalog(ROOT)["gpu-optimizations:15_tail_load_balance"]
    stages = catalog.actions(recipe, profile, {})
    compute = next(stage for stage in stages if stage.get("tool") == "compute")
    export = next(arg for arg in compute["argv"] if arg.startswith("--export="))
    assert set(export.split(",")) == {
        "--export=ALL",
        "COURSE_PROFILE_TOOL=ncu",
        "COURSE_PROFILE_RANGE=tail_measure",
        "COURSE_PROFILE_KERNEL=uniform_tail_probe",
    }
    template = list(recipe["profilers"]["compute"])
    template[template.index("--profile") + 1] = profile
    assert compute["argv"] == template
    for stage in stages:
        if stage["kind"] in ("execute", "profile") and stage is not compute:
            assert not any("COURSE_PROFILE_RANGE=" in arg for arg in stage["argv"])


@pytest.mark.parametrize("profile", ("small", "large"))
@pytest.mark.parametrize(
    "lab,region,kernel",
    [
        ("19_h2d_pipeline", "consume_batch", None),
        ("20_d2h_pipeline", "produce_output", ".*(gemm|nvjet).*"),
    ],
)
def test_pipeline_compute_selects_workload_gemm(profile, lab, region, kernel):
    recipe = catalog.catalog(ROOT)["gpu-optimizations:" + lab]
    stages = catalog.actions(recipe, profile, {})
    compute = next(stage for stage in stages if stage.get("tool") == "compute")
    export = next(arg for arg in compute["argv"] if arg.startswith("--export="))
    expected = {
        "--export=ALL",
        "COURSE_PROFILE_TOOL=ncu",
        "COURSE_PROFILE_RANGE=" + region,
    }
    if kernel is not None:
        expected.add("COURSE_PROFILE_KERNEL=" + kernel)
    assert set(export.split(",")) == expected
    template = list(recipe["profilers"]["compute"])
    template.extend(["--profile", profile])
    assert compute["argv"] == template
    for stage in stages:
        if stage["kind"] in ("execute", "profile") and stage is not compute:
            assert not any("COURSE_PROFILE_RANGE=" in arg for arg in stage["argv"])


@pytest.mark.parametrize("profile", ("small", "large"))
def test_cuda_capstone_memcheck_precedes_three_acceptance_children(profile):
    recipe = catalog.catalog(ROOT)["custom-cuda-kernels:12_capstone"]
    stages = catalog.actions(recipe, profile, {"COURSE_BUILD_DIR": "/prepared/build"})
    dependency, trials = stages[:2]
    assert dependency["id"] == "dependency-memcheck"
    assert dependency["kind"] == "dependency"
    assert dependency["argv"] == [
        "python3",
        "tools/submit_lab.py",
        "--lab",
        "12_capstone",
        "slurm/sanitizer.sbatch",
        "memcheck",
        "/prepared/build/12_capstone",
        "--profile",
        profile,
        "--variant-order",
        "baseline-first",
    ]
    assert trials["id"] == "trials-trial1" and trials["kind"] == "execute"
    assert recipe["result_count"] == 3
    assert sum(stage["kind"] == "execute" for stage in stages) == 1


def staged(root, name, files):
    p = root / name
    p.mkdir(mode=0o700)
    for key, value in files.items():
        (p / key).write_text(value)
    return p


def test_repeat_replaces_obsolete_owned_files_and_preserves_foreign(tmp_path):
    final = tmp_path / "current"
    journal = tmp_path / "journal.json"
    first = staged(tmp_path, "first", {"old.png": "old", "result.json": "one"})
    transaction.replace_sets(journal, [(first, final, True)], "one")
    (final / "personal.txt").write_text("preserve")
    second = staged(tmp_path, "second", {"new.png": "new", "result.json": "two"})
    transaction.replace_sets(journal, [(second, final, True)], "two")
    assert (final / "result.json").read_text() == "two"
    assert not (final / "old.png").exists()
    assert (final / "personal.txt").read_text() == "preserve"
    assert not journal.exists()
    assert not list(tmp_path.glob("*.run-labs-*"))


def test_interrupted_two_destination_replacement_restores_both(tmp_path):
    a = tmp_path / "public"
    b = tmp_path / "private"
    journal = tmp_path / "journal.json"
    one = staged(tmp_path, "one", {"result.json": "one"})
    transaction.replace_sets(journal, [(one, a, False), (one, b, True)], "old")
    two = staged(tmp_path, "two", {"result.json": "two"})
    with pytest.raises(RuntimeError):
        transaction.replace_sets(
            journal, [(two, a, False), (two, b, True)], "new", fail_after=0
        )
    assert (a / "result.json").read_text() == (b / "result.json").read_text() == "one"
    assert not journal.exists()


def test_foreign_collision_and_symlink_cannot_be_replaced(tmp_path):
    final = staged(tmp_path, "final", {"result.json": "mine"})
    fresh = staged(tmp_path, "fresh", {"result.json": "new"})
    with pytest.raises(ValueError, match="unrelated"):
        transaction.replace_sets(
            tmp_path / "journal.json", [(fresh, final, True)], "new"
        )
    assert (final / "result.json").read_text() == "mine"
    link = tmp_path / "link"
    link.symlink_to(final, target_is_directory=True)
    with pytest.raises(ValueError, match="Symlink"):
        transaction.replace_sets(
            tmp_path / "journal2.json", [(fresh, link, True)], "new"
        )


def test_hash_inventory_rejects_changed_bytes_and_escape(tmp_path):
    p = tmp_path / "result.json"
    p.write_text("{}")
    inventory = {
        "schema": "run-labs-inventory/v1",
        "files": [{"path": p.name, "size": 2, "sha256": skill_common.digest(p)}],
    }
    evidence.verify_inventory(tmp_path, inventory)
    p.write_text("[]")
    with pytest.raises(ValueError, match="checksum"):
        evidence.verify_inventory(tmp_path, inventory)
    inventory["files"][0]["path"] = "../escape"
    with pytest.raises(ValueError, match="path"):
        evidence.verify_inventory(tmp_path, inventory)


def test_projection_does_not_copy_infrastructure_strings():
    result = evidence.numeric_projection(
        {
            "duration_ms": 1.2,
            "server_url": "https://private.invalid",
            "hostname": "worker-secret",
            "prompt": "secret",
            "nested": {"value": 4, "file_path": "/private/data"},
        }
    )
    assert result == {"duration_ms": 1.2, "nested": {"value": 4}}


def test_all_profiles_freeze_unique_stage_sets():
    recipes = catalog.catalog(ROOT)
    for row in recipes.values():
        for profile in ("small", "large"):
            stages = catalog.actions(row, profile, {})
            assert len({s["id"] for s in stages}) == len(stages)
            assert [s["kind"] for s in stages[-4:]] == [
                "verify",
                "collect",
                "browser",
                "export",
            ]
            for stage in stages:
                if stage["kind"] in ("execute", "dependency", "profile"):
                    assert stage["argv"][:2] == ["python3", "tools/submit_lab.py"]


def test_partial_preparation_collision_leaves_no_orphan_incoming(tmp_path):
    final = staged(tmp_path, "final", {"result.json": "personal"})
    fresh = staged(tmp_path, "fresh", {"result.json": "new"})
    for _ in range(2):
        with pytest.raises(ValueError, match="unrelated"):
            transaction.replace_sets(
                tmp_path / "journal.json", [(fresh, final, True)], "new"
            )
        assert not (tmp_path / ".final.run-labs-incoming").exists()


def test_cancellation_never_dispatches_intent(monkeypatch):
    sys.path.insert(0, str(SCRIPTS))
    try:
        transport = module("transport")
    finally:
        sys.path.remove(str(SCRIPTS))
    calls = []

    def request(state, unit, stage, action):
        calls.append(action)
        return {"not_dispatched": True}

    monkeypatch.setattr(transport, "request", request)
    state = {"plan": {"units": [{"stages": [{"intent": True}]}]}}
    transport.cancel_owned(state)
    assert calls == ["reconcile"]


@pytest.mark.parametrize("with_metrics", [True, False])
@pytest.mark.parametrize("vendor_capture", [None, "baseline", "candidate"])
def test_synthetic_evidence_roundtrip_and_missing_metric_rejection(
    tmp_path, with_metrics, vendor_capture
):
    import copy
    import shutil
    import struct

    root = tmp_path / "courses"
    course_name = "advanced-gpu-communication" if vendor_capture else "example"
    course = root / course_name
    (course / "reference/grafana").mkdir(parents=True)
    (root / "tools").mkdir()
    shutil.copy2(ROOT / "tools/publish_results.py", root / "tools/publish_results.py")
    lab = "06_nvlink_bandwidth" if vendor_capture else "01_example"
    obs = {
        "lab": lab,
        "kind": "single_gpu",
        "tuning_parameters": [],
        "metrics": [
            {
                "name": "duration_seconds",
                "path": "median_ms",
                "unit": "s",
                "scale": 0.001,
            }
        ],
    }
    if not with_metrics:
        obs["metrics"] = []
    skill_common.write(
        course / "reference/observability.json", {"labs": {lab: obs}}, private=False
    )
    skill_common.write(
        course / "reference/grafana" / f"{lab}.json",
        {"uid": "course-test"},
        private=False,
    )
    raw = tmp_path / "raw"
    raw.mkdir(mode=0o700)
    unit = {
        "course": course_name,
        "lab": lab,
        "profile": "small",
        "recipe": {
            "kind": "single_gpu",
            "result_count": 1,
            "comparisons": [["baseline", "repeat"]],
            "repetitions": 1,
            "profilers": {"systems": None, "compute": None},
        },
        "stages": [
            {"id": name + "-trial1", "kind": "execute", "dispatch": {"job": job}}
            for name, job in [("baseline", 11), ("repeat", 12)]
        ],
    }
    inventory = []
    results = {}
    checks = []
    for stage in unit["stages"]:
        name = stage["id"] + ".json"
        document = {
            "schema": "gpu-course-result/v1",
            "lab_id": lab,
            "profile": "small",
            "seed": 17,
            "run_id": str(stage["dispatch"]["job"]),
            "environment": {"gpu_family": "NVIDIA H200"},
            "measurements": {"median_ms": 2.0},
            "correctness": {"close": True},
            "experiment": {
                "instrumented": False,
                "parameters": {},
                "started_unix_seconds": 1,
                "ended_unix_seconds": 2,
                "slurm_job_id": stage["dispatch"]["job"],
            },
        }
        skill_common.write(raw / name, document)
        inventory.append(
            {
                "path": name,
                "size": (raw / name).stat().st_size,
                "sha256": skill_common.digest(raw / name),
            }
        )
        results[stage["id"]] = [name]
        checks.append(
            {
                "stage": stage["id"],
                "result_index": 0,
                "metric": "duration_seconds",
                "case": "",
                "observed_base_units": 0.002,
                "unit": "s",
            }
        )
    skill_common.write(
        raw / "inventory.json", {"schema": "run-labs-inventory/v1", "files": inventory}
    )
    # This header-only image is intentionally a synthetic validation fixture.
    (raw / "view.png").write_bytes(
        b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", 1920, 1080)
    )
    receipt = {
        "schema": "run-labs-evidence/v1",
        "lab": lab,
        "profile": "small",
        "hardware": {"gpu_model": "NVIDIA H200", "gpu_count": 1},
        "results": results,
        "native_reports": [],
        "screenshots": [
            {
                "name": "grafana-comparison",
                "tool": "grafana",
                "path": "view.png",
                "sha256": skill_common.digest(raw / "view.png"),
                "browser": "playwright-headless",
                "visually_reviewed": True,
                "public_view_reviewed": True,
                "dashboard_uid": "course-test",
                "profile": "small",
                "generation": 1,
                "numeric_checks": checks if with_metrics else [],
            }
        ],
    }
    if vendor_capture:
        unit["recipe"]["profilers"]["systems"] = ["vendor-capture"]
        stage_id = "systems-" + vendor_capture
        unit["stages"].append(
            {
                "id": stage_id,
                "kind": "profile",
                "variant": vendor_capture,
                "tool": "systems",
                "expected_reports": 1,
                "dispatch": {"job": 13},
            }
        )
        for filename in ("vendor.nsys-rep", "vendor.native-proof.txt"):
            # Synthetic bytes exercise receipt binding, never native capture claims.
            (raw / filename).write_bytes(b"synthetic vendor fixture")
            inventory.append(
                {
                    "path": filename,
                    "size": (raw / filename).stat().st_size,
                    "sha256": skill_common.digest(raw / filename),
                }
            )
        skill_common.write(
            raw / "inventory.json",
            {"schema": "run-labs-inventory/v1", "files": inventory},
        )
        native = {
            "stage": stage_id,
            "tool": "systems",
            "job": 13,
            "producer": "vendor-0",
            "path": "vendor.nsys-rep",
            "sha256": skill_common.digest(raw / "vendor.nsys-rep"),
            "kernels": [
                "_Z18memcmpKernelDeviceyyyjy"
                if vendor_capture == "baseline"
                else "_Z20stridingMemcpyKerneljyP5uint4S0_m"
            ],
            "nvtx": [],
            "peer_copies": [
                {
                    "source": src,
                    "destination": dst,
                    "events": 400,
                    "bytes": 400 * 64 * 2**20,
                }
                for src in range(8)
                for dst in range(8)
                if src != dst
            ]
            if vendor_capture == "baseline"
            else [],
            "proof": "vendor.native-proof.txt",
            "proof_sha256": skill_common.digest(raw / "vendor.native-proof.txt"),
            "native_cli_verified": True,
            "warnings": ["No NVTX events collected."],
        }
        receipt["native_reports"].append(native)
        receipt["screenshots"].append(
            {
                "name": "nsight-systems-vendor-timeline",
                "tool": "systems",
                "path": "view.png",
                "sha256": skill_common.digest(raw / "view.png"),
                "browser": "playwright-headless",
                "visually_reviewed": True,
                "public_view_reviewed": True,
                "report": native["path"],
                "report_sha256": native["sha256"],
                "content_checks": {
                    "matching_job": True,
                    (
                        "matching_peer_copy"
                        if vendor_capture == "baseline"
                        else "matching_kernel_or_nvtx"
                    ): True,
                    "native_cli_verified": True,
                },
            }
        )
    evidence.validate(unit, raw, receipt, root)
    if vendor_capture:
        broken = copy.deepcopy(receipt)
        broken["screenshots"][-1]["content_checks"] = {
            "matching_job": True,
            "native_cli_verified": True,
        }
        with pytest.raises(ValueError, match="content was not independently verified"):
            evidence.validate(unit, raw, broken, root)
        for field, replacement in [
            ("nvtx", None),
            ("kernels", []),
            ("tool", "compute"),
        ]:
            broken = copy.deepcopy(receipt)
            broken["native_reports"][0][field] = replacement
            with pytest.raises(ValueError, match="Native"):
                evidence.validate(unit, raw, broken, root)
        if vendor_capture == "baseline":
            invalid_pairs = [[], native["peer_copies"][:-1]]
            for field, value in [("bytes", 4), ("events", True), ("source", 9)]:
                changed = copy.deepcopy(native["peer_copies"])
                changed[0][field] = value
                invalid_pairs.append(changed)
            invalid_pairs.append([native["peer_copies"][0]] * 56)
            for pairs in invalid_pairs:
                broken = copy.deepcopy(receipt)
                broken["native_reports"][0]["peer_copies"] = pairs
                with pytest.raises(ValueError, match="peer-copy"):
                    evidence.validate(unit, raw, broken, root)
        else:
            broken = copy.deepcopy(receipt)
            broken["native_reports"][0]["kernels"] = ["_Z18memsetKernelDeviceyyyj"]
            with pytest.raises(ValueError, match="SM copy kernel"):
                evidence.validate(unit, raw, broken, root)
    if with_metrics:
        broken = copy.deepcopy(receipt)
        broken["screenshots"][0]["numeric_checks"].pop()
        with pytest.raises(ValueError, match="every selected result metric"):
            evidence.validate(unit, raw, broken, root)
        broken["screenshots"][0]["numeric_checks"] = []
        with pytest.raises(ValueError, match="numeric comparisons"):
            evidence.validate(unit, raw, broken, root)
    else:
        broken = copy.deepcopy(receipt)
        broken["screenshots"][0]["numeric_checks"] = checks
        with pytest.raises(ValueError, match="Unexpected Grafana metric"):
            evidence.validate(unit, raw, broken, root)
    for malformed in (None, {}, ""):
        broken = copy.deepcopy(receipt)
        broken["screenshots"][0]["numeric_checks"] = malformed
        with pytest.raises(ValueError, match="numeric comparisons"):
            evidence.validate(unit, raw, broken, root)
    broken = copy.deepcopy(receipt)
    broken["results"]["baseline-trial1"].append("baseline-trial1.json")
    with pytest.raises(ValueError, match="cardinality"):
        evidence.validate(unit, raw, broken, root)
    public = course / "reference/lab-results" / lab / "small"
    evidence.build_public(unit, raw, receipt, root, public, "a" * 64)
    prepare_bundle_sources(course)
    archive = evidence.bundle(course)
    before = archive.read_bytes()
    evidence.bundle(course)
    assert archive.read_bytes() == before
    assert (public / "summary.csv").read_text().count("0.002") == (
        2 if with_metrics else 0
    )
    assert "server_url" not in (public / "result-baseline-trial1.json").read_text()


@pytest.mark.parametrize(
    "profile,buffer_bytes", [("small", 64 * 2**20), ("large", 512 * 2**20)]
)
def test_vendor_peer_capture_requires_profile_size_and_keeps_other_nvtx_gates(
    profile, buffer_bytes
):
    unit = {
        "course": "advanced-gpu-communication",
        "lab": "06_nvlink_bandwidth",
        "profile": profile,
    }
    stage = {"tool": "systems", "variant": "baseline"}
    report = {
        "tool": "systems",
        "producer": "vendor-0",
        "kernels": ["_Z18memcmpKernelDeviceyyyjy"],
        "nvtx": [],
        "peer_copies": [
            {"source": src, "destination": dst, "events": 1, "bytes": buffer_bytes}
            for src in range(8)
            for dst in range(8)
            if src != dst
        ],
    }
    evidence.verify_native_content(unit, stage, report)
    unit["profile"] = "large" if profile == "small" else "small"
    with pytest.raises(ValueError, match="peer-copy"):
        evidence.verify_native_content(unit, stage, report)
    unit["lab"] = "08_distributed_collectives"
    with pytest.raises(ValueError, match="NVTX"):
        evidence.verify_native_content(unit, stage, report)
    report["nvtx"] = ["lab_workload"]
    evidence.verify_native_content(unit, stage, report)


def test_preparing_journal_recovers_partial_copy(tmp_path):
    final = staged(tmp_path, "current", {"old.json": "keep"})
    incoming = staged(
        tmp_path, ".current.run-labs-incoming", {"partial.json": "partial"}
    )
    journal = tmp_path / "journal.json"
    skill_common.write(
        journal,
        {
            "schema": "run-labs-replacement/v1",
            "identity": "attempt",
            "phase": "preparing",
            "rows": [
                {
                    "final": str(final),
                    "incoming": str(incoming),
                    "backup": str(tmp_path / ".current.run-labs-backup"),
                    "existed": True,
                }
            ],
        },
    )
    transaction.recover(journal, [final])
    assert (final / "old.json").read_text() == "keep"
    assert not incoming.exists() and not journal.exists()


def test_bundle_rejects_changed_previous_artifact_preserves_zip(tmp_path):
    course = tmp_path / "example"
    public = course / "reference/lab-results/01_example/small"
    public.mkdir(parents=True)
    summary = public / "summary.csv"
    summary.write_text("metric,value\ntime,1\n")
    skill_common.write(
        public / "manifest.json",
        {"schema": "course-lab-results/v1", "course": course.name, "lab": "01_example", "profile": "small", "artifacts": [], "summary_sha256": skill_common.digest(summary)},
        private=False,
    )
    prepare_bundle_sources(course)
    archive = evidence.bundle(course)
    before = archive.read_bytes()
    summary.write_text("metric,value\ntime,2\n")
    with pytest.raises(ValueError, match="differs from its manifest"):
        evidence.bundle(course)
    assert archive.read_bytes() == before
    assert not list(archive.parent.glob(".course-*"))


def test_finalization_recovers_claim_after_last_stage(tmp_path, monkeypatch):
    sys.path.insert(0, str(SCRIPTS))
    try:
        controller = module("run_labs")
    finally:
        sys.path.remove(str(SCRIPTS))
    state = {"status": "running", "preflight": {}, "plan": {"units": []}}
    state["preflight"] = {"verified": True}
    released = []
    monkeypatch.setattr(controller, "release", lambda s: released.append(s["status"]))
    controller.finalize(tmp_path, state)
    assert state["status"] == "complete"
    assert released == ["complete"]
    controller.finalize(tmp_path, state)
    assert released == ["complete", "complete"]


def test_accounting_cancelled_by_user_is_terminal(monkeypatch):
    remote = module("remote_job")
    monkeypatch.setattr(
        remote,
        "call",
        lambda *a, **kw: (
            f"123|rl-example|{remote.getpass.getuser()}|CANCELLED by 0|0:0\n"
        ),
    )
    assert remote.job_rows("rl-example")[0]["state"] == "CANCELLED"


def test_public_download_links_require_manifest_ownership(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "result_link_validator", ROOT / "tools/validate_course_template.py"
    )
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)
    monkeypatch.setattr(validator, "ROOT", tmp_path)
    folder = tmp_path / "reference/lab-results/01_example/small"
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text(
        json.dumps({"artifacts": [{"file": "grafana.png"}]})
    )
    (folder / "grafana.png").write_bytes(b"PNG")
    (folder / "private.json").write_text("{}")
    assert validator.student_result_link(
        "reference/lab-results/01_example/small/grafana.png"
    )
    assert not validator.student_result_link(
        "reference/lab-results/01_example/small/private.json"
    )
    assert not validator.student_result_link(
        "reference/lab-results/01_example/small/../../private.json"
    )
    assert not validator.student_result_link("https://example.invalid/grafana.png")
    parser = validator.Parser()
    parser.feed(
        '<a href="reference/lab-results/01_example/small/private.json">data</a>'
    )
    assert not parser.errors
    with pytest.raises(SystemExit, match="not manifest-owned"):
        validator.validate_result_links(parser)


@pytest.mark.parametrize("lab", ["09_nccl_transport_sweep", "12_distributed_scaling"])
@pytest.mark.parametrize("profile", ["small", "large"])
def test_direct_systems_capture_matches_named_variant(lab, profile):
    recipe = catalog.catalog(ROOT)[f"advanced-gpu-communication:{lab}"]
    stages = catalog.actions(recipe, profile, {})
    variants = {s["variant"]: s for s in stages if s["kind"] == "execute"}
    for capture in (s for s in stages if s["kind"] == "profile"):
        command = [a for a in capture["argv"] if not a.startswith("--export=")]
        assert command == variants[capture["variant"]]["argv"]


@pytest.fixture
def controller_modules():
    import importlib

    sys.path.insert(0, str(SCRIPTS))
    try:
        importlib.import_module("prepare")
        return importlib.import_module("run_labs"), importlib.import_module("stage")
    finally:
        sys.path.remove(str(SCRIPTS))


@pytest.mark.parametrize("conflict", [False, True])
def test_interrupted_claim_acquisition_recovers_before_effects(
    tmp_path, monkeypatch, controller_modules, conflict
):
    import copy
    from types import SimpleNamespace

    controller, stages = controller_modules
    root = tmp_path / "courses"
    root.mkdir()
    private = tmp_path / "private"
    env = {"private_root": str(private), "target_id": "target"}
    recipes = {
        f"example:{lab}": {"course": "example", "lab": lab}
        for lab in ("01_first", "02_second")
    }
    monkeypatch.setattr(controller, "catalog", lambda root: recipes)
    monkeypatch.setattr(controller, "private_environment", lambda path: env)
    monkeypatch.setattr(
        controller,
        "freeze",
        lambda root, chosen, *args: {
            "units": [
                {
                    **copy.deepcopy(recipes[key]),
                    "key": key,
                    "profile": "small",
                    "stages": [{"id": "execute", "kind": "execute"}],
                }
                for key in chosen
            ]
        },
    )
    real_write = controller.write
    claims_written = 0

    def interrupt_second_claim(path, value):
        nonlocal claims_written
        if path.parent.name == "claims":
            claims_written += 1
            if claims_written == 2:
                raise OSError("interrupted claim acquisition")
        real_write(path, value)

    monkeypatch.setattr(controller, "write", interrupt_second_claim)
    args = SimpleNamespace(
        courses_root=root,
        course=[],
        lab=[],
        all_courses=True,
        profile="small",
        environment=tmp_path / "env.json",
        dry_run=False,
    )
    with pytest.raises(OSError, match="claim acquisition"):
        controller.create(args)
    first = next((private / "campaigns").iterdir())
    if conflict:
        args.all_courses = False
        args.lab = ["example:02"]
        controller.create(args)
    effects = []
    import prepare

    monkeypatch.setattr(stages, "verify_frozen", lambda state: None)
    monkeypatch.setattr(prepare, "sync", lambda *args: effects.append("sync") or {})
    if conflict:
        with pytest.raises(ValueError, match="claim|campaign"):
            stages.run(first, "sync")
        assert not effects
    else:
        stages.run(first, "sync")
        state = controller.load(first)
        assert state["status"] == "running"
        assert effects == ["sync"]
        for unit in state["plan"]["units"]:
            assert (
                skill_common.read(controller.claim_path(state, unit))["id"]
                == state["id"]
            )


@pytest.mark.parametrize("removed_before_failure", [False, True])
def test_export_resumes_cleanup_without_republishing(
    tmp_path, monkeypatch, controller_modules, removed_before_failure
):
    controller, stages = controller_modules
    private = skill_common.directory(tmp_path / "private")
    path = skill_common.directory(private / "campaigns/test")
    unit = {
        "key": "example:01_example",
        "course": "example",
        "lab": "01_example",
        "profile": "small",
        "stages": [{"id": "export", "kind": "export"}],
    }
    state = {
        "schema": "run-labs-campaign/v1",
        "id": "test",
        "status": "running",
        "courses_root": str(tmp_path / "courses"),
        "preflight": {"passed": True},
        "environment": {"private_root": str(private), "target_id": "target"},
        "plan": {"source_sha256": "a" * 64, "units": [unit]},
    }
    controller.save(path, state)
    skill_common.write(
        controller.claim_path(state, unit), {"id": "test", "campaign": str(path)}
    )
    work = skill_common.directory(private / "staging/test/example/01_example/small")
    skill_common.write(work / "evidence.json", {})
    skill_common.write(work / "raw/original.json", {"value": 1})
    publications = []

    def build(unit, raw, receipt, root, public, source):
        publications.append("build")
        skill_common.write(public / "result.json", {"value": 1})

    monkeypatch.setattr(stages, "verify_frozen", lambda state: None)
    monkeypatch.setattr(stages, "build_public", build)
    monkeypatch.setattr(stages, "bundle", lambda root: publications.append("bundle"))
    monkeypatch.setattr(stages, "request", lambda *args: {"cleaned": True})
    real_remove = stages.shutil.rmtree
    attempts = []

    def interrupted_cleanup(folder, *args, **kwargs):
        if folder == work:
            attempts.append(folder)
            if len(attempts) == 1:
                if removed_before_failure:
                    real_remove(folder, *args, **kwargs)
                raise OSError("interrupted staging cleanup")
        real_remove(folder, *args, **kwargs)

    monkeypatch.setattr(stages.shutil, "rmtree", interrupted_cleanup)
    with pytest.raises(OSError, match="staging cleanup"):
        stages.run(path, "advance")
    assert (
        controller.load(path)["plan"]["units"][0]["stages"][0].get("status")
        != "complete"
    )
    result = stages.run(path, "advance")
    assert result["status"] == "complete"
    assert publications == ["build", "bundle"]
    assert not work.exists()
    assert (private / "raw/example/01_example/small/original.json").exists()


def test_terminal_campaign_leaves_new_campaign_claim(tmp_path, controller_modules):
    controller, _ = controller_modules
    state = {
        "id": "old",
        "status": "complete",
        "preflight": {"passed": True},
        "environment": {"private_root": str(tmp_path), "target_id": "target"},
        "plan": {
            "units": [{"key": "example:01_example", "profile": "small", "stages": []}]
        },
    }
    claim = controller.claim_path(state, state["plan"]["units"][0])
    skill_common.write(claim, {"id": "new", "campaign": "new"})
    controller.finalize(tmp_path, state)
    assert skill_common.read(claim)["id"] == "new"


@pytest.mark.parametrize("profile", ("small", "large"))
@pytest.mark.parametrize(
    "key,region,kernel",
    [
        (
            "llm-inference:09_hf_prefill_decode",
            "model_forward",
            ".*(gemm|gemv|nvjet).*",
        ),
        ("llm-inference:10_vllm_offline", "vllm_generate", ".*(gemm|gemv|nvjet).*"),
        ("llm-inference:17_sampling_semantics", "generation", ".*(gemm|gemv|nvjet).*"),
        (
            "llm-inference:23_speculative_decoding",
            "target_only",
            ".*(gemm|gemv|nvjet).*",
        ),
        (
            "llm-training:02_gradient_accumulation",
            "gradient_pass",
            ".*(gemm|gemv|nvjet).*",
        ),
        ("llm-training:06_grpo_objective", "grpo_objective", None),
        ("llm-training:07_grpo_trainer", "trainer_train", ".*(gemm|gemv|nvjet).*"),
        (
            "llm-training:14_activation_checkpointing",
            "checkpoint_step",
            ".*(gemm|gemv|nvjet).*",
        ),
        (
            "llm-training:21_mixed_precision_training",
            "mixed_precision_step",
            ".*(gemm|gemv|nvjet).*",
        ),
        (
            "llm-training:22_transformer_engine_fp8",
            "bf16_step",
            ".*(gemm|gemv|nvjet).*",
        ),
        ("llm-training:30_training_profiler", "training_step", ".*(gemm|gemv|nvjet).*"),
    ],
)
def test_llm_compute_selects_scoped_operation(profile, key, region, kernel):
    recipe = catalog.catalog(ROOT)[key]
    stages = catalog.actions(recipe, profile, {})
    compute = next(stage for stage in stages if stage.get("tool") == "compute")
    expected = {
        "--export=ALL",
        "COURSE_PROFILE_TOOL=ncu",
        "COURSE_PROFILE_RANGE=" + region,
    }
    if kernel is not None:
        expected.add("COURSE_PROFILE_KERNEL=" + kernel)
    export = next(arg for arg in compute["argv"] if arg.startswith("--export="))
    assert set(export.split(",")) == expected
    template = list(recipe["profilers"]["compute"])
    assert (
        set(next(arg for arg in template if arg.startswith("--export=")).split(","))
        == expected
    )
    for stage in stages:
        if stage["kind"] in ("execute", "profile") and stage is not compute:
            assert not any("COURSE_PROFILE_RANGE=" in arg for arg in stage["argv"])


@pytest.mark.parametrize("profile", ("small", "large"))
def test_grpo_objective_baseline_matches_guide_default(profile):
    recipe = catalog.catalog(ROOT)["llm-training:06_grpo_objective"]
    stages = catalog.actions(recipe, profile, {})
    for stage in stages:
        if stage["kind"] not in ("execute", "profile"):
            continue
        argv = stage["argv"]
        if stage["variant"] == "baseline":
            assert "--group-size" not in argv
        else:
            assert stage["variant"] == "candidate"
            assert argv[argv.index("--group-size") + 1] == "4"


@pytest.mark.parametrize("profile", ["small", "large"])
@pytest.mark.parametrize(
    "key", ["llm-inference:32_inference_capstone", "llm-training:31_training_capstone"]
)
def test_capstone_preserves_two_independent_three_child_groups(profile, key):
    recipe = catalog.catalog(ROOT)[key]
    stages = catalog.actions(recipe, profile, {})
    jobs = [s for s in stages if s["kind"] == "execute"]
    assert [s["variant"] for s in jobs] == ["trials", "trials-repeat"]
    assert len({s["id"] for s in jobs}) == 2
    assert jobs[0]["argv"] == jobs[1]["argv"]
    assert jobs[0]["environment"] == jobs[1]["environment"]
    assert jobs[0]["environment"]["COURSE_WORKLOAD_PROFILE"] == profile
    assert jobs[0]["argv"][4] == "slurm/capstone_three_trials.sbatch"
    assert recipe["repetitions"] == 1 and recipe["result_count"] == 3
    assert recipe["trial_contract"] == "capstone" and recipe["comparisons"] == []
    captures = [s for s in stages if s["kind"] == "profile"]
    assert [(s["id"], s["variant"], s["expected_reports"]) for s in captures] == [
        ("systems-trials", "trials", 1),
        ("compute-trials", "trials", 1),
    ]
    assert all("slurm/single_gpu.sbatch" in s["argv"] for s in captures)


@pytest.mark.parametrize("profile", ["small", "large"])
def test_training_learning_pairs_repeat_each_device_without_cpu_capture(profile):
    recipe = catalog.catalog(ROOT)["llm-training:32_learning_basics"]
    stages = catalog.actions(recipe, profile, {})
    jobs = [s for s in stages if s["kind"] == "execute"]
    assert [s["variant"] for s in jobs] == ["cuda", "cuda-repeat", "cpu", "cpu-repeat"]
    assert len({s["id"] for s in jobs}) == 4
    assert recipe["comparisons"] == [["cuda", "cuda-repeat"], ["cpu", "cpu-repeat"]]
    for left, right in [(jobs[0], jobs[1]), (jobs[2], jobs[3])]:
        assert (
            left["argv"] == right["argv"]
            and left["environment"] == right["environment"]
        )
        assert left["argv"][left["argv"].index("--profile") + 1] == profile
        assert left["argv"][left["argv"].index("--device") + 1] == left["variant"]
    captures = [s for s in stages if s["kind"] == "profile"]
    assert len(captures) == 1 and captures[0]["id"] == "systems-cuda"
    assert captures[0]["argv"][captures[0]["argv"].index("--device") + 1] == "cuda"
    assert captures[0]["expected_reports"] == 1
    assert recipe["profilers"]["systems"] == recipe["profiling_runs"][0]["argv"]
    assert recipe["profilers"]["compute"] is None


@pytest.mark.parametrize("profile", ["small", "large"])
def test_tiering_preserves_five_single_control_policy_pairs(profile):
    recipe = catalog.catalog(ROOT)["llm-inference:36_kv_tiering"]
    stages = catalog.actions(recipe, profile, {})
    jobs = [s for s in stages if s["kind"] == "execute"]
    assert recipe["comparisons"] == [
        ["no-tier", "storage"],
        ["storage", "ttl"],
        ["storage", "bandwidth"],
        ["storage", "restart"],
        ["storage", "revision"],
    ]
    assert len(jobs) == 6 and len({s["id"] for s in jobs}) == 6
    assert not any(s["kind"] == "profile" for s in stages)
    defaults = {
        "--host-prefixes": "2",
        "--storage-prefixes": "8",
        "--ttl-ms": "500",
        "--storage-gbps": "2.0",
        "--restart-at": "-1",
        "--revision-change-at": "-1",
    }
    controls = {}
    for s in jobs:
        assert s["argv"][s["argv"].index("--profile") + 1] == profile
        controls[s["variant"]] = {
            k: s["argv"][s["argv"].index(k) + 1] if k in s["argv"] else v
            for k, v in defaults.items()
        }
    keys = [
        "--storage-prefixes",
        "--ttl-ms",
        "--storage-gbps",
        "--restart-at",
        "--revision-change-at",
    ]
    for (left, right), key in zip(recipe["comparisons"], keys, strict=True):
        assert {k for k in defaults if controls[left][k] != controls[right][k]} == {key}
