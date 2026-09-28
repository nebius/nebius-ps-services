#!/usr/bin/env python3
"""Internal reusable evidence orchestration; stage.py owns campaign transitions."""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import time
from pathlib import Path

import stage
from evidence import publisher, validate, verify_native_content
from run_labs import load, next_action, verify_frozen
from run_labs_common import canonical, digest, directory, lock, read, safe_path, write


def private_read(filename):
    path = safe_path(Path(filename))
    info = path.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("Runner input must be owner-private")
    return read(path)


def adapter(spec, journal):
    """Pinned, effect-inspected prepared adapters, with no automatic replay."""
    argv = spec["argv"]
    if (
        not argv
        or not all(isinstance(x, str) for x in argv)
        or not Path(argv[0]).is_absolute()
    ):
        raise ValueError("Adapter requires explicit argv and absolute executable")
    pins = spec.get("pins", {})
    if not pins:
        raise ValueError("Adapter source hashes are required")
    for filename, expected in pins.items():
        if digest(safe_path(Path(filename))) != expected:
            raise ValueError("Prepared adapter source changed")
    identity = canonical(spec)
    if journal.exists():
        previous = read(journal)
        if previous["adapter_sha256"] != identity:
            raise ValueError("Adapter identity changed; inspect prior effects")
        if previous["status"] != "complete":
            raise ValueError(
                "Uncertain adapter intent; independently reconcile before continuing"
            )
        for filename, expected in previous["outputs"].items():
            if digest(safe_path(Path(filename))) != expected:
                raise ValueError("Completed adapter output changed")
        return
    if any(Path(filename).exists() for filename in spec["outputs"]):
        raise ValueError("Existing adapter output needs independent reconciliation")
    timeout = spec.get("timeout_seconds", 300)
    if type(timeout) is not int or not 1 <= timeout <= 1800:
        raise ValueError("Adapter timeout must be bounded")
    write(journal, {"adapter_sha256": identity, "status": "intent"})
    # Private files prevent output from leaking through subprocess exceptions.
    with (
        (journal.with_suffix(".stdout")).open("xb") as out,
        (journal.with_suffix(".stderr")).open("xb") as err,
    ):
        os.chmod(out.name, 0o600)
        os.chmod(err.name, 0o600)
        result = subprocess.run(
            argv, stdout=out, stderr=err, timeout=timeout, check=False
        )
    if result.returncode:
        raise ValueError(
            "Prepared adapter failed; inspect private logs, never blindly replay"
        )
    outputs = {}
    for filename in spec["outputs"]:
        private_read(filename)
        outputs[filename] = digest(Path(filename))
    write(
        journal, {"adapter_sha256": identity, "status": "complete", "outputs": outputs}
    )


def context(manifest):
    campaign = Path(manifest["campaign"])
    state = load(campaign)
    verify_frozen(state)
    if (
        state["id"] != manifest["campaign_id"]
        or state["plan"]["source_sha256"] != manifest["source_sha256"]
    ):
        raise ValueError("Runner campaign or frozen source differs")
    action = next_action(state)
    if not action or (action.get("unit"), action.get("profile")) != (
        manifest["unit"],
        manifest["profile"],
    ):
        raise ValueError("Runner must identify the current unit exactly")
    unit = next(
        u
        for u in state["plan"]["units"]
        if (u["key"], u["profile"]) == (manifest["unit"], manifest["profile"])
    )
    return campaign, state, action, unit


def publication(manifest, unit, item, journal):
    """Confirmed publication is reused; uncertain intent is never resubmitted."""
    receipt = Path(item["confirmed"])
    if not receipt.exists():
        if Path(item["intent"]).exists():
            raise ValueError(
                "Uncertain publication requires independent reconciliation"
            )
        adapter(item["adapter"], journal)
    value = private_read(receipt)
    if (
        value["lab"] != unit["lab"]
        or value["profile"] != unit["profile"]
        or value["stages"] != item["stages"]
        or value["publication"]["status"] != "confirmed"
    ):
        raise ValueError("Publication identity differs")
    generation = value["publication"]["generation"]
    if type(generation) is not int or generation < 1:
        raise ValueError("Invalid confirmed publication generation")
    # Source-selected original paths are checked again by the browser receipt.
    return generation


def metric_expectations(api, originals, observability):
    """Use the course publisher's case labels and optional-value contract."""
    published = api.comparison_payload(
        originals["baseline"], originals["candidate"], observability
    )
    metrics = []
    for metric in observability["metrics"]:
        expected = {}
        for slot, original in originals.items():
            try:
                values = api.extract(original["measurements"], metric["path"])
            except ValueError:
                if metric.get("optional"):
                    continue
                raise
            values = [
                (case, value)
                for case, value in values
                if value is not None or not metric.get("optional")
            ]
            rows = [
                row for row in published if row[0] == metric["name"] and row[1] == slot
            ]
            for (case, _), (_, _, label, value) in zip(values, rows, strict=True):
                entry = expected.setdefault(
                    label, {"case": case, "display_case": label, "values": {}}
                )
                if entry["case"] != case or slot in entry["values"]:
                    raise ValueError("Published case labels are ambiguous")
                entry["values"][slot] = value
        if expected:
            metrics.append({**metric, "expected": list(expected.values())})
    return metrics


def grafana_request(manifest, state, unit, item):
    """Build capture inputs from collected originals, never a prepared example."""
    root = Path(state["courses_root"]) / unit["course"]
    raw = (
        Path(state["environment"]["private_root"])
        / "staging"
        / state["id"]
        / unit["course"]
        / unit["lab"]
        / unit["profile"]
        / "raw"
    )
    checked = private_read(manifest["checked"])
    publication_receipt = private_read(item["confirmed"])
    dashboard = read(root / "reference/grafana" / (unit["lab"] + ".json"))

    # Exact datasource replacement is explicit and limited to datasource UIDs.
    def remap(value):
        if isinstance(value, dict):
            return {
                k: (
                    manifest["datasource_map"].get(v, v)
                    if k == "uid" and isinstance(v, str)
                    else remap(v)
                )
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [remap(v) for v in value]
        return value

    results = {}
    originals = {}
    starts = []
    for slot, stage_id in zip(("baseline", "candidate"), item["stages"], strict=True):
        checked_job = next(j for j in checked["jobs"] if j["stage"] == stage_id)
        index = item.get("result_indices", {}).get(slot, 0)
        original = read(raw / checked_job["results"][index]["path"])
        expected_job = next(
            s["dispatch"]["job"] for s in unit["stages"] if s["id"] == stage_id
        )
        if (
            original["experiment"]["slurm_job_id"] != expected_job
            or original["profile"] != unit["profile"]
        ):
            raise ValueError("Grafana input original identity differs")
        starts.append(original["experiment"]["started_unix_seconds"])
        originals[slot] = original
        results[slot] = {
            "stage": stage_id,
            "job": expected_job,
            "result_index": index,
            "measurements": original["measurements"],
        }
    observability = read(root / "reference/observability.json")["labs"][unit["lab"]]
    metrics = metric_expectations(
        publisher(Path(state["courses_root"])), originals, observability
    )
    if not metrics:
        validate_overview_clip(item)
    for metric in metrics:
        if metric["unit"] in manifest.get("display_scales", {}):
            metric["display_scales"] = manifest["display_scales"][metric["unit"]]
    return {
        "operation": "grafana",
        "service": "grafana",
        "lab": unit["lab"],
        "profile": unit["profile"],
        "dashboard": remap(dashboard),
        "folder_uid": item["folder_uid"],
        "workspace": state["environment"]["workspace_id"],
        "generation": publication_receipt["publication"]["generation"],
        "results": results,
        "metrics": metrics,
        "from_ms": int(min(starts) * 1000) - 60000,
        "to_ms": int(time.time() * 1000),
        "prefix": item["prefix"],
        "output": item["observation"],
        "overview_clip": item.get("overview_clip"),
    }


def assemble(manifest, state, unit):
    checked = private_read(manifest["checked"])
    stages = {s["id"]: s for s in unit["stages"]}
    jobs = {s["id"]: s["dispatch"]["job"] for s in unit["stages"] if s.get("dispatch")}
    if {r["stage"]: r["job"] for r in checked["jobs"]} != jobs:
        raise ValueError("Independent checked job set differs")
    raw = (
        Path(state["environment"]["private_root"])
        / "staging"
        / state["id"]
        / unit["course"]
        / unit["lab"]
        / unit["profile"]
        / "raw"
    )
    directory(raw / "browser")
    result = {
        "schema": "run-labs-evidence/v1",
        "lab": unit["lab"],
        "profile": unit["profile"],
        "hardware": manifest["hardware"],
        "limitations": manifest["limitations"],
        "results": {
            j["stage"]: [r["path"] for r in j["results"]]
            for j in checked["jobs"]
            if stages[j["stage"]]["kind"] == "execute"
        },
        "native_reports": checked["native"],
        "screenshots": [],
    }

    def image(filename, expected, name, tool):
        source = safe_path(Path(filename))
        if digest(source) != expected:
            raise ValueError("Reviewed screenshot hash differs")
        target = safe_path(raw / "browser" / (name + ".png"))
        if target.parent != raw / "browser":
            raise ValueError("Invalid screenshot name")
        if target.exists():
            if digest(target) != expected:
                raise ValueError("Owned screenshot already differs")
        else:
            shutil.copyfile(source, target)
            target.chmod(0o600)
        return {
            "name": name,
            "path": str(target.relative_to(raw)),
            "sha256": expected,
            "tool": tool,
            "browser": "playwright-headless",
            "visually_reviewed": True,
            "public_view_reviewed": True,
        }

    for i, item in enumerate(manifest["grafana"]):
        observation, review = (
            private_read(item["observation"]),
            private_read(item["review"]),
        )
        for value in (observation, review):
            if value["lab"] != unit["lab"] or value["profile"] != unit["profile"]:
                raise ValueError("Grafana identity differs")
        confirmed = private_read(item["confirmed"])
        if (
            observation["generation"] != confirmed["publication"]["generation"]
            or review["generation"] != observation["generation"]
            or observation["api_definition_verified"] is not True
        ):
            raise ValueError("Grafana generation or definition differs")
        if (
            review["visual_review"] is not True
            or review["public_view_review"] is not True
        ):
            raise ValueError("Explicit visual review required")
        for slot, stage_id in zip(
            ("baseline", "candidate"), item["stages"], strict=True
        ):
            if ["value", slot, str(jobs[stage_id])] not in observation["controls"][
                "jobs"
            ] or ["value", slot, "1"] not in observation["controls"]["correctness"]:
                raise ValueError("Grafana selected job or correctness differs")
        entries = [
            *observation["screenshots"],
            *[c for p in observation["panels"] for c in p["captures"]],
        ]
        if set(review["screenshot_sha256"]) != {Path(x["path"]).name for x in entries}:
            raise ValueError("Review must cover every captured image exactly")
        for index, entry in enumerate(entries):
            if review["screenshot_sha256"][Path(entry["path"]).name] != entry["sha256"]:
                raise ValueError("Grafana review hash differs")
            checks = entry["numeric_checks"]
            if any(c["case"] not in entry.get("cases", []) for c in checks):
                raise ValueError("Numeric check lacks a visible row in this image")
            shot = image(
                entry["path"],
                entry["sha256"],
                f"grafana-pair{i + 1}-view{index + 1}",
                "grafana",
            )
            shot.update(
                dashboard_uid=observation["dashboard_uid"],
                profile=unit["profile"],
                generation=observation["generation"],
                numeric_checks=checks,
            )
            result["screenshots"].append(shot)
    for filename in manifest["native_reviews"]:
        review = private_read(filename)
        native = next(
            (
                n
                for n in checked["native"]
                if n["stage"] == review["stage"] and n["producer"] == review["producer"]
            ),
            None,
        )
        if (
            not native
            or review["job"] != native["job"]
            or review["lab"] != unit["lab"]
            or review["profile"] != unit["profile"]
        ):
            raise ValueError("Native review identity differs")
        required = [
            "visual_review",
            "public_view_review",
            "numeric_match",
            verify_native_content(unit, stages[native["stage"]], native),
        ]
        if native["tool"] == "compute":
            required += ["launch_geometry_match", "sol_occupancy_memory_match"]
        if any(review.get(k) is not True for k in required):
            raise ValueError("Native review is incomplete")
        shot = image(
            str(Path(filename).parent / review["screenshot"]),
            review["screenshot_sha256"],
            native["stage"] + "-" + native["producer"],
            native["tool"],
        )
        shot.update(
            report=native["path"],
            report_sha256=native["sha256"],
            content_checks={
                "matching_job": True,
                "native_cli_verified": True,
                required[3]: True,
            },
        )
        result["screenshots"].append(shot)
    validate(unit, raw, result, Path(state["courses_root"]))
    return result


def publication_captures(manifest):
    """Bind captures before effects; each published selection must be captured."""
    publications = manifest["publications"]
    if not publications:
        raise ValueError("At least one publication and capture are required")
    groups = [[] for _ in publications]
    owners = {}
    for index, item in enumerate(publications):
        key = item["confirmed"]
        if key in owners:
            raise ValueError("Duplicate publication confirmation")
        owners[key] = index
    for index, item in enumerate(manifest["grafana"]):
        owner = owners.get(item["confirmed"])
        if owner is None or item["stages"] != publications[owner]["stages"]:
            raise ValueError("Grafana capture has no matching publication")
        groups[owner].append((index, item))
    if not all(groups):
        raise ValueError("Every publication requires a Grafana capture")
    return groups


def validate_overview_clip(item):
    """Match the prepared browser's fixed native viewport before effects."""
    clip = item.get("overview_clip")
    if (
        not isinstance(clip, dict)
        or any(
            type(clip.get(k)) not in (int, float) or not math.isfinite(clip[k])
            for k in ("x", "y", "width", "height")
        )
        or clip["x"] < 0
        or clip["y"] < 0
        or clip["width"] < 640
        or clip["height"] < 360
        or clip["x"] + clip["width"] > 1920
        or clip["y"] + clip["height"] > 1080
    ):
        raise ValueError("Zero-metric capture requires a readable overview_clip")


def validate_capture_inputs(manifest, state, unit):
    root = Path(state["courses_root"]) / unit["course"]
    metrics = read(root / "reference/observability.json")["labs"][unit["lab"]][
        "metrics"
    ]
    for item in manifest["grafana"]:
        if not metrics or "overview_clip" in item:
            validate_overview_clip(item)


def capture_grafana(manifest, state, unit, item, index, runtime):
    request_path = runtime / f"grafana-request-{index}.json"
    if not request_path.exists():
        write(request_path, grafana_request(manifest, state, unit, item))
    script = Path(__file__).parent / "capture_browser.cjs"
    spec = {
        "argv": [
            manifest["node"],
            str(script),
            "request",
            manifest["browser_config"],
            str(request_path),
        ],
        "pins": {
            str(script): digest(script),
            str(request_path): digest(request_path),
            manifest["browser_config"]: digest(Path(manifest["browser_config"])),
        },
        "outputs": [item["observation"]],
        "timeout_seconds": 1800,
    }
    adapter(spec, runtime / f"capture-{index}.json")


def run(filename, operation):
    manifest = private_read(filename)
    if manifest.get("schema") != "run-labs-evidence-runner/v1":
        raise ValueError("Invalid runner manifest")
    campaign, state, action, unit = context(manifest)
    if operation == "status":
        return {
            "next_action": action["kind"],
            "unit": unit["key"],
            "profile": unit["profile"],
        }
    runtime = directory(
        campaign
        / "evidence-runner"
        / unit["course"]
        / (unit["lab"] + "-" + unit["profile"])
    )
    with lock(runtime / "runner.lock"):
        campaign, state, action, unit = context(manifest)
        identity = {
            "manifest_sha256": digest(Path(filename)),
            "source_sha256": state["plan"]["source_sha256"],
            "runner_sha256": canonical(
                {
                    p.name: digest(p)
                    for p in Path(__file__).parent.glob("*.*")
                    if p.suffix in (".py", ".cjs")
                }
            ),
        }
        identity_path = runtime / "identity.json"
        if identity_path.exists() and read(identity_path) != identity:
            raise ValueError(
                "Runner revision differs; reconcile at the evidence boundary"
            )
        write(identity_path, identity)
        if operation == "prepare":
            captures = publication_captures(manifest)
            validate_capture_inputs(manifest, state, unit)
            if action["kind"] == "verify":
                adapter(manifest["verify"], runtime / "verify.json")
                stage.run(
                    campaign,
                    "record",
                    Path(manifest["verification_receipt"]),
                    expected_action=action,
                )
            campaign, state, action, unit = context(manifest)
            if action["kind"] == "collect":
                stage.run(campaign, "collect", expected_action=action)
            campaign, state, action, unit = context(manifest)
            if action["kind"] != "browser":
                raise ValueError("Runner prepares only verify/collect/browser stages")
            for i, spec in enumerate(manifest.get("viewer_aliases", [])):
                adapter(spec, runtime / f"viewer-{i}.json")
            for i, item in enumerate(manifest["publications"]):
                publication(manifest, unit, item, runtime / f"publish-{i}.json")
                for index, capture in captures[i]:
                    capture_grafana(manifest, state, unit, capture, index, runtime)
            return {
                "gate": "visual-review",
                "unit": unit["key"],
                "profile": unit["profile"],
                "automatic_approval": False,
            }
        if operation != "finish" or action["kind"] not in ("browser", "export"):
            raise ValueError(
                "Finish requires reviewed browser evidence or pending export"
            )
        if action["kind"] == "browser":
            result = assemble(manifest, state, unit)
            receipt = runtime / "evidence.json"
            write(receipt, result)
            stage.run(campaign, "record", receipt, expected_action=action)
            campaign, state, action, unit = context(manifest)
        if action["kind"] != "export":
            raise ValueError("Runner may only advance its exact export stage")
        stage.run(campaign, "advance", expected_action=action)
        return {"exported": True, "unit": unit["key"], "profile": unit["profile"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["status", "prepare", "finish"])
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.manifest, args.operation)))
    except (ValueError, KeyError, OSError, subprocess.TimeoutExpired) as error:
        # No subprocess output, credentials or private paths in the public result.
        raise SystemExit(
            f"Evidence runner stopped ({type(error).__name__}); inspect private inputs and receipts."
        ) from None
