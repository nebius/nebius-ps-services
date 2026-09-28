"""Controlled NCCL latency and NIC-selection experiments, with lazy GPU imports."""

from __future__ import annotations

import argparse
import os
import re

from common import (
    add_common_args,
    close_distributed,
    validate_common_args,
    write_result,
)
from course_evidence import evidence_phase
from fabric_common import all_correct, initialize, measure


def configure_transport(algorithm, policy, server_hca, client_hca):
    if any(
        name in os.environ
        for name in ("NCCL_ALGO", "NCCL_PROTO", "NCCL_IB_HCA", "NCCL_IB_DISABLE")
    ):
        raise ValueError(
            "Start from a clean job environment: remove previous NCCL tuning overrides"
        )
    if algorithm != "auto":
        os.environ["NCCL_ALGO"] = algorithm
    if policy == "single":
        if not all(
            re.fullmatch(r"mlx5_[0-9]+", value or "")
            for value in (server_hca, client_hca)
        ):
            raise ValueError("Supply the observed mlx5 device on each allocated node")
        node = int(os.environ["RANK"]) // 8
        os.environ["NCCL_IB_HCA"] = "=" + (server_hca, client_hca)[node] + ":1"
    # Do not force a registration mode or claim IB selection from configuration.
    os.environ["NCCL_DEBUG"] = "INFO"
    os.environ["NCCL_DEBUG_SUBSYS"] = "INIT,NET,GRAPH"


def run(kind):
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument("--algorithm", choices=("auto", "Ring", "Tree"), default="auto")
    parser.add_argument("--hca-policy", choices=("all", "single"), default="all")
    parser.add_argument("--server-hca")
    parser.add_argument("--client-hca")
    args = parser.parse_args()
    validate_common_args(args)
    if kind == "latency" and args.hca_policy != "all":
        parser.error("The latency experiment changes algorithm only")
    if kind == "rails" and args.algorithm != "auto":
        parser.error("The NIC experiment retains NCCL automatic algorithms")
    configure_transport(
        args.algorithm, args.hca_policy, args.server_hca, args.client_hca
    )
    torch, rank, world, _, env = initialize(args.seed)
    try:
        if world != 16:
            raise ValueError(
                "These network experiments require two nodes and sixteen ranks"
            )
        sizes = (
            (4, 4096, 1048576)
            if kind == "latency"
            else ((4 if args.profile == "small" else 64) * 2**20,)
        )
        rows = []
        for size in sizes:
            tensor = torch.full((size // 4,), float(rank + 1), device="cuda")

            def operation(tensor=tensor):
                with evidence_phase("network_all_reduce"):
                    torch.distributed.all_reduce(
                        tensor, op=torch.distributed.ReduceOp.MAX
                    )

            times = measure(torch, operation, args.warmup, args.iterations)
            all_correct(
                torch,
                bool((tensor == world).all().item()),
                "Collective payload differs from MAX reference",
            )
            rows.append(
                {
                    "payload_bytes": size,
                    "median_ms": times["median_ms"],
                    "max_ms": times["max_ms"],
                    "useful_GBps": size / times["median_ms"] / 1e6,
                    "samples_ms": times["samples_ms"],
                }
            )
        if rank == 0:
            print(
                write_result(
                    args,
                    lab_id="26_collective_latency"
                    if kind == "latency"
                    else "28_nic_selection",
                    environment=env,
                    measurements={"cases": rows},
                    correctness={"max_reference_exact": True},
                )
            )
    finally:
        close_distributed(torch)
