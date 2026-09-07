"""Keep repeated practice intentional and one canonical partition experiment."""

from collections import defaultdict
import json
import re
from types import SimpleNamespace

import pytest

from test_course_content_contract import COURSES, ROOT, load_builder
from test_course_review_fixes import load_lab


def test_one_inference_partition_lab_keeps_both_collectives():
    root = ROOT / "llm-inference"
    assert not (root / "labs/31_inference_parallelism.py").exists()
    assert not (root / "reference/labs/31_inference_parallelism.md").exists()
    source = (root / "labs/19_tensor_parallel_linear.py").read_text()
    for term in ("broadcast", "all_gather", "all_reduce", "--batch-size", "finally:"):
        assert term in source
    inventory = json.loads((root / "reference/course.json").read_text())["labs"]
    assert len(inventory) == 25


@pytest.mark.parametrize("batch", [1, 3])
def test_partition_paths_reconstruct_common_weight(batch):
    torch = pytest.importorskip("torch")
    inputs = torch.arange(batch * 4, dtype=torch.float32).reshape(batch, 4)
    weight = torch.arange(16, dtype=torch.float32).reshape(4, 4)
    reference = inputs @ weight.T
    calls = []

    def gather(outputs, local):
        calls.append("gather")
        torch.testing.assert_close(local, inputs @ weight.chunk(2, dim=0)[rank].T)
        for output, shard in zip(outputs, weight.chunk(2, dim=0)):
            output.copy_(inputs @ shard.T)

    def reduce(output):
        calls.append("reduce")
        torch.testing.assert_close(
            output, inputs.chunk(2, dim=1)[rank] @ weight.chunk(2, dim=1)[rank].T
        )
        output.copy_(reference)

    proxy = SimpleNamespace(
        empty_like=torch.empty_like,
        cat=torch.cat,
        distributed=SimpleNamespace(all_gather=gather, all_reduce=reduce),
    )
    with load_lab("llm-inference/labs/19_tensor_parallel_linear.py") as module:
        for rank in range(2):
            column, row = module.partition_operations(proxy, inputs, weight, rank, 2)
            column_output, row_output = column(), row()
            errors = module.validate_outputs(
                torch, reference, column_output, row_output
            )
            assert errors["maximum_relative_l2"] == 0
            with pytest.raises(ValueError, match="reference"):
                module.validate_outputs(
                    torch, reference, column_output.flip(-1), row_output
                )
        with pytest.raises(ValueError, match="reference"):
            module.validate_outputs(
                torch, reference, reference * float("nan"), reference
            )
    assert calls == ["gather", "reduce"] * 2


def test_inference_partition_cleanup_on_experiment_failure(monkeypatch):
    with load_lab("llm-inference/labs/19_tensor_parallel_linear.py") as module:
        closed = []
        monkeypatch.setattr("sys.argv", ["lab"])
        monkeypatch.setattr(module, "load_torch", lambda: object())
        monkeypatch.setattr(module, "require_h100", lambda torch: {})
        monkeypatch.setattr(module, "init_nccl", lambda torch: (0, 2, 0))
        monkeypatch.setattr(
            module, "close_distributed", lambda torch: closed.append(True)
        )

        def fail(*args):
            raise RuntimeError("controlled experiment failure")

        monkeypatch.setattr(module, "run_experiment", fail)
        with pytest.raises(RuntimeError, match="controlled"):
            module.main()
        assert closed == [True]


@pytest.mark.parametrize("rank,peer_ok", [(0, True), (1, True), (0, False)])
def test_partition_experiment_common_state_and_rank_agreement(
    monkeypatch, rank, peer_ok
):
    """Exercise orchestration with CPU tensors and deterministic collective fixtures."""
    torch = pytest.importorskip("torch")
    common = []
    events = []
    records = []

    def broadcast(value, src):
        assert src == 0
        events.append("broadcast")
        # The fixture stands in for rank zero's authoritative values on both ranks.
        value.fill_(0.125 if not common else 0.25)
        common.append(value)

    def gather(outputs, local):
        expected = common[0] @ common[1].chunk(2, dim=0)[rank].T
        torch.testing.assert_close(local, expected)
        for output, shard in zip(outputs, common[1].chunk(2, dim=0)):
            output.copy_(common[0] @ shard.T)

    def reduce(value, op=None):
        if value.ndim == 2:
            expected = (
                common[0].chunk(2, dim=1)[rank] @ common[1].chunk(2, dim=1)[rank].T
            )
            torch.testing.assert_close(value, expected)
            value.copy_(common[0] @ common[1].T)
        elif op == "min":
            events.append("agreement")
            assert value.item() == 1
            if not peer_ok:
                value.zero_()

    class CpuFixture:
        cuda = SimpleNamespace(synchronize=lambda: None)
        distributed = SimpleNamespace(
            broadcast=broadcast,
            all_gather=gather,
            all_reduce=reduce,
            barrier=lambda: None,
            ReduceOp=SimpleNamespace(MAX="max", MIN="min"),
        )

        def __getattr__(self, name):
            return getattr(torch, name)

        def empty(self, shape, **kwargs):
            # Small CPU shape while preserving both equal shard axes.
            return torch.empty((shape[0] if shape[0] == 1 else 4, 4))

        def tensor(self, value, **kwargs):
            kwargs.pop("device", None)
            return torch.tensor(value, **kwargs)

    with load_lab("llm-inference/labs/19_tensor_parallel_linear.py") as module:
        monkeypatch.setattr(module, "seed_everything", lambda *_: None)
        monkeypatch.setattr(module, "write_result", lambda *a, **k: records.append(k))
        args = SimpleNamespace(
            profile="smoke", seed=17, batch_size=1, warmup=0, iterations=2
        )
        if peer_ok:
            module.run_experiment(CpuFixture(), args, {}, rank, 2, 0)
        else:
            with pytest.raises(SystemExit, match="another rank"):
                module.run_experiment(CpuFixture(), args, {}, rank, 2, 0)
    assert events == ["broadcast", "broadcast", "agreement"]
    assert len(records) == int(rank == 0 and peer_ok)
    if records:
        metrics = records[0]["measurements"]
        assert metrics["batch"] == 1
        assert metrics["maximum_absolute_error"] == 0
        assert len(metrics["column_parallel_all_gather_samples_ms"]) == 2
        assert len(metrics["row_parallel_all_reduce_samples_ms"]) == 2


@pytest.mark.parametrize("batch_size", ["0", "-1"])
def test_invalid_partition_batch_rejected_before_torch(monkeypatch, batch_size):
    with load_lab("llm-inference/labs/19_tensor_parallel_linear.py") as module:
        monkeypatch.setattr("sys.argv", ["lab", "--batch-size", batch_size])
        monkeypatch.setattr(
            module, "load_torch", lambda: pytest.fail("must not load torch")
        )
        with pytest.raises(SystemExit) as error:
            module.main()
        assert error.value.code == 2


@pytest.mark.parametrize("world_size", [1, 3])
def test_partition_rank_contract_precedes_allocation(world_size):
    with load_lab("llm-inference/labs/19_tensor_parallel_linear.py") as module:
        with pytest.raises(SystemExit, match="exactly two ranks"):
            module.run_experiment(None, None, {}, 0, world_size, 0)


def test_no_identical_complete_lessons_or_non_preflight_labs():
    lessons = defaultdict(list)
    labs = defaultdict(list)
    for course in COURSES:
        for lesson in load_builder().parse_course(ROOT / course / "COURSE.md")[2]:
            key = tuple(
                " ".join(re.findall(r"\w+", lesson[f].lower()))
                for f in ("Objective", "Mental model", "Mechanism")
            )
            lessons[key].append((course, lesson["title"]))
        for path in (ROOT / course / "labs").glob("[0-9]*"):
            if path.suffix not in {".py", ".cu", ".cpp"}:
                continue
            if path.name != "00_cluster_preflight.py":
                labs[path.read_bytes()].append(str(path.relative_to(ROOT)))
    assert all(len(items) == 1 for items in lessons.values())
    assert all(len(items) == 1 for items in labs.values())
