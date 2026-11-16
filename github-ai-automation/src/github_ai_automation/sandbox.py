"""Disposable non-root agent with a credentialless source mount and restricted relay."""

from __future__ import annotations

import json
import os
import re
import secrets
import selectors
import subprocess
import time
from pathlib import Path

from .contracts import Blocked, Contract, digest, safe_path


def command(args, *, env=None, timeout=1800, stdin=None):
    """Bound subprocess output while draining both pipes and feeding input concurrently."""
    child = None
    try:
        child = subprocess.Popen(
            args,
            stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        output = bytearray()
        size = 0
        pending = memoryview(stdin or b"")
        deadline = time.monotonic() + timeout
        with selectors.DefaultSelector() as selector:
            for pipe in (child.stdout, child.stderr):
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, selectors.EVENT_READ)
            if child.stdin is not None:
                os.set_blocking(child.stdin.fileno(), False)
                selector.register(child.stdin, selectors.EVENT_WRITE)
            while selector.get_map():
                if time.monotonic() >= deadline:
                    raise Blocked("EXECUTION_TIMEOUT")
                for event, _ in selector.select(min(0.2, max(0, deadline - time.monotonic()))):
                    pipe = event.fileobj
                    if pipe is child.stdin:
                        if pending:
                            try:
                                pending = pending[os.write(pipe.fileno(), pending[:8192]) :]
                            except BrokenPipeError:
                                pending = pending[:0]
                        if not pending:
                            selector.unregister(pipe)
                            pipe.close()
                    else:
                        chunk = os.read(pipe.fileno(), 8192)
                        if not chunk:
                            selector.unregister(pipe)
                            pipe.close()
                            continue
                        size += len(chunk)
                        if size > 4_194_304:
                            raise Blocked("EXECUTION_OUTPUT_LIMIT")
                        if pipe is child.stdout:
                            output.extend(chunk)
        if child.wait(timeout=max(0.001, deadline - time.monotonic())):
            raise Blocked("EXECUTION_FAILED")
        return bytes(output)
    except (OSError, subprocess.TimeoutExpired):
        raise Blocked("EXECUTION_UNAVAILABLE") from None
    finally:
        if child is not None:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)
            for pipe in (child.stdin, child.stdout, child.stderr):
                if pipe and not pipe.closed:
                    pipe.close()


def run_checks(*, image, source, paths, profiles, timeout):
    """Execute observed checks without model, GitHub, cloud or state credentials."""
    script = """import ast,json,pathlib,sys
root=pathlib.Path('/source')
for name in json.loads(sys.argv[1]):
 p=root/name
 if not p.exists(): continue
 if p.is_symlink() or not p.resolve().is_relative_to(root): raise ValueError('unsafe path')
 if p.suffix=='.py': ast.parse(p.read_text(),filename=name)
 if p.suffix=='.json': json.loads(p.read_text())
"""
    commands = [
        {
            "id": "structure",
            "image": image,
            "argv": ["python3", "-I", "-c", script, json.dumps(paths)],
            "cwd": ".",
        },
        *profiles,
    ]
    receipts = []
    started = time.monotonic()
    for check in commands:
        if not re.fullmatch(r"(?:[\w./:-]+@)?sha256:[a-f0-9]{64}", check["image"]):
            raise Blocked("VALIDATION_IMAGE_NOT_PINNED")
        argv = check["argv"]
        if (
            not isinstance(argv, list)
            or not argv
            or any(not isinstance(s, str) or not s or "\0" in s for s in argv)
        ):
            raise Blocked("VALIDATION_COMMAND_INVALID")
        cwd = check["cwd"]
        if cwd != ".":
            safe_path(cwd)
        name = "ai-check-" + secrets.token_hex(12)
        remaining = timeout - int(time.monotonic() - started)
        if remaining <= 0:
            raise Blocked("VALIDATION_TIMEOUT")
        try:
            output = command(
                [
                    "docker",
                    "run",
                    "--name",
                    name,
                    "--network=none",
                    "--init",
                    "--cap-drop=ALL",
                    "--security-opt=no-new-privileges",
                    "--read-only",
                    "--user=10001:10001",
                    "--pids-limit=128",
                    "--memory=2g",
                    "--cpus=2",
                    "--tmpfs",
                    "/tmp:rw,nosuid,nodev,size=256m",
                    "--env",
                    "PYTHONDONTWRITEBYTECODE=1",
                    "--workdir",
                    "/source" + ("/" + cwd if cwd != "." else ""),
                    "--mount",
                    f"type=bind,src={source.resolve()},dst=/source,readonly",
                    check["image"],
                    *argv,
                ],
                env={"PATH": os.environ["PATH"]},
                timeout=remaining,
            )
        except Blocked:
            raise Blocked("VALIDATION_FAILED_OR_UNAVAILABLE") from None
        finally:
            subprocess.run(
                ["docker", "rm", "--force", name], capture_output=True, timeout=30, check=False
            )
        receipts.append(
            {
                "id": check["id"],
                "image": check["image"],
                "profile_sha256": digest(check),
                "output_sha256": __import__("hashlib").sha256(output).hexdigest(),
                "exit_code": 0,
            }
        )
    return receipts


def run_agent(
    *,
    image: str,
    source: Path,
    bundle: Path,
    scratch: Path,
    model: str,
    prompt: str,
    output_type: type[Contract],
    api_key: str,
    writable=False,
    timeout=1800,
    telemetry=None,
):
    if not re.fullmatch(r"(?:[\w./:-]+@)?sha256:[a-f0-9]{64}", image):
        raise Blocked("AGENT_IMAGE_NOT_PINNED")
    if not api_key or api_key.lower() in {"placeholder", "changeme", "todo"}:
        raise Blocked("MODEL_NOT_CONFIGURED")
    if not re.fullmatch(r"[A-Za-z0-9_.:/-]+", model):
        raise Blocked("MODEL_INVALID")
    scratch.mkdir(mode=0o700, parents=True, exist_ok=True)
    identifier = "ai-" + secrets.token_hex(12)
    network, relay, agent = identifier + "-net", identifier + "-relay", identifier + "-agent"
    ticket = secrets.token_urlsafe(32)
    config = scratch / "config"
    config.mkdir(mode=0o755)
    (config / "config.toml").write_text(
        'model_provider = "relay"\nmodel = ' + json.dumps(model) + "\n"
        '[model_providers.relay]\nname = "Scoped Responses relay"\n'
        'base_url = "http://relay:8080/v1"\nenv_key = "INFERENCE_TICKET"\nwire_api = "responses"\n'
    )
    (config / "schema.json").write_text(json.dumps(output_type.model_json_schema()))
    output = scratch / "output"
    output.mkdir(mode=0o700)
    # Only output and the explicitly writable source are accessible to UID 10001.
    command(["chmod", "777", str(output)])
    env = {
        "PATH": os.environ["PATH"],
        "AI_AUTOMATION_TF_API_KEY": api_key,
        "INFERENCE_TICKET": ticket,
        "INFERENCE_MODEL": model,
    }
    hardening = [
        "--init",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--read-only",
        "--pids-limit=256",
        "--memory=4g",
        "--cpus=2",
        "--user=10001:10001",
    ]
    try:
        if writable:
            # Source is an isolated exported tree, never the caller's checkout.
            command(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    "none",
                    "--cap-drop=ALL",
                    "--cap-add=CHOWN",
                    "--security-opt=no-new-privileges",
                    "--user=0:0",
                    "--read-only",
                    "--mount",
                    f"type=bind,src={source.resolve()},dst=/source",
                    image,
                    "chown",
                    "-hR",
                    "10001:10001",
                    "/source",
                ]
            )
        command(["docker", "network", "create", "--internal", network])
        command(
            [
                "docker",
                "create",
                "--name",
                relay,
                "--network",
                "bridge",
                *hardening,
                "--env",
                "AI_AUTOMATION_TF_API_KEY",
                "--env",
                "INFERENCE_TICKET",
                "--env",
                "INFERENCE_MODEL",
                image,
                "python3",
                "/opt/relay.py",
            ],
            env=env,
        )
        command(["docker", "network", "connect", "--alias", "relay", network, relay])
        command(["docker", "start", relay])
        for _ in range(20):
            try:
                command(
                    [
                        "docker",
                        "exec",
                        relay,
                        "python3",
                        "-c",
                        "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health',timeout=1).read()",
                    ],
                    timeout=3,
                )
                break
            except Blocked:
                time.sleep(0.25)
        else:
            raise Blocked("INFERENCE_RELAY_UNAVAILABLE")
        args = [
            "docker",
            "run",
            "--interactive",
            "--name",
            agent,
            "--network",
            network,
            *hardening,
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=256m",
            "--tmpfs",
            "/home/agent/.codex:rw,uid=10001,gid=10001,size=128m",
            "--tmpfs",
            "/work:rw,uid=10001,gid=10001,size=128m",
            "--workdir",
            "/work",
            "--env",
            "INFERENCE_TICKET",
            "--mount",
            f"type=bind,src={source.resolve()},dst=/source" + ("" if writable else ",readonly"),
            "--mount",
            f"type=bind,src={bundle.resolve()},dst=/etc/codex/skills,readonly",
            "--mount",
            f"type=bind,src={config.resolve()},dst=/config,readonly",
            "--mount",
            f"type=bind,src={output.resolve()},dst=/output",
            image,
            "sh",
            "-c",
            "cp /config/config.toml /home/agent/.codex/config.toml; exec codex exec --json --skip-git-repo-check --ephemeral --sandbox danger-full-access --output-schema /config/schema.json -o /output/result.json -",
        ]
        # Codex's sandbox is replaced by the containing non-root, network-isolated
        # container; the host never executes model commands. stdin is task data.
        events = command(
            args,
            env={"PATH": os.environ["PATH"], "INFERENCE_TICKET": ticket},
            timeout=timeout,
            stdin=prompt.encode(),
        )
        if telemetry is not None:
            for line in events.splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                item = event.get("item", {})
                if (
                    event.get("type") == "item.completed"
                    and item.get("type") == "command_execution"
                ):
                    if item.get("exit_code") == 0:
                        telemetry["tool_success"] = True
                    elif type(item.get("exit_code")) is int and item["exit_code"] != 0:
                        telemetry["tool_failure"] = True
        result = output / "result.json"
        if result.is_symlink() or not result.is_file() or result.stat().st_size > 1_048_576:
            raise Blocked("AGENT_OUTPUT_INVALID")
        return output_type.model_validate_json(result.read_bytes())
    finally:
        for name in (agent, relay):
            subprocess.run(
                ["docker", "rm", "--force", name], capture_output=True, timeout=30, check=False
            )
        subprocess.run(
            ["docker", "network", "rm", network], capture_output=True, timeout=30, check=False
        )
