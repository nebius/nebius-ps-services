#!/usr/bin/env python3
"""Offline quota-reserve arithmetic; never authenticates or submits requests."""

import argparse
import json
import sys
from decimal import Decimal, InvalidOperation, ROUND_CEILING, localcontext

MAX_INPUT = 1024 * 1024


def integer(value, field, nullable=False):
    if nullable and value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a nonnegative integer")
    if value > 2**63 - 1:
        raise ValueError(f"{field} exceeds signed 64-bit provider units")
    return value


def required_text(row, field):
    value = row.get(field)
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ValueError(f"{field} must be nonempty text of at most 256 characters")
    return value


def plan(data):
    if not isinstance(data, dict):
        raise ValueError("input must be an object")
    raw = data.get("multiplier", "1")
    if isinstance(raw, bool) or not isinstance(raw, (str, int, float, Decimal)):
        raise ValueError("multiplier must be a finite decimal at least 1")
    try:
        multiplier = Decimal(str(raw))
    except InvalidOperation as exc:
        raise ValueError("invalid multiplier") from exc
    if not multiplier.is_finite() or multiplier < 1 or multiplier > 2**63 - 1:
        raise ValueError("multiplier must be finite, at least 1 and bounded")
    complete = data.get("collection_complete")
    if not isinstance(complete, bool):
        raise ValueError("collection_complete must be boolean")
    rows = data.get("dimensions")
    if not isinstance(rows, list) or not rows or len(rows) > 1000:
        raise ValueError("dimensions must contain 1 to 1000 aggregated rows")
    result, candidates, seen = [], [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("dimension must be an object")
        identity = {
            k: required_text(row, k) for k in ("scope", "region", "quota_name", "unit")
        }
        key = tuple(identity[k] for k in ("scope", "region", "quota_name"))
        if key in seen:
            raise ValueError("duplicate scoped quota dimension; aggregate demand first")
        seen.add(key)
        envelope = integer(row.get("envelope"), "envelope")
        limit = integer(row.get("limit"), "limit", nullable=True)
        usage = integer(row.get("usage"), "usage", nullable=True)
        increment = integer(row.get("increment", 1), "increment")
        if increment == 0:
            raise ValueError("increment must be positive")
        with localcontext() as ctx:
            ctx.prec = max(80, len(multiplier.as_tuple().digits) + 40)
            free = int(
                (Decimal(envelope) * multiplier).to_integral_value(
                    rounding=ROUND_CEILING
                )
            )
        integer(free, "required_free")
        known = (
            limit is not None
            and usage is not None
            and row.get("state") == "STATE_ACTIVE"
            and row.get("usage_state") in ("USAGE_STATE_USED", "USAGE_STATE_NOT_USED")
        )
        if row.get("usage_state") == "USAGE_STATE_NOT_USED" and usage not in (None, 0):
            raise ValueError("NOT_USED conflicts with positive usage")
        item = {
            **identity,
            "envelope": envelope,
            "required_free": free,
            "limit": limit,
            "usage": usage,
            "headroom": None,
            "minimum_total": None,
            "shortfall": None,
            "increase": None,
            "status": "unresolved",
            "request_candidate": False,
        }
        if known:
            total = ((usage + free + increment - 1) // increment) * increment
            integer(total, "minimum_total")
            shortfall = max(0, free - (limit - usage))
            item.update(
                headroom=limit - usage,
                minimum_total=total,
                shortfall=shortfall,
                increase=max(0, total - limit) if shortfall else 0,
                status="insufficient" if shortfall else "sufficient",
                request_candidate=bool(shortfall and complete),
            )
            if item["request_candidate"]:
                candidates.append({**identity, "requested_limit": total})
        result.append(item)
    return {
        "multiplier": str(multiplier),
        "collection_complete": complete,
        "dimensions": result,
        "request_candidates": candidates,
        "boundary": "arithmetic only; verify scope, identity, freshness, duplicates and authorization before submission",
    }


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT:
            raise ValueError("input exceeds 1 MiB")
        output = plan(
            json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=unique_object,
                parse_float=Decimal,
            )
        )
    except InvalidOperation:
        error = "invalid decimal number"
    except UnicodeError:
        error = "input must be valid UTF-8 JSON"
    except RecursionError:
        error = "input nesting exceeds parser limits"
    except (ValueError, TypeError, OverflowError) as exc:
        error = str(exc)
    else:
        print(json.dumps(output, indent=2, allow_nan=False))
        return 0
    print(json.dumps({"error": error}), file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
