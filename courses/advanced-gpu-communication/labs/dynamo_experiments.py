"""Own a private two-node Dynamo deployment for one bounded Slurm experiment."""

from __future__ import annotations

import argparse
import contextlib
import ipaddress
import json
import os
import shutil
import socket
import time
import urllib.request
from pathlib import Path

from common import add_common_args, validate_common_args, write_result
from course_evidence import allocation_gpu_family
from job_processes import (
    Processes,
    allocated_nodes,
    private_folder,
    start_etcd,
    step,
    wait_http,
)
from serving_client import benchmark, request
from vendor_capture import (
    add_worker_prefix,
    native_systems_prefix,
    validate_worker_prefix,
)

MODEL_REVISION = "b968826d9c46dd6066d109eabc6255188de91218"


def wait_frontend_model(url, processes, timeout=600):
    """Wait for asynchronous discovery, retaining the exact model identity gate."""
    deadline = time.monotonic() + timeout
    empty_polls = 0
    while (remaining := deadline - time.monotonic()) > 0:
        models = wait_http(url + "/v1/models", processes, timeout=remaining)
        rows = models.get("data") if isinstance(models, dict) else None
        if not isinstance(rows, list) or any(
            not isinstance(row, dict) or not isinstance(row.get("id"), str)
            for row in rows
        ):
            raise ValueError("Frontend discovery returned malformed model data")
        identities = {row["id"] for row in rows}
        if identities == {"course-model"}:
            print(
                json.dumps(
                    {
                        "frontend_discovery": {
                            "model": "course-model",
                            "empty_polls": empty_polls,
                        }
                    }
                ),
                flush=True,
            )
            return
        if identities:
            raise ValueError("Frontend discovery differs from the expected model")
        empty_polls += 1
        time.sleep(min(0.5, max(0, deadline - time.monotonic())))
    raise RuntimeError("Frontend model discovery did not complete within its deadline")


def profile_control(address, action):
    """Require the allocated worker to acknowledge its CUDA profiler control."""
    url = f"http://{address}:8081/engine/control/{action}_profile"
    request = urllib.request.Request(
        url, data=b"{}", headers={"Content-Type": "application/json"}, method="POST"
    )
    # Stop blocks on collection-end report serialization, which can exceed
    # three minutes for the full LARGE workload. Keep its writer alive until
    # acknowledgment, with a bounded deadline distinct from capture startup.
    timeout = 600 if action == "stop" else 90
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read(65537)
        if response.status != 200 or len(body) > 65536:
            raise RuntimeError("Worker did not acknowledge profiler control")
        document = json.loads(body)
        if not isinstance(document, dict) or document.get("status") != "ok":
            raise RuntimeError("Worker did not acknowledge profiler control")


def wait_server_reports(folder, count, processes, timeout=180):
    """Retain reports before engine shutdown can terminate GPU worker processes."""
    reports = [folder / f"server-rank{rank}.nsys-rep" for rank in range(count)]
    deadline = time.monotonic() + timeout
    while not all(report.is_file() and report.stat().st_size for report in reports):
        processes.healthy()
        if time.monotonic() >= deadline:
            raise RuntimeError("Server profiling did not produce both reports")
        time.sleep(1)


@contextlib.contextmanager
def server_capture(addresses, folder, processes):
    """End collection while every owned engine is still alive, including on failure."""
    with (folder / "capture-control.jsonl").open("x") as evidence:

        def control(rank, action):
            profile_control(addresses[rank], action)
            evidence.write(
                json.dumps(
                    {"rank": rank, "action": action, "unix_seconds": time.time()}
                )
                + "\n"
            )
            evidence.flush()

        with contextlib.ExitStack() as cleanup:
            for rank in range(len(addresses)):
                # A failed acknowledgment can follow a partially started engine.
                cleanup.callback(control, rank, "stop")
                control(rank, "start")
            yield
        wait_server_reports(folder, len(addresses), processes)


def worker_command(python, model, role):
    command = [
        python,
        "-m",
        "dynamo.vllm",
        "--model",
        str(model),
        "--served-model-name",
        "course-model",
        "--tensor-parallel-size",
        "8",
        "--dtype",
        "bfloat16",
        "--max-model-len",
        "8192",
        "--block-size",
        "64",
        "--enable-prefix-caching",
        "--attention-config",
        json.dumps({"flash_attn_version": 2}),
        "--compilation-config",
        json.dumps(
            {
                "custom_ops": ["+rms_norm"],
                "pass_config": {"fuse_allreduce_rms": False},
            }
        ),
        "--disaggregation-mode",
        role,
    ]
    command += [
        "--kv-events-config",
        json.dumps(
            {
                "publisher": "zmq",
                "topic": "kv-events",
                "endpoint": "tcp://*:20080",
                "enable_kv_cache_events": True,
            }
        ),
    ]
    if role != "agg":
        command += [
            "--kv-transfer-config",
            json.dumps({"kv_connector": "NixlConnector", "kv_role": "kv_both"}),
        ]
    return command


@contextlib.contextmanager
def service(args, folder):
    if os.environ.get("COURSE_PROFILE_TOOL", "none") != "none":
        raise ValueError("Use --capture systems to profile the owned GPU servers")
    nodes = allocated_nodes()
    addresses = [socket.gethostbyname(node) for node in nodes]
    if any(
        not ipaddress.ip_address(ip).is_private or ipaddress.ip_address(ip).is_loopback
        for ip in addresses
    ):
        raise ValueError(
            "Allocated worker names must resolve to reachable private addresses"
        )
    python = os.environ.get("COURSE_DYNAMO_PYTHON")
    if not python or not Path(python).is_file():
        raise ValueError(
            "Complete the shared README setup and export COURSE_DYNAMO_PYTHON on shared storage"
        )
    if (
        args.model_dir.resolve().name != MODEL_REVISION
        or not (args.model_dir / "config.json").is_file()
    ):
        raise ValueError(
            "Use the shared README setup's pinned Qwen3-8B snapshot directory"
        )
    with Processes(folder) as processes:
        endpoint = start_etcd(processes, nodes[0])
        shared = {
            # Preserve the venv path even when its Python is a symlink.
            "PATH": str(Path(python).absolute().parent)
            + os.pathsep
            + os.environ.get("PATH", os.defpath),
            "ETCD_ENDPOINTS": endpoint,
            "DYN_DISCOVERY_BACKEND": "etcd",
            "DYN_REQUEST_PLANE": "tcp",
            "DYN_EVENT_PLANE": "zmq",
            "DYN_NAMESPACE": "course-" + args.run_id,
            "PYTHONHASHSEED": "0",
            "VLLM_BATCH_INVARIANT": "1",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
        }
        for rank, node in enumerate(nodes):
            role = "agg" if args.layout == "aggregated" else ("prefill", "decode")[rank]
            environment = {
                **shared,
                "DYN_TCP_RPC_HOST": addresses[rank],
                "DYN_TCP_RPC_PORT": "8090",
                "DYN_SYSTEM_HOST": addresses[rank],
                "DYN_SYSTEM_PORT": "8081",
                "VLLM_NIXL_SIDE_CHANNEL_PORT": "5600",
            }
            command = worker_command(python, args.model_dir, role)
            if args.capture == "systems":
                command += ["--profiler-config", json.dumps({"profiler": "cuda"})]
                binary = shutil.which("nsys")
                if not binary:
                    raise ValueError("Source the shared profiler environment first")
                if getattr(args, "worker_prefix", None) is not None:
                    command = [*native_systems_prefix(args.worker_prefix, folder / f"server-rank{rank}", server=True), *command]
                else:
                    command = [
                        "env",
                        "-u",
                        "DEBUGINFOD_URLS",
                        binary,
                        "profile",
                        "--trace=cuda,nvtx,osrt,nccl",
                        "--cuda-trace-scope=process-tree",
                        "--trace-fork-before-exec=true",
                        "--cuda-graph-trace=node",
                        "--capture-range=cudaProfilerApi",
                        "--capture-range-end=stop",
                        "--flush-on-cudaprofilerstop=false",
                        "--kill=none",
                        "--wait=primary",
                        "--sample=none",
                        "--discard-environment=true",
                        "--force-overwrite=false",
                        "--output=" + str(folder / f"server-rank{rank}"),
                        *command,
                    ]
            processes.start(
                f"worker{rank}",
                step(
                    node,
                    [
                        "env",
                        "-u",
                        "NATS_SERVER",
                        *(key + "=" + value for key, value in environment.items()),
                        *command,
                    ],
                    forward_interrupt=args.capture == "systems",
                ),
                persistent=True,
                interrupt_on_exit=args.capture == "systems",
                shutdown_timeout=180 if args.capture == "systems" else 10,
            )
        frontend = [
            python,
            "-m",
            "dynamo.frontend",
            "--http-host",
            "127.0.0.1",
            "--http-port",
            "8000",
            "--namespace",
            shared["DYN_NAMESPACE"],
            "--router-mode",
            args.router,
        ]
        processes.start(
            "frontend",
            [
                "env",
                "-u",
                "NATS_SERVER",
                *(key + "=" + value for key, value in shared.items()),
                *frontend,
            ],
            persistent=True,
        )
        for ip in addresses:
            ready = wait_http(f"http://{ip}:8081/health", processes)
            if ready.get("status") != "ready":
                raise ValueError("Worker health does not declare ready")
        url = "http://127.0.0.1:8000"
        wait_frontend_model(url, processes)
        request(url, "Write a short greeting. /no_think", 8)
        capture = (
            server_capture(addresses, folder, processes)
            if args.capture == "systems"
            else contextlib.nullcontext()
        )
        with capture:
            yield url, processes
    if args.capture == "systems":
        for rank in range(len(nodes)):
            report = folder / f"server-rank{rank}.nsys-rep"
            if not report.is_file() or report.stat().st_size == 0:
                raise RuntimeError(
                    "Server profiling did not produce both reports; inspect worker logs"
                )


def run(kind):
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.set_defaults(warmup=4, iterations=64)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument(
        "--layout", choices=("aggregated", "disaggregated"), default="aggregated"
    )
    parser.add_argument(
        "--router", choices=("round-robin", "kv"), default="round-robin"
    )
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--capture", choices=("none", "systems"), default="none")
    add_worker_prefix(parser)
    args = parser.parse_args()
    validate_worker_prefix(args, server=True)
    if (
        not 1 <= args.concurrency <= 64
        or not 8 <= args.iterations <= 4096
        or not 0 <= args.warmup <= args.iterations
    ):
        parser.error("Use concurrency 1..64, requests 8..4096 and bounded warm-up")
    if (kind == "routing" and args.layout != "aggregated") or (
        kind == "disaggregation" and args.router != "round-robin"
    ):
        parser.error("Keep other serving controls fixed for this experiment")
    validate_common_args(args)
    lab = (
        "32_dynamo_disaggregation" if kind == "disaggregation" else "33_dynamo_routing"
    )
    folder = private_folder(args, lab)
    with service(args, folder) as (url, processes):
        measured = benchmark(
            url,
            count=args.iterations,
            words=16 if args.profile == "small" else 256,
            output_tokens=32 if args.profile == "small" else 128,
            concurrency=args.concurrency,
            warmup=args.warmup,
            folder=folder,
        )
        processes.healthy()
    # Capture is diagnostic: never publish timings with profiled GPU servers.
    if args.capture != "none":
        print("Diagnostic server reports and client measurements:", folder)
        return
    print(
        write_result(
            args,
            lab_id=lab,
            environment={
                "gpu_family": allocation_gpu_family(),
                "allocated_gpus": 16,
                "dynamo_candidate": "1.4.2",
                "model_revision": MODEL_REVISION,
            },
            measurements=measured,
            correctness={
                "all_streams_complete": True,
                "fixed_output_token_count": True,
            },
        )
    )
