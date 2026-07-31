#!/usr/bin/env python3
"""Collect bounded application evidence through generation-fenced helpers."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from task_implementer_lifecycle import (
    LifecycleError,
    compose_ps,
    database_probe,
    restart_api,
    status,
)


def _request(url: str, method: str = "GET", body: dict[str, Any] | None = None) -> Any:
    payload = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        if response.status < 200 or response.status >= 300:
            raise LifecycleError(f"HTTP {method} returned {response.status}")
        return json.loads(response.read(262144).decode("utf-8"))


def _eventually(url: str, task_id: int | None = None) -> Any:
    deadline = time.monotonic() + 60
    last_error = "service unavailable"
    while time.monotonic() < deadline:
        try:
            value = _request(url)
            if task_id is None:
                return value
            tasks = value if isinstance(value, list) else value.get("tasks", [])
            for task in tasks:
                if task.get("id") == task_id:
                    return task
            last_error = "task absent from API response"
        except (OSError, ValueError, urllib.error.URLError) as exc:
            last_error = str(exc)[:200]
        time.sleep(1)
    raise LifecycleError(f"HTTP evidence timed out: {last_error}")


def _frontend(url: str) -> str:
    deadline = time.monotonic() + 60
    last_error = "frontend unavailable"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                body = response.read(262144).decode("utf-8")
            if response.status == 200 and "task" in body.lower():
                return body
            last_error = "frontend lacks the task-board marker"
        except (OSError, UnicodeDecodeError, urllib.error.URLError) as exc:
            last_error = str(exc)[:200]
        time.sleep(1)
    raise LifecycleError(f"frontend evidence timed out: {last_error}")


def collect(
    root: Path,
    generation: str,
    *,
    current: dict[str, Any] | None = None,
    compose_ps_fn: Callable[[Path, str], dict[str, Any]] | None = None,
    database_probe_fn: Callable[[Path, str, int], dict[str, Any]] | None = None,
    restart_api_fn: Callable[[Path, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    current = status(root) if current is None else current
    compose_ps_fn = compose_ps if compose_ps_fn is None else compose_ps_fn
    database_probe_fn = (
        database_probe if database_probe_fn is None else database_probe_fn
    )
    restart_api_fn = restart_api if restart_api_fn is None else restart_api_fn
    if (
        current.get("generation_id") != generation
        or type(current.get("web_port")) is not int
    ):
        raise LifecycleError("active lifecycle or dynamic web port does not match")
    port = current["web_port"]
    base = f"http://127.0.0.1:{port}"
    frontend_body = _frontend(base)
    _eventually(f"{base}/api/tasks")
    created = _request(f"{base}/api/tasks", "POST", {"title": "Verifier task"})
    task_id = created.get("id")
    if type(task_id) is not int:
        raise LifecycleError("API create response lacks an integer task ID")
    updated = _request(f"{base}/api/tasks/{task_id}", "PATCH", {"completed": True})
    task = _eventually(f"{base}/api/tasks", task_id)
    if updated.get("completed") is not True or task.get("completed") is not True:
        raise LifecycleError("API update did not persist")
    database = database_probe_fn(root, generation, task_id)
    if database["title"] != "Verifier task" or database["completed"] is not True:
        raise LifecycleError("database row does not match the updated API task")
    live = compose_ps_fn(root, generation)
    services_value = live["services"]
    records = services_value if isinstance(services_value, list) else [services_value]
    names = sorted(
        {
            str(record.get("Service"))
            for record in records
            if isinstance(record, dict) and record.get("Service")
        }
    )
    if names != ["api", "db", "frontend"]:
        raise LifecycleError("Compose evidence does not show exactly three services")
    if any(
        record.get("State") != "running"
        or record.get("Health") not in {None, "", "healthy"}
        for record in records
        if isinstance(record, dict)
    ):
        raise LifecycleError("Compose evidence includes a stopped or unhealthy service")
    restart_api_fn(root, generation)
    persisted = _eventually(f"{base}/api/tasks", task_id)
    if (
        persisted.get("title") != "Verifier task"
        or persisted.get("completed") is not True
    ):
        raise LifecycleError("task did not persist after API restart")
    created_task = {"id": task_id, "title": "Verifier task", "completed": True}
    database_task = {
        "id": database["task_id"],
        "title": database["title"],
        "completed": database["completed"],
    }
    return {
        "schema": "task-implementer-test/application-evidence-v1",
        "generation_id": generation,
        "services": names,
        "web_port": port,
        "frontend_body_sha256": hashlib.sha256(
            frontend_body.encode("utf-8")
        ).hexdigest(),
        "created_task": created_task,
        "database_task": database_task,
        "persisted_after_restart": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--expected-generation", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        raise LifecycleError(
            "direct collection is disabled; use lifecycle collect-application"
        )
    except (LifecycleError, OSError, ValueError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)[:500]}, sort_keys=True))
        return 1
    print(json.dumps({"status": "PASS", "evidence": str(args.output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
