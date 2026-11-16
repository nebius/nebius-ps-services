#!/usr/bin/env python3
"""Execute a reviewed scenario adapter; the host agent owns scenario inference."""

from __future__ import annotations

import argparse
import hashlib
import json
import signal
import sys
import time
import types
from pathlib import Path

from admission import bind_dependencies, retain_dependencies, verify_dependencies
from live_state import Journal, StateError, digest


def duration(value):
    try:
        seconds = int(value[:-1]) * {"s": 1, "m": 60, "h": 3600}[value[-1]]
    except (ValueError, KeyError, IndexError):
        raise argparse.ArgumentTypeError(
            "use a positive integer with s, m or h"
        ) from None
    if seconds <= 0 or seconds > 43200:
        raise argparse.ArgumentTypeError("duration must be between 1s and 12h")
    return seconds


def state_root():
    support = Path(__file__).resolve().parents[2] / "global-context-management/scripts"
    if not (support / "agent_runtime.py").is_file():
        raise StateError("HOST_RUNTIME_REQUIRED")
    sys.path.insert(0, str(support))
    from agent_runtime import agent_home

    return agent_home() / "nebius-live-runs"


def validate_plan(plan):
    required = {
        "tenant",
        "project",
        "region",
        "engine",
        "source",
        "objective",
        "acceptance",
        "resources",
        "adapter",
        "adapter_sha256",
        "source_identity",
    }
    if not required <= set(plan) or plan["engine"] not in {"cxcli", "sdk"}:
        raise StateError("SCENARIO_INCOMPLETE")
    if not all(
        isinstance(plan[k], str) and plan[k]
        for k in required - {"resources", "acceptance", "source_identity"}
    ):
        raise StateError("SCENARIO_FIELDS")
    if not isinstance(plan["source_identity"], dict) or not plan["source_identity"]:
        raise StateError("SOURCE_IDENTITY_REQUIRED")
    if not plan["tenant"].startswith("tenant-") or not plan["project"].startswith(
        "project-"
    ):
        raise StateError("SCENARIO_SCOPE")
    if (
        not isinstance(plan["acceptance"], list)
        or not plan["acceptance"]
        or not all(isinstance(x, str) and x for x in plan["acceptance"])
    ):
        raise StateError("ACCEPTANCE_REQUIRED")
    if not isinstance(plan["resources"], list) or not plan["resources"]:
        raise StateError("RESOURCE_PLAN_REQUIRED")
    for resource in plan["resources"]:
        if not isinstance(resource, dict) or not all(
            resource.get(k) for k in ("kind", "owner", "count")
        ):
            raise StateError("RESOURCE_PLAN_INVALID")
        if (
            resource["owner"] not in {"sdk", "cxcli", "borrowed"}
            or type(resource["count"]) is not int
            or resource["count"] < 1
        ):
            raise StateError("RESOURCE_PLAN_INVALID")
    return plan


def load_adapter(plan):
    verify_dependencies(plan)
    path = Path(plan["adapter"])
    if path.is_symlink() or not path.is_file():
        raise StateError("ADAPTER_CHANGED")
    source = path.read_bytes()
    if hashlib.sha256(source).hexdigest() != plan["adapter_sha256"]:
        raise StateError("ADAPTER_CHANGED")
    module = types.ModuleType("live_test_adapter")
    module.__file__ = str(path)
    exec(compile(source, str(path), "exec"), module.__dict__)  # noqa: S102 - Execute exactly the reviewed privileged adapter bytes.
    for name in ("preflight", "provision", "verify", "reconcile", "cleanup"):
        if not callable(getattr(module, name, None)):
            raise StateError("ADAPTER_CONTRACT")
    return module


def pending_operations(journal):
    return any(
        o["status"] in {"intent", "accepted"}
        for o in journal.data["operations"].values()
    )


def bounded_call(callback, journal, deadline):
    if "dependency_sha256" in journal.data["plan"]:
        verify_dependencies(journal.data["plan"])
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise StateError("DEADLINE_EXCEEDED")
    old = signal.signal(
        signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError())
    )
    signal.setitimer(signal.ITIMER_REAL, remaining)
    try:
        result = callback(journal, deadline)
        if time.monotonic() >= deadline:
            raise StateError("DEADLINE_EXCEEDED")
        return result
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def execute(journal, adapter=None, *, cleanup_only=False):
    with journal.lock():
        if journal.data["cleanup_complete"]:
            return 0 if journal.data["evidence"].get("acceptance_passed") else 1
        if adapter is None:
            retain_dependencies(journal)
            adapter = load_adapter(journal.data["plan"])
        was_cleanup = journal.data["phase"] in {
            "cleanup",
            "cleanup_pending",
            "provisioning",
            "testing",
        } or bool(journal.data["resources"])
        plan = journal.data["plan"]
        failed = False
        journal.phase("cleanup" if cleanup_only or was_cleanup else "preflight")
        try:
            bounded_call(adapter.reconcile, journal, time.monotonic() + 120)
            if pending_operations(journal):
                raise StateError("OPERATIONS_UNRESOLVED")
            if not cleanup_only and not was_cleanup:
                deadline = time.monotonic() + plan.get("timeout", 3600)
                result = bounded_call(adapter.preflight, journal, deadline)
                if result == "awaiting_quota":
                    if journal.data["resources"] or pending_operations(journal):
                        raise StateError("QUOTA_WAIT_WITH_RESOURCES")
                    journal.phase("awaiting_quota")
                    return 3
                if result is not True:
                    raise StateError("PREFLIGHT_INCOMPLETE")
                journal.phase("provisioning")
                bounded_call(adapter.provision, journal, deadline)
                journal.phase("testing")
                if bounded_call(adapter.verify, journal, deadline) is not True:
                    raise StateError("ACCEPTANCE_FAILED")
                journal.evidence("acceptance_passed", True)
        except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001 - Cleanup and redact arbitrary adapter failures.
            failed = True
            journal.evidence("failure", type(exc).__name__)
        finally:
            if journal.data["phase"] != "awaiting_quota":
                journal.phase("cleanup")
                deadline = time.monotonic() + 1800
                try:
                    bounded_call(adapter.reconcile, journal, deadline)
                    if pending_operations(journal):
                        raise StateError("OPERATIONS_UNRESOLVED")
                    quiescent = bounded_call(adapter.cleanup, journal, deadline)
                    journal.finish_cleanup(writers_quiescent=quiescent is True)
                except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001 - Cleanup and redact arbitrary adapter failures.
                    journal.evidence("cleanup_failure", type(exc).__name__)
                    journal.phase("cleanup_pending")
        if not journal.data["cleanup_complete"]:
            return 2
        return (
            1 if failed or not journal.data["evidence"].get("acceptance_passed") else 0
        )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", nargs="?", default="run", choices=("plan", "run", "resume", "cleanup")
    )
    parser.add_argument("run_id", nargs="?")
    for flag in ("tenant", "project", "region", "source", "scenario", "cxcli-source"):
        parser.add_argument("--" + flag)
    parser.add_argument("--engine", choices=("auto", "cxcli", "sdk"))
    parser.add_argument("--timeout", type=duration)
    parser.add_argument("--quota-mode", choices=("request", "fail"))
    args = parser.parse_args(argv)
    if args.action in {"resume", "cleanup"}:
        import uuid

        if not args.run_id or str(uuid.UUID(args.run_id)) != args.run_id:
            raise StateError("RUN_ID_REQUIRED")
        if any(
            value is not None
            for value in (
                args.tenant,
                args.project,
                args.region,
                args.scenario,
                args.source,
                args.cxcli_source,
                args.engine,
                args.timeout,
                args.quota_mode,
            )
        ):
            raise StateError("RESUME_SCOPE_IMMUTABLE")
        journal = Journal(state_root() / args.run_id)
    else:
        if not args.tenant or not args.project or not args.scenario or args.run_id:
            parser.error(
                "--tenant, --project and a normalized --scenario file are required"
            )
        plan = json.loads(Path(args.scenario).read_text())
        if plan.get("tenant") != args.tenant or plan.get("project") != args.project:
            raise StateError("SCOPE_MISMATCH")
        for key, value in (
            ("region", args.region),
            ("source", args.source),
            ("cxcli_source", args.cxcli_source),
        ):
            if value is not None:
                if key in plan and plan[key] != value:
                    raise StateError("SCENARIO_CONFLICT")
                plan[key] = value
        if args.engine not in {None, "auto"} and plan.get("engine") != args.engine:
            raise StateError("ENGINE_MISMATCH")
        plan.update(timeout=args.timeout or 3600, quota_mode=args.quota_mode or "fail")
        validate_plan(plan)
        if args.action == "plan":
            print(
                json.dumps(
                    {
                        "plan_sha256": digest(plan),
                        "engine": plan["engine"],
                        "resources": plan["resources"],
                        "cloud_effects": False,
                    }
                )
            )
            return 0
        bind_dependencies(plan)
        journal = Journal.create(state_root(), plan)
    print(
        json.dumps({"run_id": journal.data["run_id"], "phase": journal.data["phase"]}),
        flush=True,
    )
    old = signal.signal(
        signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt())
    )
    try:
        code = execute(journal, cleanup_only=args.action == "cleanup")
    finally:
        signal.signal(signal.SIGTERM, old)
    print(
        json.dumps(
            {
                "run_id": journal.data["run_id"],
                "phase": journal.data["phase"],
                "cleanup_complete": journal.data["cleanup_complete"],
                "acceptance_passed": journal.data["evidence"].get(
                    "acceptance_passed", False
                ),
            }
        )
    )
    return code


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (Exception, KeyboardInterrupt) as error:  # noqa: BLE001 - Do not expose provider diagnostics.
        print(
            json.dumps(
                {
                    "error": str(error)
                    if isinstance(error, StateError)
                    else type(error).__name__
                }
            ),
            file=sys.stderr,
        )
        sys.exit(2)
