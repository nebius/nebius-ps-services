"""Supervised native-job publication with ordered delivery and explicit recovery.

Original-evidence selection and prepared-root storage have separate owners.
Optional client dependencies are loaded only after workload execution.
"""

from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from course_publication_state import atomic, authority, locked, read, register, settings
from course_publication_evidence import candidates, freeze, payload


def prepare_push(candidate, rows, identity, generation):
    """Complete all local client/registry work before journaling a network attempt."""
    from prometheus_client import CollectorRegistry, Gauge, push_to_gateway

    registry, gauges = CollectorRegistry(), {}
    prefix = "course_" + candidate["channel"] + "_"
    course = Path(candidate["snapshot"]).parents[4]
    recipe = read(course / "reference/observability.json")["labs"][
        candidate["labels"]["lab"]
    ]
    units = {metric["name"]: metric.get("unit", "none") for metric in recipe["metrics"]}
    for name, role, trial, case, number in rows:
        if name not in gauges:
            gauges[name] = Gauge(
                prefix + name,
                "Completed lab evidence: " + name,
                ["role", "trial", "case", "measurement", "unit"],
                registry=registry,
            )
        gauges[name].labels(
            role, trial, case, name.removeprefix("control_"), units.get(name, "none")
        ).set(number)
    for name, number in (
        ("generation", generation),
        ("job_id", candidate["job_id"]),
        ("ended_at_seconds", candidate["ended_at_seconds"]),
        ("published_at_seconds", time.time()),
    ):
        Gauge(prefix + name, "Publication " + name, registry=registry).set(number)
    return lambda: push_to_gateway(
        identity["binding"]["gateway"],
        job="course_lab_" + candidate["channel"],
        grouping_key=candidate["labels"],
        registry=registry,
        timeout=5,
    )


def visible(candidate, identity, generation, attempted_at):
    labels = ",".join(f'{k}="{v}"' for k, v in candidate["labels"].items())
    prefix = "course_" + candidate["channel"] + "_"
    metric = f"{prefix}generation{{{labels}}}"
    query = f"({metric} == {generation}) and (timestamp({metric}) >= {attempted_at:.6f}) and ({prefix}job_id{{{labels}}} == {candidate['job_id']})"
    url = (
        identity["binding"]["metrics_url"]
        + "/api/v1/query?"
        + urllib.parse.urlencode({"query": query})
    )
    with urllib.request.urlopen(url, timeout=5) as response:
        data = json.loads(response.read(2 * 1024 * 1024))
    values = data.get("data", {}).get("result", [])
    return (
        data.get("status") == "success"
        and len(values) == 1
        and float(values[0]["value"][1]) == generation
    )


def save_offer(course, directory, candidate):
    payload(course, candidate)
    path = directory / "offers" / (candidate["key"] + ".json")
    # This short local lock is independent of the potentially stalled transport.
    with locked(path.with_suffix(".lock"), timeout=1):
        previous = read(path) if path.exists() else None
        if (
            previous is None
            and candidate["key"] in read(directory / "groups.json")["keys"]
        ):
            raise ValueError("Known publication candidate state is missing")
        if (
            previous
            and previous["sequence"] == candidate["sequence"]
            and previous != candidate
        ):
            raise ValueError("Conflicting publication candidate for the same run")
        if not previous or candidate["sequence"] > previous["sequence"]:
            atomic(path, candidate)


def offer(course, directory, identity, candidate, *, expected=None, attest=False):
    save_offer(course, directory, candidate)
    try:
        return send_offer(
            course, directory, identity, candidate, expected=expected, attest=attest
        )
    except BlockingIOError:
        return {"key": candidate["key"], "status": "queued"}


def send_offer(course, directory, identity, candidate, *, expected=None, attest=False):
    key = candidate["key"]
    state_path = directory / "groups" / (key + ".json")
    with locked(directory / "groups.lock", timeout=1):
        index = read(directory / "groups.json")
        if key not in index["keys"]:
            if expected is not None or state_path.exists():
                raise ValueError("Publication group registry differs")
            index["keys"].append(key)
            atomic(directory / "groups.json", index)
            atomic(state_path, {"generation": 0, "status": "new"})
        elif not state_path.exists():
            raise ValueError(
                "Publication group state is missing; cannot reset its generation"
            )
    with locked(directory / "groups" / (key + ".lock")):
        state = (
            read(state_path)
            if state_path.exists()
            else {"generation": 0, "status": "new"}
        )
        if expected is not None and (
            state["generation"] != expected
            or state["status"] not in {"sending", "paused"}
        ):
            raise ValueError("Recovery generation or paused state changed")
        # Validation precedes registering an eligible candidate. A failed newer
        # result must not supersede an earlier correct benchmark.
        selected = read(directory / "offers" / (key + ".json"))
        for prior in (state.get("candidate"), state.get("sent")):
            if prior and (
                selected["sequence"] < prior["sequence"]
                or (selected["sequence"] == prior["sequence"] and selected != prior)
            ):
                raise ValueError(
                    "Publication candidate is older than durable ordering state"
                )
        state["candidate"] = selected
        paused = state["status"] in {"sending", "paused"}
        if paused and not attest:
            state["status"] = "paused"
            atomic(state_path, state)
            return {"key": key, "status": "paused", "generation": state["generation"]}
        selected = state["candidate"]
        if candidate["sequence"] < selected["sequence"] and not attest:
            return {
                "key": key,
                "status": "superseded",
                "generation": state["generation"],
            }
        if (
            state.get("sent") == selected
            and state["status"] in {"acknowledged", "confirmed"}
            and not attest
        ):
            return {
                "key": key,
                "status": "confirmed"
                if state["status"] == "confirmed"
                else "unconfirmed",
                "generation": state["generation"],
            }
        rows = payload(course, selected)
        generation = state["generation"] + 1
        send = prepare_push(selected, rows, identity, generation)
        state.update(
            generation=generation,
            status="sending",
            sent=selected,
            attempted_at=time.time(),
        )
        if attest:
            state["quiescence_attestation"] = {
                "previous_generation": expected,
                "at": time.time(),
            }
        atomic(state_path, state)
        try:
            send()
        except (OSError, ValueError, RuntimeError):
            state["status"] = "paused"
            atomic(state_path, state)
            return {"key": key, "status": "paused", "generation": state["generation"]}
        state["status"] = "acknowledged"
        atomic(state_path, state)
        return {"key": key, "status": "unconfirmed", "generation": state["generation"]}


def confirm(directory, identity, outcomes, deadline):
    while time.monotonic() < deadline and any(
        row["status"] == "unconfirmed" for row in outcomes
    ):
        for outcome in outcomes:
            if outcome["status"] != "unconfirmed" or time.monotonic() >= deadline:
                continue
            path = directory / "groups" / (outcome["key"] + ".json")
            try:
                with locked(path.with_suffix(".lock")):
                    state = read(path)
                    if state["generation"] != outcome["generation"]:
                        outcome["status"] = "superseded"
                    elif state["status"] in {"acknowledged", "confirmed"} and visible(
                        state["sent"],
                        identity,
                        state["generation"],
                        state["attempted_at"],
                    ):
                        state["status"] = "confirmed"
                        atomic(path, state)
                        outcome["status"] = "confirmed"
            except (OSError, ValueError, KeyError):
                pass
        if any(row["status"] == "unconfirmed" for row in outcomes):
            time.sleep(min(1, max(0, deadline - time.monotonic())))


def publish_job(course, job_path, *, timeout=35):
    directory, identity = authority(course)
    path, snapshot = freeze(course, job_path, identity, directory)
    outcomes = []
    deadline = time.monotonic() + timeout
    pending = candidates(course, path, snapshot, identity)
    ready = []
    for candidate in pending:
        try:
            save_offer(course, directory, candidate)
            ready.append(candidate)
        except (OSError, ValueError, KeyError, ImportError):
            outcomes.append(
                {
                    "key": candidate["key"],
                    "channel": candidate["channel"],
                    "status": "failed",
                    "reason": "Invalid evidence or unavailable candidate state",
                }
            )
    for candidate in ready:
        try:
            outcome = offer(course, directory, identity, candidate)
            while outcome["status"] == "queued" and time.monotonic() < deadline:
                time.sleep(min(0.1, max(0, deadline - time.monotonic())))
                outcome = offer(course, directory, identity, candidate)
            outcomes.append(outcome)
        except (OSError, ValueError, KeyError, ImportError) as exc:
            outcomes.append(
                {
                    "key": candidate["key"],
                    "channel": candidate["channel"],
                    "status": "failed",
                    "reason": type(exc).__name__
                    + ": invalid evidence, unavailable state or publication dependency",
                }
            )
    confirm(directory, identity, outcomes, deadline)
    statuses = {row["status"] for row in outcomes}
    status = next(
        (
            name
            for name in (
                "paused",
                "failed",
                "queued",
                "unconfirmed",
                "confirmed",
                "superseded",
            )
            if name in statuses
        ),
        "skipped",
    )
    return {
        "status": status,
        "groups": outcomes,
        "registration": snapshot["registration"],
    }


def resume(course, key, expected, attest, timeout=35):
    if not attest or expected is None or not re.fullmatch(r"[0-9a-f]{32}", key):
        raise ValueError(
            "Recovery requires a key, expected generation and explicit quiescence attestation"
        )
    directory, identity = authority(course)
    candidate = read(directory / "offers" / (key + ".json"))
    if candidate["labels"]["course"] != course.name:
        raise ValueError("Recovery must run from the candidate's course")
    # The writer lock re-reads and validates the newest durable offer. Superseded
    # evidence is not a prerequisite for replacing an uncertain old write.
    try:
        outcome = send_offer(
            course, directory, identity, candidate, expected=expected, attest=True
        )
    except BlockingIOError:
        outcome = {"key": key, "status": "queued"}
    confirm(directory, identity, [outcome], time.monotonic() + timeout)
    return outcome


def supervise(
    course,
    job_path,
    publication=None,
    *,
    register_only=False,
    timeout=None,
    cancelled=lambda: False,
):
    """Parent-enforced wall-clock bound also covers DNS and stalled filesystems."""
    if os.environ.get("COURSE_AUTO_PUBLISH", "1") == "0":
        return {"status": "disabled"}
    if not register_only and (not publication or "registration" not in publication):
        return publication or {"status": "skipped"}
    if cancelled():
        return {
            **(publication or {}),
            "status": "skipped",
            "reason": "Job interrupted before publication",
        }
    output = job_path.parent / (
        "publication-registration.json" if register_only else "publication-outcome.json"
    )
    command = [
        sys.executable if register_only else publication["registration"]["python"],
        "-B",
        str(Path(__file__).absolute()),
        "register" if register_only else "publish",
        "--course-root",
        str(course),
        "--job-path",
        str(job_path),
        "--output",
        str(output),
    ]
    budget = timeout if timeout is not None else (5 if register_only else 45)
    deadline = time.monotonic() + budget
    work_deadline = deadline - min(2, budget / 2)
    child = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        while child.poll() is None:
            if cancelled() or time.monotonic() >= work_deadline:
                raise subprocess.TimeoutExpired(command, budget)
            time.sleep(min(0.05, max(0, work_deadline - time.monotonic())))
    except BaseException:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        unresolved = False
        try:
            child.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            unresolved = True
        return {
            **(publication or {}),
            "status": "failed",
            "cleanup_unresolved": unresolved,
            "reason": "Publication deadline or interruption; pending writes require quiescence before recovery",
        }
    if output.exists():
        return {**(publication or {}), **read(output)}
    return {
        **(publication or {}),
        "status": "failed",
        "reason": "Publisher did not return an outcome",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("register", "publish"))
    parser.add_argument("--course-root", required=True, type=Path)
    parser.add_argument("--job-path", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if args.action == "register" and settings(args.course_root)[1] is None:
            result = {"status": "unconfigured"}
        else:
            result = (
                register(args.course_root, args.job_path)
                if args.action == "register"
                else publish_job(args.course_root, args.job_path)
            )
    except (OSError, ValueError, KeyError, TypeError, ImportError) as exc:
        result = {
            "status": "failed",
            "reason": type(exc).__name__
            + ": publication setup or evidence invalid; inspect private settings and originals",
        }
    atomic(args.output, result)


if __name__ == "__main__":
    main()
