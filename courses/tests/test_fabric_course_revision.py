"""Setup, submission and hardware-route contracts without a live cluster."""

import json
from pathlib import Path

import pytest
from test_observability_integration import load


def project(tmp_path):
    (tmp_path / "reference").mkdir()
    (tmp_path / "slurm").mkdir()
    (tmp_path / "reference/course.json").write_text(
        json.dumps({"labs": [{"path": "labs/01_example.py"}]})
    )
    (tmp_path / "slurm/single_gpu.sbatch").write_text("#!/bin/bash\n")
    return tmp_path


def test_submission_creates_private_logs_before_scheduler_runs(tmp_path):
    root = project(tmp_path)
    command = load("submit_lab").submission(
        root,
        "01_example",
        [
            "--export=ALL,COURSE_PROFILE_TOOL=nsys",
            "slurm/single_gpu.sbatch",
            "labs/01_example.py",
            "--label",
            "space; literal",
        ],
    )
    logs = root / "results/01_example/logs"
    assert logs.is_dir() and logs.stat().st_mode & 0o777 == 0o700
    assert command[1:4] == [
        f"--chdir={root}",
        f"--output={logs}/%j.out",
        f"--error={logs}/%j.err",
    ]
    assert command[-1] == "space; literal"
    assert command[-4] == str(root / "slurm/single_gpu.sbatch")


@pytest.mark.parametrize(
    "defect", ["identity", "log-override", "source", "symlink", "public"]
)
def test_unsafe_submission_fails_before_sbatch(tmp_path, defect):
    root = project(tmp_path)
    lab, argv = "01_example", ["slurm/single_gpu.sbatch", "labs/01_example.py"]
    if defect == "identity":
        lab = "../../elsewhere"
    elif defect == "log-override":
        argv.insert(0, "--output=/tmp/elsewhere")
    elif defect == "source":
        argv[-1] = "labs/02_other.py"
    elif defect == "symlink":
        (root / "results").symlink_to(root / "reference", target_is_directory=True)
    else:
        (root / "results").mkdir()
        (root / "results").chmod(0o755)
    with pytest.raises(ValueError):
        load("submit_lab").submission(root, lab, argv)


def hardware():
    names = "\n".join(["NVIDIA H100 80GB HBM3"] * 8)
    matrix = "\n".join(
        "GPU" + str(i) + " " + " ".join("X" if i == j else "NV18" for j in range(8))
        for i in range(8)
    )
    return names, matrix, [{"state": "4: ACTIVE", "link_layer": "InfiniBand"}]


@pytest.mark.parametrize(
    "defect", [None, "one-gpu", "peer", "no-ib", "mig", "restricted"]
)
def test_fabric_admission_requires_real_full_worker_evidence(defect):
    names, matrix, ports = hardware()
    mig = "\n".join(["Disabled"] * 8)
    visible = 8
    if defect == "one-gpu":
        names = names.splitlines()[0]
    elif defect == "peer":
        matrix = matrix.replace("NV18", "SYS", 1)
    elif defect == "no-ib":
        ports[0]["link_layer"] = "Ethernet"
    elif defect == "mig":
        mig = mig.replace("Disabled", "Enabled", 1)
    elif defect == "restricted":
        visible = 1
    guard = load("fabric_guard")
    if defect:
        with pytest.raises(ValueError):
            guard.inspect_worker(
                names, matrix, ports, mig_modes=mig, visible_devices=visible
            )
    else:
        assert guard.inspect_worker(
            names, matrix, ports, mig_modes=mig, visible_devices=visible
        ) == {
            "gpu_family": "NVIDIA H100",
            "gpus_per_node": 8,
            "nvlink_peers_per_gpu": 7,
            "active_ib_ports": 1,
        }


def test_vendor_reports_require_complete_verified_measurements():
    from test_course_review_fixes import load_lab

    with load_lab("gpu-optimizations/labs/fabric_tools.py") as module:
        document = {
            "nvbandwidth": {
                "testcases": [
                    {
                        "name": "device_to_device_memcpy_write_ce",
                        "status": "Passed",
                        "bandwidth_matrix": [
                            ["N/A" if i == j else "200" for j in range(8)]
                            for i in range(8)
                        ],
                    }
                ]
            }
        }
        result = module.nvbandwidth_result(
            document, "device_to_device_memcpy_write_ce", 64, 20
        )
        assert result["pair_count"] == 56 and result["minimum_GBps"] == 200
        assert result["pairs"][0] == {"source": 0, "destination": 1, "GBps": 200}
        for value in ("N/A", "nan", "inf", "0"):
            document["nvbandwidth"]["testcases"][0]["bandwidth_matrix"][0][1] = value
            with pytest.raises(ValueError):
                module.nvbandwidth_result(
                    document, "device_to_device_memcpy_write_ce", 64, 20
                )
        rdma = {
            "test_info": {
                "Connection_type": "RC",
                "Link_type": "IB",
                "TX_depth": 128,
                "cuda_device": 0,
            },
            "results": {"MsgSize": 65536, "n_iterations": 1000, "BW_average": 200},
        }
        valid = "VALIDATION: PASSED (SERVER) - 32 chunks, 65536 bytes"
        assert (
            module.rdma_result(rdma, valid, 65536, 1000, 128, "cuda-dmabuf")[
                "validated_bytes"
            ]
            == 65536
        )
        for log in (
            "",
            valid.replace("32 chunks", "0 chunks"),
            valid + "\nVALIDATION: FAILED - 1 error",
        ):
            with pytest.raises(ValueError):
                module.rdma_result(rdma, log, 65536, 1000, 128, "cuda-dmabuf")
        rdma["test_info"]["Link_type"] = "Ethernet"
        with pytest.raises(ValueError):
            module.rdma_result(rdma, valid, 65536, 1000, 128, "cuda-dmabuf")


def test_rank_inventory_rejects_duplicate_gpu_and_wrong_host_count():
    from test_course_review_fixes import load_lab

    with load_lab("advanced-gpu-communication/labs/01_fabric_topology.py") as lab:
        rows = [
            (rank, rank % 8, "node" + str(rank // 8), "uuid" + str(rank % 8))
            for rank in range(16)
        ]
        assert lab.validate_placement(rows, 16)
        rows[1] = (1, 1, "node0", "uuid0")
        with pytest.raises(ValueError):
            lab.validate_placement(rows, 16)


def test_ddp_reference_rejects_noop_and_accepts_scaled_accumulation():
    import copy

    torch = pytest.importorskip("torch")
    from test_course_review_fixes import load_lab

    torch.manual_seed(17)
    model = torch.nn.Sequential(
        torch.nn.Linear(32, 32), torch.nn.GELU(), torch.nn.Linear(32, 32)
    )
    baseline, candidate, noop = (
        copy.deepcopy(model),
        copy.deepcopy(model),
        copy.deepcopy(model),
    )
    initial = tuple(p.detach().clone() for p in model.parameters())
    x, target = torch.randn(128, 32), torch.randn(128, 32)
    torch.nn.functional.mse_loss(baseline(x), target).backward()
    torch.optim.SGD(baseline.parameters(), lr=0.01, foreach=False, fused=False).step()
    for batch in range(0, 128, 4):
        (
            torch.nn.functional.mse_loss(
                candidate(x[batch : batch + 4]), target[batch : batch + 4]
            )
            / 32
        ).backward()
    torch.optim.SGD(candidate.parameters(), lr=0.01, foreach=False, fused=False).step()
    for a, b in zip(noop.parameters(), baseline.parameters()):
        a.grad = b.grad.clone()
    with load_lab("llm-training/labs/common.py") as common:
        assert common.sgd_updates_match(
            torch,
            initial,
            tuple(baseline.parameters()),
            tuple(candidate.parameters()),
            learning_rate=0.01,
        )
        assert not common.sgd_updates_match(
            torch,
            initial,
            tuple(baseline.parameters()),
            tuple(noop.parameters()),
            learning_rate=0.01,
        )


def test_tensor_projection_partitions_preserve_global_requests():
    torch = pytest.importorskip("torch")
    torch.manual_seed(17)
    x, weight = torch.randn(32, 64) * 0.01, torch.randn(64, 64) / 8
    expected = x.clone()
    observed = x.clone()
    for _ in range(8):
        expected = torch.tanh(expected @ weight)
        observed = torch.tanh(
            sum(a @ b for a, b in zip(observed.chunk(8, dim=1), weight.chunk(8, dim=0)))
        )
    assert torch.allclose(observed, expected, rtol=3e-3, atol=3e-5)
    assert sum(len(part) for part in expected.chunk(8, dim=0)) == 32


def test_all_distributed_recipes_are_in_advanced_route():
    from test_course_content_contract import COURSES, ROOT

    for course in COURSES:
        root = ROOT / course
        meta = json.loads((root / "reference/course.json").read_text())
        assert meta["advanced_lessons"] == []
        recipes = json.loads((root / "reference/observability.json").read_text())[
            "labs"
        ]
        assert all(recipe["kind"] != "distributed" for recipe in recipes.values())
        assert not (root / "slurm/two_node.sbatch").exists()
        assert not (root / "slurm/fabric.sbatch").exists()
    advanced = ROOT / "advanced-gpu-communication"
    metadata = json.loads((advanced / "reference/course.json").read_text())
    recipes = json.loads((advanced / "reference/observability.json").read_text())[
        "labs"
    ]
    assert {Path(row["path"]).stem for row in metadata["labs"]} == set(recipes) - {
        "environment_readiness"
    }
    source = (advanced / "slurm/fabric.sbatch").read_text()
    assert source.index("export COURSE_RUN_ID") < source.index("--nproc-per-node=8")
