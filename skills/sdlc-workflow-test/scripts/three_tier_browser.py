"""Owned, bounded headless Playwright stages; no desktop or native capture dependency."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import time
from typing import Any
import uuid

import three_tier_runtime

BROWSER_SCHEMA = "agentic-sdlc/headless-browser-v1"
RECEIPT_SCHEMA = "agentic-sdlc/browser-stage-v1"
BROWSER_NAME = "Google Chrome (headless)"
STAGES = ("capability-discovery", "evaluate", "uat-before-restart", "uat-after-restart")
ASSETS = Path(__file__).resolve().parents[1] / "assets/headless-browser"
STAGE_TIMEOUT_SECONDS = 300


class BrowserOwnershipError(RuntimeError):
    """Browser execution or ownership could not be established."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def private_json(path: Path, value: object) -> None:
    # Publish complete IPC/receipt bytes atomically; readers never see partial JSON.
    if path.exists() or path.is_symlink():
        raise BrowserOwnershipError("Browser artifact already exists.")
    fd, temporary = tempfile.mkstemp(prefix=".publish-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def safe_path(path: Path, root: Path) -> Path:
    if not path.is_absolute() or not path.is_relative_to(root):
        raise BrowserOwnershipError("Browser path escaped its owned run.")
    for item in (path, *path.parents):
        if item.is_symlink():
            raise BrowserOwnershipError("Browser path contains a symlink.")
        if item == root:
            break
    if path.is_file() and path.stat().st_nlink != 1:
        raise BrowserOwnershipError("Browser artifact is hard-linked.")
    return path


def initial_state(verification_id: str) -> dict[str, Any]:
    return {
        "schema": BROWSER_SCHEMA,
        "verification_id": verification_id,
        "status": "NOT_STARTED",
        "active": None,
    }


def validate_state(value: object, verification_id: str) -> None:
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "verification_id", "status", "active"}
        or value["schema"] != BROWSER_SCHEMA
        or value["verification_id"] != verification_id
        or value["status"] not in {"NOT_STARTED", "RUNNING", "CLOSED"}
    ):
        raise BrowserOwnershipError("Headless browser state is invalid.")
    active = value["active"]
    if value["status"] == "RUNNING":
        if (
            not isinstance(active, dict)
            or set(active) != {"attempt_id", "pid"}
            or re.fullmatch(r"[0-9a-f]{32}", str(active["attempt_id"])) is None
            or type(active["pid"]) is not int
            or active["pid"] < 2
        ):
            raise BrowserOwnershipError("Headless process ownership is invalid.")
    elif active is not None:
        raise BrowserOwnershipError("Closed browser retains an active process.")


def freeze_bundle(run_root: Path) -> dict[str, str]:
    bundle = safe_path(run_root / "private/browser/bundle", run_root)
    bundle.mkdir(parents=True, mode=0o700)
    hashes = {}
    for name in (
        "package.json",
        "package-lock.json",
        "playwright.config.mjs",
        "acceptance.spec.mjs",
    ):
        source = ASSETS / name
        shutil.copyfile(source, bundle / name)
        os.chmod(bundle / name, 0o600)
        hashes[name] = digest(bundle / name)
    return hashes


def verify_bundle(state: dict) -> Path:
    root = Path(state["run_root"])
    bundle = safe_path(root / "private/browser/bundle", root)
    expected = state.get("browser_bundle")
    if not isinstance(expected, dict) or set(expected) != {
        "package.json",
        "package-lock.json",
        "playwright.config.mjs",
        "acceptance.spec.mjs",
    }:
        raise BrowserOwnershipError("Frozen browser oracle is missing.")
    for name, sha in expected.items():
        item = safe_path(bundle / name, root)
        if not item.is_file() or digest(item) != sha:
            raise BrowserOwnershipError(
                "Frozen browser oracle changed; start a new trial."
            )
    return bundle


def _processes() -> list[tuple[int, int, str]]:
    result = subprocess.run(
        ["ps", "-axo", "pid=,pgid=,stat=,command="],
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.returncode:
        raise BrowserOwnershipError("Cannot inspect owned browser processes.")
    values = []
    for line in result.stdout.splitlines():
        parts = line.strip().split(maxsplit=3)
        if (
            len(parts) == 4
            and parts[0].isdigit()
            and parts[1].isdigit()
            and not parts[2].startswith("Z")
        ):
            values.append((int(parts[0]), int(parts[1]), parts[3]))
    return values


def close(run_root: Path, verification_id: str, value: dict, *, reap=None) -> dict:
    validate_state(value, verification_id)
    if value["active"] is None:
        return {**value, "status": "CLOSED"}
    active = value["active"]
    attempt = safe_path(run_root / "private/browser" / active["attempt_id"], run_root)
    config_marker = str(attempt / "playwright.config.mjs")
    profile_marker = "--user-data-dir=" + str(attempt / "tmp") + "/playwright_"

    # Playwright launches Chrome in a separate session. Inspect both owned groups.
    def owned_groups():
        if reap is not None:
            reap()
        processes = _processes()
        chrome_groups = {
            pgid
            for pid, pgid, cmd in processes
            if pid == pgid and profile_marker in cmd and "--headless" in cmd
        }
        groups = set(chrome_groups)
        for pid, pgid, cmd in processes:
            if pid == active["pid"]:
                if config_marker not in cmd or pgid != pid:
                    raise BrowserOwnershipError(
                        "Browser runner PID identity changed; refusing signal."
                    )
                groups.add(pgid)
            if profile_marker in cmd and pgid not in chrome_groups:
                raise BrowserOwnershipError("Chrome process ownership is ambiguous.")
        return groups

    groups = owned_groups()
    if not groups:
        return {**value, "status": "CLOSED", "active": None}
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for group in groups:
            try:
                os.killpg(group, sig)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if reap is not None:
                reap()
            # macOS may clear argv while a process exits. Absence observation
            # does not authorize another signal and must not require intact argv.
            remaining = [
                row
                for row in _processes()
                if row[1] in groups or profile_marker in row[2]
            ]
            if not remaining:
                return {**value, "status": "CLOSED", "active": None}
            time.sleep(0.05)
        # Escalation requires a fresh complete ownership proof, including argv.
        refreshed = owned_groups()
        if {row[1] for row in remaining} - refreshed:
            raise BrowserOwnershipError(
                "Browser group remains without a provable owner."
            )
        groups = refreshed
    raise BrowserOwnershipError("Owned browser processes did not stop.")


def recover_interrupted_stage(state: dict) -> None:
    """Close an abandoned owner and preserve interruption as a failed attempt."""
    root = Path(state["run_root"])
    active = state["browser_instance"]["active"]
    closed = close(root, state["verification_id"], state["browser_instance"])
    if active is not None:
        attempt_id = active["attempt_id"]
        input_path = safe_path(
            root / "private/browser" / attempt_id / "input.json", root
        )
        value = json.loads(input_path.read_text())
        if (
            value.get("attempt_id") != attempt_id
            or value.get("verification_id") != state["verification_id"]
            or value.get("stage") not in STAGES
        ):
            raise BrowserOwnershipError(
                "Interrupted browser stage identity is invalid."
            )
        receipt_path = safe_path(
            root / "evidence/gui-uat" / attempt_id / "interrupted-recovery.json", root
        )
        receipt = {
            "schema": RECEIPT_SCHEMA,
            "verification_id": state["verification_id"],
            "attempt_id": attempt_id,
            "stage": value["stage"],
            "outcome": "FAIL",
            "headless": True,
            "browser": "chrome",
            "cleanup": "PASS",
            "error": "InterruptedStage",
        }
        if receipt_path.exists():
            if json.loads(receipt_path.read_text()) != receipt:
                raise BrowserOwnershipError("Interrupted browser receipt changed.")
        else:
            private_json(receipt_path, receipt)
        relative = str(receipt_path.relative_to(root))
        entry = {
            "stage": value["stage"],
            "outcome": "FAIL",
            "path": relative,
            "sha256": digest(receipt_path),
        }
        if not any(item["path"] == relative for item in state["browser_stages"]):
            state["browser_stages"].append(entry)
        state["environment"]["headless_browser"] = "FAIL"
    state["browser_instance"] = closed


def run_stage(
    state: dict, stage: str, target: dict, *, persist, checkpoint, record=None
) -> dict:
    if stage not in STAGES:
        raise BrowserOwnershipError("Unsupported browser stage.")
    if state["browser_instance"]["status"] == "RUNNING":
        raise BrowserOwnershipError(
            "Recover the previous owned stage before starting another."
        )
    bundle = verify_bundle(state)
    root = Path(state["run_root"])
    attempt_id = uuid.uuid4().hex
    private = safe_path(root / "private/browser" / attempt_id, root)
    output = safe_path(root / "evidence/gui-uat" / attempt_id, root)
    private.mkdir(mode=0o700)
    output.mkdir(parents=True, mode=0o700)
    (private / "tmp").mkdir(mode=0o700)
    input_value = {
        "verification_id": state["verification_id"],
        "attempt_id": attempt_id,
        "stage": stage,
        "endpoint": state.get("endpoints", {}).get("web"),
        "record": record,
    }
    private_json(private / "input.json", input_value)
    (private / "playwright.config.mjs").write_text(
        "import config from "
        + json.dumps(str(bundle / "playwright.config.mjs"))
        + ";\nexport default config;\n"
    )
    env = {
        **os.environ,
        "SDLC_BROWSER_INPUT": str(private / "input.json"),
        "SDLC_BROWSER_OUTPUT": str(output),
        "TMPDIR": str(private / "tmp"),
        "CI": "1",
    }
    receipt = {
        "schema": RECEIPT_SCHEMA,
        **{
            k: input_value[k]
            for k in ("verification_id", "attempt_id", "stage", "endpoint")
        },
        "target": target,
        "started_at": utc_now(),
        "headless": True,
        "browser": "chrome",
        "outcome": "FAIL",
        "exit_code": None,
        "cleanup": "NOT_RUN",
        "artifacts": {},
        "checks": [],
    }
    process = None
    failure = None
    try:
        if not (bundle / "node_modules/@playwright/test/cli.js").is_file():
            with (output / "install.log").open("w") as log:
                installed = subprocess.run(
                    ["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"],
                    cwd=bundle,
                    env=env,
                    stdout=log,
                    stderr=log,
                    timeout=120,
                )
            if installed.returncode:
                raise BrowserOwnershipError(
                    "Pinned Playwright dependency installation failed."
                )
        with (output / "runner.log").open("w") as log:
            process = subprocess.Popen(
                [
                    "node",
                    str(bundle / "node_modules/@playwright/test/cli.js"),
                    "test",
                    "--config",
                    str(private / "playwright.config.mjs"),
                ],
                cwd=bundle,
                env=env,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
            state["browser_instance"] = {
                **initial_state(state["verification_id"]),
                "status": "RUNNING",
                "active": {"attempt_id": attempt_id, "pid": process.pid},
            }
            persist()
            deadline = time.monotonic() + STAGE_TIMEOUT_SECONDS
            seen = set()
            while process.poll() is None:
                if time.monotonic() >= deadline:
                    raise BrowserOwnershipError("Headless browser stage timed out.")
                for name in ("blank", "created", "completed", "post-restart"):
                    request = safe_path(output / ("request-" + name + ".json"), root)
                    if request.exists() and name not in seen:
                        value = json.loads(request.read_text())
                        checkpoint(name, value)
                        receipt["checks"].append(
                            {"name": name, "record": value, "observed_at": utc_now()}
                        )
                        private_json(
                            output / ("response-" + name + ".json"), {"ok": True}
                        )
                        seen.add(name)
                time.sleep(0.05)
            receipt["exit_code"] = process.returncode
            if process.returncode:
                raise BrowserOwnershipError(
                    "Headless browser assertions failed; inspect the preserved trace and report."
                )
        observations = json.loads(
            safe_path(output / "observations.json", root).read_text()
        )
        if (
            observations.get("attempt_id") != attempt_id
            or observations.get("headless") is not True
            or not observations.get("browser_version")
            or not any(
                a["action"] == "close-browser" for a in observations.get("actions", [])
            )
        ):
            raise BrowserOwnershipError("Browser observations are incomplete.")
        required = (
            set()
            if stage == "capability-discovery"
            else (
                {"post-restart"}
                if stage == "uat-after-restart"
                else {"blank", "created", "completed"}
            )
        )
        if {item["name"] for item in receipt["checks"]} != required:
            raise BrowserOwnershipError(
                "Independent API/database checkpoints are incomplete."
            )
        receipt["browser_version"] = observations["browser_version"]
        receipt["record"] = observations.get("record")
        receipt["outcome"] = "PASS"
    except (
        OSError,
        ValueError,
        subprocess.SubprocessError,
        BrowserOwnershipError,
        RuntimeError,
    ) as error:
        failure = error
        receipt["error"] = type(error).__name__  # raw output stays in private artifacts
    finally:
        try:
            if process is not None and process.poll() is not None:
                # Reap before process ownership inspection.
                process.wait()
            state["browser_instance"] = close(
                root,
                state["verification_id"],
                state["browser_instance"],
                reap=process.poll if process is not None else None,
            )
            if process is not None:
                process.wait(timeout=5)
            receipt["cleanup"] = "PASS"
        except (BrowserOwnershipError, subprocess.SubprocessError) as error:
            failure = error
            receipt["cleanup"] = "FAIL"
            receipt["outcome"] = "FAIL"
        receipt["finished_at"] = utc_now()
        for item in sorted(output.iterdir()):
            safe_path(item, root)
            if item.is_file():
                os.chmod(item, 0o600)
                receipt["artifacts"][str(item.relative_to(root))] = digest(item)
        receipt_path = output / "receipt.json"
        private_json(receipt_path, receipt)
        entry = {
            "stage": stage,
            "outcome": receipt["outcome"],
            "path": str(receipt_path.relative_to(root)),
            "sha256": digest(receipt_path),
        }
        state["browser_stages"].append(entry)
        state["environment"]["headless_browser"] = (
            "PASS"
            if all(x["outcome"] == "PASS" for x in state["browser_stages"])
            else "FAIL"
        )
        persist()
    if failure is not None:
        raise BrowserOwnershipError(str(failure)) from failure
    return receipt


def validate_receipts(state: dict, *, require_complete: bool = True) -> list[dict]:
    root = Path(state["run_root"])
    if require_complete:
        verify_bundle(state)
    receipts = []
    for entry in state.get("browser_stages", []):
        if not isinstance(entry, dict) or set(entry) != {
            "stage",
            "outcome",
            "path",
            "sha256",
        }:
            raise BrowserOwnershipError("Browser stage binding is invalid.")
        relative = Path(entry["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise BrowserOwnershipError("Browser receipt path is unsafe.")
        path = safe_path(root / relative, root)
        if not path.is_file() or digest(path) != entry["sha256"]:
            raise BrowserOwnershipError("Browser stage receipt changed or is missing.")
        value = json.loads(path.read_text())
        if (
            value.get("schema") != RECEIPT_SCHEMA
            or value.get("verification_id") != state["verification_id"]
            or value.get("stage") != entry["stage"]
            or value.get("outcome") != entry["outcome"]
            or value.get("headless") is not True
            or value.get("browser") != "chrome"
        ):
            raise BrowserOwnershipError("Browser receipt identity is invalid.")
        if require_complete and (
            value["outcome"] != "PASS"
            or value.get("exit_code") != 0
            or value.get("cleanup") != "PASS"
        ):
            raise BrowserOwnershipError(
                "A failed browser attempt cannot be erased by a later success."
            )
        artifacts = value.get("artifacts", {})
        if (
            not artifacts
            or not any(p.endswith("/trace.zip") for p in artifacts)
            or not any(p.endswith("/test-results.json") for p in artifacts)
        ):
            raise BrowserOwnershipError("Browser trace or assertion report is missing.")
        for name, sha in artifacts.items():
            if Path(name).is_absolute() or ".." in Path(name).parts:
                raise BrowserOwnershipError("Browser artifact path is unsafe.")
            artifact = safe_path(root / name, root)
            if not artifact.is_file() or digest(artifact) != sha:
                raise BrowserOwnershipError(
                    "Browser stage artifact changed or is missing."
                )
        parent = str(path.parent.relative_to(root))
        if (
            parent != "evidence/gui-uat/" + str(value.get("attempt_id"))
            or path.name != "receipt.json"
        ):
            raise BrowserOwnershipError(
                "Browser receipt escaped its attempt directory."
            )
        report_path = parent + "/test-results.json"
        observations_path = parent + "/observations.json"
        if report_path not in artifacts or observations_path not in artifacts:
            raise BrowserOwnershipError(
                "Browser assertion report or observations are missing."
            )
        if value["outcome"] == "PASS":
            if value["stage"] != "capability-discovery":
                target = value.get("target", {})
                deployment = target.get("deployment", {})
                try:
                    build = three_tier_runtime.load_build(state, deployment.get("build"), target)
                except three_tier_runtime.RuntimeEvidenceError as error:
                    raise BrowserOwnershipError(str(error)) from error
                if (deployment.get("image_id") != build["image_id"]
                        or not deployment.get("web_container")):
                    raise BrowserOwnershipError("Browser deployment does not match its owned build.")
            report = json.loads((root / report_path).read_text())
            stats = report.get("stats", {})
            if stats.get("expected") != 1 or any(
                stats.get(k) != 0 for k in ("unexpected", "flaky", "skipped")
            ):
                raise BrowserOwnershipError(
                    "Browser assertion report did not pass exactly one test."
                )
            observed = json.loads((root / observations_path).read_text())
            if (
                observed.get("attempt_id") != value["attempt_id"]
                or observed.get("stage") != value["stage"]
                or observed.get("verification_id") != state["verification_id"]
                or observed.get("headless") is not True
                or observed.get("browser") != "chrome"
                or not observed.get("browser_version")
                or observed.get("record") != value.get("record")
                or (observed.get("actions") or [{}])[-1].get("action")
                != "close-browser"
            ):
                raise BrowserOwnershipError(
                    "Browser observations do not match the owned stage."
                )
            expected_checks = (
                set()
                if value["stage"] == "capability-discovery"
                else (
                    {"post-restart"}
                    if value["stage"] == "uat-after-restart"
                    else {"blank", "created", "completed"}
                )
            )
            if {c.get("name") for c in value.get("checks", [])} != expected_checks:
                raise BrowserOwnershipError(
                    "Independent API/database checkpoint receipts are missing."
                )
            if {parent + "/" + name for name in observed.get("screenshots", [])} != {
                p for p in artifacts if p.endswith(".png")
            }:
                raise BrowserOwnershipError(
                    "Screenshot observations do not match owned artifacts."
                )
        receipts.append(value)
    if require_complete:
        if (
            tuple(r["stage"] for r in receipts) != STAGES
            or state["browser_instance"]["status"] != "CLOSED"
        ):
            raise BrowserOwnershipError(
                "PASS requires four ordered fresh stages and closed browsers."
            )
        before, after = receipts[2:]
        if (
            before.get("record") != after.get("record")
            or before["target"] != after["target"]
        ):
            raise BrowserOwnershipError(
                "Post-restart browser evidence changed task or target."
            )
        if after["target"].get("head") != state["git"]["promoted_sha"]:
            raise BrowserOwnershipError(
                "UAT receipt is not bound to the promoted revision."
            )
        restart = state.get("browser_restart", {})
        if (
            restart.get("after_attempt") != before["attempt_id"]
            or restart.get("target") != before["target"]
            or restart.get("volumes") != state["resources"]["volumes"]
        ):
            raise BrowserOwnershipError("Owned Compose restart receipt is missing.")
    return receipts
