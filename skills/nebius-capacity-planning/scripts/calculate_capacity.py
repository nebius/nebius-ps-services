#!/usr/bin/env python3
"""Check normalized capacity arithmetic from JSON stdin; no network or writes."""

from __future__ import annotations

import argparse
import json
import re
import sys

RESOURCES = ("cpu_m", "memory_mib", "ephemeral_mib", "gpus")
MAX_INPUT = 2 * 1024 * 1024
MAX_NUMBER = 10**15


class InputError(ValueError):
    """Invalid normalized model (messages never include input values)."""


def fields(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= value.keys():
        raise InputError("Expected object with all required fields.")
    if value.keys() - set(required) - set(optional):
        raise InputError("Unknown field; consult references/calculator.md.")


def integer(value):
    if type(value) is not int or not 0 <= value <= MAX_NUMBER:
        raise InputError("Quantities must be bounded non-negative integers.")
    return value


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", value):
        raise InputError("Identifiers must be lowercase hyphenated names.")
    return value


def rows(value):
    if not isinstance(value, list) or len(value) > 1000:
        raise InputError("Expected an array with at most 1000 entries.")
    return value


def vector(value, limits=False):
    fields(value, RESOURCES)
    for resource in RESOURCES:
        if limits and value[resource] is None and resource != "gpus":
            continue
        integer(value[resource])
    return value


def allocation(value):
    fields(value, ("requests", "limits", "runtime"))
    vector(value["requests"])
    vector(value["limits"], limits=True)
    if value["runtime"] is not None:
        vector(value["runtime"])
    for resource in RESOURCES:
        limit = value["limits"][resource]
        if limit is not None and value["requests"][resource] > limit:
            raise InputError("Request exceeds its finite limit.")
        if resource == "gpus" and limit != value["requests"][resource]:
            raise InputError("Whole GPU requests and limits must match.")
        if (
            value["runtime"] is not None
            and limit is not None
            and value["runtime"][resource] > limit
        ):
            raise InputError("Runtime estimate exceeds its finite limit.")


def index(items, required):
    result = {}
    for item in rows(items):
        fields(item, required)
        key = identifier(item["id"])
        if key in result:
            raise InputError("Duplicate identifier.")
        result[key] = item
    return result


def total(terms):
    """Unknown/unbounded terms count only when their multiplicity is nonzero."""
    active = [(value, count) for value, count in terms if count]
    if any(value is None for value, _ in active):
        return None
    return sum(value * count for value, count in active)


def margin(capacity, demand):
    if capacity is None or demand is None:
        return {"spare": None, "spare_percent": None}
    spare = capacity - demand
    return {
        "spare": spare,
        "spare_percent": round(100 * spare / capacity, 2) if capacity else None,
    }


def budgets(items):
    output = []
    for item in index(items, ("id", "unit", "capacity", "items")).values():
        if item["unit"] not in ("count", "MiB", "GiB", "Mbps", "IOPS", "connections"):
            raise InputError("Unsupported budget unit.")
        if item["capacity"] is not None:
            integer(item["capacity"])
        entries = index(item["items"], ("id", "quantity", "copies"))
        demand = sum(
            integer(entry["quantity"]) * integer(entry["copies"])
            for entry in entries.values()
        )
        output.append({
            "id": item["id"],
            "unit": item["unit"],
            "capacity": item["capacity"],
            "demand": demand,
            **margin(item["capacity"], demand),
        })
    return output


def calculate(model):
    fields(model, ("schema_version", "pools", "components", "scenarios"))
    if type(model["schema_version"]) is not int or model["schema_version"] != 1:
        raise InputError("Unsupported schema_version.")
    pools = index(
        model["pools"], ("id", "allocatable", "pod_slots", "overhead_pods", "overhead")
    )
    components = index(model["components"], ("id", "pool", "allocation"))
    scenarios = index(model["scenarios"], ("id", "nodes", "replicas", "budgets"))
    if not pools or not scenarios:
        raise InputError("At least one pool and scenario are required.")
    for pool in pools.values():
        vector(pool["allocatable"])
        integer(pool["pod_slots"])
        integer(pool["overhead_pods"])
        if pool["overhead_pods"] > pool["pod_slots"]:
            raise InputError("Infrastructure Pod count exceeds node Pod slots.")
        allocation(pool["overhead"])
        if any(
            pool["overhead"]["requests"][r] > pool["allocatable"][r]
            for r in RESOURCES
        ):
            raise InputError("Infrastructure requests exceed node allocatable.")
    for component in components.values():
        identifier(component["pool"])
        if component["pool"] not in pools:
            raise InputError("Component references an unknown pool.")
        allocation(component["allocation"])
    output = []
    for scenario in scenarios.values():
        fields(scenario["nodes"], pools)
        fields(scenario["replicas"], components)
        for count in scenario["replicas"].values():
            integer(count)
        pool_results = []
        issues = []
        for pool_id, pool in pools.items():
            nodes = scenario["nodes"][pool_id]
            fields(nodes, ("base", "surge", "unavailable"))
            for count in nodes.values():
                integer(count)
            provisioned = nodes["base"] + nodes["surge"]
            if nodes["unavailable"] > provisioned:
                raise InputError("Unavailable nodes exceed provisioned nodes.")
            usable = provisioned - nodes["unavailable"]
            active = [
                (c, scenario["replicas"][c["id"]])
                for c in components.values()
                if c["pool"] == pool_id and scenario["replicas"][c["id"]]
            ]
            resource_results = {}
            for resource in RESOURCES:
                capacity = pool["allocatable"][resource] * usable
                values = {}
                known_subtotals = {}
                for kind in ("requests", "limits", "runtime"):
                    terms = []
                    for alloc, count in [(pool["overhead"], usable)] + [
                        (c["allocation"], count) for c, count in active
                    ]:
                        value = alloc[kind]
                        terms.append((None if value is None else value[resource], count))
                    values[kind] = total(terms)
                    known_subtotals[kind] = sum(
                        value * count for value, count in terms if value is not None
                    )
                resource_results[resource] = {
                    "usable_allocatable": capacity,
                    **values,
                    "known_subtotals": known_subtotals,
                    "request_margin": margin(capacity, values["requests"]),
                    "runtime_margin": margin(capacity, values["runtime"]),
                    "limit_margin": margin(capacity, values["limits"]),
                }
                for kind in ("requests", "limits", "runtime"):
                    if known_subtotals[kind] > capacity:
                        issues.append({
                            "pool": pool_id, "resource": resource,
                            "code": kind.upper() + "_EXCEED_CAPACITY",
                        })
                available_per_node = (
                    pool["allocatable"][resource] - pool["overhead"]["requests"][resource]
                )
                for component, _ in active:
                    if component["allocation"]["requests"][resource] > available_per_node:
                        issues.append({
                            "pool": pool_id, "component": component["id"],
                            "resource": resource, "code": "POD_EXCEEDS_NODE",
                        })
            demand_pods = sum(count for _, count in active) + pool["overhead_pods"] * usable
            capacity_pods = pool["pod_slots"] * usable
            if demand_pods > capacity_pods:
                issues.append({"pool": pool_id, "code": "POD_SLOTS_EXCEEDED"})
            pool_results.append({
                "id": pool_id, "provisioned_nodes": provisioned, "usable_nodes": usable,
                "resources": resource_results,
                "pod_slots": {
                    "capacity": capacity_pods, "demand": demand_pods,
                    **margin(capacity_pods, demand_pods),
                },
            })
        budget_results = budgets(scenario["budgets"])
        for budget in budget_results:
            if budget["spare"] is not None and budget["spare"] < 0:
                issues.append({"budget": budget["id"], "code": "BUDGET_EXCEEDED"})
        output.append({
            "id": scenario["id"], "pools": pool_results, "budgets": budget_results,
            "issues": issues,
        })
    return {
        "schema_version": 1,
        "status": "violations_found" if any(s["issues"] for s in output) else "arithmetic_clear",
        "placement": "not_evaluated",
        "cloud_feasibility": "not_evaluated",
        "scenarios": output,
    }


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InputError("Duplicate JSON object key.")
        result[key] = value
    return result


def reject_constant(_value):
    raise InputError("Non-finite JSON numbers are invalid.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT:
            raise InputError("Input exceeds 2 MiB.")
        model = json.loads(
            raw, object_pairs_hook=unique_object, parse_constant=reject_constant
        )
        result = calculate(model)
    except (InputError, ValueError, UnicodeError, RecursionError):
        print(json.dumps({"error": "INVALID_INPUT", "hint": "See references/calculator.md."}))
        return 2
    print(json.dumps(result, indent=2, allow_nan=False))
    return 1 if result["status"] == "violations_found" else 0


if __name__ == "__main__":
    sys.exit(main())
