"""CPU controls for transfer ownership, DDP checks and modeled KV retention."""

from concurrent.futures import Future
from contextlib import nullcontext
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from test_course_review_fixes import load_lab


@pytest.mark.parametrize("exit_code", [0, 9])
def test_ddp_profiler_launch_preserves_rank_arguments_and_failure(tmp_path, exit_code):
    bash = next(
        (
            path
            for path in ("/opt/homebrew/bin/bash", "/usr/bin/bash")
            if Path(path).exists()
        ),
        shutil.which("bash"),
    )
    version = subprocess.run(
        [bash, "-c", "printf '%s' ${BASH_VERSINFO[0]}"], capture_output=True, text=True
    )
    if int(version.stdout) < 4:
        pytest.skip("The cluster launcher requires Bash 4+ mapfile")
    root = Path(__file__).resolve().parents[1]
    (tmp_path / "labs").mkdir()
    (tmp_path / "labs/33_ddp_buckets.py").write_text("# Non-executed lab fixture\n")
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()

    def executable(name, body):
        path = binary_dir / name
        path.write_text(f"#!{sys.executable}\n" + body)
        path.chmod(0o700)
        return path

    executable("scontrol", "print('node-a\\nnode-b')\n")
    executable(
        "torchrun", "raise SystemExit('profiler fixture must not run training')\n"
    )
    executable(
        "srun",
        """import os, subprocess, sys
args = sys.argv[1:]
assert '--kill-on-bad-exit=1' in args
assert '--ntasks=2' in args and '--ntasks-per-node=1' in args
while args[0].startswith('--'):
    args.pop(0)
env = dict(os.environ, SLURM_NODEID='1')
raise SystemExit(subprocess.run(args, env=env).returncode)
""",
    )
    executable(
        "nsys",
        """import json, os, pathlib, sys
pathlib.Path(os.environ['CAPTURE_PATH']).write_text(json.dumps(sys.argv[1:]))
raise SystemExit(int(os.environ['FAKE_NSYS_EXIT']))
""",
    )
    capture = tmp_path / "capture.json"
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("SLURM_", "MASTER_", "COURSE_"))
    }
    env.update(
        PATH=str(binary_dir) + os.pathsep + env["PATH"],
        SLURM_JOB_ID="123",
        SLURM_JOB_NODELIST="node-[a-b]",
        CAPTURE_PATH=str(capture),
        FAKE_NSYS_EXIT=str(exit_code),
    )
    literal = "results/literal spaces;$(no-execution)"
    run = subprocess.run(
        [
            bash,
            str(root / "llm-training/slurm/nsys_ddp.sbatch"),
            "--hook",
            "allreduce",
            "--output-dir",
            literal,
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert run.returncode == exit_code, run.stderr
    captured = json.loads(capture.read_text())
    assert "--node-rank=1" in captured
    assert "--master-addr=node-a" in captured
    assert captured[-5:] == [
        "labs/33_ddp_buckets.py",
        "--hook",
        "allreduce",
        "--output-dir",
        literal,
    ]
    report = Path(
        next(
            arg.removeprefix("--output=")
            for arg in captured
            if arg.startswith("--output=")
        )
    )
    assert report.name == "node-1"
    assert report.parent.parent == tmp_path / "results"
    assert report.parent.stat().st_mode & 0o777 == 0o700
    assert ("Completed diagnostic capture" in run.stdout) == (exit_code == 0)


class ImmediateCPU:
    """Exercise loop logic with real tensors; deliberately no CUDA concurrency."""

    def __init__(self, torch):
        self.torch = torch
        self.cuda = SimpleNamespace(
            Stream=lambda: SimpleNamespace(
                wait_event=lambda event: None, synchronize=lambda: None
            ),
            Event=lambda: SimpleNamespace(
                record=lambda stream: None, synchronize=lambda: None
            ),
            synchronize=lambda: None,
            stream=lambda stream: nullcontext(),
            nvtx=SimpleNamespace(range=lambda name: nullcontext()),
        )

    def __getattr__(self, name):
        if name in ("empty", "full"):

            def allocate(*args, **kwargs):
                kwargs.pop("device", None)
                kwargs.pop("pin_memory", None)
                return getattr(self.torch, name)(*args, **kwargs)

            return allocate
        return getattr(self.torch, name)


def test_output_wait_precedes_cpu_read_and_slot_release():
    with load_lab("gpu-optimizations/labs/20_d2h_pipeline.py") as lab:
        events = []
        result = lab.consume_after_copy(
            SimpleNamespace(synchronize=lambda: events.append("copy_complete")),
            lambda: events.append("read") or 7,
        )
        assert result == 7
        assert events == ["copy_complete", "read"]
        slot = lab.OutputSlot()
        pending = Future()
        slot.submit(pending)
        with pytest.raises(RuntimeError, match="consumer"):
            slot.submit(Future())
        pending.set_result(7)
        assert slot.acquire() == 7
        assert slot.acquire() is None


def test_failed_consumer_does_not_release_slot():
    with load_lab("gpu-optimizations/labs/20_d2h_pipeline.py") as lab:
        slot = lab.OutputSlot()
        failed = Future()
        failed.set_exception(ValueError("sink failed"))
        slot.submit(failed)
        with pytest.raises(ValueError, match="sink failed"):
            slot.acquire()
        assert slot.future is failed


@pytest.mark.parametrize(
    "mode", ["serial", "workers", "pooled", "nonblocking", "pipeline"]
)
def test_output_modes_complete_cpu_control_and_propagate_corruption(mode, monkeypatch):
    torch = pytest.importorskip("torch")
    torch.set_num_threads(1)
    with load_lab("gpu-optimizations/labs/20_d2h_pipeline.py") as lab:
        args = SimpleNamespace(
            profile="smoke", mode=mode, slots=2, workers=2, batches=3, sink_ms=0
        )
        elapsed, capacity = lab.run_pipeline(ImmediateCPU(torch), args)
        assert elapsed > 0 and capacity == 2 * 512 * 512 * 4

        def broken(*args):
            raise ValueError("corrupt output")

        monkeypatch.setattr(lab, "check_output", broken)
        with pytest.raises(ValueError, match="corrupt output"):
            lab.run_pipeline(ImmediateCPU(torch), args)


@pytest.mark.parametrize(
    "mode,slots", [("serial", 2), ("pipeline", 1), ("pipeline", 2)]
)
def test_input_modes_verify_every_batch_on_cpu_control(mode, slots, monkeypatch):
    torch = pytest.importorskip("torch")
    torch.set_num_threads(1)
    proxy = ImmediateCPU(torch)
    with load_lab("gpu-optimizations/labs/19_h2d_pipeline.py") as lab:
        args = SimpleNamespace(
            profile="smoke", mode=mode, slots=slots, batches=3, work=1
        )
        assert lab.run_pipeline(proxy, args)[0] > 0
        monkeypatch.setattr(proxy, "mm", lambda a, b, out: out.zero_(), raising=False)
        with pytest.raises(RuntimeError, match="reference"):
            lab.run_pipeline(proxy, args)


def test_ddp_error_and_update_checks_reject_omitted_or_corrupted_work():
    torch = pytest.importorskip("torch")
    with load_lab("llm-training/labs/33_ddp_buckets.py") as lab:
        x = torch.tensor([1.0, 2.0])
        assert lab.relative_l2(torch, [x], [x]) == 0
        assert lab.relative_l2(torch, [x * 2], [x]) == 1
        for bad in [None, torch.tensor([float("nan"), 0]), torch.tensor([1.0])]:
            with pytest.raises(ValueError):
                lab.relative_l2(torch, [bad], [x])
        model = torch.nn.Linear(2, 1, bias=False)
        optimizer = torch.optim.SGD(model.parameters(), lr=0.05, foreach=False)
        model(torch.ones(1, 2)).sum().backward()
        lab.checked_sgd(torch, model, optimizer, 0.05)
        before = [p.detach().clone() for p in model.parameters()]
        with pytest.raises(ValueError, match="differs"):
            lab.verify_update(torch, before, list(model.parameters()), 0.05)
        optimizer.zero_grad(set_to_none=True)
        with pytest.raises(ValueError, match="Missing"):
            lab.checked_sgd(torch, model, optimizer, 0.05)


@pytest.mark.parametrize(
    "hook,attribute",
    [
        ("allreduce", "allreduce_hook"),
        ("fp16", "fp16_compress_hook"),
        ("bf16", "bf16_compress_hook"),
    ],
)
def test_ddp_hook_records_actual_bucket_and_preserves_future(
    hook, attribute, monkeypatch
):
    torch = pytest.importorskip("torch")
    from torch.distributed.algorithms.ddp_comm_hooks import default_hooks

    future = torch.futures.Future()
    calls = []
    monkeypatch.setattr(
        default_hooks, attribute, lambda state, bucket: calls.append(bucket) or future
    )
    with load_lab("llm-training/labs/33_ddp_buckets.py") as lab:
        log = []
        state, selected = lab.make_hook(
            ImmediateCPU(torch), SimpleNamespace(hook=hook), log
        )
        bucket = SimpleNamespace(index=lambda: 3, buffer=lambda: torch.zeros(7))
        assert selected(state, bucket) is future
        assert calls == [bucket]
        assert log == [{"index": 3, "uncompressed_bytes": 28}]


def test_kv_expiry_identity_lru_and_zero_capacity():
    with load_lab("llm-inference/labs/36_kv_tiering.py") as lab:
        cache = lab.TierCache(lab.Policy(capacities=(1, 0, 1), ttl_ms=10))
        assert cache.access("a", 0, 0)["found_tier"] == "miss"
        cache.access("a", 1, 1)
        assert cache.access("a", 0, 2)["found_tier"] == "storage"
        assert cache.access("b", 0, 3)["found_tier"] == "miss"
        assert cache.access("a", 0, 12)["found_tier"] == "miss"
        cache.assert_invariants()
        result = lab.simulate(
            lab.Policy(capacities=(0, 0, 0)), prefixes=2, rounds=2, gap_ms=1
        )
        assert result["reuse_fraction"] == 0
        assert (
            result["modeled_foreground_total_ms"] == result["no_cache_prefill_total_ms"]
        )


def test_kv_restore_recompute_and_write_accounting():
    with load_lab("llm-inference/labs/36_kv_tiering.py") as lab:
        policy = lab.Policy(capacities=(1, 0, 2))
        assert policy.transfer_ms(2) == pytest.approx(2.297152)
        cache = lab.TierCache(policy)
        assert cache.access("a", 0, 0)["write_bytes"] == 0
        assert cache.access("a", 1, 1)["write_bytes"] == 4 * 2**20
        cache.restart_worker()
        restored = cache.access("a", 0, 2)
        assert restored["found_tier"] == "storage" and restored["decision"] == "reuse"
        slow = lab.TierCache(lab.Policy(capacities=(0, 0, 2), storage_gbps=0.2))
        slow.access("a", 0, 0)
        row = slow.access("a", 0, 1)
        assert row["found_tier"] == "storage" and row["decision"] == "recompute"
        assert row["read_bytes"] == 0
        assert row["modeled_foreground_ms"] == 8


@pytest.mark.parametrize(
    "field,value",
    [
        ("host_gbps", float("nan")),
        ("storage_gbps", 1e-300),
        ("ttl_ms", 0),
        ("capacities", (1, -1, 2)),
        ("prefix_bytes", True),
    ],
)
def test_invalid_policy_rejected(field, value):
    with load_lab("llm-inference/labs/36_kv_tiering.py") as lab:
        with pytest.raises(ValueError):
            lab.Policy(**{field: value})


@pytest.mark.parametrize(
    "path,options",
    [
        ("gpu-optimizations/labs/19_h2d_pipeline.py", ["--slots", "0"]),
        ("gpu-optimizations/labs/20_d2h_pipeline.py", ["--sink-ms", "nan"]),
        (
            "llm-training/labs/33_ddp_buckets.py",
            ["--hook", "powersgd", "--warmup", "2"],
        ),
        ("llm-inference/labs/36_kv_tiering.py", ["--storage-gbps", "0"]),
    ],
)
def test_invalid_cli_fails_before_gpu_import_or_execution(path, options, tmp_path):
    source = Path(__file__).resolve().parents[1] / path
    result = subprocess.run(
        [sys.executable, "-B", str(source), *options, "--output-dir", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "error:" in result.stderr
    assert not list(tmp_path.iterdir())
