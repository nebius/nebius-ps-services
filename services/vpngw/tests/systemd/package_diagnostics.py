"""Bounded, value-free observations of the unchanged package transport."""

from pathlib import Path


def loader_code(loader: str) -> str:
    return Path(__file__).read_text() + "\n_run(" + repr(loader) + ")\n"


def _run(loader: str) -> None:
    import collections
    import json
    import sys

    events = collections.deque(maxlen=16)
    previous = sys.gettrace()
    diagnostic_stream = sys.__stderr__
    functions = {"run", "apt_indexes", "stage", "verify_staged", "stage_payload"}
    exceptions = (
        TimeoutError,
        FileNotFoundError,
        PermissionError,
        RuntimeError,
        ValueError,
        OSError,
    )

    def trace(frame, event, arg):
        try:
            return observe(frame, event, arg)
        except Exception:
            return None  # Observation must not change the executed package path.

    def observe(frame, event, arg):
        if frame.f_code.co_filename != "<runtime-migration>":
            return None
        function = frame.f_code.co_name
        if function not in functions:
            return None
        frame.f_trace_lines = False
        exception = (
            next((kind.__name__ for kind in exceptions if isinstance(arg[1], kind)), None)
            if event == "exception"
            else None
        )
        if exception:
            observation = {
                "function": function,
                "line": frame.f_lineno,
                "exception": exception,
            }
            if function == "run":
                args = frame.f_locals.get("args")
                if type(args) is list and args and args[0] == "apt-get":
                    for command in ("update", "download"):
                        if command in args:
                            observation["phase"] = "apt-" + command
                            break
                child = frame.f_locals.get("child")
                code = getattr(child, "returncode", None)
                if type(code) is int:
                    observation["exit"] = code
            events.append(observation)
        return trace

    try:
        sys.settrace(trace)
        exec(compile(loader, "<qualification-loader>", "exec"), {"__name__": "__main__"})
    finally:
        sys.settrace(previous)
        if events:
            try:
                diagnostic_stream.write(
                    "VPNGW_PACKAGE_DIAGNOSTIC="
                    + json.dumps({"schema": "package-stage-diagnostic/v1", "events": list(events)})
                    + "\n"
                )
                diagnostic_stream.flush()
            except Exception:
                pass  # Diagnostic delivery must not replace the transport outcome.


def failure_diagnostic(stderr: bytes) -> str:
    """Accept only the bounded schema; never reflect arbitrary child stderr."""
    import json

    for line in stderr[-16384:].splitlines():
        if not line.startswith(b"VPNGW_PACKAGE_DIAGNOSTIC="):
            continue
        try:
            value = json.loads(line.partition(b"=")[2])
            if (
                set(value) != {"schema", "events"}
                or value["schema"] != "package-stage-diagnostic/v1"
            ):
                continue
            events = value["events"]
            if not isinstance(events, list) or not 0 < len(events) <= 16:
                continue
            for event in events:
                if (
                    not isinstance(event, dict)
                    or not {"function", "line", "exception"} <= set(event)
                    or set(event) - {"function", "line", "exception", "phase", "exit"}
                    or event["function"]
                    not in {"run", "apt_indexes", "stage", "verify_staged", "stage_payload"}
                    or event["exception"]
                    not in {
                        "RuntimeError",
                        "TimeoutError",
                        "FileNotFoundError",
                        "PermissionError",
                        "ValueError",
                        "OSError",
                    }
                    or type(event["line"]) is not int
                    or not 0 < event["line"] < 100000
                    or ("phase" in event and event["phase"] not in {"apt-update", "apt-download"})
                    or ("exit" in event and type(event["exit"]) is not int)
                ):
                    raise ValueError("invalid diagnostic event")
            return json.dumps(value, sort_keys=True)
        except (TypeError, ValueError, KeyError):
            continue
    return "unavailable"
