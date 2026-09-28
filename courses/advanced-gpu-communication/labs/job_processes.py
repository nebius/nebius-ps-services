"""Private, bounded, job-owned subprocesses for communication experiments."""

from __future__ import annotations

import contextlib
import json
import os
import signal
import socket
import subprocess
import time
import urllib.request
from pathlib import Path


def allocated_nodes():
    if (
        not os.environ.get("SLURM_JOB_ID")
        or os.environ.get("SLURM_JOB_NUM_NODES") != "2"
    ):
        raise ValueError("Use the exclusive two-node vendor_job.sbatch launcher")
    nodes = subprocess.check_output(
        ["scontrol", "show", "hostnames", os.environ["SLURM_JOB_NODELIST"]], text=True
    ).split()
    if len(nodes) != 2 or len(set(nodes)) != 2:
        raise ValueError("Expected exactly two distinct allocated nodes")
    return nodes


def step(node, command, *, gpus=8, forward_interrupt=False):
    return [
        "srun",
        "--overlap",
        "--nodes=1",
        "--ntasks=1",
        "--nodelist=" + node,
        "--gpus-per-task=" + str(gpus),
        "--kill-on-bad-exit=1",
        *(["--disable-status"] if forward_interrupt else []),
        *command,
    ]


def private_folder(args, lab):
    folder = (args.output_dir / lab / ("vendor-" + args.run_id)).resolve()
    folder.mkdir(parents=True, mode=0o700, exist_ok=False)
    return folder


class Processes:
    def __init__(self, folder):
        self.folder, self.children, self.files, self.signals = folder, [], [], {}
        self.persistent = []
        self.shutdown_options = {}

    def __enter__(self):
        def interrupted(signum, frame):
            raise KeyboardInterrupt("Job interrupted")

        self.signals = {
            sig: signal.signal(sig, interrupted)
            for sig in (signal.SIGTERM, signal.SIGINT)
        }
        return self

    def start(
        self,
        name,
        command,
        env=None,
        *,
        persistent=False,
        interrupt_on_exit=False,
        shutdown_timeout=10,
    ):
        output = (self.folder / (name + ".log")).open("x")
        self.files.append(output)
        child = subprocess.Popen(
            command,
            stdout=output,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
        self.children.append(child)
        self.shutdown_options[child.pid] = (
            signal.SIGINT if interrupt_on_exit else signal.SIGTERM,
            shutdown_timeout,
        )
        if persistent:
            self.persistent.append(child)
        return child

    def wait(self, child, timeout=900):
        status = child.wait(timeout=timeout)
        if status:
            raise RuntimeError(
                f"Vendor process exited with status {status}; inspect the private job log"
            )

    def healthy(self):
        if any(child.poll() is not None for child in self.persistent) or any(
            child.poll() not in (None, 0) for child in self.children
        ):
            raise RuntimeError("An owned process failed before readiness")

    def __exit__(self, *_):
        for child in reversed(self.children):
            if child.poll() is None:
                stop_signal, timeout = self.shutdown_options[child.pid]
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(child.pid, stop_signal)
                try:
                    child.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(child.pid, signal.SIGKILL)
                    child.wait()
        for output in self.files:
            output.close()
        for sig, handler in self.signals.items():
            signal.signal(sig, handler)


def wait_http(url, processes, timeout=600):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        processes.healthy()
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return json.load(response)
        except (OSError, ValueError):
            pass
        time.sleep(0.5)
    raise RuntimeError("Owned service did not become ready within its deadline")


def start_etcd(processes, node):
    port = 25000 + int(os.environ["SLURM_JOB_ID"]) % 10000
    endpoint = f"http://{node}:{port}"
    listen_endpoint = f"http://{socket.gethostbyname(node)}:{port}"
    peer = f"http://127.0.0.1:{port + 1}"
    binary = os.environ.get("COURSE_ETCD")
    if not binary or not Path(binary).is_file():
        raise ValueError(
            "Complete the shared README setup: COURSE_ETCD must identify the prepared etcd executable"
        )
    command = [
        binary,
        "--name",
        "course",
        "--data-dir",
        str(processes.folder / "etcd"),
        "--listen-client-urls",
        listen_endpoint,
        "--advertise-client-urls",
        endpoint,
        "--listen-peer-urls",
        peer,
        "--initial-advertise-peer-urls",
        peer,
        "--initial-cluster",
        "course=" + peer,
        "--initial-cluster-state",
        "new",
    ]
    processes.start("etcd", step(node, command, gpus=0), persistent=True)
    wait_http(endpoint + "/health", processes, timeout=30)
    return endpoint
