#!/usr/bin/env python3
"""Internal stage protocol for the run-labs agent. Not a standalone lab skill."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from catalog import expand
from evidence import build_public, bundle, validate, verify_inventory
from run_labs import (
    describe,
    ensure_claims,
    finalize,
    load,
    next_action,
    save,
    verify_frozen,
)
from run_labs_common import canonical, digest, directory, lock, read, write
from transaction import replace_sets
from transport import request


def run(path, operation, receipt_path=None, *, expected_action=None):
    with lock(path / "state.lock"):
        state = load(path)
        verify_frozen(state)
        action = next_action(state)
        if expected_action is not None and action != expected_action:
            raise ValueError("Campaign action changed; no stage effect performed")
        ensure_claims(path.resolve(), state)
        action = next_action(state)
        if action is None:
            finalize(path, state)
            return describe(path, state)
        if action["kind"] == "preflight":
            if operation == "sync":
                from prepare import sync

                state["sync"] = sync(state, path)
                save(path, state)
                return describe(path, state)
            if operation != "record" or not receipt_path:
                raise ValueError(
                    "Sync, inspect prepared prerequisites, then record the preflight receipt"
                )
            receipt = read(receipt_path)
            if not state.get("sync"):
                raise ValueError("Run the sync stage before preflight completion")
            if (
                receipt.get("schema") != "run-labs-preflight/v1"
                or receipt.get("environment_sha256") != state["environment_sha256"]
            ):
                raise ValueError("Preflight receipt differs from prepared environment")
            needed = {
                "cluster_identity",
                "login",
                "slurm",
                "software",
                "gpu_topology",
                "grafana",
                "systems_viewer",
                "compute_viewer",
                "prepared_assets",
            }
            if set(receipt.get("checks", {})) != needed or any(
                v is not True for v in receipt["checks"].values()
            ):
                raise ValueError(
                    "Preflight is incomplete; do not install missing infrastructure"
                )
            state["preflight"] = receipt
            save(path, state)
            return describe(path, state)
        unit = next(
            u
            for u in state["plan"]["units"]
            if u["key"] == action["unit"] and u["profile"] == action["profile"]
        )
        stage = next(s for s in unit["stages"] if s["id"] == action["id"])
        private = Path(state["environment"]["private_root"])
        work = directory(
            private
            / "staging"
            / state["id"]
            / unit["course"]
            / unit["lab"]
            / unit["profile"]
        )
        raw = directory(work / "raw")
        if stage["kind"] in ("execute", "dependency", "profile"):
            if operation == "bind":
                if stage.get("intent"):
                    raise ValueError("Cannot alter a dispatched stage")
                bindings = read(receipt_path)
                # Only substitution of declared unresolved prerequisites is allowed.
                stage["argv"] = expand(stage["argv"], unit["profile"], bindings)
                stage["environment"] = {
                    k: expand([v], unit["profile"], bindings, unresolved=True)[0]
                    for k, v in stage.get("environment", {}).items()
                }
                stage["bindings_sha256"] = canonical(bindings)
                save(path, state)
                return describe(path, state)
            if operation != "advance":
                raise ValueError("Execution stages use advance or prerequisite bind")
            if not stage.get("dispatch"):
                expand(stage["argv"], unit["profile"], {})
                expand(list(stage.get("environment", {}).values()), unit["profile"], {})
                stage["intent"] = True
                save(path, state)
                stage["dispatch"] = request(state, unit, stage, "submit")
                save(path, state)
            result = request(state, unit, stage, "query")
            stage["slurm"] = result
            if result["state"] == "COMPLETED" and result["exit_code"] == "0:0":
                stage["status"] = "complete"
            elif result["state"] in (
                "BOOT_FAIL",
                "DEADLINE",
                "FAILED",
                "CANCELLED",
                "TIMEOUT",
                "OUT_OF_MEMORY",
                "NODE_FAIL",
                "PREEMPTED",
            ):
                stage["status"] = "failed"
                unit["status"] = "failed"
                save(path, state)
            save(path, state)
        elif stage["kind"] in ("verify", "collect", "browser"):
            if stage["kind"] == "collect" and operation == "collect":
                from collect import collect

                collect(state, unit, raw)
                stage["receipt"] = {
                    "schema": "run-labs-collection/v1",
                    "inventory_sha256": digest(raw / "inventory.json"),
                }
                stage["status"] = "complete"
                save(path, state)
                return describe(path, state)
            if operation != "record" or not receipt_path:
                raise ValueError(
                    "This stage requires its independently checked receipt"
                )
            receipt = read(receipt_path)
            if stage["kind"] == "collect":
                verify_inventory(raw, read(raw / "inventory.json"))
                if receipt.get("schema") != "run-labs-collection/v1" or receipt.get(
                    "inventory_sha256"
                ) != digest(raw / "inventory.json"):
                    raise ValueError(
                        "Collection receipt does not match copied originals"
                    )
            elif stage["kind"] == "verify":
                if (
                    receipt.get("schema") != "run-labs-verification/v1"
                    or receipt.get("source_sha256") != state["plan"]["source_sha256"]
                ):
                    raise ValueError("Invalid independent verification receipt")
                jobs = {
                    str(s["dispatch"]["job"])
                    for s in unit["stages"]
                    if s.get("dispatch")
                }
                if set(receipt.get("jobs", {})) != jobs or any(
                    v.get("passed") is not True or not v.get("checks")
                    for v in receipt["jobs"].values()
                ):
                    raise ValueError("Independent job/content checks are incomplete")
            else:
                validate(unit, raw, receipt, Path(state["courses_root"]))
                write(work / "evidence.json", receipt)
            stage["receipt"] = receipt
            stage["status"] = "complete"
            save(path, state)
        elif stage["kind"] == "export":
            if operation != "advance":
                raise ValueError("Use advance to export verified evidence")
            if not stage.get("published"):
                public = work / "public"
                if public.exists():
                    shutil.rmtree(public)
                receipt = read(work / "evidence.json")
                write(raw / "evidence.json", receipt)
                build_public(
                    unit,
                    raw,
                    receipt,
                    Path(state["courses_root"]),
                    public,
                    state["plan"]["source_sha256"],
                )
                destination = (
                    Path(state["courses_root"])
                    / unit["course"]
                    / "reference/lab-results"
                    / unit["lab"]
                    / unit["profile"]
                )
                raw_destination = (
                    private / "raw" / unit["course"] / unit["lab"] / unit["profile"]
                )
                key = canonical([unit["key"], unit["profile"]])[:24]
                course_key = canonical([state["courses_root"], unit["course"]])[:24]
                with (
                    lock(private / "locks" / ("course-" + course_key + ".lock")),
                    lock(private / "locks" / (key + ".lock")),
                ):
                    replace_sets(
                        private / "journals" / (key + ".json"),
                        [(raw, raw_destination, True), (public, destination, False)],
                        state["id"],
                    )
                    bundle(Path(state["courses_root"]) / unit["course"])
                stage["published"] = True
                save(path, state)
            # Local originals are verified and committed before removing the
            # duplicated task-owned remote results. Failure leaves export pending.
            request(state, unit, stage, "cleanup")
            # Only the exact task-owned staging tree is removed after publication.
            shutil.rmtree(work)
            stage["status"] = "complete"
            save(path, state)
        finalize(path, state)
        return describe(path, state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("campaign", type=Path)
    parser.add_argument(
        "operation", choices=("sync", "advance", "record", "bind", "collect")
    )
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.campaign, args.operation, args.receipt), indent=2))
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(2, f"run-labs stage: {exc}\n")


if __name__ == "__main__":
    main()
