#!/usr/bin/env python3
"""Create and resume agent-driven course campaigns with frozen recipes and evidence."""

from __future__ import annotations

import argparse
import os
import re
import secrets
from pathlib import Path

from catalog import RECIPE_FILE, catalog, freeze, select, source_identity
from run_labs_common import canonical, digest, directory, lock, read, safe_path, write


def courses_root(explicit=None):
    """Find the enclosing checkout in source and project-installed layouts."""
    if explicit is not None:
        return safe_path(explicit).resolve()
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "sync-labs.sh").is_file() and any(
            candidate.glob("*/reference/course.json")
        ):
            return safe_path(candidate)
    raise ValueError("Cannot locate the course checkout; supply --courses-root")


def private_environment(path):
    safe_path(path)
    if path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077:
        raise ValueError("Environment receipt must be a user-owned mode 600 file")
    env = read(path)
    required = {
        "schema",
        "private_root",
        "target_id",
        "workspace_id",
        "ssh",
        "cxcli",
        "config",
        "target",
        "kube_context",
        "reports",
    }
    if not required <= set(env) or env["schema"] != "run-labs-environment/v1":
        raise ValueError(
            "Use the prepared environment receipt schema in references/environment.md"
        )
    if set(env) - required - {"variables", "course_variables", "lab_variables", "prepared_root"}:
        raise ValueError("Unknown environment fields; use references for credentials")
    if "prepared_root" in env and (
        not isinstance(env["prepared_root"], str)
        or not Path(env["prepared_root"]).is_absolute()
        or ".." in Path(env["prepared_root"]).parts
        or any(c in env["prepared_root"] for c in "\x00\n\r")
    ):
        raise ValueError("prepared_root must be an absolute remote catalog path")
    if set(env["ssh"]) - {"target", "port", "identity_file"}:
        raise ValueError("SSH credentials must use existing identity-file references")
    if set(env["reports"]) != {"producer_root", "viewer_root"}:
        raise ValueError("Reports require explicit producer and viewer roots")
    variable_sets = [
        env.get("variables", {}),
        *env.get("course_variables", {}).values(),
        *env.get("lab_variables", {}).values(),
    ]
    for name, value in (
        (name, value) for values in variable_sets for name, value in values.items()
    ):
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", name) or not isinstance(value, str):
            raise ValueError(
                "Prepared variables must be explicit string-valued environment names"
            )
        if re.search(
            r"(?:^|_)(?:ACCESS_TOKEN|AUTH_TOKEN|BEARER_TOKEN|API_TOKEN|PASSWORD|SECRET|CREDENTIAL|API_KEY|PRIVATE_KEY)(?:$|_)|^TOKEN$",
            name,
        ):
            raise ValueError(
                "Credential values must remain in their existing store or process memory"
            )
        if name in {
            "COURSE_RUN_ID",
            "COURSE_CAPTURE",
            "COURSE_PROFILE_TOOL",
            "COURSE_WORKLOAD",
        } or name.startswith("SLURM_"):
            raise ValueError("Runtime job/profile variables are controller-owned")
    if not env["target"] or not env["target_id"] or not env["workspace_id"]:
        raise ValueError("Target and workspace identities must be explicit")
    return env


def claim_path(state, unit):
    key = canonical([state["environment"]["target_id"], unit["key"], unit["profile"]])[
        :24
    ]
    return Path(state["environment"]["private_root"]) / "claims" / (key + ".json")


def verify_frozen(state):
    if state.get("plan", {}).get("execution_contract") != "native-jobs/v2":
        raise ValueError("Saved execution plan predates managed preparation binding; preserve its evidence and create a new campaign")
    root = Path(state["courses_root"])
    actual = source_identity(root, [u["course"] for u in state["plan"]["units"]])
    if (
        actual != state["plan"]["source"]
        or digest(RECIPE_FILE) != state["plan"]["recipes_sha256"]
    ):
        raise ValueError(
            "Source or recipe drift: start a fresh campaign; do not resume mixed evidence"
        )


def save(path, state):
    write(path / "campaign.json", state)


def next_action(state):
    if state["status"] in ("cancelled", "complete"):
        return None
    if state["status"] == "initializing":
        return {"kind": "initialize", "reference": "references/execution.md"}
    if not state.get("preflight"):
        return {"kind": "preflight", "reference": "references/environment.md"}
    for unit in state["plan"]["units"]:
        if unit.get("status") == "failed":
            continue
        for stage in unit["stages"]:
            if stage.get("status") != "complete":
                return {"unit": unit["key"], "profile": unit["profile"], **stage}
    return None


def finalize(path, state):
    """Idempotently finish claims after the last durable stage transition."""
    if next_action(state) is None:
        if state["status"] != "cancelled":
            state["status"] = (
                "failed"
                if any(u.get("status") == "failed" for u in state["plan"]["units"])
                else "complete"
            )
        save(path, state)
        release(state)


def describe(path, state):
    stages = [s for u in state["plan"]["units"] for s in u["stages"]]
    return {
        "campaign": str(path),
        "status": state["status"],
        "lab_profiles": len(state["plan"]["units"]),
        "stages_complete": sum(s.get("status") == "complete" for s in stages),
        "stages_total": len(stages),
        "next_action": next_action(state),
    }


def create(args):
    root = courses_root(args.courses_root)
    recipes = catalog(root)
    chosen = select(recipes, args.course, args.lab, args.all_courses)
    profiles = ["small", "large"] if args.workload == "both" else [args.workload]
    env = private_environment(args.environment) if args.environment else None
    plan = freeze(
        root, chosen, recipes, profiles, (env or {}).get("variables", {}), env
    )
    if args.dry_run:
        return {"dry_run": True, "plan": plan}
    if env is None:
        raise ValueError(
            "--environment is required for execution; dry-run needs no connection"
        )
    private = directory(Path(env["private_root"]))
    # Artifact roots must remain outside the course checkout.
    if private.is_relative_to(root) or root.is_relative_to(private):
        raise ValueError("Private root must be separate from the course checkout")
    identity = secrets.token_hex(8)
    path = directory(private / "campaigns" / identity)
    state = {
        "schema": "run-labs-campaign/v1",
        "id": identity,
        "status": "initializing",
        "courses_root": str(root),
        "environment": env,
        "environment_sha256": canonical(env),
        "plan": plan,
    }
    save(path, state)
    ensure_claims(path, state)
    return describe(path, state)


def ensure_claims(path, state):
    """Recover initialization and prove all claims before campaign effects."""
    if state["status"] in ("complete", "cancelled", "failed"):
        return
    private = Path(state["environment"]["private_root"])
    expected = {"campaign": str(path), "id": state["id"]}
    with lock(private / "control.lock"):
        claims = [claim_path(state, unit) for unit in state["plan"]["units"]]
        # Check every owner first; never partially take over a conflicting run.
        if any(p.exists() and read(p) != expected for p in claims):
            raise ValueError("A selected claim belongs to another campaign")
        for p in claims:
            if not p.exists():
                write(p, expected)
        if state["status"] == "initializing":
            state["status"] = "running"
            save(path, state)


def load(path):
    path = safe_path(path).resolve()
    state = read(path / "campaign.json")
    if state.get("schema") != "run-labs-campaign/v1":
        raise ValueError("Invalid campaign")
    if path != Path(state["environment"]["private_root"]) / "campaigns" / state["id"]:
        raise ValueError("Campaign path differs from its ownership receipt")
    return state


def release(state):
    private = Path(state["environment"]["private_root"])
    with lock(private / "control.lock"):
        for unit in state["plan"]["units"]:
            p = claim_path(state, unit)
            # A terminal campaign may be resumed after a newer run acquired it.
            if p.exists() and read(p)["id"] == state["id"]:
                p.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    run = commands.add_parser(
        "run",
        help="Start a fresh selected campaign; the skill executes returned stages",
    )
    run.add_argument(
        "--course",
        action="append",
        default=[],
        help="Practical course slug; repeatable",
    )
    run.add_argument(
        "--lab",
        action="append",
        default=[],
        help="course:lab-number or course:source-stem; repeatable",
    )
    run.add_argument(
        "--all-courses", action="store_true", help="All executable labs in six courses"
    )
    run.add_argument("--workload", choices=("small", "large", "both"), default="both")
    run.add_argument(
        "--courses-root", type=Path, help="Course checkout; inferred from this skill"
    )
    run.add_argument(
        "--environment", type=Path, help="Private prepared-target JSON receipt"
    )
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="Print frozen plan without writes or connections",
    )
    for name in ("status", "resume", "cancel"):
        cmd = commands.add_parser(
            name,
            help={
                "status": "Read progress without connecting",
                "resume": "Continue the same campaign after validating source identity",
                "cancel": "Cancel only owned jobs and release selected claims",
            }[name],
        )
        cmd.add_argument("campaign", type=Path)
    args = parser.parse_args()
    try:
        if args.action == "run":
            result = create(args)
        else:
            state = load(args.campaign)
            if args.action == "resume":
                with lock(args.campaign / "state.lock"):
                    state = load(args.campaign)
                    verify_frozen(state)
                    ensure_claims(args.campaign.resolve(), state)
                    finalize(args.campaign, state)
            if args.action == "cancel":
                from transport import cancel_owned

                with lock(args.campaign / "state.lock"):
                    state = load(args.campaign)
                    cancel_owned(state)
                    state["status"] = "cancelled"
                    save(args.campaign, state)
                    release(state)
            result = describe(args.campaign, state)
        import json

        print(json.dumps(result, indent=2, allow_nan=False))
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(2, f"run-labs: {exc}\n")


if __name__ == "__main__":
    main()
