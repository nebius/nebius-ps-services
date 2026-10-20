"""Owner-executed Django test results; free-form logs never grant acceptance."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import uuid

import three_tier_browser
import three_tier_runtime


TEST_CLASSES = ("unit", "api", "database", "migration", "vertical")
SCHEMA = "agentic-sdlc/test-execution-v1"
COUNTERS = ("tests", "failures", "errors", "skipped", "expected_failures", "unexpected_successes")
PREFIX = "SDLC_TEST_RESULT="
# The selected runner owns counts. Product stdout is neither evidence nor a parser.
RUNNER = '''import contextlib, json, os, runpy, sys
import django.test.runner
summary = None
class EvidenceRunner(django.test.runner.DiscoverRunner):
    def run_suite(self, suite, **kwargs):
        global summary
        result = super().run_suite(suite, **kwargs)
        summary = {"tests": result.testsRun, "failures": len(result.failures),
                   "errors": len(result.errors), "skipped": len(result.skipped),
                   "expected_failures": len(result.expectedFailures),
                   "unexpected_successes": len(result.unexpectedSuccesses)}
        return result
django.test.runner.EvidenceRunner = EvidenceRunner
sys.argv = ["manage.py", "test", sys.argv[1], "--noinput", "--verbosity=0",
            "--testrunner=django.test.runner.EvidenceRunner"]
code = 0
with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
    try:
        runpy.run_path("manage.py", run_name="__main__")
    except SystemExit as error:
        code = error.code
if summary is not None:
    print("SDLC_TEST_RESULT=" + json.dumps(summary, sort_keys=True), flush=True)
sys.exit(code)
'''
RUNNER_SHA256 = hashlib.sha256(RUNNER.encode()).hexdigest()


class TestEvidenceError(RuntimeError):
    """Observed execution cannot certify this class and promoted revision."""


def successful_counts(value: object) -> bool:
    return (
        isinstance(value, dict) and set(value) == set(COUNTERS)
        and all(type(value[key]) is int and value[key] >= 0 for key in COUNTERS)
        and value["tests"] > 0
        and all(value[key] == 0 for key in COUNTERS if key != "tests")
    )


def observed_counts(stdout: str) -> dict:
    lines = [line[len(PREFIX):] for line in stdout.splitlines() if line.startswith(PREFIX)]
    if len(lines) != 1:
        raise TestEvidenceError("Test runner did not return one structured result.")
    try:
        value = json.loads(lines[0])
    except (ValueError, TypeError) as error:
        raise TestEvidenceError("Test runner returned an invalid result.") from error
    if not successful_counts(value):
        raise TestEvidenceError("Tests failed, were skipped, or executed no checks.")
    return value


def test_target(state: dict, target: dict) -> dict:
    value = {key: target[key] for key in ("path", "head", "deployment")}
    if (value["path"] != state["project_root"]
            or not state["git"].get("promoted_sha")
            or value["head"] != state["git"]["promoted_sha"]):
        raise TestEvidenceError("Test acceptance requires the clean promoted deployment.")
    return value


def read_receipt(state: dict, binding: dict) -> dict:
    if not isinstance(binding, dict) or set(binding) != {"test_class", "path", "sha256", "outcome"}:
        raise TestEvidenceError("Test execution binding is missing or invalid.")
    relative = binding["path"]
    if not isinstance(relative, str) or re.fullmatch(r"evidence/tests/[a-f0-9]{32}\.json", relative) is None:
        raise TestEvidenceError("Test execution receipt path is invalid.")
    root = Path(state["run_root"])
    path = root / relative
    for item in (path, *path.parents):
        if item == root:
            break
        if item.is_symlink():
            raise TestEvidenceError("Test execution receipt is symlinked.")
    try:
        stat = path.stat()
        if (not path.is_file() or stat.st_nlink != 1 or stat.st_uid != os.getuid()
                or stat.st_mode & 0o077 or stat.st_size > 65536):
            raise TestEvidenceError("Test execution receipt is unsafe.")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != binding["sha256"]:
            raise TestEvidenceError("Test execution receipt changed.")
        value = json.loads(raw)
    except (OSError, ValueError, TypeError) as error:
        raise TestEvidenceError("Test execution receipt is missing or invalid.") from error
    fields = {"schema", "verification_id", "test_class", "label", "runner_sha256",
              "target", "exit_code", "counts", "outcome"}
    if (not isinstance(value, dict) or set(value) != fields
            or value["schema"] != SCHEMA or value["verification_id"] != state["verification_id"]
            or value["test_class"] != binding["test_class"]
            or value["test_class"] not in TEST_CLASSES
            or value["runner_sha256"] != RUNNER_SHA256
            or not valid_label(value["label"])
            or value["outcome"] != binding["outcome"] or value["outcome"] != "PASS"
            or type(value["exit_code"]) is not int or value["exit_code"] != 0
            or not successful_counts(value["counts"])):
        raise TestEvidenceError("Test execution did not pass its exact owner contract.")
    try:
        target = test_target(state, value["target"])
        deployment = target["deployment"]
        build = three_tier_runtime.load_build(state, deployment["build"], target)
        if (deployment["build"] != state["runtime_build"]
                or deployment["image_id"] != build["image_id"]
                or deployment["web_container"] != state["resources"]["containers"][0]):
            raise TestEvidenceError("Test deployment no longer matches the owned runtime.")
    except (KeyError, TypeError, IndexError, three_tier_runtime.RuntimeEvidenceError) as error:
        raise TestEvidenceError("Test deployment evidence is stale or invalid.") from error
    return value


def valid_label(value: object) -> bool:
    return isinstance(value, str) and len(value) <= 256 and re.fullmatch(
        r"[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)+", value
    ) is not None


def run_class(state: dict, name: str, label: str, *, resolve_target, command,
              persist, write_json) -> dict:
    """Run once under the lifecycle generation lock; preserve interrupted failure."""
    if name not in TEST_CLASSES or not valid_label(label):
        raise TestEvidenceError("Test class or dotted Django test label is invalid.")
    target = test_target(state, resolve_target(state))
    runs = state.setdefault("test_runs", [])
    if not isinstance(runs, list):
        raise TestEvidenceError("Test execution history is invalid.")
    if any(not isinstance(item, dict) for item in runs):
        raise TestEvidenceError("Test execution history is invalid.")
    prior = [item for item in runs if item.get("test_class") == name]
    if prior:
        if len(prior) != 1:
            raise TestEvidenceError("Duplicate test executions cannot certify a class.")
        value = read_receipt(state, prior[0])
        if value["target"] != target or value["label"] != label:
            raise TestEvidenceError("Test execution changed; start a fresh trial.")
        return state
    if any(item.get("stage", "").startswith("uat-") for item in state["browser_stages"]):
        raise TestEvidenceError("Run product test classes before the first UAT stage.")
    relative = "evidence/tests/" + uuid.uuid4().hex + ".json"
    artifact = Path(state["run_root"]) / relative
    # Validate the parent before mkdir/write; never follow redirected evidence.
    for parent in (artifact.parent, artifact.parent.parent):
        if parent.is_symlink():
            raise TestEvidenceError("Test execution directory is symlinked.")
    artifact.parent.mkdir(mode=0o700, exist_ok=True)
    value = {"schema": SCHEMA, "verification_id": state["verification_id"],
             "test_class": name, "label": label, "runner_sha256": RUNNER_SHA256,
             "target": target, "exit_code": None, "counts": None, "outcome": "INCOMPLETE"}
    binding = {"test_class": name, "path": relative, "sha256": "", "outcome": "INCOMPLETE"}
    runs.append(binding)

    def save():
        write_json(artifact, value)
        binding.update(sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(), outcome=value["outcome"])
        persist()

    save()
    try:
        result = command(["docker", "compose", "--project-name", state["compose_project"],
                          "--project-directory", target["path"], "exec", "-T", "web",
                          "python", "-c", RUNNER, label], timeout=300)
        value["exit_code"] = result.returncode
        if result.returncode != 0:
            raise TestEvidenceError("Owned test command failed; start a fresh trial.")
        value["counts"] = observed_counts(result.stdout)
        if test_target(state, resolve_target(state)) != target:
            raise TestEvidenceError("Test source or deployment changed during execution.")
        value["outcome"] = "PASS"
    except Exception as error:
        value["outcome"] = "FAIL"
        save()
        # Never copy product stdout, stderr, or exception payloads into evidence.
        raise TestEvidenceError("Owned test execution failed; start a fresh trial.") from error
    save()
    return state


def validate_evidence(state: dict, tests: dict) -> None:
    runs = state.get("test_runs")
    if not isinstance(runs, list) or len(runs) != len(TEST_CLASSES):
        raise TestEvidenceError("Every test class requires an owned execution receipt.")
    seen = set()
    labels = set()
    for binding in runs:
        value = read_receipt(state, binding)
        name = value["test_class"]
        if name in seen or value["label"] in labels:
            raise TestEvidenceError("Test classes require distinct executions and labels.")
        seen.add(name)
        labels.add(value["label"])
        if tests[name]["evidence"] != [binding["path"]] or tests[name]["assertions"] != value["counts"]["tests"]:
            raise TestEvidenceError("Declared test evidence or count differs from execution.")
    validate_gui_claim(state, tests["gui"])


def validate_claim(state: dict, name: str, claim: dict) -> None:
    if name == "gui":
        validate_gui_claim(state, claim)
        return
    runs = state.get("test_runs", [])
    if not isinstance(runs, list) or any(not isinstance(item, dict) for item in runs):
        raise TestEvidenceError("Test execution history is invalid.")
    matching = [item for item in runs if item.get("test_class") == name]
    if len(matching) != 1:
        raise TestEvidenceError("Test class requires exactly one owner execution.")
    value = read_receipt(state, matching[0])
    if claim["evidence"] != [matching[0]["path"]] or claim["assertions"] != value["counts"]["tests"]:
        raise TestEvidenceError("Declared test evidence or count differs from execution.")


def validate_gui_claim(state: dict, claim: dict) -> None:
    try:
        receipts = three_tier_browser.validate_receipts(state)
        evidence = []
        count = 0
        for binding, receipt in zip(state["browser_stages"], receipts):
            if receipt["stage"] not in {"uat-before-restart", "uat-after-restart"}:
                continue
            evidence.append(binding["path"])
            report = next(name for name in receipt["artifacts"] if name.endswith("/test-results.json"))
            count += json.loads((Path(state["run_root"]) / report).read_text())["stats"]["expected"]
        if len(evidence) != 2 or claim["evidence"] != evidence or claim["assertions"] != count:
            raise TestEvidenceError("GUI tests must match the owned Playwright UAT executions.")
    except three_tier_browser.BrowserOwnershipError as error:
        raise TestEvidenceError(str(error)) from error
    except (OSError, KeyError, ValueError, TypeError, StopIteration) as error:
        raise TestEvidenceError("Owned GUI test evidence is missing or invalid.") from error
