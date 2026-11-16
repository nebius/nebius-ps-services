#!/usr/bin/env python3
"""Verifier-owned public Implement companion for an active SDLC generation."""
import argparse
import json
import os
from pathlib import Path
import sys

import three_tier_lifecycle as lifecycle
import verification_workspace as workspace
import verification_catalog as catalog
from run_with_git_environment import native_environment


def record_terminal(root, state, evidence, result, groups):
    """Keep failure cleanup possible without asserting successful invocation."""
    for pid in groups:
        try:
            os.killpg(pid, 0)
        except ProcessLookupError:
            continue
        raise ValueError("companion native group remains active; preserve this trial")
    with workspace.operation(root):
        _, current = lifecycle.load_active(root)
        if current["verification_id"] != state["verification_id"]:
            raise ValueError("companion generation changed during native execution")
        lifecycle.private_json(evidence / "finished.json", {
            "verification_id": state["verification_id"], "status": result["status"],
            "process_groups": groups, "cleanup_verified": True})


def main():
    parser = argparse.ArgumentParser(allow_abbrev=False, description=__doc__)
    parser.add_argument("--verification-root", type=Path, required=True)
    parser.add_argument("--expected-verification-id", required=True)
    parser.add_argument("--observation-interval", type=int, default=30)
    args = parser.parse_args()
    if not 1 <= args.observation_interval <= 300:
        raise ValueError("observation interval must be between 1 and 300 seconds")
    root = lifecycle.verification_root(args.verification_root, create=False)
    with workspace.operation(root):
        _, state = lifecycle.load_active(root)
        if state["verification_id"] != args.expected_verification_id:
            raise ValueError("companion generation changed")
        catalog.require_native_installation(state, lifecycle.agent_name())
        home = Path(state["private_root"]) / (lifecycle.agent_name() + "-home")
        project = Path(state["companion_project_root"])
        profile = lifecycle.owned_git_origin.profile_path(Path(state["project_root"]))
        env = native_environment(project, profile)
        workspace.register_native(root)
        evidence = Path(state["evidence_root"]) / "companion"
        evidence.mkdir(mode=0o700, parents=True, exist_ok=True)
        if (evidence / "launch.json").exists():
            raise ValueError("companion invocation already exists; preserve this trial")
        lifecycle.private_json(evidence / "launch.json", {"verification_id": state["verification_id"]})
    os.environ.clear()
    os.environ.update(env)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "implement-test/scripts"))
    from public_workflow_trial import run_project
    request = ("Create text_tool.py as a local CLI taking one string argument, normalize whitespace "
               "and lowercase it, then print one newline. Add unittest coverage for mixed case, "
               "repeated whitespace and empty input. Own only project-b and use one task worker. "
               "Use available native agent tools for that fresh assignment-only worker, even though "
               "there is only one task. Verify capabilities before arming: supported explicit command "
               "cwd and existing inherited grants can satisfy the envelope without spawn-level setters. "
               "Consume the admitted launch envelope with its exact command cwd "
               "and support_write_roots; preserve the configured sandbox and approval policy. "
               "Sequential CLI dispatch is a fallback when native agent tools are unavailable "
               "and the host supports that launch path, not a consequence of having one worker. "
               "If neither path is available, preserve the capability blocker without bypassing "
               "controls or automatically retrying the failed assignment. "
               "Complete automatic aggregation and cleanup. Do not invoke continuation, publish, "
               "push, run Docker, or modify project-a. A sibling SDLC project is active in the same home.")
    groups = []

    def launched(pid):
        groups.append(pid)
        workspace.register_native(root, pid=pid)

    result = {"status": "FAIL"}
    try:
        result = run_project(project, home, lifecycle.agent_name(), request, args.observation_interval,
                             evidence, on_launch=launched)
    finally:
        record_terminal(root, state, evidence, result, groups)
    print(json.dumps(result))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({"status": "blocked", "reason": str(error)[:200]}))
        raise SystemExit(2)
