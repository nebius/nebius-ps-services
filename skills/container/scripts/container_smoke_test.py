#!/usr/bin/env python3
"""Run a bounded, task-owned Docker container smoke and shutdown test."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import sys
import time
import urllib.error
import urllib.request
from typing import Any

from container_runtime_common import (
    MAX_COMMAND_OUTPUT,
    run_command,
)

SCHEMA = "container-smoke/v1"
OWNERSHIP_LABEL = "com.openai.codex.container-smoke"
MAX_STREAM_BYTES = MAX_COMMAND_OUTPUT
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
CONTAINER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
TMPFS_RE = re.compile(r"^/[A-Za-z0-9_./-]+(?::[A-Za-z0-9_=,.-]+)?$")
MEMORY_RE = re.compile(r"^[1-9][0-9]*(?:[bkmgBKMG])?$")


class SmokeError(ValueError):
    """Invalid request, missing prerequisite, or unsafe runtime state."""


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Keep loopback health checks from following redirects off host."""

    def redirect_request(
        self,
        request: Any,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> None:
        return None


def _json_array(value: str, label: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise SmokeError(f"{label} must be a JSON string array") from exc
    if (
        not isinstance(parsed, list)
        or not parsed
        or not all(isinstance(item, str) and item for item in parsed)
    ):
        raise SmokeError(f"{label} must be a non-empty JSON string array")
    if any("\x00" in item for item in parsed):
        raise SmokeError(f"{label} must not contain NUL characters")
    return parsed


def _inspect_state(container_id: str, *, timeout: float = 30.0) -> dict[str, Any]:
    result = run_command(
        [
            "docker",
            "inspect",
            "--format",
            "{{json .State}}",
            container_id,
        ],
        timeout=timeout,
    )
    if result.returncode != 0:
        raise SmokeError("cannot inspect task-created container state")
    try:
        state = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise SmokeError("Docker returned invalid state JSON") from exc
    if not isinstance(state, dict):
        raise SmokeError("Docker returned invalid state data")
    return state


def _verify_ownership(
    container_id: str,
    token: str,
    *,
    timeout: float = 30.0,
) -> bool:
    result = run_command(
        [
            "docker",
            "inspect",
            "--format",
            f'{{{{ index .Config.Labels "{OWNERSHIP_LABEL}" }}}}',
            container_id,
        ],
        timeout=timeout,
    )
    return result.returncode == 0 and result.stdout.strip() == token


def _cleanup(container_id: str, token: str, *, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    if not _verify_ownership(
        container_id,
        token,
        timeout=min(10.0, max(0.01, deadline - time.monotonic())),
    ):
        return False
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return False
    return (
        run_command(
            ["docker", "rm", "--force", container_id],
            timeout=remaining,
        ).returncode
        == 0
    )


def _host_port(container_id: str, container_port: int, timeout: float) -> int:
    result = run_command(
        [
            "docker",
            "port",
            container_id,
            f"{container_port}/tcp",
        ],
        timeout=timeout,
    )
    if result.returncode != 0:
        raise SmokeError("cannot resolve the ephemeral loopback port")
    last = result.stdout.strip().splitlines()[-1]
    try:
        return int(last.rsplit(":", 1)[1])
    except (IndexError, ValueError) as exc:
        raise SmokeError("Docker returned an invalid published port") from exc


def _endpoint_ready(port: int, path: str, deadline: float) -> bool:
    url = f"http://127.0.0.1:{port}{path}"
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        NoRedirectHandler(),
    )
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            with opener.open(url, timeout=min(2.0, remaining)) as response:
                if 200 <= response.status < 400:
                    return True
        except (OSError, ValueError, urllib.error.URLError):
            pass
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(0.25, remaining))
    return False


def _command_ready(container_id: str, command: list[str], deadline: float) -> bool:
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        result = run_command(
            ["docker", "exec", container_id, *command],
            timeout=min(5.0, remaining),
        )
        if result.returncode == 0:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        state = _inspect_state(container_id, timeout=min(5.0, remaining))
        if not state.get("Running", False):
            return False
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(0.25, remaining))
    return False


def _wait_for_stop(
    container_id: str,
    deadline: float,
) -> tuple[bool, dict[str, Any]]:
    state: dict[str, Any] = {}
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        state = _inspect_state(container_id, timeout=min(5.0, remaining))
        if not state.get("Running", False):
            return True, state
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(0.2, remaining))
    return False, state


def _safe_log_summary(container_id: str) -> dict[str, Any]:
    result = run_command(
        ["docker", "logs", "--tail", "200", container_id],
        timeout=10,
    )
    return {
        "available": result.returncode == 0,
        "stdout_bytes": len(result.stdout.encode("utf-8")),
        "stderr_bytes": len(result.stderr.encode("utf-8")),
        "truncated": result.truncated,
    }


def smoke(args: argparse.Namespace) -> dict[str, Any]:
    if shutil.which("docker") is None:
        raise SmokeError("Docker CLI is not installed")
    if not CONTAINER_NAME_RE.fullmatch(args.name_prefix):
        raise SmokeError("--name-prefix is not a bounded Docker name prefix")
    if (
        not args.image
        or "\x00" in args.image
        or "\n" in args.image
        or not MEMORY_RE.fullmatch(args.memory)
    ):
        raise SmokeError("image and memory inputs must be non-empty bounded values")
    if args.external_network and args.network == "none":
        raise SmokeError("--external-network requires --network bridge")
    if args.network != "none" and not args.external_network:
        raise SmokeError("non-isolated networking requires --external-network")
    if args.health_path and not args.container_port:
        raise SmokeError("--health-path requires --container-port")
    if args.container_port and not args.health_path:
        raise SmokeError("--container-port requires --health-path")
    if args.health_path and args.health_command_json:
        raise SmokeError("choose one health endpoint or health command")
    if args.health_path and (
        len(args.health_path) > 2048
        or not re.fullmatch(r"/[A-Za-z0-9._~!$&'()*+,;=:@%/?-]*", args.health_path)
    ):
        raise SmokeError("--health-path must be a bounded origin-form path")

    command = (
        _json_array(args.command_json, "--command-json") if args.command_json else []
    )
    health_command = (
        _json_array(args.health_command_json, "--health-command-json")
        if args.health_command_json
        else []
    )
    for name in args.env:
        if not NAME_RE.fullmatch(name):
            raise SmokeError(f"invalid environment variable name: {name}")
        if name not in os.environ:
            raise SmokeError(f"environment variable is not set: {name}")
    for spec in args.tmpfs:
        if not TMPFS_RE.fullmatch(spec) or ".." in spec.split(":", 1)[0].split("/"):
            raise SmokeError(f"invalid tmpfs specification: {spec}")

    deadline = time.monotonic() + args.timeout

    def remaining(maximum: float) -> float:
        available = deadline - time.monotonic()
        if available <= 0:
            raise SmokeError("runtime test exceeded the total timeout")
        return min(maximum, available)

    token = secrets.token_hex(12)
    name = f"{args.name_prefix}-{token}"
    create = [
        "docker",
        "create",
        "--name",
        name,
        "--label",
        f"{OWNERSHIP_LABEL}={token}",
        "--pull",
        "never",
        "--network",
        args.network,
        "--memory",
        args.memory,
        "--cpus",
        str(args.cpus),
        "--pids-limit",
        str(args.pids_limit),
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
    ]
    if args.read_only:
        create.append("--read-only")
    for tmpfs in args.tmpfs:
        create.extend(["--tmpfs", tmpfs])
    for name_value in args.env:
        create.extend(["--env", name_value])
    if args.container_port:
        create.extend(["--publish", f"127.0.0.1::{args.container_port}"])
    create.extend(["--", args.image])
    create.extend(command)

    container_id = ""
    cleanup_target = name
    create_attempted = False
    started = False
    terminated_with_kill = False
    cleanup_verified = False
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "status": "fail",
        "image": args.image,
        "container_name": name,
        "environment_names": sorted(args.env),
        "limits": {
            "total_timeout_seconds": args.timeout,
            "shutdown_timeout_seconds": args.shutdown_timeout,
            "memory": args.memory,
            "cpus": args.cpus,
            "pids": args.pids_limit,
            "stream_bytes": MAX_STREAM_BYTES,
            "cleanup_timeout_seconds": 30.0,
        },
        "security": {
            "pull": "never",
            "network": args.network,
            "capabilities": "drop ALL",
            "no_new_privileges": True,
            "read_only": args.read_only,
            "tmpfs": list(args.tmpfs),
            "privileged": False,
            "docker_socket": False,
        },
        "evidence": {},
    }
    try:
        create_timeout = remaining(30)
        create_attempted = True
        created = run_command(create, timeout=create_timeout)
        report["evidence"]["create_returncode"] = created.returncode
        report["evidence"]["create_output_truncated"] = created.truncated
        if created.returncode != 0:
            raise SmokeError("Docker could not create the bounded test container")
        container_id = created.stdout.strip().splitlines()[-1]
        if not re.fullmatch(r"[0-9a-f]{12,64}", container_id):
            raise SmokeError("Docker returned an invalid container identifier")
        cleanup_target = container_id
        if not _verify_ownership(
            container_id,
            token,
            timeout=remaining(30),
        ):
            raise SmokeError("task ownership label verification failed after create")

        started_result = run_command(
            ["docker", "start", container_id],
            timeout=remaining(30),
        )
        report["evidence"]["start_returncode"] = started_result.returncode
        if started_result.returncode != 0:
            raise SmokeError("Docker could not start the bounded test container")
        started = True

        state = _inspect_state(container_id, timeout=remaining(30))
        report["evidence"]["started_running"] = bool(state.get("Running", False))
        if not state.get("Running", False):
            report["evidence"]["exit_code"] = state.get("ExitCode")
            raise SmokeError("container exited before the runtime check")

        if args.health_path:
            port = _host_port(
                container_id,
                args.container_port,
                remaining(10),
            )
            report["evidence"]["health"] = {
                "type": "loopback-http",
                "container_port": args.container_port,
                "host_port_ephemeral": True,
                "ready": _endpoint_ready(
                    port,
                    args.health_path,
                    deadline,
                ),
            }
            if not report["evidence"]["health"]["ready"]:
                raise SmokeError("health endpoint did not become ready")
        elif health_command:
            ready = _command_ready(
                container_id,
                health_command,
                deadline,
            )
            report["evidence"]["health"] = {
                "type": "exec",
                "argument_count": len(health_command),
                "ready": ready,
            }
            if not ready:
                raise SmokeError("health command did not succeed")
        else:
            report["evidence"]["health"] = {
                "type": "process-running",
                "ready": True,
            }

        state = _inspect_state(container_id, timeout=remaining(30))
        if state.get("Running", False):
            term = run_command(
                ["docker", "kill", "--signal", "TERM", container_id],
                timeout=remaining(10),
            )
            report["evidence"]["sigterm_returncode"] = term.returncode
            if term.returncode != 0:
                raise SmokeError("could not send SIGTERM to the task container")
            shutdown_deadline = min(
                deadline,
                time.monotonic() + args.shutdown_timeout,
            )
            stopped, state = _wait_for_stop(container_id, shutdown_deadline)
            report["evidence"]["graceful_shutdown"] = stopped
            if not stopped:
                terminated_with_kill = True
                run_command(
                    ["docker", "kill", "--signal", "KILL", container_id],
                    timeout=5.0,
                )
                raise SmokeError("container exceeded the graceful-shutdown timeout")
        else:
            report["evidence"]["graceful_shutdown"] = True
        report["evidence"]["exit_code"] = state.get("ExitCode")
        report["status"] = "pass"
    except SmokeError as exc:
        report["error"] = str(exc)
        if container_id:
            report["evidence"]["logs"] = _safe_log_summary(container_id)
    finally:
        if create_attempted:
            cleanup_verified = _cleanup(cleanup_target, token)
        report["evidence"]["cleanup_verified"] = cleanup_verified
        report["evidence"]["sigkill_after_timeout"] = terminated_with_kill
        report["evidence"]["container_started"] = started
        if container_id and not cleanup_verified:
            report["status"] = "fail"
            report["error"] = "task-owned container cleanup could not be verified"
    return report


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Container Smoke Test",
        "",
        f"- Status: `{report['status']}`",
        f"- Image: `{report['image']}`",
        f"- Network: `{report['security']['network']}`",
        f"- Read-only: `{str(report['security']['read_only']).lower()}`",
        f"- Cleanup verified: `{str(report['evidence'].get('cleanup_verified', False)).lower()}`",
    ]
    if report.get("error"):
        lines.extend(["", f"Error: {report['error']}"])
    lines.extend(["", "## Evidence", "", "```json"])
    lines.append(json.dumps(report["evidence"], indent=2, sort_keys=True))
    lines.append("```")
    return "\n".join(lines) + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--name-prefix", default="codex-container-smoke")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--shutdown-timeout", type=float, default=10.0)
    parser.add_argument("--memory", default="1g")
    parser.add_argument("--cpus", type=float, default=2.0)
    parser.add_argument("--pids-limit", type=int, default=256)
    parser.add_argument("--read-only", action="store_true")
    parser.add_argument("--tmpfs", action="append", default=[])
    parser.add_argument("--env", action="append", default=[])
    parser.add_argument("--command-json")
    parser.add_argument("--health-command-json")
    parser.add_argument("--health-path")
    parser.add_argument("--container-port", type=int)
    parser.add_argument("--network", choices=("none", "bridge"), default="none")
    parser.add_argument("--external-network", action="store_true")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if (
        args.timeout <= 0
        or args.shutdown_timeout <= 0
        or args.cpus <= 0
        or args.pids_limit <= 0
        or (args.container_port is not None and not 1 <= args.container_port <= 65535)
    ):
        print(
            json.dumps(
                {"schema": SCHEMA, "error": "limits and ports must be positive"}
            ),
            file=sys.stderr,
        )
        return 2
    try:
        report = smoke(args)
    except SmokeError as exc:
        print(
            json.dumps({"schema": SCHEMA, "error": str(exc)}, sort_keys=True),
            file=sys.stderr,
        )
        return 2
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        sys.stdout.write(_render_markdown(report))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
