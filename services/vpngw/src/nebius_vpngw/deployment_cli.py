"""One outer boundary for installed, module, Click and pair invocations."""

from __future__ import annotations

import functools
import inspect
import re
import sys
from pathlib import Path
from typing import Any

import click

from .deploy.recovery import CURRENT, Invocation, advice


def invocation(args: list[str]) -> Invocation | None:
    """Recover selectors even when Click cannot finish parsing the command.

    This is diagnostic context only: it never validates or authorizes effects.
    Do not include unknown options or their values in advice.
    """
    command = None
    values: dict[str, str] = {}
    flags: set[str] = set()
    resume: list[str] = []
    selectors = {
        "--local-config-file": "config",
        "-c": "config",
        "--project-id": "project",
        "--region": "region",
        "--ssh-identity": "ssh_identity",
        "--sa": "sa",
        "--output-format": "output_format",
    }
    consumes = {
        "--output",
        "-o",
        "--approve",
        "--approve-disruption",
        "--approve-a",
        "--approve-b",
        "--timeout",
        "--resume-replacement",
        "--standby-auto-healing",
        "--approve-vm-ha-migration",
        "--recover-vm-ha-migration",
        "--replace-failed-vm-ha-passive",
    }
    resume_flags = {
        "--dry-run",
        "--rotate-mtls",
        "--convert-to-ha",
        "--recreate-gw",
        "--prepare-vm-ha-peer-rotation",
        "--force",
        "-f",
        "--prepare-network",
    }
    index = 0
    while index < len(args):
        token = args[index]
        if token == "--":
            break
        key, equal, value = token.partition("=")
        if token.startswith("-c") and not token.startswith("--") and len(token) > 2:
            key, equal, value = "-c", "=", token[2:].removeprefix("=")
        if key in selectors or key in consumes:
            if not equal and index + 1 < len(args) and not args[index + 1].startswith("-"):
                index += 1
                value = args[index]
            if key in selectors and value:
                values[selectors[key]] = value
            elif key in consumes and value:
                valid = (
                    len(value) <= 4
                    and value.isascii()
                    and value.isdecimal()
                    and 300 <= int(value) <= 7200
                    if key == "--timeout"
                    else value in {"enabled", "disabled"}
                    if key == "--standby-auto-healing"
                    else re.fullmatch(r"[a-f0-9]{64}", value) is not None
                    if "approve" in key
                    or key in {"--recover-vm-ha-migration", "--replace-failed-vm-ha-passive"}
                    else True
                )
                if valid:
                    resume += [key, value]
        elif token.startswith("-"):
            flags.add(token)
            if token in resume_flags:
                resume.append(token)
        elif command is None:
            command = token
        elif command == "pair":
            command = "pair " + token
        elif command == "pair apply" and "config" not in values:
            values["config"] = token
        index += 1
    if command not in {"apply", "pair apply"}:
        return None
    return Invocation(
        command,
        Path(
            values.get(
                "config", "pair.json" if command == "pair apply" else "nebius-vpngw.config.yaml"
            )
        ),
        project=values.get("project"),
        region=values.get("region"),
        ssh_identity=values.get("ssh_identity"),
        sa=values.get("sa"),
        repair="--repair" in flags,
        dry_run="--dry-run" in flags,
        structured=command == "pair apply" or values.get("output_format", "").lower() == "json",
        resume_options=tuple(resume),
    )


def emit_failure(context: Invocation, error: BaseException | None) -> None:
    """Publish one complete diagnostic, including early parser/lock failures."""
    if context.emitted:
        return
    recovery = advice(context, error)
    if context.command == "pair apply":
        import json

        click.echo(json.dumps({"phase": "failed", "recovery": recovery.mapping()}, sort_keys=True))
    elif context.command == "apply":
        import json

        from .deploy import pair_phases
        from .deploy.apply_result import ApplyReport

        report = ApplyReport(dry_run=context.dry_run)
        report.failed(error)
        report.recovery = recovery
        report.next_command = recovery.resume_command
        pair_phases.report(report)
        click.echo(
            json.dumps(report.mapping(), sort_keys=True) if context.structured else recovery.text()
        )
    context.emitted = True


def invoke(call: Any, ctx: Any) -> Any:
    """Retain typed causes before Click converts an Exit into an integer."""
    try:
        return call(ctx)
    except (Exception, KeyboardInterrupt, SystemExit) as error:
        context = CURRENT.get()
        code = (
            error.exit_code
            if isinstance(error, click.exceptions.Exit)
            else error.code
            if isinstance(error, SystemExit)
            else None
        )
        if context is not None and code != 0:
            emit_failure(context, error)
        raise


def main(invoke: Any, args: Any, kwargs: dict) -> Any:
    """Click owns parsing, context cleanup and its native exit conversions.

    Click returns an Exit's integer in non-standalone mode. Never discard that
    return value (notably in pair children), or infer success from no exception.
    """
    selected = list(args if args is not None else sys.argv[1:])
    context = invocation(selected)
    if context is None or CURRENT.get() is not None:
        return invoke(args=args, **kwargs)
    standalone = kwargs.pop("standalone_mode", True)
    token = CURRENT.set(context)
    try:
        # Suppress Click's raw parameter rendering, which can reflect secret
        # values. Preserve its integer Exit return and public process codes.
        result = invoke(args=args, standalone_mode=False, **kwargs)
        if isinstance(result, int) and result != 0 and not context.emitted:
            emit_failure(context, None)
        if standalone:
            raise SystemExit(result if isinstance(result, int) else 0)
        return result
    except click.ClickException as error:
        emit_failure(context, error)
        if standalone:
            raise SystemExit(error.exit_code) from None
        raise
    except click.Abort as error:
        emit_failure(context, error)
        if standalone:
            raise SystemExit(1) from None
        raise
    except (SystemExit, click.exceptions.Exit) as error:
        code = error.code if isinstance(error, SystemExit) else error.exit_code
        if code and not context.emitted:
            emit_failure(context, error)
        if (
            standalone
            and isinstance(error, SystemExit)
            and code is not None
            and not isinstance(code, int)
        ):
            # Python prints a non-integer SystemExit value verbatim. Keep its
            # native failure status without reflecting an arbitrary payload.
            raise SystemExit(1) from None
        raise
    except (Exception, KeyboardInterrupt) as error:
        emit_failure(context, error)
        if standalone:
            raise SystemExit(130 if isinstance(error, KeyboardInterrupt) else 1) from None
        raise
    finally:
        CURRENT.reset(token)
        context.preparation.close()


def callback(invoke: Any, command: str):
    """Give direct Python wrappers the same diagnostic context as Click."""
    signature = inspect.signature(invoke)

    @functools.wraps(invoke)
    def wrapped(*args, **kwargs):
        values = signature.bind_partial(*args, **kwargs).arguments
        context = CURRENT.get()
        token = None
        if context is None:
            path = values.get("local_config_file")
            context = Invocation(
                command,
                Path(path) if isinstance(path, (str, Path)) else Path("nebius-vpngw.config.yaml"),
            )
            token = CURRENT.set(context)
        for source, target in (("project_id", "project"), ("region", "region"), ("sa", "sa")):
            if isinstance(values.get(source), str):
                setattr(context, target, values[source])
        context.repair = values.get("repair") is True
        context.dry_run = values.get("dry_run") is True
        if values.get("output_format") is not None:
            context.structured = values["output_format"] == "json"
        try:
            return invoke(*args, **kwargs)
        except (Exception, KeyboardInterrupt) as error:
            if isinstance(error, click.exceptions.Exit) and error.exit_code == 0:
                raise
            emit_failure(context, error)
            if advice(context, error).reason_code in {
                "authentication-or-provider-unavailable",
                "operation-timeout",
            }:
                raise click.exceptions.Exit(1) from error
            raise
        finally:
            if token is not None:
                CURRENT.reset(token)
                context.preparation.close()

    return wrapped
