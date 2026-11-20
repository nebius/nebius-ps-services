"""Private unittest inventory and bounded process scheduler for both verifiers.

The caller owns prerequisite admission, frozen catalog identity and publication.
Children never publish reports. Exact test IDs survive interruption; a partial
group never becomes successful through an early passing capability test.
"""
from __future__ import annotations

import argparse
import builtins
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from dataclasses import dataclass
import importlib.util
import json
from pathlib import Path
import re
import sys
import threading
import time
import unittest


PREFIX = b"WORKFLOW_TEST_EVENT "
CATALOG = Path(__file__).resolve().parents[2]
EXCEPTION_NAMES = {value: value.__name__ for value in vars(builtins).values()
                   if isinstance(value, type) and issubclass(value, BaseException)}
AUDITED = frozenset({
    "implement-test/scripts/test_app_objective.py",
    "implement-test/scripts/test_implement_reporting.py",
    "implement-test/scripts/test_implement_catalog.py",
    "sdlc-workflow-test/scripts/test_three_tier_objective.py",
    "sdlc-workflow-test/scripts/test_three_tier_statistics.py",
    "worktree/scripts/test_workflow_overhead.py",
})


def source_module(name, path):
    """Import a same-catalog owner under its canonical name, rejecting conflicts."""
    path = Path(path).resolve(strict=True)
    previous = sys.modules.get(name)
    if previous is not None:
        if Path(getattr(previous, "__file__", "")).resolve() != path:
            raise ImportError("conflicting verification owner: " + name)
        return previous
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError("verification owner is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[name]
        raise
    return module


@dataclass(frozen=True)
class Group:
    id: str
    suite: str
    arguments: tuple[str, ...]
    cwd: Path
    parallel_safe: bool = False


def groups_for_commands(commands, catalog):
    """Preserve discovery coverage; split only explicitly audited modules."""
    groups = []
    catalog = Path(catalog).resolve()
    for suite, command, cwd in commands:
        args = list(command[1:])
        if args[:2] == ["-m", "unittest"]:
            args = args[2:]
        args = [a for a in args if a not in {"-v", "-q", "--buffer", "-b"}]
        if args and args[0] == "discover":
            start = Path(args[args.index("-s") + 1]) if "-s" in args else Path(cwd)
            pattern = args[args.index("-p") + 1] if "-p" in args else "test*.py"
            # Keep unittest discovery itself as the collection authority. Only
            # a flat directory can be split without changing package semantics.
            files = sorted(start.glob(pattern))
            flat = not any(p.is_dir() and (p / "__init__.py").exists() for p in start.iterdir())
            if flat and files:
                args = [str(p.resolve()) for p in files]
        audited, remaining = [], []
        for argument in args:
            path = (Path(cwd) / argument).resolve()
            relative = str(path.relative_to(catalog)) if path.is_relative_to(catalog) else ""
            if relative in AUDITED:
                audited.append((argument, relative))
            else:
                remaining.append(argument)
        for argument, relative in audited:
            groups.append(Group(suite + ":" + relative, suite, (argument,), Path(cwd), True))
        if remaining:
            groups.append(Group(suite + ":exclusive", suite, tuple(remaining), Path(cwd)))
    if len({g.id for g in groups}) != len(groups):
        raise ValueError("duplicate verification group")
    return groups


def _tests(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from _tests(test)
        else:
            yield test


def _emit(value):
    sys.__stdout__.write(PREFIX.decode() + json.dumps(value, sort_keys=True) + "\n")
    sys.__stdout__.flush()


def failure_diagnostic(error):
    """Retain locations and built-in types, never messages, source or locals."""
    chain, seen = [], set()
    current, trace = error[1], error[2]
    while current is not None and id(current) not in seen and len(chain) < 4:
        seen.add(id(current))
        frames = []
        while trace is not None:
            try:
                path = Path(trace.tb_frame.f_code.co_filename).resolve(strict=True)
                relative = path.relative_to(CATALOG).as_posix()
                if (path.is_file() and path.suffix == ".py" and len(relative) <= 512
                        and re.fullmatch(r"[A-Za-z0-9_./-]+", relative)):
                    frames.append({"path": relative, "line": trace.tb_lineno})
            except (OSError, ValueError):
                pass  # External/generated frames are not safe source references.
            trace = trace.tb_next
        chain.append({"exception": EXCEPTION_NAMES.get(type(current), "custom"), "frames": frames[-8:]})
        current = current.__cause__ or (None if current.__suppress_context__ else current.__context__)
        trace = current.__traceback__ if current is not None else None
    return chain


def valid_diagnostic(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 4:
        return False
    for entry in value:
        if (not isinstance(entry, dict) or set(entry) != {"exception", "frames"}
                or not isinstance(entry["exception"], str)
                or entry["exception"] not in {*EXCEPTION_NAMES.values(), "custom"}
                or not isinstance(entry["frames"], list) or len(entry["frames"]) > 8):
            return False
        for frame in entry["frames"]:
            if (not isinstance(frame, dict) or set(frame) != {"path", "line"}
                    or not isinstance(frame["path"], str) or len(frame["path"]) > 512
                    or re.fullmatch(r"[A-Za-z0-9_./-]+\.py", frame["path"]) is None
                    or Path(frame["path"]).is_absolute() or ".." in Path(frame["path"]).parts
                    or type(frame["line"]) is not int or not 1 <= frame["line"] <= 10_000_000):
                return False
    return True


class EventResult(unittest.TestResult):
    def __init__(self, identities):
        super().__init__()
        self.identities = identities
        self.outcomes = {}
        self.diagnostics = {}

    def addSuccess(self, test):
        super().addSuccess(test)
        self.outcomes[self.identities[id(test)]] = "passed"

    def addFailure(self, test, error):
        self.failures.append((test, None))
        self.outcomes[self.identities[id(test)]] = "failed"
        self.diagnostics.setdefault(self.identities[id(test)], failure_diagnostic(error))

    def addError(self, test, error):
        self.errors.append((test, None))
        self.outcomes[self.identities[id(test)]] = "error"
        self.diagnostics.setdefault(self.identities[id(test)], failure_diagnostic(error))

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.outcomes[self.identities[id(test)]] = "skipped"

    def addExpectedFailure(self, test, error):
        self.expectedFailures.append((test, None))
        self.outcomes[self.identities[id(test)]] = "expected_failure"
        self.diagnostics.setdefault(self.identities[id(test)], failure_diagnostic(error))

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self.outcomes[self.identities[id(test)]] = "unexpected_success"

    def addSubTest(self, test, subtest, error):
        super().addSubTest(test, subtest, error)
        if error is not None:
            self.outcomes[self.identities[id(test)]] = "failed"
            self.diagnostics.setdefault(self.identities[id(test)], failure_diagnostic(error))

    def stopTest(self, test):
        super().stopTest(test)
        identity = self.identities.pop(id(test))
        _emit({"event": "test", "id": identity, "outcome": self.outcomes.get(identity, "error"),
               "diagnostic": self.diagnostics.get(identity)})


class EventRunner:
    def __init__(self, *, inventory=False):
        self.inventory = inventory

    def run(self, suite):
        # Fixture setup may overwrite TestCase.id; bind callbacks to the exact
        # collected objects before any test can mutate its attributes.
        tests = list(_tests(suite))
        identities = {id(test): test.id() for test in tests}
        ids = [identities[id(test)] for test in tests]
        del tests
        if not ids or len(ids) != len(set(ids)) or any("_FailedTest" in name for name in ids):
            raise ValueError("test inventory is empty, duplicated or uncollectable")
        _emit({"event": "inventory", "ids": ids})
        result = EventResult(identities)
        if not self.inventory:
            suite.run(result)
            _emit({"event": "complete", "successful": result.wasSuccessful(), "count": result.testsRun})
        return result


class Events:
    def __init__(self):
        self.buffer = bytearray()
        self.discard = False
        self.inventory = None
        self.outcomes = {}
        self.diagnostics = {}
        self.complete = None
        self.invalid = False

    def feed(self, chunk):
        for index, part in enumerate(chunk.split(b"\n")):
            if index:
                if not self.discard:
                    self.line(bytes(self.buffer))
                self.buffer.clear()
                self.discard = False
            if not self.discard:
                if len(self.buffer) + len(part) > 4 * 1024 * 1024:
                    if self.buffer.startswith(PREFIX):
                        self.invalid = True
                    self.buffer.clear()
                    self.discard = True
                else:
                    self.buffer.extend(part)

    def line(self, line):
        if not line.startswith(PREFIX):
            return
        try:
            value = json.loads(line[len(PREFIX):])
            if value["event"] == "inventory":
                ids = value["ids"]
                if (self.inventory is not None or not isinstance(ids, list) or not ids or len(ids) > 20000
                        or any(not isinstance(i, str) or len(i) > 1024 for i in ids)
                        or len(ids) != len(set(ids))):
                    raise ValueError()
                self.inventory = ids
            elif value["event"] == "test":
                name, outcome = value["id"], value["outcome"]
                diagnostic = value["diagnostic"]
                if (self.complete is not None or self.inventory is None or name not in self.inventory or name in self.outcomes
                        or outcome not in {"passed", "failed", "error", "skipped", "expected_failure", "unexpected_success"}):
                    raise ValueError()
                if outcome in {"failed", "error", "expected_failure"}:
                    if not valid_diagnostic(diagnostic):
                        raise ValueError()
                    self.diagnostics[name] = diagnostic
                elif diagnostic is not None:
                    raise ValueError()
                self.outcomes[name] = outcome
            elif value["event"] == "complete":
                if (self.complete is not None or type(value["successful"]) is not bool or type(value["count"]) is not int
                        or self.inventory is None or set(self.outcomes) != set(self.inventory)
                        or value["count"] != len(self.inventory)):
                    raise ValueError()
                self.complete = value
            else:
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            self.invalid = True


def _run(group, env, *, inventory=False, cancel=None):
    owner = source_module("workflow_process", Path(__file__).with_name("workflow_process.py"))
    events = Events()
    argv = [sys.executable, str(Path(__file__).resolve()), "--inventory" if inventory else "--execute", "--", *group.arguments]
    try:
        result = owner.run_owned(argv, cwd=group.cwd, env=env, on_stdout=events.feed,
                                 policy={"observation_interval_seconds": 1, "deadline": None},
                                 control=lambda sample: "cancelled" if cancel is not None and cancel.is_set() else "continue")
    except BaseException as error:
        result = getattr(error, "observation", {"local_group_quiescent": False})
        status = ("EXECUTION_UNPROVEN" if not result.get("local_group_quiescent")
                  else "START_FAILED" if isinstance(error, OSError) else "INTERRUPTED" if isinstance(error, KeyboardInterrupt) else "FAIL")
        return {"id": group.id, "suite": group.suite, "status": status,
                "inventory": events.inventory, "outcomes": events.outcomes,
                "diagnostics": events.diagnostics, "observation": result}
    valid = not events.invalid and not events.buffer and events.inventory is not None
    if not inventory:
        valid = (valid and events.complete is not None and events.complete["successful"]
                 and events.complete["count"] == len(events.inventory)
                 and set(events.outcomes) == set(events.inventory))
    status = "PASS" if valid and result["exit_code"] == 0 else "FAIL"
    if result["stop_reason"] == "cancelled":
        status = "INTERRUPTED"
    if not (result.get("local_group_quiescent") is True
            and all(result.get(key, {}).get("complete") is True for key in ("stdout", "stderr"))):
        status = "EXECUTION_UNPROVEN"
    return {"id": group.id, "suite": group.suite, "status": status,
            "inventory": events.inventory, "outcomes": events.outcomes,
            "diagnostics": events.diagnostics, "observation": result}


def schedule(groups, env, *, on_result=None, admission=None, validate_inventory=None, memory_probe=None,
             on_checkpoint=None, observation_interval_seconds=30):
    """Freeze exact collection, then admit at most two audited independent groups."""
    frozen = {}
    collection = {}
    results = {}
    cancelled = threading.Event()
    if memory_probe is None:
        memory = source_module("host_memory", Path(__file__).resolve().parents[2] / "global-context-management/scripts/host_memory.py")
        memory_probe = memory.sample
    memory_status = None
    sampled_at = 0
    started = time.monotonic()
    next_checkpoint = started + observation_interval_seconds

    def checkpoint(phase, active):
        nonlocal next_checkpoint
        now = time.monotonic()
        if on_checkpoint and now >= next_checkpoint:
            on_checkpoint({"phase": phase, "active": active, "completed_groups": len(results),
                           "elapsed_seconds": round(now - started, 3)})
            next_checkpoint = now + observation_interval_seconds

    def headroom():
        nonlocal sampled_at, memory_status
        now = time.monotonic()
        if now - sampled_at >= 1:
            status = memory_probe()["status"]
            if status != memory_status and status != "normal":
                messages = {"warning": "Memory headroom is low; verification continues above 2 GiB.",
                            "critical": "The host is running out of memory; I stopped my work to protect the remaining headroom.",
                            "unknown": "Host memory measurement is unavailable; heavy verification is paused."}
                print(messages.get(status, messages["unknown"]), flush=True)
            memory_status, sampled_at = status, now
        if memory_status in {"critical", "unknown"}:
            cancelled.set()
            return False
        return memory_status in {"normal", "warning"}

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="verification-inventory") as collectors:
        for group in groups:
            if not headroom():
                break
            collecting = collectors.submit(_run, group, env, inventory=True, cancel=cancelled)
            try:
                while not collecting.done():
                    headroom()
                    checkpoint("collection", [group.id])
                    wait([collecting], timeout=.1)
            except KeyboardInterrupt:
                cancelled.set()
                results[group.id] = collecting.result()
                break
            except BaseException:
                cancelled.set()
                collected = collecting.result()
                if on_result:
                    on_result({**collected, "collection_observation": collected["observation"]})
                raise
            collected = collecting.result()
            collection[group.id] = collected["observation"]
            if on_result:
                on_result({**collected, "status": "NOT_RUN" if collected["status"] == "PASS" else collected["status"],
                           "observation": None, "collection_observation": collected["observation"]})
            if collected["status"] != "PASS":
                results[group.id] = collected
                cancelled.set()
                break
            frozen[group.id] = collected["inventory"]
    if not cancelled.is_set() and validate_inventory:
        validate_inventory(frozen)
    queue = sorted(groups, key=lambda group: not group.parallel_safe)
    queue_started = time.monotonic()
    queue_waits = {}
    active = {}
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="verification-group") as pool:
        try:
            while queue or active:
                headroom()
                checkpoint("execution", [g.id for g in active.values()])
                while queue and not cancelled.is_set() and len(active) < 2:
                    group = queue[0]
                    if active and (not group.parallel_safe or any(not g.parallel_safe for g in active.values())):
                        break
                    if admission is not None and not admission():
                        break
                    queue.pop(0)
                    queue_waits[group.id] = time.monotonic() - queue_started
                    active[pool.submit(_run, group, env, cancel=cancelled)] = group
                    if not group.parallel_safe:
                        break
                if not active:
                    if cancelled.is_set():
                        break
                    time.sleep(.1)
                    continue
                finished, _ = wait(active, timeout=.25, return_when=FIRST_COMPLETED)
                for future in finished:
                    group = active.pop(future)
                    result = future.result()
                    if result["inventory"] != frozen[group.id]:
                        result["status"] = "FAIL"
                    results[group.id] = result
                    result["collection_observation"] = collection.get(group.id)
                    result["queue_wait_seconds"] = queue_waits.get(group.id)
                    if result["status"] != "PASS":
                        cancelled.set()
                    if on_result:
                        on_result(result)
        except KeyboardInterrupt:
            cancelled.set()
            for future, group in active.items():
                results[group.id] = future.result()
        except BaseException:
            cancelled.set()
            for future, group in active.items():
                result = future.result()
                if on_result:
                    on_result(result)
            raise
    for group in groups:
        results.setdefault(group.id, {"id": group.id, "suite": group.suite, "status": "NOT_RUN",
                                      "inventory": frozen.get(group.id), "outcomes": {},
                                      "diagnostics": {}, "observation": None})
        results[group.id]["collection_observation"] = collection.get(group.id)
        results[group.id]["queue_wait_seconds"] = queue_waits.get(group.id)
        results[group.id]["timing_clock"] = "time.monotonic; same-process durations"
        if on_result:
            on_result(results[group.id])
    return [results[group.id] for group in groups]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--inventory", action="store_true")
    mode.add_argument("--execute", action="store_true")
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    arguments = args.arguments[1:] if args.arguments[:1] == ["--"] else args.arguments
    # unittest owns filename/discovery semantics; the wrapper only observes its
    # collected IDs and native result callbacks. No custom test selection rules.
    runner = EventRunner(inventory=args.inventory)
    if arguments and all(Path(argument).is_file() and argument.endswith(".py") for argument in arguments):
        suite = unittest.TestSuite()
        for argument in arguments:
            path = Path(argument).resolve()
            # Direct script suites include hyphenated filenames that unittest
            # discovery deliberately ignores. Load each explicitly frozen file
            # with its own script import directory, preserving that coverage.
            if str(path.parent) not in sys.path:
                sys.path.insert(0, str(path.parent))
            module = source_module(path.stem.replace("-", "_"), path)
            suite.addTests(unittest.TestLoader().loadTestsFromModule(module))
        result = runner.run(suite)
    else:
        program = unittest.TestProgram(module=None, argv=["workflow-verification", *arguments],
                                       testRunner=runner, exit=False)
        result = program.result
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
