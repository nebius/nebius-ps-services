"""Framework-neutral collection and result accounting; never run tests."""

from tsc_support.contracts import (
    PREFIX,
    digest,
    finite_seconds,
    fingerprint,
    identifier,
    list_value,
    object_value,
    require,
    string,
    verify_seal,
)

INVENTORY = f"{PREFIX}.inventory.v2"
RECEIPT = f"{PREFIX}.suite-result.v2"
PHASES = {"setup", "call", "teardown"}
OUTCOMES = {"passed", "failed", "skipped", "xfail", "xpass"}


def ids(value, label):
    list_value(value, label)
    require(len(value) <= 50000, "too many tests; split the suite")
    for item in value:
        string(item, label)
    require(len(value) == len(set(value)), f"duplicate {label}")
    return value


def runner(value):
    object_value(value, "runner")
    require(set(value) == {"name", "version"}, "invalid runner fields")
    string(value["name"], "runner name")
    string(value["version"], "runner version")


def deselections(value):
    list_value(value, "deselected")
    found = []
    for item in value:
        object_value(item, "deselected test")
        require(set(item) == {"id", "reason"}, "invalid deselection fields")
        string(item["reason"], "deselection reason")
        found.append(item["id"])
    ids(found, "deselected id")
    return found


def validate_inventory(value):
    verify_seal(value, INVENTORY)
    for key in ("suite_sha256", "selection_sha256", "input_sha256"):
        digest(value.get(key), key)
    runner(value.get("runner"))
    selected = ids(value.get("selected"), "selected id")
    excluded = deselections(value.get("deselected"))
    require(not set(selected) & set(excluded), "selected and deselected overlap")
    require(value.get("complete") is True, "inventory collection incomplete")
    return value


def validate_suite(suite, *, allow_missing_inventory=False):
    object_value(suite, "suite")
    identifier(suite.get("kind"), "suite category")
    digest(suite.get("sha256"), "suite digest")
    inventory = suite.get("inventory")
    if inventory is None:
        require(allow_missing_inventory, "missing independently collected inventory")
    else:
        validate_inventory(inventory)
        require(
            inventory["suite_sha256"] == suite["sha256"], "inventory suite mismatch"
        )
    exemptions = list_value(suite.get("exemptions", []), "exemptions")
    keys = set()
    for exemption in exemptions:
        object_value(exemption, "exemption")
        string(exemption.get("id"), "exempted id")
        require(exemption.get("phase") in {"setup", "call"}, "invalid exemption phase")
        require(
            exemption.get("outcome") in {"skipped", "xfail"},
            "invalid exemption outcome",
        )
        if exemption["outcome"] == "xfail":
            require(exemption["phase"] == "call", "only call failures may be expected")
            digest(exemption.get("failure_sha256"), "expected failure identity")
        string(exemption.get("reason"), "exemption reason")
        key = (exemption["id"], exemption["phase"])
        require(key not in keys, "duplicate exemption")
        keys.add(key)
        if inventory:
            require(
                exemption["id"] in inventory["selected"],
                "exemption for unselected test",
            )


def assess_receipt(
    suite, receipt, target, bundle_sha256, *, case_id=None, plan_sha256=None
):
    """Return derived status, counts and gaps; caller claims cannot override them."""
    validate_suite(suite)
    object_value(receipt, "suite receipt")
    require(receipt.get("schema") == RECEIPT, "unsupported suite receipt")
    if case_id is not None:
        require(
            receipt.get("case_id") == case_id
            and receipt.get("plan_sha256") == plan_sha256,
            "suite receipt belongs to another trial or plan",
        )
    inventory = suite["inventory"]
    require(inventory["input_sha256"] == bundle_sha256, "inventory input mismatch")
    require(
        receipt.get("inventory_sha256") == inventory["sha256"],
        "inventory receipt mismatch",
    )
    for key in ("suite_sha256", "selection_sha256", "input_sha256", "runner"):
        require(receipt.get(key) == inventory[key], f"receipt {key} mismatch")
    require(type(receipt.get("completed")) is bool, "missing completion state")
    code = receipt.get("exit_code")
    require(
        code is None or type(code) is int and 0 <= code <= 255, "invalid runner exit"
    )
    require(not receipt["completed"] or code is not None, "completed runner lacks exit")
    collected = ids(receipt.get("collected"), "collected id")
    deselected = receipt.get("deselected")
    deselections(deselected)
    timings = object_value(receipt.get("phase_seconds"), "suite phase seconds")
    require(set(timings) == PHASES, "missing suite phase timings")
    for value in timings.values():
        finite_seconds(value)
    errors = list_value(receipt.get("errors"), "runner errors")
    for error in errors:
        object_value(error, "runner error")
        require(
            error.get("phase") in {"collection", "runner"}, "invalid runner error phase"
        )
        string(error.get("reason"), "runner error reason")
    skips = list_value(receipt.get("collection_skips"), "collection skips")
    for skip in skips:
        object_value(skip, "collection skip")
        string(skip.get("id"), "skipped module id")
        string(skip.get("reason"), "collection skip reason")
    process_completed = receipt.get("process_completed")
    process_code = receipt.get("process_exit_code")
    require(type(process_completed) is bool, "missing process completion observation")
    require(
        process_code is None
        or type(process_code) is int
        and -255 <= process_code <= 255,
        "invalid process exit observation",
    )
    require(
        not process_completed or process_code is not None,
        "completed process lacks exit",
    )
    interrupted = receipt.get("interrupted")
    require(type(interrupted) is bool, "missing interruption state")
    counts = dict(
        passed=0,
        failed=0,
        skipped=0,
        xfail=0,
        xpass=0,
        deselected=len(deselected),
        observed=0,
        expected=len(inventory["selected"]),
    )
    empty_pytest = (
        inventory["runner"]["name"] == "pytest"
        and code == 5
        and not collected
        and not receipt.get("observed")
    )
    fail = (
        bool(errors)
        or any(
            c not in (None, 0) and not (c == 5 and empty_pytest)
            for c in (code, process_code)
        )
        and not interrupted
    )
    gaps = []
    if collected != inventory["selected"] or deselected != inventory["deselected"]:
        gaps.append("collection drift")
    if not receipt["completed"] or interrupted:
        gaps.append("missing completion or interruption")
    if not process_completed or process_code is None:
        gaps.append("missing independent process completion")
    if skips:
        gaps.append("collection skipped")
    exemptions = {(e["id"], e["phase"]): e for e in suite.get("exemptions", [])}
    observed = list_value(receipt.get("observed"), "observed tests")
    seen = set()
    for test in observed:
        object_value(test, "observed test")
        tid = string(test.get("id"), "observed id")
        if tid in seen or tid not in inventory["selected"]:
            fail = True
            gaps.append("duplicate or unexpected test")
        seen.add(tid)
        origin = object_value(test.get("origin"), "test origin")
        require(origin.get("kind") == target, "test origin target mismatch")
        string(origin.get("reference"), "origin reference")
        phases = object_value(test.get("phases"), "test phases")
        require(set(phases) <= PHASES, "unknown test phase")
        complete = {"setup", "teardown"} <= set(phases)
        ordinary = set(phases) == PHASES
        setup_skipped = False
        for phase, result in phases.items():
            object_value(result, "phase result")
            outcome = result.get("outcome")
            require(outcome in OUTCOMES, "unknown test outcome")
            finite_seconds(result.get("seconds"))
            ordinary &= outcome == "passed"
            if outcome in {"failed", "xpass"}:
                fail = True
            if outcome != "passed":
                counts[outcome] += 1
                string(result.get("reason"), "phase reason")
            if outcome in {"skipped", "xfail"}:
                exemption = exemptions.get((tid, phase))
                matches = exemption is not None and all(
                    exemption.get(k) == result.get(k) for k in ("outcome", "reason")
                )
                if outcome == "xfail":
                    matches &= (
                        phase == "call"
                        and exemption is not None
                        and (
                            exemption.get("failure_sha256")
                            == result.get("failure_sha256")
                        )
                    )
                    if not matches:
                        fail = (
                            True  # An unrecognized expected failure is still a failure.
                        )
                if not matches:
                    gaps.append("unapproved skipped or expected-failure outcome")
                setup_skipped |= phase == "setup" and outcome == "skipped"
            if phase in {"setup", "teardown"} and outcome in {
                "failed",
                "xfail",
                "xpass",
            }:
                fail = True
        complete &= (
            "call" in phases
            or setup_skipped
            or phases.get("setup", {}).get("outcome") == "failed"
        )
        if not complete:
            gaps.append("missing test phase")
        if ordinary:
            counts["passed"] += 1
    counts["observed"] = len(observed)
    missing = sorted(set(inventory["selected"]) - seen)
    if missing:
        gaps.append("missing expected tests")
    if not counts["passed"]:
        gaps.append("no ordinary passing test")
    return {
        "status": "FAIL" if fail else "INCOMPLETE" if gaps else "PASS",
        "counts": counts,
        "missing": missing,
        "gaps": sorted(set(gaps)),
        "receipt_sha256": fingerprint(receipt),
    }
