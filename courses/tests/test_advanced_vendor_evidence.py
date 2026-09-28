"""Fail closed on malformed vendor measurements and invalid workload comparisons."""

import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest
from test_course_review_fixes import load_lab

PREFIX = "advanced-gpu-communication/labs/"


@pytest.mark.parametrize("empty_polls", [0, 1, 3])
def test_dynamo_frontend_waits_for_model_discovery(monkeypatch, capsys, empty_polls):
    with load_lab(PREFIX + "dynamo_experiments.py") as lab:
        responses = iter(
            [{"data": []}] * empty_polls + [{"data": [{"id": "course-model"}]}]
        )
        elapsed = [0.0]
        deadlines = []

        def observe(url, processes, timeout):
            assert url == "http://localhost/v1/models"
            deadlines.append(timeout)
            return next(responses)

        def sleep(seconds):
            elapsed[0] += seconds

        monkeypatch.setattr(lab, "wait_http", observe)
        monkeypatch.setattr(lab.time, "monotonic", lambda: elapsed[0])
        monkeypatch.setattr(lab.time, "sleep", sleep)
        lab.wait_frontend_model("http://localhost", None, timeout=10)
        assert len(deadlines) == empty_polls + 1
        assert deadlines == [10 - 0.5 * n for n in range(empty_polls + 1)]
        receipt = json.loads(capsys.readouterr().out.splitlines()[-1])
        assert receipt["frontend_discovery"]["empty_polls"] == empty_polls
        assert receipt["frontend_discovery"]["model"] == "course-model"


@pytest.mark.parametrize("payload", [{"data": [{"id": "different-model"}]}, {}, []])
def test_dynamo_frontend_rejects_wrong_or_malformed_models(monkeypatch, payload):
    with load_lab(PREFIX + "dynamo_experiments.py") as lab:
        monkeypatch.setattr(lab, "wait_http", lambda *a, **kw: payload)
        monkeypatch.setattr(lab.time, "sleep", lambda _: pytest.fail("Must fail now"))
        with pytest.raises(ValueError, match="Frontend discovery"):
            lab.wait_frontend_model("http://localhost", None)


def test_dynamo_frontend_discovery_has_one_deadline(monkeypatch):
    with load_lab(PREFIX + "dynamo_experiments.py") as lab:
        elapsed = [0.0]

        def sleep(seconds):
            elapsed[0] += seconds

        monkeypatch.setattr(lab, "wait_http", lambda *a, **kw: {"data": []})
        monkeypatch.setattr(lab.time, "monotonic", lambda: elapsed[0])
        monkeypatch.setattr(lab.time, "sleep", sleep)
        with pytest.raises(RuntimeError, match="Frontend.*deadline"):
            lab.wait_frontend_model("http://localhost", None, timeout=1)
        assert elapsed[0] == 1


def test_dynamo_frontend_propagates_owned_process_failure(monkeypatch):
    with load_lab(PREFIX + "dynamo_experiments.py") as lab:

        def failed(*args, **kwargs):
            raise RuntimeError("Owned worker exited")

        monkeypatch.setattr(lab, "wait_http", failed)
        with pytest.raises(RuntimeError, match="Owned worker exited"):
            lab.wait_frontend_model("http://localhost", None)


@pytest.mark.parametrize(
    "status,body,valid",
    [
        (200, b'{"status":"ok"}', True),
        (200, b'{"status":"error"}', False),
        (500, b'{"status":"ok"}', False),
        (200, b"[]", False),
        (200, b"x" * 65537, False),
    ],
)
@pytest.mark.parametrize("action,deadline", [("start", 90), ("stop", 600)])
def test_dynamo_profile_control_checks_application_acknowledgment(
    monkeypatch, status, body, valid, action, deadline
):
    from contextlib import nullcontext

    with load_lab(PREFIX + "dynamo_experiments.py") as lab:
        requests = []

        def open_request(request, timeout):
            requests.append((request, timeout))
            return nullcontext(SimpleNamespace(status=status, read=lambda size: body))

        monkeypatch.setattr(lab.urllib.request, "urlopen", open_request)
        if valid:
            lab.profile_control("10.0.0.1", action)
        else:
            with pytest.raises(RuntimeError, match="acknowledge"):
                lab.profile_control("10.0.0.1", action)
        request, timeout = requests[0]
        assert (
            request.method == "POST" and request.data == b"{}" and timeout == deadline
        )
        assert (
            request.full_url == f"http://10.0.0.1:8081/engine/control/{action}_profile"
        )


@pytest.mark.parametrize("finalization_seconds,passes", [(240, True), (601, False)])
def test_dynamo_capture_waits_for_slow_finalization_without_retry(
    tmp_path, monkeypatch, finalization_seconds, passes
):
    from contextlib import nullcontext

    with load_lab(PREFIX + "dynamo_experiments.py") as lab:
        controls, finalized, report_checks = [], [], []

        def open_request(request, timeout):
            address = request.host.split(":")[0]
            action = request.selector.rsplit("/", 1)[-1]
            controls.append((address, action))
            if action == "stop_profile":
                if finalization_seconds > timeout:
                    raise TimeoutError("report writer still finalizing")
                finalized.append(address)
            return nullcontext(
                SimpleNamespace(status=200, read=lambda size: b'{"status":"ok"}')
            )

        def check_reports(folder, count, processes):
            assert finalized == ["second", "first"]
            assert folder == tmp_path and count == 2
            report_checks.append(True)

        monkeypatch.setattr(lab.urllib.request, "urlopen", open_request)
        monkeypatch.setattr(lab, "wait_server_reports", check_reports)
        if passes:
            with lab.server_capture(["first", "second"], tmp_path, None):
                assert finalized == []
        else:
            with pytest.raises(TimeoutError, match="still finalizing"):
                with lab.server_capture(["first", "second"], tmp_path, None):
                    pass
        assert controls == [
            ("first", "start_profile"),
            ("second", "start_profile"),
            ("second", "stop_profile"),
            ("first", "stop_profile"),
        ]
        acknowledgments = [
            json.loads(line)
            for line in (tmp_path / "capture-control.jsonl").read_text().splitlines()
        ]
        assert len(acknowledgments) == (4 if passes else 2)
        assert report_checks == ([True] if passes else [])


@pytest.mark.parametrize("failure", ["start-second", "workload", "stop-second"])
def test_dynamo_profile_cleanup_stops_every_attempted_engine(
    tmp_path, monkeypatch, failure
):
    with load_lab(PREFIX + "dynamo_experiments.py") as lab:
        controls = []

        def control(address, action):
            controls.append((address, action))
            if address == "second" and failure == action + "-second":
                raise RuntimeError(failure)

        monkeypatch.setattr(lab, "profile_control", control)
        with pytest.raises(RuntimeError, match=failure):
            with lab.server_capture(["first", "second"], tmp_path, None):
                if failure == "workload":
                    raise RuntimeError(failure)
        assert controls == [
            ("first", "start"),
            ("second", "start"),
            ("second", "stop"),
            ("first", "stop"),
        ]


@pytest.mark.parametrize("layout", ["aggregated", "disaggregated"])
@pytest.mark.parametrize(
    "capture,report_state",
    [
        ("none", "missing"),
        ("systems", "valid"),
        ("systems", "missing"),
        ("systems", "empty"),
    ],
)
def test_dynamo_service_requires_batch_invariant_workers(
    tmp_path, monkeypatch, layout, capture, report_state
):
    with load_lab(PREFIX + "dynamo_experiments.py") as lab:
        model = tmp_path / lab.MODEL_REVISION
        model.mkdir()
        (model / "config.json").write_text("{}")
        runtime = tmp_path / "runtime with spaces" / "bin"
        runtime.mkdir(parents=True)
        python = runtime / "python"
        python.symlink_to(sys.executable)
        ninja = runtime / "ninja"
        ninja.write_text('#!/bin/sh\nprintf "prepared-ninja\\n"\n')
        ninja.chmod(0o700)
        inherited_path = str(tmp_path / "other tools")
        monkeypatch.setenv("PATH", inherited_path)
        monkeypatch.setenv("COURSE_DYNAMO_PYTHON", str(python))
        monkeypatch.setenv("COURSE_PROFILE_TOOL", "none")
        monkeypatch.setenv("VLLM_BATCH_INVARIANT", "0")
        monkeypatch.setattr(lab, "allocated_nodes", lambda: ["worker-a", "worker-b"])
        monkeypatch.setattr(
            lab.socket,
            "gethostbyname",
            lambda node: {"worker-a": "10.0.0.1", "worker-b": "10.0.0.2"}[node],
        )
        monkeypatch.setattr(lab, "start_etcd", lambda *_: "http://10.0.0.1:2379")
        monkeypatch.setattr(lab.shutil, "which", lambda _: "/tools/nsys")
        monkeypatch.setattr(lab, "request", lambda *_: None)
        controls = []

        def control(address, action):
            controls.append((address, action))
            if action == "stop" and report_state != "missing":
                rank = 0 if address == "10.0.0.1" else 1
                (tmp_path / f"server-rank{rank}.nsys-rep").write_bytes(
                    b"report" if report_state == "valid" else b""
                )

        monkeypatch.setattr(lab, "profile_control", control)
        clock = iter(range(0, 1000, 181))
        monkeypatch.setattr(lab.time, "monotonic", lambda: next(clock))
        monkeypatch.setattr(
            lab,
            "wait_http",
            lambda url, _, **kwargs: (
                {"data": [{"id": "course-model"}]}
                if url.endswith("/v1/models")
                else {"status": "ready"}
            ),
        )
        commands = {}
        shutdown = {}

        class Processes:
            def __init__(self, folder):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_):
                if capture == "systems" and report_state == "valid":
                    assert all(
                        (tmp_path / f"server-rank{rank}.nsys-rep").stat().st_size
                        for rank in (0, 1)
                    )
                return False

            def healthy(self):
                pass

            def start(self, name, command, **kwargs):
                commands[name] = command
                shutdown[name] = kwargs

        monkeypatch.setattr(lab, "Processes", Processes)
        args = SimpleNamespace(
            model_dir=model,
            layout=layout,
            run_id="test-run",
            capture=capture,
            router="round-robin",
        )
        if capture == "systems" and report_state != "valid":
            with pytest.raises(RuntimeError, match="both reports"):
                with lab.service(args, tmp_path):
                    pass
        else:
            with lab.service(args, tmp_path):
                # Reports must be retained after the workload, before shutdown.
                assert not (tmp_path / "server-rank0.nsys-rep").exists()
        for worker in ("worker0", "worker1"):
            child_env = dict(os.environ)
            child_env.update(
                token.split("=", 1)
                for token in commands[worker]
                if token.startswith("PATH=")
            )
            probe = subprocess.run(
                ["ninja", "--version"],
                env=child_env,
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert probe.stdout == "prepared-ninja\n"
            assert child_env["PATH"].split(os.pathsep)[1:] == [inherited_path]
            assert "VLLM_BATCH_INVARIANT=1" in commands[worker]
            assert ("--disable-status" in commands[worker]) == (capture == "systems")
            if capture == "systems":
                assert "--wait=primary" in commands[worker]
                assert "--cuda-trace-scope=process-tree" in commands[worker]
                assert "--capture-range=cudaProfilerApi" in commands[worker]
                assert "--capture-range-end=stop" in commands[worker]
                assert "--flush-on-cudaprofilerstop=false" in commands[worker]
                assert json.loads(
                    commands[worker][commands[worker].index("--profiler-config") + 1]
                ) == {"profiler": "cuda"}
            assert shutdown[worker]["interrupt_on_exit"] == (capture == "systems")
            assert shutdown[worker]["shutdown_timeout"] == (
                180 if capture == "systems" else 10
            )
        assert lab.os.environ["VLLM_BATCH_INVARIANT"] == "0"
        assert lab.os.environ["PATH"] == inherited_path
        assert controls == (
            [
                ("10.0.0.1", "start"),
                ("10.0.0.2", "start"),
                ("10.0.0.2", "stop"),
                ("10.0.0.1", "stop"),
            ]
            if capture == "systems"
            else []
        )


@pytest.mark.parametrize("interrupt", [False, True])
@pytest.mark.parametrize("hangs", [False, True])
def test_owned_process_shutdown_reaps_after_grace_or_bounded_escalation(
    tmp_path, monkeypatch, interrupt, hangs
):
    with load_lab(PREFIX + "job_processes.py") as lab:
        signals, waits = [], []

        def wait(timeout=None):
            waits.append(timeout)
            if hangs and timeout is not None:
                raise lab.subprocess.TimeoutExpired("owned-service", timeout)
            return 130 if interrupt else 143

        child = SimpleNamespace(pid=12345, poll=lambda: None, wait=wait)
        monkeypatch.setattr(lab.subprocess, "Popen", lambda *a, **kw: child)
        monkeypatch.setattr(
            lab.os, "killpg", lambda pid, sig: signals.append((pid, sig))
        )
        owned = lab.Processes(tmp_path)
        with owned:
            owned.start(
                "server",
                ["owned-service"],
                interrupt_on_exit=interrupt,
                shutdown_timeout=180,
            )
        expected = lab.signal.SIGINT if interrupt else lab.signal.SIGTERM
        assert signals == [(12345, expected)] + (
            [(12345, lab.signal.SIGKILL)] if hangs else []
        )
        assert waits == ([180, None] if hangs else [180])
        assert all(output.closed for output in owned.files)


@pytest.mark.parametrize("defect", [None, "layout", "revision", "tokenizer"])
def test_goodput_tokenizer_uses_the_pinned_offline_cache(tmp_path, monkeypatch, defect):
    with load_lab(PREFIX + "34_serving_goodput.py") as lab:
        cache = tmp_path / "cache with spaces"
        model = cache / "models--Qwen--Qwen3-8B" / "snapshots" / lab.MODEL_REVISION
        if defect == "layout":
            model = cache / lab.MODEL_REVISION
        if defect == "revision":
            model = model.with_name("different-revision")
        model.mkdir(parents=True)
        for name in ("config.json", "tokenizer.json", "tokenizer_config.json"):
            if defect != "tokenizer" or name != "tokenizer.json":
                (model / name).write_text("{}")
        monkeypatch.setenv("HF_HUB_CACHE", "unrelated-cache")
        monkeypatch.setenv("COURSE_TEST_CONTROL", "preserved")
        if defect:
            with pytest.raises(ValueError, match="pinned.*snapshot"):
                lab.aiperf_tokenizer_config(model)
        else:
            arguments, environment = lab.aiperf_tokenizer_config(model)
            assert arguments == [
                "--tokenizer",
                "Qwen/Qwen3-8B",
                "--tokenizer-revision",
                lab.MODEL_REVISION,
            ]
            assert environment["HF_HUB_CACHE"] == str(cache)
            assert environment["HF_HUB_OFFLINE"] == "1"
            assert environment["TRANSFORMERS_OFFLINE"] == "1"
            assert environment["COURSE_TEST_CONTROL"] == "preserved"
            assert lab.os.environ["HF_HUB_CACHE"] == "unrelated-cache"


def test_bridge_launcher_uses_selected_python_without_venv_console_script(tmp_path):
    import os
    from pathlib import Path
    import subprocess

    course = Path(__file__).resolve().parents[1] / "advanced-gpu-communication"
    tools = tmp_path / "tools with spaces"
    python = tools / "vendor-candidates/bridge/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text(
        "#!/usr/bin/env python3\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\n"
    )
    python.chmod(0o700)
    assert not python.with_name("torchrun").exists()
    arguments = ["--nproc-per-node=8", "training script.py", "--value", "two words"]
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source env/vendor-environment.sh; exec "$COURSE_BRIDGE_TORCHRUN" "$@"',
            "bridge-test",
            *arguments,
        ],
        cwd=course,
        env={
            **os.environ,
            "COURSE_TOOLS": str(tools),
            "UCX_PREFIX": str(tmp_path / "ucx"),
            "COURSE_ETCD": str(tmp_path / "etcd"),
        },
        text=True,
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ["-m", "torch.distributed.run", *arguments]


def test_nixl_parser_requires_one_complete_requested_case():
    with load_lab(PREFIX + "29_nixl_transfer.py") as lab:
        row = "4096 1 12.5 1.1 0.1 0.2 0.3 0.4 0.5 0.9"
        assert lab.parse_rows(row, 4096, 1)["transfer_p99_us"] == 0.9
        for broken in (
            "",
            row + "\n" + row,
            row.replace("4096", "8192"),
            row.replace("12.5", "0"),
            row.replace("0.5", "nan"),
        ):
            with pytest.raises(ValueError):
                lab.parse_rows(broken, 4096, 1)
        command = lab.benchmark_command(
            "nixlbench",
            "http://node:1",
            "group",
            SimpleNamespace(
                profile="small", warmup=50, iterations=500, progress_thread="off"
            ),
        )
        assert "--check_consistency" in command and "--enable_pt=false" in command
        assert command[command.index("--backend") + 1] == "UCX"


def test_rdma_latency_requires_requested_transport_memory_and_work():
    with load_lab(PREFIX + "27_rdma_latency.py") as lab:
        document = {
            "test_info": {"Connection_type": "RC", "Link_type": "IB", "cuda_device": 0},
            "results": {
                "MsgSize": 64,
                "n_iterations": 1000,
                "t_typical": 3,
                "t_avg": 4,
                "percentile_99": 6,
            },
        }
        assert lab.latency_result(document, 64, 1000, "cuda-dmabuf")["median_us"] == 3
        for key, value in (
            ("t_typical", float("nan")),
            ("t_avg", -1),
            ("n_iterations", 1),
            ("MsgSize", 1024),
        ):
            broken = json.loads(json.dumps(document))
            broken["results"][key] = value
            with pytest.raises(ValueError):
                lab.latency_result(broken, 64, 1000, "cuda-dmabuf")
        with pytest.raises(ValueError):
            lab.latency_result(document, 64, 1000, "host")


def test_megatron_full_parameter_reference_rejects_missing_nonfinite_and_wrong_values():
    torch = pytest.importorskip("torch")
    with load_lab(PREFIX + "bridge_experiments.py") as lab:
        expected = {"weight": torch.ones(3)}
        assert lab.compare_weights(torch, expected, expected, atol=0, rtol=0) == 0
        for actual in (
            {},
            {"weight": torch.ones(2)},
            {"weight": torch.full((3,), float("nan"))},
            {"weight": torch.zeros(3)},
        ):
            with pytest.raises(ValueError):
                lab.compare_weights(torch, actual, expected, atol=0.001, rtol=0.001)


@pytest.mark.parametrize("profile,length", [("small", 2048), ("large", 16384)])
@pytest.mark.parametrize("kind", ["overlap", "context"])
def test_bridge_comparisons_keep_a_supported_seeded_dataloader(
    monkeypatch, tmp_path, profile, length, kind
):
    import sys

    config_names = (
        "CheckpointConfig ConfigContainer DistributedDataParallelConfig "
        "DistributedInitConfig GPTDatasetConfig LoggerConfig RNGConfig "
        "TokenizerConfig TrainingConfig ValidationConfig"
    ).split()
    # Model the pinned API's unset loader default, without importing its GPU stack.
    configs = {name: SimpleNamespace for name in config_names}
    configs["GPTDatasetConfig"] = (
        lambda dataloader_type=None, create_attention_mask=True, **values: (
            SimpleNamespace(
                dataloader_type=dataloader_type,
                create_attention_mask=create_attention_mask,
                **values,
            )
        )
    )
    modules = {
        "megatron.bridge.models": SimpleNamespace(GPTModelProvider=SimpleNamespace),
        "megatron.bridge.training.config": SimpleNamespace(**configs),
        "megatron.bridge.training.comm_overlap": SimpleNamespace(
            CommOverlapConfig=SimpleNamespace
        ),
        "megatron.bridge.recipes.utils.optimizer_utils": SimpleNamespace(
            distributed_fused_adam_with_cosine_annealing=lambda **values: (
                SimpleNamespace(**values),
                SimpleNamespace(**values),
            )
        ),
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    with load_lab(PREFIX + "bridge_experiments.py") as lab:
        args = SimpleNamespace(
            profile=profile,
            seed=17,
            warmup=5,
            iterations=20,
            overlap="off",
            layout="flat",
        )
        baseline = lab.make_config(args, tmp_path, kind)
        if kind == "overlap":
            args.overlap = "on"
        else:
            args.layout = "hierarchical"
        candidate = lab.make_config(args, tmp_path, kind)
    assert baseline.dataset.dataloader_type == "single"
    assert baseline.dataset.skip_getting_attention_mask_from_dataset is True
    assert baseline.dataset.create_attention_mask is False
    assert vars(baseline.dataset) == vars(candidate.dataset)
    assert baseline.dataset.seq_length == length
    assert baseline.dataset.random_seed == baseline.rng.seed == 17
    assert baseline.dataset.blend is baseline.dataset.blend_per_split is None
    assert baseline.train.train_iters == 25


@pytest.mark.parametrize("profile,output_tokens", [("small", 32), ("large", 128)])
def test_goodput_cli_requests_and_verifies_fixed_server_work(
    tmp_path, monkeypatch, profile, output_tokens
):
    import sys
    from contextlib import nullcontext

    with load_lab(PREFIX + "34_serving_goodput.py") as lab:
        binary = tmp_path / "aiperf"
        binary.touch()
        monkeypatch.setenv("COURSE_AIPERF", str(binary))
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "34_serving_goodput.py",
                "--profile",
                profile,
                "--model-dir",
                str(tmp_path),
                "--concurrency",
                "16",
            ],
        )
        monkeypatch.setattr(lab, "validate_common_args", lambda args: None)
        monkeypatch.setattr(
            lab, "aiperf_tokenizer_config", lambda path: ([], {"offline": "1"})
        )
        monkeypatch.setattr(lab, "private_folder", lambda *args: tmp_path)
        commands, verified, written = [], [], []

        def start(name, command, *, env):
            assert name == "aiperf" and env == {"offline": "1"}
            commands.append(command)
            return SimpleNamespace(wait=lambda timeout: 0)

        processes = SimpleNamespace(start=start, healthy=lambda: None)
        monkeypatch.setattr(
            lab, "service", lambda *args: nullcontext(("http://localhost", processes))
        )

        def summarize(folder, expected, **controls):
            verified.append((folder, expected, controls))
            return {"verified": True}

        monkeypatch.setattr(lab, "summarize", summarize)
        monkeypatch.setattr(lab, "allocation_gpu_family", lambda: "H100")
        monkeypatch.setattr(
            lab, "write_result", lambda args, **result: written.append(result)
        )
        lab.main()
        command = commands[0]
        assert "--use-server-token-count" in command
        assert command[command.index("--export-level") + 1] == "raw"
        assert command[command.index("--output-tokens-mean") + 1] == str(output_tokens)
        assert command[command.index("--concurrency") + 1] == "16"
        controls = json.loads(command[command.index("--extra-inputs") + 1])
        assert controls["min_tokens"] == output_tokens
        assert controls["ignore_eos"] is True and controls["n"] == 1
        assert controls["temperature"] == 0
        assert verified == [
            (
                tmp_path / "aiperf",
                128,
                {"output_tokens": output_tokens, "seed": controls["seed"]},
            )
        ]
        assert written[0]["measurements"] == {"verified": True}
        assert written[0]["correctness"]["fixed_generated_token_work"] is True


def aiperf_fixture(folder):
    summary = {"schema_version": "1.4", "was_cancelled": False}
    for name, unit, value in (
        ("request_count", "requests", 2),
        ("good_request_count", "requests", 1),
        ("request_throughput", "requests/sec", 2),
        ("goodput", "requests/sec", 1),
        ("time_to_first_token", "ms", 7),
        ("inter_token_latency", "ms", 3),
        ("output_sequence_length", "tokens", 4),
        ("input_sequence_length", "tokens", 8),
    ):
        summary[name] = {"unit": unit, "avg": value, "p99": value, "count": 999}
    (folder / "profile_export_aiperf.json").write_text(json.dumps(summary))
    rows = []
    records = []
    raw = []
    metrics = {
        "time_to_first_token": {"value": 7, "unit": "ms"},
        "inter_token_latency": {"value": 3, "unit": "ms"},
        "request_latency": {"value": 16, "unit": "ms"},
        "input_sequence_length": {"value": 8, "unit": "tokens"},
        "output_sequence_length": {"value": 4, "unit": "tokens"},
    }
    for n in range(2):
        identity = {
            "session_num": n,
            "turn_index": 0,
            "conversation_id": "conversation" + str(n),
            "x_request_id": str(n),
            "request_start_ns": 1000 + n * 100,
            "request_end_ns": 1090 + n * 100,
        }
        records.append(
            {
                "metadata": {
                    **identity,
                    "benchmark_phase": "profiling",
                    "was_cancelled": False,
                },
                "error": None,
                "metrics": metrics,
            }
        )
        rows.append(
            {
                **identity,
                "response_text": "hello",
                "metrics": {k: v["value"] for k, v in metrics.items()},
            }
        )
        raw.append(
            {
                "metadata": dict(records[-1]["metadata"]),
                "status": 200,
                "payload": {
                    "model": "course-model",
                    "messages": [{"role": "user", "content": f"Explain packet {n}."}],
                    "temperature": 0,
                    "seed": 17,
                    "min_tokens": 4,
                    "max_completion_tokens": 4,
                    "ignore_eos": True,
                    "n": 1,
                    "stream": True,
                    "stream_options": {"include_usage": True},
                },
                "responses": [
                    {
                        "perf_ns": 10,
                        "packets": [
                            {
                                "name": "data",
                                "value": json.dumps(
                                    {
                                        "choices": [{"delta": {"content": "hello"}}],
                                        "usage": {
                                            "prompt_tokens": 8,
                                            "completion_tokens": 4,
                                            "total_tokens": 12,
                                        },
                                    }
                                ),
                            }
                        ],
                    },
                    {"perf_ns": 10, "packets": [{"name": "data", "value": "[DONE]"}]},
                ],
            }
        )
    (folder / "profile_export.jsonl").write_text(
        "\n".join(json.dumps(row) for row in records)
    )
    outputs = {"schema_version": "1.0", "data": rows}
    (folder / "outputs.json").write_text(json.dumps(outputs))
    (folder / "profile_export_raw.jsonl").write_text(
        "\n".join(json.dumps(row) for row in raw)
    )
    return summary, outputs


@pytest.mark.parametrize(
    "defect",
    [
        None,
        "unit",
        "errors",
        "missing-output",
        "duplicate-id",
        "cancelled",
        "unrelated-output",
        "timestamp",
        "missing-itl",
    ],
)
def test_aiperf_uses_scalar_avg_not_sample_count_and_rejects_incomplete_runs(
    tmp_path, defect
):
    summary, outputs = aiperf_fixture(tmp_path)
    if defect == "unit":
        summary["time_to_first_token"]["unit"] = "seconds"
    if defect == "errors":
        summary["error_request_count"] = {"unit": "requests", "avg": 1}
    if defect == "missing-output":
        outputs["data"].pop()
    if defect == "duplicate-id":
        outputs["data"][1]["x_request_id"] = "0"
    if defect == "unrelated-output":
        outputs["data"][1]["x_request_id"] = "other"
    if defect == "timestamp":
        outputs["data"][1]["request_start_ns"] += 1
    if defect == "missing-itl":
        del outputs["data"][1]["metrics"]["inter_token_latency"]
    if defect == "cancelled":
        summary["was_cancelled"] = True
    (tmp_path / "profile_export_aiperf.json").write_text(json.dumps(summary))
    (tmp_path / "outputs.json").write_text(json.dumps(outputs))
    with load_lab(PREFIX + "34_serving_goodput.py") as lab:
        if defect:
            with pytest.raises(ValueError):
                lab.summarize(tmp_path, 2, output_tokens=4, seed=17)
        else:
            result = lab.summarize(tmp_path, 2, output_tokens=4, seed=17)
            assert (
                result["completed_requests"] == 2 and result["goodput_per_second"] == 1
            )


@pytest.mark.parametrize(
    "defect",
    [
        "missing-file",
        "missing-record",
        "duplicate-id",
        "wrong-id",
        "wrong-session",
        "duplicate-session",
        "missing-position",
        "wrong-turn",
        "bool-turn",
        "timestamp",
        "http-error",
        "http-missing",
        "cancelled",
        "raw-error",
        "missing-payload",
        "min-tokens",
        "max-tokens",
        "ignore-eos",
        "n",
        "stream",
        "seed",
        "temperature",
        "usage-option",
        "null-stream-options",
        "stop",
        "legacy-max",
        "no-prompts",
        "missing-usage",
        "early-eos",
        "float-count",
        "bool-count",
        "zero-input",
        "float-input",
        "wrong-total",
        "reasoning-over-total",
        "no-done",
        "trailing-data",
        "server-error",
        "invalid-json",
        "bad-packet",
        "length-metric",
        "summary-mean",
    ],
)
def test_goodput_rejects_unproven_fixed_work(tmp_path, defect):
    aiperf_fixture(tmp_path)
    path = tmp_path / "profile_export_raw.jsonl"
    raw = [json.loads(line) for line in path.read_text().splitlines()]
    row = raw[0]
    event = json.loads(row["responses"][0]["packets"][0]["value"])
    payload = row["payload"]
    if defect == "missing-file":
        path.unlink()
    elif defect == "missing-record":
        raw.pop()
    elif defect in ("duplicate-id", "wrong-id"):
        row["metadata"]["x_request_id"] = "1" if defect == "duplicate-id" else "unknown"
    elif defect in ("wrong-session", "timestamp", "cancelled"):
        field, value = {
            "wrong-session": ("session_num", 9),
            "timestamp": ("request_start_ns", 999),
            "cancelled": ("was_cancelled", True),
        }[defect]
        row["metadata"][field] = value
    elif defect in ("duplicate-session", "missing-position", "wrong-turn", "bool-turn"):
        field = "turn_index" if defect in ("wrong-turn", "bool-turn") else "session_num"
        value = {
            "duplicate-session": 1,
            "missing-position": 9,
            "wrong-turn": 1,
            "bool-turn": False,
        }[defect]
        row["metadata"][field] = value
        record_path = tmp_path / "profile_export.jsonl"
        records = [json.loads(line) for line in record_path.read_text().splitlines()]
        records[0]["metadata"][field] = value
        record_path.write_text("\n".join(json.dumps(item) for item in records))
        output_path = tmp_path / "outputs.json"
        outputs = json.loads(output_path.read_text())
        outputs["data"][0][field] = value
        output_path.write_text(json.dumps(outputs))
    elif defect in ("http-error", "http-missing"):
        row["status"] = 500 if defect == "http-error" else None
    elif defect == "raw-error":
        row["error"] = {"message": "failed"}
    elif defect == "missing-payload":
        row["payload"] = None
    elif defect in (
        "min-tokens",
        "max-tokens",
        "ignore-eos",
        "n",
        "stream",
        "seed",
        "temperature",
    ):
        field, value = {
            "min-tokens": ("min_tokens", 3),
            "max-tokens": ("max_completion_tokens", 5),
            "ignore-eos": ("ignore_eos", False),
            "n": ("n", 2),
            "stream": ("stream", False),
            "seed": ("seed", 18),
            "temperature": ("temperature", 1),
        }[defect]
        payload[field] = value
    elif defect == "usage-option":
        payload["stream_options"]["include_usage"] = False
    elif defect == "null-stream-options":
        payload["stream_options"] = None
    elif defect == "stop":
        payload["stop"] = ["end"]
    elif defect == "legacy-max":
        payload["max_tokens"] = 4
    elif defect == "no-prompts":
        payload["messages"] = []
    elif defect == "missing-usage":
        event.pop("usage")
    elif defect in (
        "early-eos",
        "float-count",
        "bool-count",
        "zero-input",
        "float-input",
        "wrong-total",
    ):
        field, value = {
            "early-eos": ("completion_tokens", 3),
            "float-count": ("completion_tokens", 4.0),
            "bool-count": ("completion_tokens", True),
            "zero-input": ("prompt_tokens", 0),
            "float-input": ("prompt_tokens", 8.5),
            "wrong-total": ("total_tokens", 13),
        }[defect]
        event["usage"][field] = value
    elif defect == "reasoning-over-total":
        event["usage"]["completion_tokens_details"] = {"reasoning_tokens": 5}
    elif defect == "no-done":
        row["responses"].pop()
    elif defect == "trailing-data":
        row["responses"].append(row["responses"][0])
    elif defect == "server-error":
        event["error"] = {"message": "failed"}
    elif defect == "length-metric":
        event["usage"]["prompt_tokens"] = 9
        event["usage"]["total_tokens"] = 13
    elif defect == "summary-mean":
        summary_path = tmp_path / "profile_export_aiperf.json"
        summary = json.loads(summary_path.read_text())
        summary["input_sequence_length"]["avg"] = 9
        summary_path.write_text(json.dumps(summary))
    row["responses"][0]["packets"][0]["value"] = (
        "{" if defect == "invalid-json" else json.dumps(event)
    )
    if defect == "bad-packet":
        row["responses"][0]["packets"] = [None]
    if defect != "missing-file":
        path.write_text("\n".join(json.dumps(item) for item in raw))
    with load_lab(PREFIX + "34_serving_goodput.py") as lab:
        with pytest.raises(ValueError):
            lab.summarize(tmp_path, 2, output_tokens=4, seed=17)


def test_goodput_uses_actual_payloads_and_total_server_work(tmp_path):
    summary, outputs = aiperf_fixture(tmp_path)
    with load_lab(PREFIX + "34_serving_goodput.py") as lab:
        baseline = lab.summarize(tmp_path, 2, output_tokens=4, seed=17)
        raw_path = tmp_path / "profile_export_raw.jsonl"
        raw = [json.loads(line) for line in raw_path.read_text().splitlines()]
        # Metadata order, request IDs, timestamps and response wording may differ.
        outputs["data"][0]["response_text"] = "a different answer"
        (tmp_path / "outputs.json").write_text(json.dumps(outputs))
        event = json.loads(raw[0]["responses"][0]["packets"][0]["value"])
        event["usage"]["completion_tokens_details"] = {"reasoning_tokens": 3}
        # Native SSE permits comments and multiple data lines in one message.
        raw[0]["responses"][0]["packets"] = [
            {"name": "comment", "value": "keepalive"},
            *[
                {"name": "data", "value": line}
                for line in json.dumps(event, indent=2).splitlines()
            ],
        ]
        raw_path.write_text("\n".join(json.dumps(row) for row in reversed(raw)))
        candidate = lab.summarize(tmp_path, 2, output_tokens=4, seed=17)
        assert candidate["workload_sha256"] == baseline["workload_sha256"]
        assert candidate["output_signature"] != baseline["output_signature"]
        assert candidate["output_lengths"] == [4, 4]
        assert candidate["measurement_contract"] == "fixed-token-goodput-v1"
        # Prompt identity, not just equal token length, is part of the comparison.
        raw[0]["payload"]["messages"][0]["content"] = "Explain socket 0."
        raw_path.write_text("\n".join(json.dumps(row) for row in raw))
        changed = lab.summarize(tmp_path, 2, output_tokens=4, seed=17)
        assert changed["workload_sha256"] != baseline["workload_sha256"]
        summary["good_request_count"]["avg"] = 0
        summary["goodput"]["avg"] = 0
        (tmp_path / "profile_export_aiperf.json").write_text(json.dumps(summary))
        assert (
            lab.summarize(tmp_path, 2, output_tokens=4, seed=17)["goodput_per_second"]
            == 0
        )


def test_nic_selection_is_exact_per_worker_and_rejects_inherited_overrides(monkeypatch):
    with load_lab(PREFIX + "network_experiments.py") as lab:
        for name in ("NCCL_ALGO", "NCCL_PROTO", "NCCL_IB_HCA", "NCCL_IB_DISABLE"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("RANK", "8")
        lab.configure_transport("auto", "single", "mlx5_0", "mlx5_7")
        assert lab.os.environ["NCCL_IB_HCA"] == "=mlx5_7:1"
        with pytest.raises(ValueError, match="clean job"):
            lab.configure_transport("auto", "all", None, None)


def test_dynamo_worker_commands_keep_resource_and_model_contract():
    with load_lab(PREFIX + "dynamo_experiments.py") as lab:
        for role in ("agg", "prefill", "decode"):
            argv = lab.worker_command("python", "/model", role)
            assert argv[argv.index("--tensor-parallel-size") + 1] == "8"
            compilation = json.loads(argv[argv.index("--compilation-config") + 1])
            assert compilation == {
                "custom_ops": ["+rms_norm"],
                "pass_config": {"fuse_allreduce_rms": False},
            }
            attention = json.loads(argv[argv.index("--attention-config") + 1])
            assert attention == {"flash_attn_version": 2}
            assert "--enforce-eager" not in argv
            assert ("--kv-transfer-config" in argv) == (role != "agg")
            assert "--enforce-disagg" not in argv


def test_persistent_service_exit_zero_is_not_healthy(tmp_path):
    with load_lab(PREFIX + "job_processes.py") as lab:
        process = SimpleNamespace(poll=lambda: 0)
        owned = lab.Processes(tmp_path)
        owned.children = [process]
        owned.healthy()  # A completed one-shot client is allowed.
        owned.persistent = [process]
        with pytest.raises(RuntimeError):
            owned.healthy()


def test_etcd_binds_worker_ip_and_advertises_worker_name(monkeypatch, tmp_path):
    import ipaddress
    import socket
    from urllib.parse import urlsplit

    with load_lab(PREFIX + "job_processes.py") as lab:
        binary = tmp_path / "etcd"
        binary.touch()
        monkeypatch.setenv("COURSE_ETCD", str(binary))
        monkeypatch.setenv("SLURM_JOB_ID", "42")
        monkeypatch.setattr(socket, "gethostbyname", lambda node: "192.0.2.8")
        started, checked = [], []

        def start(name, command, *, persistent):
            listen = command[command.index("--listen-client-urls") + 1]
            # etcd rejects DNS names at its bind boundary even when DNS works.
            assert (
                ipaddress.ip_address(urlsplit(listen).hostname).compressed
                == "192.0.2.8"
            )
            assert persistent and name == "etcd"
            assert "--gpus-per-task=0" in command
            started.append(command)

        processes = SimpleNamespace(folder=tmp_path, start=start)
        monkeypatch.setattr(lab, "wait_http", lambda url, *a, **kw: checked.append(url))
        assert lab.start_etcd(processes, "worker-a") == "http://worker-a:25042"
        assert checked == ["http://worker-a:25042/health"]
        assert (
            started[0][started[0].index("--advertise-client-urls") + 1]
            == "http://worker-a:25042"
        )


@pytest.mark.parametrize("per_node", [1, 8])
def test_monitoring_uses_observed_cluster_inventory(monkeypatch, per_node):
    from test_observability_integration import ROOT, load

    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    monitor = load("verify_monitoring")
    nodes = [
        {
            "metadata": {"name": name},
            "status": {
                "capacity": {"nvidia.com/gpu": str(per_node)},
                "conditions": [{"type": "Ready", "status": "True"}],
            },
        }
        for name in ("worker-a", "worker-b")
    ]
    inventory = monitor.expected_gpu_inventory({"slug": "gpu-fundamentals"}, nodes)
    assert sum(inventory.values()) == 2 * per_node
    advanced = {"slug": "advanced-gpu-communication", "profile": "labs-only"}
    if per_node == 8:
        assert monitor.expected_gpu_inventory(advanced, nodes) == inventory
    else:
        with pytest.raises(ValueError):
            monitor.expected_gpu_inventory(advanced, nodes)
    rows = [
        {"metric": {"Hostname": node, "gpu": str(index)}, "value": [1, "1"]}
        for node, count in inventory.items()
        for index in range(count)
    ]
    payload = {"status": "success", "data": {"result": rows}}
    monitor.verify_gpu_inventory(payload, inventory)
    rows.pop()
    with pytest.raises(ValueError, match="worker/index"):
        monitor.verify_gpu_inventory(payload, inventory)
    # A different worker cannot fill in missing telemetry merely by matching total count.
    rows.append({"metric": {"Hostname": "foreign", "gpu": "0"}, "value": [1, "1"]})
    with pytest.raises(ValueError, match="worker/index"):
        monitor.verify_gpu_inventory(payload, inventory)
    nodes[0]["status"]["conditions"][0]["status"] = "False"
    with pytest.raises(ValueError, match="ready GPU"):
        monitor.expected_gpu_inventory({"slug": "gpu-fundamentals"}, nodes)
