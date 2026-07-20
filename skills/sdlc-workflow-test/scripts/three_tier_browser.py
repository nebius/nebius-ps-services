"""Dedicated Chrome ownership for the three-tier live verifier."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import signal
import subprocess
import time
from typing import Any, Callable
from urllib.parse import quote


CHROME_EXECUTABLE = Path(
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
)
BROWSER_SCHEMA = "agentic-sdlc/dedicated-chrome-v1"
BROWSER_NAME = "Google Chrome"
PROCESS_TIMEOUT_SECONDS = 10.0


class BrowserOwnershipError(RuntimeError):
    """The dedicated browser identity cannot be established safely."""


def utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def marker_for(verification_id: str) -> str:
    return f"Agentic SDLC verifier {verification_id}"


def profile_path(run_root: Path) -> Path:
    return run_root / "private" / "browser" / "chrome-profile"


def marker_path(run_root: Path) -> Path:
    return run_root / "private" / "browser" / "ready.html"


def initial_state(verification_id: str) -> dict[str, Any]:
    return {
        "schema": BROWSER_SCHEMA,
        "status": "NOT_STARTED",
        "window_marker": marker_for(verification_id),
        "pid": None,
        "process_group": None,
        "launched_at": None,
        "closed_at": None,
    }


def validate_state(value: object, verification_id: str) -> None:
    if not isinstance(value, dict) or set(value) != {
        "schema",
        "status",
        "window_marker",
        "pid",
        "process_group",
        "launched_at",
        "closed_at",
    }:
        raise BrowserOwnershipError("Dedicated Chrome state is invalid.")
    if (
        value["schema"] != BROWSER_SCHEMA
        or value["status"] not in {"NOT_STARTED", "RUNNING", "CLOSED"}
        or value["window_marker"] != marker_for(verification_id)
    ):
        raise BrowserOwnershipError("Dedicated Chrome identity is invalid.")
    running = value["status"] == "RUNNING"
    if running != (
        isinstance(value["pid"], int)
        and value["pid"] > 1
        and isinstance(value["process_group"], int)
        and value["process_group"] > 1
        and isinstance(value["launched_at"], str)
        and value["closed_at"] is None
    ):
        raise BrowserOwnershipError("Dedicated Chrome process state is invalid.")
    if value["status"] == "NOT_STARTED" and any(
        value[key] is not None
        for key in ("pid", "process_group", "launched_at", "closed_at")
    ):
        raise BrowserOwnershipError("Unstarted Chrome state contains process data.")
    if value["status"] == "CLOSED" and (
        value["pid"] is not None
        or value["process_group"] is not None
        or not isinstance(value["launched_at"], str)
        or not isinstance(value["closed_at"], str)
    ):
        raise BrowserOwnershipError("Closed Chrome state is invalid.")


def _reject_symlinks(path: Path, boundary: Path) -> None:
    current = path.absolute()
    while True:
        if current.is_symlink():
            raise BrowserOwnershipError(
                f"Dedicated Chrome path is symlinked: {current}"
            )
        if current == boundary:
            return
        if current.parent == current:
            raise BrowserOwnershipError("Dedicated Chrome path escaped its run.")
        current = current.parent


def _process_info(pid: int) -> tuple[int, str] | None:
    try:
        result = subprocess.run(
            ["/bin/ps", "-p", str(pid), "-o", "pgid=", "-o", "command="],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BrowserOwnershipError(f"Could not inspect dedicated Chrome: {error}") from error
    if result.returncode != 0:
        if result.stderr.strip():
            raise BrowserOwnershipError(
                f"Could not inspect dedicated Chrome: {result.stderr.strip()}"
            )
        return None
    if not result.stdout.strip():
        return None
    raw = result.stdout.strip().split(maxsplit=1)
    if len(raw) != 2 or not raw[0].isdigit():
        raise BrowserOwnershipError("Dedicated Chrome process identity is malformed.")
    return int(raw[0]), raw[1]


def assert_owned_running(
    run_root: Path,
    verification_id: str,
    state: dict[str, Any],
    *,
    process_info: Callable[[int], tuple[int, str] | None] = _process_info,
) -> None:
    validate_state(state, verification_id)
    if state["status"] != "RUNNING":
        raise BrowserOwnershipError("Dedicated Chrome is not running.")
    profile = profile_path(run_root).resolve(strict=False)
    info = process_info(state["pid"])
    expected_prefix = str(CHROME_EXECUTABLE)
    expected_flag = f"--user-data-dir={profile}"
    if (
        info is None
        or info[0] != state["process_group"]
        or not info[1].startswith(f"{expected_prefix} ")
        or expected_flag not in info[1]
    ):
        raise BrowserOwnershipError(
            "Dedicated Chrome process identity changed; refusing browser action."
        )


def launch(
    run_root: Path,
    verification_id: str,
    state: dict[str, Any],
    *,
    popen: Callable[..., subprocess.Popen[Any]] = subprocess.Popen,
    process_info: Callable[[int], tuple[int, str] | None] = _process_info,
    getpgid: Callable[[int], int] = os.getpgid,
) -> dict[str, Any]:
    validate_state(state, verification_id)
    if state["status"] == "RUNNING":
        assert_owned_running(
            run_root, verification_id, state, process_info=process_info
        )
        return state
    if state["status"] == "CLOSED":
        raise BrowserOwnershipError("Closed dedicated Chrome cannot be relaunched.")
    if not CHROME_EXECUTABLE.is_file() or CHROME_EXECUTABLE.is_symlink():
        raise BrowserOwnershipError("Google Chrome is not installed at the canonical path.")

    private_root = (run_root / "private").resolve(strict=True)
    browser_root = private_root / "browser"
    profile = profile_path(run_root)
    ready = marker_path(run_root)
    _reject_symlinks(browser_root, private_root)
    if browser_root.exists():
        raise BrowserOwnershipError(
            "Dedicated Chrome storage already exists; refusing profile reuse."
        )
    browser_root.mkdir(mode=0o700)
    profile.mkdir(mode=0o700)
    marker = marker_for(verification_id)
    ready.write_text(
        "<!doctype html><meta charset=utf-8><title>"
        + marker
        + "</title><h1>"
        + marker
        + "</h1>\n",
        encoding="utf-8",
    )
    os.chmod(ready, 0o600)
    marker_url = ready.resolve(strict=True).as_uri() + "?verification_id=" + quote(
        verification_id, safe=""
    )
    args = [
        str(CHROME_EXECUTABLE),
        f"--user-data-dir={profile.resolve(strict=True)}",
        "--no-first-run",
        "--no-default-browser-check",
        "--new-window",
        marker_url,
    ]
    try:
        process = popen(
            args,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        pgid = getpgid(process.pid)
    except OSError as error:
        raise BrowserOwnershipError(f"Could not launch dedicated Chrome: {error}") from error
    if pgid != process.pid:
        try:
            process.terminate()
        except OSError:
            pass
        raise BrowserOwnershipError(
            "Dedicated Chrome did not receive a unique process group."
        )
    candidate = {
        **state,
        "status": "RUNNING",
        "pid": process.pid,
        "process_group": pgid,
        "launched_at": utc_now(),
    }
    deadline = time.monotonic() + PROCESS_TIMEOUT_SECONDS
    while True:
        try:
            assert_owned_running(
                run_root, verification_id, candidate, process_info=process_info
            )
            return candidate
        except BrowserOwnershipError:
            if time.monotonic() >= deadline:
                try:
                    os.killpg(pgid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                raise
            time.sleep(0.1)


def _process_group_exists(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError) as error:
        raise BrowserOwnershipError(
            f"Could not inspect dedicated Chrome process group: {error}"
        ) from error
    return True


def close(
    run_root: Path,
    verification_id: str,
    state: dict[str, Any],
    *,
    process_info: Callable[[int], tuple[int, str] | None] = _process_info,
    killpg: Callable[[int, int], None] = os.killpg,
    process_group_exists: Callable[[int], bool] = _process_group_exists,
) -> dict[str, Any]:
    validate_state(state, verification_id)
    if state["status"] in {"NOT_STARTED", "CLOSED"}:
        return state
    assert_owned_running(run_root, verification_id, state, process_info=process_info)
    pgid = state["process_group"]
    pid = state["pid"]
    try:
        killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + PROCESS_TIMEOUT_SECONDS
    while process_group_exists(pgid) and time.monotonic() < deadline:
        time.sleep(0.1)
    if process_group_exists(pgid):
        leader = process_info(pid)
        if leader is not None:
            assert_owned_running(
                run_root, verification_id, state, process_info=process_info
            )
        killpg(pgid, signal.SIGKILL)
        deadline = time.monotonic() + PROCESS_TIMEOUT_SECONDS
        while process_group_exists(pgid) and time.monotonic() < deadline:
            time.sleep(0.1)
    if process_group_exists(pgid):
        raise BrowserOwnershipError("Dedicated Chrome did not stop after exact cleanup.")
    return {
        **state,
        "status": "CLOSED",
        "pid": None,
        "process_group": None,
        "closed_at": utc_now(),
    }
