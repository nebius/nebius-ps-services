"""Terraform command wrappers used by the CLI."""

from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import shlex
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any

from .deployment_timing import timed
from .managed_tools import resolve_terraform_binary
from .terraform_provider import PROVIDER_MODULE_NAME_MAX_LENGTH


def _require_terraform() -> str:
    return resolve_terraform_binary()


def _saved_plan_identity(
    infra_dir: Path,
    plan_file: Path,
    *,
    create: bool,
) -> tuple[Path, tuple[int, int]]:
    resolved_infra = infra_dir.resolve()
    absolute_plan = Path(os.path.abspath(plan_file))
    try:
        relative = absolute_plan.relative_to(resolved_infra)
    except ValueError as exc:
        raise ValueError(
            "Terraform saved plan must be inside the rendered infra directory"
        ) from exc
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("Terraform saved plan path is invalid")

    current = resolved_infra
    for part in relative.parts[:-1]:
        current = current / part
        if not current.exists():
            current.mkdir(mode=0o700)
        current_stat = current.lstat()
        if (
            not stat.S_ISDIR(current_stat.st_mode)
            or current_stat.st_uid != os.geteuid()
            or current_stat.st_mode & 0o022
        ):
            raise ValueError("Terraform saved plan parent directory is not privately owned")

    parent_stat = absolute_plan.parent.lstat()
    if (
        not stat.S_ISDIR(parent_stat.st_mode)
        or parent_stat.st_uid != os.geteuid()
        or parent_stat.st_mode & 0o022
    ):
        raise ValueError("Terraform saved plan parent directory is not privately owned")

    if create and not absolute_plan.exists() and not absolute_plan.is_symlink():
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(absolute_plan, flags, 0o600)
        os.close(descriptor)
    try:
        plan_stat = absolute_plan.lstat()
    except FileNotFoundError as exc:
        raise RuntimeError(f"Terraform saved plan does not exist: {absolute_plan}") from exc
    if (
        not stat.S_ISREG(plan_stat.st_mode)
        or plan_stat.st_nlink != 1
        or plan_stat.st_uid != os.geteuid()
        or stat.S_IMODE(plan_stat.st_mode) != 0o600
    ):
        raise ValueError("Terraform saved plan must be an owner-only, single-link regular file")
    return absolute_plan, (plan_stat.st_dev, plan_stat.st_ino)


def _saved_plan_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _format_command(cmd: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in cmd)


def _terraform_error_blocks(stderr: str) -> tuple[str, ...]:
    blocks = tuple(
        block.strip()
        for block in re.split(r"╷|(?=^Error:)", stderr, flags=re.MULTILINE)
        if "Error:" in block
    )
    if blocks:
        return blocks
    text = stderr.strip()
    return (text,) if text else ()


def _format_json_diagnostic_event(event: dict[str, Any]) -> str:
    diagnostic = event.get("diagnostic")
    if not isinstance(diagnostic, dict):
        return str(event.get("@message", "")).strip()

    severity = str(diagnostic.get("severity", "")).strip().lower()
    summary = str(diagnostic.get("summary", "")).strip() or "Terraform diagnostic"
    detail = str(diagnostic.get("detail", "")).strip()
    header = "Error" if severity == "error" else "Warning"

    lines = [f"{header}: {summary}"]
    range_payload = diagnostic.get("range")
    snippet = diagnostic.get("snippet")
    filename = ""
    line_number = None
    if isinstance(range_payload, dict):
        filename = str(range_payload.get("filename", "")).strip()
        start = range_payload.get("start")
        if isinstance(start, dict):
            line_number = start.get("line")
    context = ""
    code = ""
    if isinstance(snippet, dict):
        context = str(snippet.get("context", "")).strip()
        code = str(snippet.get("code", "")).rstrip()
    if filename and line_number:
        if context:
            lines.append(f"  on {filename} line {line_number}, in {context}:")
        else:
            lines.append(f"  on {filename} line {line_number}:")
        if code:
            lines.append(f"  {code}")
    if detail:
        lines.append("")
        lines.append(detail)
    return "\n".join(lines).strip()


def _terraform_failure_text_from_events(events: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for event in events:
        if str(event.get("type", "")).strip() != "diagnostic":
            continue
        diagnostic = event.get("diagnostic")
        if not isinstance(diagnostic, dict):
            continue
        if str(diagnostic.get("severity", "")).strip().lower() != "error":
            continue
        block = _format_json_diagnostic_event(event)
        if block:
            blocks.append(block)
    return "\n\n".join(blocks).strip()


def _parse_state_lock_info(stderr: str) -> dict[str, str]:
    info: dict[str, str] = {}
    collecting = False
    for raw_line in stderr.splitlines():
        line = raw_line.lstrip("│").rstrip().strip()
        if not collecting:
            if line == "Lock Info:":
                collecting = True
            continue
        if not line:
            break
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        normalized_key = key.strip()
        normalized_value = value.strip()
        if normalized_key:
            info[normalized_key] = normalized_value
    return info


def _state_lock_object_hint(lock_path: str) -> str:
    bucket, separator, key = lock_path.partition("/")
    if not separator or not bucket or not key:
        return ""
    return f"bucket `{bucket}`, object `{key}.tflock`"


def _translate_terraform_failure(*, cmd: list[str], cwd: Path, stderr: str) -> str:
    command_label = _format_command(cmd)
    prefix = f"Terraform command `{command_label}` failed in {cwd}"
    diagnostics = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", stderr).strip()
    if not diagnostics:
        return prefix

    issues: list[str] = []
    for block in _terraform_error_blocks(diagnostics):
        location_match = re.search(r"on (?P<path>[^\n]+) line (?P<line>\d+)(?:,|:)", block)
        location = None
        if location_match:
            location = f"{location_match.group('path')}:{location_match.group('line')}"

        if "Error acquiring the state lock" in block:
            lock_info = _parse_state_lock_info(block)
            who = lock_info.get("Who", "").strip()
            created = lock_info.get("Created", "").strip()
            path = lock_info.get("Path", "").strip()
            object_hint = _state_lock_object_hint(path)
            guidance = (
                "Terraform never acquired the remote state lock, so this run did not create or change any resources. "
                "This usually means another Terraform operation is still using the same state, or a previous apply/plan was canceled and left a stale lockfile behind."
            )
            if object_hint:
                guidance += f" If you have confirmed no other Terraform operation is running, remove the stale lockfile from {object_hint} and retry."
            else:
                guidance += " If you have confirmed no other Terraform operation is running, remove the stale backend lockfile and retry."
            if who or created:
                details: list[str] = []
                if who:
                    details.append(f"owner `{who}`")
                if created:
                    details.append(f"created `{created}`")
                guidance += " Reported lock metadata: " + ", ".join(details) + "."
            issues.append(guidance)
            continue

        # Terraform wraps provider errors across lines and adds box margins.
        detail = " ".join(line.lstrip("│ ").strip() for line in block.splitlines())
        if "exchange token:" in detail and re.search(
            r"\blookup \S+(?: on \S+)?: no such host\b", detail
        ):
            issues.append(
                "DNS resolution failed during provider token exchange. "
                "Check DNS and network connectivity to the token service from this machine, "
                "then rerun the same command. This diagnostic does not establish invalid "
                "credentials or a module-code defect."
            )
            continue

        module_name_match = re.search(
            r"Attribute module_name must be a string of \[a-zA-Z0-9_\], "
            r"not more than 16 characters, got: (?P<value>[^\n]+)",
            block,
        )
        if module_name_match:
            got_value = module_name_match.group("value").strip()
            issues.append(
                "Nebius provider `module_name` is invalid: "
                f"`{got_value}`. It must match `[A-Za-z0-9_]` and be at most "
                f"{PROVIDER_MODULE_NAME_MAX_LENGTH} characters. "
                "Check `TF_VAR_nebius_provider_module_name` if you override it."
            )
            continue

        if 'Call to function "coalesce" failed: no non-null, non-empty-string arguments.' in block:
            issues.append(
                f"Terraform source module expression failed at `{location or 'unknown location'}`: "
                "`coalesce(...)` received only null or empty values. "
                "This usually means the source Terraform module is using "
                "`coalesce(..., null)` for an optional field. Fix the module source "
                "or pin a corrected module version."
            )
            continue

        missing_output_match = re.search(
            r'This object does not have an attribute named "(?P<attribute>[A-Za-z0-9_]+)"',
            block,
        )
        if missing_output_match and location and location.startswith("outputs.tf:"):
            attribute = missing_output_match.group("attribute")
            issues.append(
                f"Rendered Terraform root expects child module output `{attribute}` at `{location}`. "
                "If you use a custom source for a component with built-in cluster handoff, that "
                f'module must expose `output "{attribute}"` for deploy/bootstrap cluster handoff.'
            )
            continue

        if (
            "operation wait: can't get operation" in block
            and "409 (Conflict)" in block
            and 'content-type "text/plain' in block
        ):
            issues.append(
                "Nebius accepted the resource operation, but the Terraform provider lost the "
                "operation polling request with a 409 Conflict. Check the live Nebius resource "
                "status; if the resource is RUNNING/ACTIVE, rerun deploy so Terraform can "
                "refresh state and continue."
            )
            continue

    if not issues:
        return f"{prefix}:\n{diagnostics}"

    return prefix + ":\n  - " + "\n  - ".join(issues) + "\n\nTerraform diagnostics:\n" + diagnostics


def _run(
    cmd: list[str],
    *,
    cwd: Path,
    timeout: int,
    extra_env: dict[str, str] | None = None,
    abort_check: Callable[[], str | None] | None = None,
) -> None:
    options = {"abort_check": abort_check} if abort_check is not None else {}
    stdout, stderr = _run_capture(cmd, cwd=cwd, timeout=timeout, extra_env=extra_env, **options)
    if stdout:
        sys.stdout.write(stdout)
        if not stdout.endswith("\n"):
            sys.stdout.write("\n")
    if stderr:
        sys.stderr.write(stderr)
        if not stderr.endswith("\n"):
            sys.stderr.write("\n")


@timed("terraform-subprocess", category="infrastructure")
def _run_capture(
    cmd: list[str],
    *,
    cwd: Path,
    timeout: int,
    extra_env: dict[str, str] | None = None,
    abort_check: Callable[[], str | None] | None = None,
) -> tuple[str, str]:
    from .owned_process import run as owned_run

    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    try:
        completed = owned_run(
            cmd,
            cwd=cwd,
            check=True,
            timeout=timeout,
            env=env,
            capture_output=True,
            text=True,
            abort_check=abort_check,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            _translate_terraform_failure(
                cmd=cmd,
                cwd=cwd,
                stderr=exc.stderr or "",
            )
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"Terraform command `{_format_command(cmd)}` timed out after {timeout} seconds in {cwd}"
        ) from exc

    return completed.stdout or "", completed.stderr or ""


@timed("terraform-subprocess", category="infrastructure")
def _stream_json_events(
    cmd: list[str],
    *,
    cwd: Path,
    timeout: int,
    extra_env: dict[str, str] | None = None,
    event_callback: Callable[[dict[str, Any]], None] | None = None,
    abort_check: Callable[[], str | None] | None = None,
) -> None:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)

    from .owned_process import popen as owned_popen

    process = owned_popen(
        cmd,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    line_queue: queue.Queue[tuple[str, str | None]] = queue.Queue()
    collected_events: list[dict[str, Any]] = []
    stdout_fallback: list[str] = []
    stderr_lines: list[str] = []

    def _reader(stream, source: str) -> None:
        try:
            for line in iter(stream.readline, ""):
                line_queue.put((source, line))
        finally:
            stream.close()
            line_queue.put((source, None))

    stdout_thread = threading.Thread(
        target=_reader,
        args=(process.stdout, "stdout"),
        name="terraform-json-stdout",
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_reader,
        args=(process.stderr, "stderr"),
        name="terraform-json-stderr",
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()

    deadline = time.monotonic() + timeout
    next_abort_check = 0.0

    def stop_process() -> None:
        with suppress(Exception):
            process.terminate()
        with suppress(Exception):
            process.wait(timeout=5)
        with suppress(Exception):
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)

    def check_abort(*, force: bool = False) -> None:
        nonlocal next_abort_check
        if abort_check is None or (not force and time.monotonic() < next_abort_check):
            return
        try:
            reason = abort_check()
        except BaseException:
            stop_process()
            raise
        # Authority checks can make remote calls. Measure from their completion
        # so a slow check does not force another one for every queued JSON line.
        next_abort_check = time.monotonic() + 0.25
        if reason:
            stop_process()
            raise RuntimeError(
                f"Terraform command `{_format_command(cmd)}` aborted early in {cwd}: {reason}"
            )

    closed_streams = 0
    try:
        while closed_streams < 2:
            check_abort()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                process.kill()
                with suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=5)
                raise RuntimeError(
                    f"Terraform command `{_format_command(cmd)}` timed out after {timeout} seconds in {cwd}"
                )
            try:
                source, payload = line_queue.get(timeout=min(0.25, remaining))
            except queue.Empty:
                continue
            if payload is None:
                closed_streams += 1
                continue
            text = payload.rstrip("\n")
            if not text:
                continue
            if source == "stderr":
                stderr_lines.append(text)
                continue
            try:
                event = json.loads(text)
            except json.JSONDecodeError:
                stdout_fallback.append(text)
                continue
            if isinstance(event, dict):
                collected_events.append(event)
                if event_callback is not None:
                    with suppress(Exception):
                        event_callback(event)
            else:
                stdout_fallback.append(text)
    except BaseException:
        stop_process()
        raise
    finally:
        stdout_thread.join(timeout=1)
        stderr_thread.join(timeout=1)

    try:
        while True:
            check_abort()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                process.kill()
                with suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=5)
                raise RuntimeError(
                    f"Terraform command `{_format_command(cmd)}` timed out after {timeout} seconds in {cwd}"
                )
            try:
                return_code = process.wait(timeout=min(0.25, remaining))
                break
            except subprocess.TimeoutExpired:
                continue
    except BaseException:
        stop_process()
        raise
    check_abort(force=True)
    if return_code == 0:
        return

    event_errors = _terraform_failure_text_from_events(collected_events)
    diagnostics = "\n".join(
        part
        for part in [
            event_errors,
            "\n".join(stdout_fallback).strip(),
            "\n".join(stderr_lines).strip(),
        ]
        if part
    ).strip()
    raise RuntimeError(
        _translate_terraform_failure(
            cmd=cmd,
            cwd=cwd,
            stderr=diagnostics,
        )
    )


def terraform_init(
    infra_dir: Path,
    *,
    extra_env: dict[str, str] | None = None,
    backend: bool = True,
    quiet: bool = False,
) -> None:
    """Run terraform init in the rendered infra directory."""
    terraform_bin = _require_terraform()
    if not infra_dir.exists():
        raise RuntimeError(f"Rendered infra directory does not exist: {infra_dir}")
    from .deployment_preparation import current_preparation, initialized_identity, terraform_inputs

    prepared = current_preparation()
    key = (
        prepared.key(infra_dir, terraform_bin, extra_env, backend=backend, initialization=True)
        if prepared
        else ""
    )
    identity = initialized_identity(infra_dir)
    if prepared is not None and identity is not None and prepared.initialized.get(key) == identity:
        return
    cmd = [terraform_bin, "init", "-input=false", "-no-color"]
    if not backend:
        cmd.append("-backend=false")
    before_inputs = terraform_inputs(infra_dir, initialization=True, ignore_lock=True)
    run = _run_capture if quiet else _run
    run(cmd, cwd=infra_dir, timeout=300, extra_env=extra_env)
    identity = initialized_identity(infra_dir)
    if (
        prepared is not None
        and identity is not None
        and before_inputs == terraform_inputs(infra_dir, initialization=True, ignore_lock=True)
    ):
        # init can create/update the provider lock; remember its postimage.
        key = prepared.key(
            infra_dir, terraform_bin, extra_env, backend=backend, initialization=True
        )
        prepared.initialized[key] = identity


def terraform_plan(
    infra_dir: Path,
    *,
    extra_env: dict[str, str] | None = None,
    initialize: bool = True,
    quiet: bool = False,
    plan_file: Path | None = None,
    destroy: bool = False,
    targets: Sequence[str] = (),
    replace_addresses: Sequence[str] = (),
) -> None:
    """Run terraform plan in the rendered infra directory."""
    terraform_bin = _require_terraform()
    if initialize:
        if quiet:
            terraform_init(infra_dir, extra_env=extra_env, quiet=True)
        else:
            terraform_init(infra_dir, extra_env=extra_env)
    cmd = [terraform_bin, "plan", "-input=false", "-lock-timeout=5m"]
    if destroy and replace_addresses:
        raise ValueError("Terraform replacement planning cannot use destroy mode")
    if destroy:
        cmd.append("-destroy")
    for address in replace_addresses:
        normalized = str(address).strip()
        if not normalized or normalized.startswith("-") or any(c.isspace() for c in normalized):
            raise ValueError("Terraform replacement address is invalid")
        cmd.append(f"-replace={normalized}")
    for target in targets:
        normalized = str(target).strip()
        if (
            not normalized
            or normalized.startswith("-")
            or any(char.isspace() for char in normalized)
        ):
            raise ValueError("Terraform target address is invalid")
        cmd.append(f"-target={normalized}")
    if plan_file is not None:
        resolved_plan, plan_identity = _saved_plan_identity(
            infra_dir,
            plan_file,
            create=True,
        )
        cmd.append(f"-out={resolved_plan}")
    if quiet:
        _run_capture(cmd, cwd=infra_dir, timeout=1800, extra_env=extra_env)
    else:
        _run(
            cmd,
            cwd=infra_dir,
            timeout=1800,
            extra_env=extra_env,
        )
    if plan_file is not None:
        verified_plan, verified_identity = _saved_plan_identity(
            infra_dir,
            plan_file,
            create=False,
        )
        if verified_plan != resolved_plan or verified_identity != plan_identity:
            raise RuntimeError("Terraform saved plan identity changed while planning")


def terraform_validate(
    infra_dir: Path,
    *,
    extra_env: dict[str, str] | None = None,
    initialize: bool = True,
) -> None:
    """Run terraform validate in the rendered infra directory."""
    terraform_bin = _require_terraform()
    if initialize:
        terraform_init(infra_dir, extra_env=extra_env)
    from .deployment_preparation import current_preparation, initialized_identity

    prepared = current_preparation()
    identity = initialized_identity(infra_dir)
    key = prepared.key(infra_dir, terraform_bin, extra_env) + str(identity) if prepared else ""
    if prepared is not None and identity is not None and key in prepared.validated:
        return
    _run(
        [terraform_bin, "validate", "-no-color"],
        cwd=infra_dir,
        timeout=300,
        extra_env=extra_env,
    )

    if prepared is not None and identity is not None:
        prepared.validated.add(key)


def terraform_state_list(
    infra_dir: Path,
    *,
    extra_env: dict[str, str] | None = None,
    initialize: bool = True,
) -> tuple[str, ...]:
    """List Terraform state addresses, returning an empty tuple when no state exists yet."""
    terraform_bin = _require_terraform()
    if initialize:
        terraform_init(infra_dir, extra_env=extra_env)
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    try:
        completed = subprocess.run(
            [terraform_bin, "state", "list"],
            cwd=infra_dir,
            check=True,
            timeout=120,
            env=env,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or exc.stdout or "").strip()
        lowered = stderr.lower()
        if "no state file was found" in lowered or "no stored state was found" in lowered:
            return ()
        raise RuntimeError(
            _translate_terraform_failure(
                cmd=[terraform_bin, "state", "list"],
                cwd=infra_dir,
                stderr=stderr,
            )
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"Terraform command `{terraform_bin} state list` timed out after 120 seconds in {infra_dir}"
        ) from exc
    return tuple(line.strip() for line in (completed.stdout or "").splitlines() if line.strip())


def terraform_state_show(
    infra_dir: Path,
    address: str,
    *,
    extra_env: dict[str, str] | None = None,
    initialize: bool = True,
) -> str:
    """Render one Terraform state address in text form."""
    terraform_bin = _require_terraform()
    if initialize:
        terraform_init(infra_dir, extra_env=extra_env)
    stdout, _stderr = _run_capture(
        [terraform_bin, "state", "show", "-no-color", address],
        cwd=infra_dir,
        timeout=120,
        extra_env=extra_env,
    )
    return stdout


def terraform_apply(
    infra_dir: Path,
    *,
    extra_env: dict[str, str] | None = None,
    initialize: bool = True,
    event_callback: Callable[[dict[str, Any]], None] | None = None,
    abort_check: Callable[[], str | None] | None = None,
    plan_file: Path | None = None,
    expected_plan_sha256: str | None = None,
) -> None:
    """Run terraform apply in the rendered infra directory."""
    terraform_bin = _require_terraform()
    if initialize:
        terraform_init(infra_dir, extra_env=extra_env)
    resolved_plan: Path | None = None
    if plan_file is not None:
        resolved_plan, _plan_identity = _saved_plan_identity(
            infra_dir,
            plan_file,
            create=False,
        )
        if expected_plan_sha256 is not None:
            actual_plan_sha256 = _saved_plan_sha256(resolved_plan)
            if actual_plan_sha256 != expected_plan_sha256:
                raise RuntimeError(
                    "Terraform saved plan no longer matches its approved receipt digest"
                )
    apply_args = [terraform_bin, "apply", "-input=false"]
    if resolved_plan is None:
        apply_args.append("-auto-approve")
    apply_args.append("-lock-timeout=5m")
    if resolved_plan is not None:
        apply_args.append(str(resolved_plan))
    if abort_check is not None:
        abort_reason = abort_check()
        if abort_reason:
            raise RuntimeError(f"Terraform apply aborted before launch: {abort_reason}")
    if event_callback is None:
        _run(
            apply_args,
            cwd=infra_dir,
            timeout=7200,
            extra_env=extra_env,
            **({"abort_check": abort_check} if abort_check is not None else {}),
        )
        return
    json_apply_args = [terraform_bin, "apply", "-json", *apply_args[2:]]
    _stream_json_events(
        json_apply_args,
        cwd=infra_dir,
        timeout=7200,
        extra_env=extra_env,
        event_callback=event_callback,
        abort_check=abort_check,
    )


def terraform_destroy(
    infra_dir: Path,
    *,
    extra_env: dict[str, str] | None = None,
    initialize: bool = True,
    event_callback: Callable[[dict[str, Any]], None] | None = None,
    abort_check: Callable[[], str | None] | None = None,
) -> None:
    """Run terraform destroy in the rendered infra directory."""
    terraform_bin = _require_terraform()
    if initialize:
        terraform_init(infra_dir, extra_env=extra_env)
    from .destroy_target import require_non_mk8s_destroy

    current = terraform_show_json(infra_dir, extra_env=extra_env, initialize=False, quiet=True)
    values = current.get("values", {})
    if not isinstance(values, dict):
        raise RuntimeError("Terraform destroy state inventory is incomplete")
    require_non_mk8s_destroy({}, state_values=values)
    if event_callback is None:
        _run(
            [terraform_bin, "destroy", "-input=false", "-auto-approve", "-lock-timeout=5m"],
            cwd=infra_dir,
            timeout=7200,
            extra_env=extra_env,
            **({"abort_check": abort_check} if abort_check is not None else {}),
        )
        return
    _stream_json_events(
        [terraform_bin, "destroy", "-json", "-input=false", "-auto-approve", "-lock-timeout=5m"],
        cwd=infra_dir,
        timeout=7200,
        extra_env=extra_env,
        event_callback=event_callback,
        abort_check=abort_check,
    )


def terraform_force_unlock(
    infra_dir: Path,
    lock_id: str,
    *,
    extra_env: dict[str, str] | None = None,
) -> None:
    """Run terraform force-unlock in the rendered infra directory."""
    terraform_bin = _require_terraform()
    normalized_lock_id = str(lock_id).strip()
    if not normalized_lock_id:
        raise RuntimeError("Terraform lock ID is required for force-unlock")
    terraform_init(infra_dir, extra_env=extra_env)
    _run(
        [terraform_bin, "force-unlock", "-force", normalized_lock_id],
        cwd=infra_dir,
        timeout=120,
        extra_env=extra_env,
    )


def terraform_output_raw(
    infra_dir: Path,
    output_name: str,
    *,
    extra_env: dict[str, str] | None = None,
    initialize: bool = True,
) -> str:
    """Read one Terraform output as raw text from the rendered infra directory."""
    terraform_bin = _require_terraform()
    if not infra_dir.exists():
        raise RuntimeError(f"Rendered infra directory does not exist: {infra_dir}")
    if initialize:
        terraform_init(infra_dir, extra_env=extra_env)
    stdout, stderr = _run_capture(
        [terraform_bin, "output", "-raw", output_name],
        cwd=infra_dir,
        timeout=120,
        extra_env=extra_env,
    )
    if stderr:
        sys.stderr.write(stderr)
        if not stderr.endswith("\n"):
            sys.stderr.write("\n")
    return stdout.strip()


def terraform_output_json(
    infra_dir: Path,
    *,
    extra_env: dict[str, str] | None = None,
    initialize: bool = True,
) -> dict[str, object]:
    """Read all Terraform outputs as JSON from the rendered infra directory."""
    terraform_bin = _require_terraform()
    if not infra_dir.exists():
        raise RuntimeError(f"Rendered infra directory does not exist: {infra_dir}")
    if initialize:
        terraform_init(infra_dir, extra_env=extra_env)
    stdout, stderr = _run_capture(
        [terraform_bin, "output", "-json"],
        cwd=infra_dir,
        timeout=120,
        extra_env=extra_env,
    )
    if stderr:
        sys.stderr.write(stderr)
        if not stderr.endswith("\n"):
            sys.stderr.write("\n")
    try:
        payload = json.loads(stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Terraform output -json returned invalid JSON in {infra_dir}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"Terraform output -json returned a non-mapping payload in {infra_dir}")
    return payload


def terraform_provider_schema_json(
    infra_dir: Path, *, extra_env: dict[str, str] | None = None
) -> dict[str, Any]:
    """Read schemas from the initialized, locked provider installation."""
    stdout, _stderr = _run_capture(
        [_require_terraform(), "providers", "schema", "-json"],
        cwd=infra_dir,
        timeout=120,
        extra_env=extra_env,
    )
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Terraform provider schema returned invalid JSON") from exc
    if (
        not isinstance(payload, dict)
        or str(payload.get("format_version", "")).split(".")[0] != "1"
        or not isinstance(payload.get("provider_schemas"), dict)
    ):
        raise RuntimeError("Terraform provider schema has an unsupported format")
    return payload


def terraform_show_json(
    infra_dir: Path,
    *,
    extra_env: dict[str, str] | None = None,
    initialize: bool = True,
    plan_file: Path | None = None,
    quiet: bool = False,
) -> dict[str, object]:
    """Render the current Terraform state or a saved plan as JSON."""
    terraform_bin = _require_terraform()
    if not infra_dir.exists():
        raise RuntimeError(f"Rendered infra directory does not exist: {infra_dir}")
    if initialize:
        if quiet:
            terraform_init(infra_dir, extra_env=extra_env, quiet=True)
        else:
            terraform_init(infra_dir, extra_env=extra_env)
    command = [terraform_bin, "show", "-json"]
    if plan_file is not None:
        resolved_plan, _plan_identity = _saved_plan_identity(
            infra_dir,
            plan_file,
            create=False,
        )
        relative_plan = resolved_plan.relative_to(infra_dir.resolve())
        command.append(str(relative_plan))
    stdout, stderr = _run_capture(
        command,
        cwd=infra_dir,
        timeout=120,
        extra_env=extra_env,
    )
    if stderr and not quiet:
        sys.stderr.write(stderr)
        if not stderr.endswith("\n"):
            sys.stderr.write("\n")
    try:
        payload = json.loads(stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Terraform show -json returned invalid JSON in {infra_dir}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"Terraform show -json returned a non-mapping payload in {infra_dir}")
    return payload
