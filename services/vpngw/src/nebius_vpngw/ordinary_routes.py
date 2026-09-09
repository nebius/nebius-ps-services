"""Ordinary static-route ownership, approval and durable retirement (stdlib).

This exact source is also streamed to older guests. Historical absence evidence
is never itself deletion authority. The ordinary operation remains the sole writer.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import _vpngw_ordinary_operations as operations  # type: ignore[import-not-found]
    from _vpngw_tunnel_state import collect_tunnel_state  # type: ignore[import-not-found]
except ModuleNotFoundError:
    from . import ordinary_operations as operations
    from .tunnel_state import collect_tunnel_state

LIMIT = 1024 * 1024
MAX_ITEMS = 4096
PLAN_SCHEMA = "nebius-vpngw.ordinary-route-plan.v1"
STORE_SCHEMA = "nebius-vpngw.ordinary-route-store.v1"
PROOF_KEY = "route_retirement_sha256"
OWNERSHIP_SOURCES = (
    "ordinary_routes.py",
    "tunnel_state.py",
    "agent/ordinary.py",
    "agent/strongswan_renderer.py",
)


def digest(value: Any) -> str:
    return operations.digest(value)


def require(condition: bool, code: str = "ordinary_route_ownership_unverified") -> None:
    if not condition:
        raise RuntimeError(code)


def streamed_bootstrap() -> str:
    from_source = []
    for name, path in (
        ("_vpngw_tunnel_state", Path(__file__).with_name("tunnel_state.py")),
        ("_vpngw_ordinary_routes", Path(__file__)),
    ):
        raw = base64.b64encode(path.read_bytes()).decode()
        from_source.append(
            f"_m=types.ModuleType({name!r});sys.modules[_m.__name__]=_m;"
            f"exec(compile(base64.b64decode({raw!r}),{name!r},'exec'),_m.__dict__);"
        )
    return "import base64,sys,types;" + "".join(from_source)


def prefix(value: str) -> str:
    return str(ipaddress.IPv4Network("0.0.0.0/0" if value == "default" else value, strict=False))


def projection(cfg: dict[str, Any] | None) -> list[dict[str, Any]]:
    if cfg is None:
        return []
    require(isinstance(cfg, dict) and cfg.get("vm_ha") is None)
    require(isinstance(cfg.get("connections", []), list))
    _, _, endpoints = collect_tunnel_state(cfg, log=lambda message: None)
    result = []
    for endpoint in endpoints:
        mode = endpoint["mode"]
        require(mode in {"static", "bgp"})
        result.append(
            {
                "name": endpoint["name"],
                "if_id": endpoint["if_id"],
                "mode": mode,
                "prefixes": sorted({prefix(p) for p in endpoint["remote_prefixes"]})
                if mode == "static"
                else [],
                "peer": prefix(endpoint["remote_inner_ip"] + "/32")
                if mode == "bgp" and endpoint.get("remote_inner_ip")
                else None,
                "inner": [endpoint["local_inner_ip"], prefix(endpoint["cidr"])]
                if endpoint.get("local_inner_ip") and endpoint.get("cidr")
                else None,
            }
        )
    require(len(result) <= MAX_ITEMS)
    return result


def effective_static_routes(endpoints: list[dict]) -> dict[str, str]:
    """Final ordinary forwarding after ordered replacements, not historical claims.

    Consume only the current per-VM projection: recovery sources are deduplicated
    and sorted independently of tunnel precedence. Keep every claim in keys().
    """
    return {
        prefix(destination): endpoint["name"]
        for endpoint in endpoints
        if endpoint["mode"] == "static"
        for destination in endpoint["prefixes"]
    }


def keys(endpoints: list[dict], *, static_only: bool = False) -> set[tuple[str, str]]:
    return {
        (e["name"], p)
        for e in endpoints
        for p in (
            e["prefixes"]
            if e["mode"] == "static"
            else []
            if static_only or not e["peer"]
            else [e["peer"]]
        )
    }


def _read(path: Path, *, private: bool = False) -> tuple[bytes, Any] | None:
    try:
        parent = path.parent.lstat()
        require(
            stat.S_ISDIR(parent.st_mode)
            and parent.st_uid == os.geteuid()
            and not parent.st_mode & (0o077 if private else 0o022),
            "ordinary_route_store_unsafe",
        )
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    try:
        info = os.fstat(fd)
        require(
            stat.S_ISREG(info.st_mode)
            and info.st_uid == os.geteuid()
            and info.st_nlink == 1
            and not info.st_mode & (0o077 if private else 0o022),
            "ordinary_route_store_unsafe",
        )
        raw = os.read(fd, LIMIT + 1)
        require(len(raw) <= LIMIT, "ordinary_route_record_too_large")
        fields = (
            "st_dev",
            "st_ino",
            "st_uid",
            "st_gid",
            "st_mode",
            "st_nlink",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        require(
            all(
                getattr(other, field) == getattr(info, field)
                for other in (os.fstat(fd), path.stat(follow_symlinks=False))
                for field in fields
            ),
            "ordinary_route_store_changed",
        )
        return raw, json.loads(raw)
    finally:
        os.close(fd)


def _fsync(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write(path: Path, value: Any) -> None:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    require(len(raw) <= LIMIT, "ordinary_route_record_too_large")
    for directory in (path.parent.parent, path.parent):
        if not directory.exists():
            missing = []
            cursor = directory
            while not cursor.exists():
                missing.append(cursor)
                cursor = cursor.parent
            for item in reversed(missing):
                item.mkdir(mode=0o700)
                _fsync(item.parent)
        info = directory.lstat()
        require(
            stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid() and not info.st_mode & 0o077,
            "ordinary_route_store_unsafe",
        )
    # Validate an existing target before replacing it; never follow an alias.
    _read(path, private=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".route-")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _deduplicate(values: list[dict]) -> list[dict]:
    unique = {digest(item): item for item in values}
    require(len(unique) <= MAX_ITEMS, "ordinary_route_record_too_large")
    return [unique[key] for key in sorted(unique)]


def _hex(value: Any, length: int = 64) -> bool:
    return (
        isinstance(value, str) and re.fullmatch(r"[a-f0-9]{" + str(length) + "}", value) is not None
    )


def _binding(value: Any) -> None:
    require(isinstance(value, dict) and set(value) == {"id", "config", "artifact", "predecessor"})
    require(_hex(value["id"], 32) and all(_hex(value[k]) for k in value if k != "id"))


def _prefix(value: Any) -> None:
    require(isinstance(value, str) and prefix(value) == value)


def _items(value: Any) -> None:
    require(isinstance(value, list) and len(value) <= MAX_ITEMS)


def _identity(value: Any) -> None:
    require(
        isinstance(value, dict)
        and set(value) == {"name", "ifindex", "kind", "if_id", "parent", "parent_ifindex", "boot"}
    )
    require(
        isinstance(value["name"], str)
        and re.fullmatch(r"xfrm[0-9]+", value["name"]) is not None
        and value["kind"] == "xfrm"
        and all(type(value[k]) is int and value[k] > 0 for k in ("ifindex", "if_id", "parent"))
        and value["if_id"] == 100 + int(value["name"][4:])
        and type(value["parent_ifindex"]) is int
        and value["parent_ifindex"] == value["parent"]
        and isinstance(value["boot"], str)
        and re.fullmatch(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", value["boot"]) is not None
    )


def _obligations(value: Any) -> None:
    _items(value)
    for item in value:
        require(isinstance(item, dict) and set(item) == {"link", "prefix"})
        _identity(item["link"])
        _prefix(item["prefix"])


def _endpoints(value: Any) -> None:
    _items(value)
    for item in value:
        require(
            isinstance(item, dict)
            and set(item) == {"name", "if_id", "mode", "prefixes", "peer", "inner"}
            and isinstance(item["name"], str)
            and re.fullmatch(r"xfrm[0-9]+", item["name"]) is not None
            and type(item["if_id"]) is int
            and item["if_id"] == 100 + int(item["name"][4:])
            and item["mode"] in {"static", "bgp"}
        )
        _items(item["prefixes"])
        for dest in item["prefixes"]:
            _prefix(dest)
        if item["peer"] is not None:
            _prefix(item["peer"])
        require(not item["prefixes"] if item["mode"] == "bgp" else item["peer"] is None)
        if item["inner"] is not None:
            require(isinstance(item["inner"], list) and len(item["inner"]) == 2)
            require(str(ipaddress.IPv4Address(item["inner"][0])) == item["inner"][0])
            _prefix(item["inner"][1])


def _route(row: dict) -> dict:
    result = dict(row)
    result.update(
        dst=prefix(row.get("dst", "default")),
        table=254 if str(row.get("table", "main")) in {"main", "254"} else row.get("table"),
        type=row.get("type", "unicast"),
        protocol={"boot": 3, "kernel": 2, "bgp": 186}.get(
            row.get("protocol", "boot"), row.get("protocol", 3)
        ),
        scope={"link": 253, "global": 0, "host": 254}.get(
            row.get("scope", "global"), row.get("scope", 0)
        ),
        metric=row.get("metric", 0),
        tos=row.get("tos", 0),
        flags=row.get("flags", []),
    )
    return result


def _devices(row: dict) -> set[str]:
    return {row.get("dev"), *(hop.get("dev") for hop in row.get("nexthops", []))} - {None}


def _relevant(row: dict, selected: set[tuple[str, str]]) -> bool:
    return (
        row["table"] == 254
        and row["type"] == "unicast"
        and row["protocol"] not in {2, 186}
        and any((name, row["dst"]) in selected for name in _devices(row))
    )


def _simple(row: dict, name: str) -> bool:
    return (
        set(row) == {"dst", "dev", "table", "type", "protocol", "scope", "metric", "tos", "flags"}
        and row["dev"] == name
        and row["protocol"] == 3
        and row["scope"] == 253
        and row["metric"] == 0
        and row["tos"] == 0
        and isinstance(row["flags"], list)
        and set(row["flags"]) <= {"linkdown"}
    )


def kernel(
    runner,
    names: set[str],
    selected: set[tuple[str, str]],
    boot: str,
    known_indices: set[int] | None = None,
) -> dict:
    if not names and not selected and not known_indices:
        return {"boot": boot, "links": [], "routes": []}

    def read(args):
        result = runner(args, capture_output=True, text=True)
        require(result.returncode == 0, "ordinary_route_observation_failed")
        require(len(result.stdout.encode()) <= LIMIT, "ordinary_route_record_too_large")
        value = json.loads(result.stdout)
        require(isinstance(value, list) and all(isinstance(v, dict) for v in value))
        return value

    links = read(["ip", "-d", "-j", "link", "show"])
    parent = next((row for row in links if row.get("ifname") == "eth0"), None)
    identities = []
    for row in links:
        if row.get("ifname") not in names and row.get("ifindex") not in (known_indices or set()):
            continue
        info = row.get("linkinfo") or {}
        if_id = (info.get("info_data") or {}).get("if_id")
        if isinstance(if_id, str):
            if_id = int(if_id, 0)
        identities.append(
            {
                "name": row["ifname"],
                "ifindex": row.get("ifindex"),
                "kind": info.get("info_kind"),
                "if_id": if_id,
                "parent": row.get("link_index")
                if row.get("link_index") is not None
                else (parent or {}).get("ifindex")
                if row.get("link") == "eth0"
                else None,
                "parent_ifindex": (parent or {}).get("ifindex"),
                "boot": boot,
            }
        )
    routes = [_route(row) for row in read(["ip", "-j", "-4", "route", "show", "table", "all"])]
    return {
        "boot": boot,
        "links": _deduplicate(identities),
        "routes": _deduplicate([row for row in routes if _relevant(row, selected)]),
    }


def _link(observed: dict, name: str, expected_id: int) -> dict | None:
    found = [row for row in observed["links"] if row["name"] == name]
    require(len(found) <= 1)
    if not found:
        return None
    row = found[0]
    require(
        row["kind"] == "xfrm"
        and row["if_id"] == expected_id
        and type(row["ifindex"]) is int
        and row["ifindex"] > 0
        and type(row["parent"]) is int
        and row["parent"] == row["parent_ifindex"]
    )
    return row


def _retained(obligations: list[dict], observed: dict, desired: list[dict]) -> list[dict]:
    _obligations(obligations)
    wanted = keys(desired)
    retained = []
    for item in obligations:
        link = item["link"]
        if (link["name"], item["prefix"]) in wanted or link["boot"] != observed["boot"]:
            continue
        same = [v for v in observed["links"] if v["ifindex"] == link["ifindex"]]
        if not same:
            continue  # old kernel interface no longer exists
        require(same == [link], "ordinary_route_interface_changed")
        retained.append(item)
    return _deduplicate(retained)


@dataclass(frozen=True)
class RouteRetirementPlan:
    payload: dict[str, Any]

    def __post_init__(self) -> None:
        value = self.payload
        require(
            isinstance(value, dict)
            and set(value)
            == {
                "schema",
                "snapshot",
                "sources",
                "desired",
                "accepted_history",
                "retired",
                "deletions",
                "obligations",
            }
            and value["schema"] == PLAN_SCHEMA
            and _hex(value["snapshot"])
            and (value["accepted_history"] is None or _hex(value["accepted_history"]))
        )
        _endpoints(value["sources"])
        _endpoints(value["desired"])
        _obligations(value["obligations"])
        _items(value["retired"])
        require(
            value["retired"]
            == [
                list(k)
                for k in sorted(keys(value["sources"], static_only=True) - keys(value["desired"]))
            ]
        )
        _items(value["deletions"])
        for item in value["deletions"]:
            require(isinstance(item, dict) and set(item) == {"link", "route"})
            _identity(item["link"])
            row, name = item["route"], item["link"]["name"]
            require(
                isinstance(row, dict)
                and _simple(row, name)
                and row["table"] == 254
                and row["type"] == "unicast"
                and [name, row["dst"]] in value["retired"]
                and {"link": item["link"], "prefix": row["dst"]} in value["obligations"]
            )
            _prefix(row["dst"])

    @property
    def digest(self) -> str:
        return digest(self.payload)

    @property
    def retirement(self) -> bool:
        return bool(self.payload["retired"])

    @classmethod
    def build(cls, observed: dict, desired: list[dict]) -> RouteRetirementPlan:
        require(observed["status"] in {"valid", "missing"}, "ordinary_route_state_invalid")
        if observed["status"] == "missing":
            require(
                not observed["configured"] or observed["current"] == desired,
                "ordinary_route_history_required",
            )
        sources = _deduplicate(observed["previous"] + observed["pending_sources"])
        authorized = keys(sources, static_only=True)
        retired = authorized - keys(desired)
        old = _retained(observed["obligations"], observed["kernel"], desired)
        deletions = []
        obligations = list(old)
        for name, dest in sorted(retired):
            expected_ids = {e["if_id"] for e in sources if e["name"] == name}
            require(len(expected_ids) == 1)
            link = _link(observed["kernel"], name, next(iter(expected_ids)))
            rows = [row for row in observed["kernel"]["routes"] if _relevant(row, {(name, dest)})]
            require(
                len(rows) <= 1 and all(_simple(row, name) for row in rows),
                "ordinary_route_shape_conflict",
            )
            require(not rows or link is not None)
            if link:
                obligations.append({"link": link, "prefix": dest})
            if rows:
                deletions.append({"link": link, "route": rows[0]})
        for item in old:
            key = (item["link"]["name"], item["prefix"])
            if key not in retired:
                require(
                    not any(_relevant(row, {key}) for row in observed["kernel"]["routes"]),
                    "ordinary_retired_route_reappeared",
                )
        return cls(
            {
                "schema": PLAN_SCHEMA,
                "snapshot": digest(observed),
                "sources": sources,
                "desired": desired,
                "accepted_history": observed["history_digest"],
                "retired": [list(k) for k in sorted(retired)],
                "deletions": deletions,
                "obligations": _deduplicate(obligations),
            }
        )


class RouteStore:
    def __init__(self):
        self.root = operations.JOURNAL.parent / "routes"
        self.history = self.root / "history.json"

    def ledger(self) -> dict | None:
        read = _read(self.history, private=True)
        if read is None:
            return None
        value = read[1]
        require(
            isinstance(value, dict)
            and set(value) == {"schema", "binding", "obligations"}
            and value["schema"] == STORE_SCHEMA
            and isinstance(value["obligations"], list)
            and len(value["obligations"]) <= MAX_ITEMS,
            "ordinary_route_ledger_invalid",
        )
        _binding(value["binding"])
        _obligations(value["obligations"])
        return value

    def envelope(self, operation: dict) -> dict:
        require(re.fullmatch(r"[a-f0-9]{32}", operation["id"]) is not None)
        read = _read(self.root / (operation["id"] + ".json"), private=True)
        require(read is not None, "ordinary_route_plan_missing")
        assert read is not None
        value = read[1]
        require(
            isinstance(value, dict)
            and set(value) == {"schema", "binding", "plan", "plan_digest", "approved"}
            and value["schema"] == STORE_SCHEMA
            and value["binding"] == self.binding(operation)
            and value["plan_digest"] == digest(value["plan"])
            and value["plan"]["schema"] == PLAN_SCHEMA
            and type(value["approved"]) is bool,
            "ordinary_route_plan_invalid",
        )
        _binding(value["binding"])
        RouteRetirementPlan(value["plan"])
        return value

    @staticmethod
    def binding(operation: dict) -> dict:
        return {key: operation[key] for key in ("id", "config", "artifact", "predecessor")}

    def prepare(self, operation: dict, plan: RouteRetirementPlan, *, approved: bool) -> None:
        require(approved or not plan.retirement, "ordinary_route_approval_required")
        value = {
            "schema": STORE_SCHEMA,
            "binding": self.binding(operation),
            "plan": plan.payload,
            "plan_digest": plan.digest,
            "approved": approved,
        }
        path = self.root / (operation["id"] + ".json")
        old = _read(path, private=True)
        require(old is None or old[1] == value, "ordinary_route_plan_changed")
        _write(path, value)
        self.compact(operation["id"])

    def compact(self, prepared_id: str) -> None:
        """Discard only sidecars no longer referenced by a journal or ledger.

        Caller holds ordinary admission and routing locks. The prepared sidecar
        is durable before pruning; a prior pending operation retains its source.
        """
        journal, ledger = operations.Journal().read(), self.ledger()
        keep = {prepared_id}
        if journal is not None:
            keep.add(journal["id"])
        if ledger is not None:
            keep.add(ledger["binding"]["id"])
        paths: list[Path] = []
        with os.scandir(self.root) as entries:
            for entry in entries:
                require(len(paths) < MAX_ITEMS, "ordinary_route_record_too_large")
                if re.fullmatch(r"[a-f0-9]{32}\.json", entry.name):
                    paths.append(Path(entry.path))
        for path in paths:
            if path.stem not in keep:
                _read(path, private=True)
                path.unlink()
        if paths:
            _fsync(self.root)

    def snapshot(
        self,
        cfg: dict | None,
        state: Path,
        runner,
        boot: str,
        *,
        proof: Path | None = None,
        future: list[dict] | None = None,
    ) -> dict:
        status, state_digest, previous = "missing", None, []
        read = _read(state)
        if read is not None:
            raw, value = read
            state_digest = hashlib.sha256(raw).hexdigest()
            require(
                isinstance(value, dict)
                and value.get("render_version") == 4
                and isinstance(value.get("resolved_config"), dict),
                "ordinary_route_state_invalid",
            )
            expected = hashlib.sha256(
                json.dumps(
                    {"config": value["resolved_config"], "render_version": value["render_version"]},
                    sort_keys=True,
                ).encode()
            ).hexdigest()
            require(value.get("config_hash") == expected, "ordinary_route_state_invalid")
            previous, status = projection(value["resolved_config"]), "valid"
        operation = operations.Journal().read()
        ledger = self.ledger()
        pending = None
        if (
            operation is not None
            and operation["purpose"] == "ordinary"
            and operation["state"] != "complete"
        ):
            pending = self.envelope(operation)
        if proof is not None:
            saved = _read(proof)
            if saved and isinstance(saved[1], dict) and PROOF_KEY in saved[1]:
                expected = saved[1][PROOF_KEY]
                require(
                    ledger is not None
                    and (
                        digest(ledger) == expected
                        or (
                            pending is not None
                            and (
                                (
                                    ledger["binding"] == pending["binding"]
                                    and all(
                                        i in pending["plan"]["obligations"]
                                        for i in ledger["obligations"]
                                    )
                                )
                                or digest(ledger) == pending["plan"]["accepted_history"]
                            )
                        )
                    ),
                    "ordinary_route_ledger_missing_or_changed",
                )
        current = projection(cfg)
        future = future or []
        pending_sources = (
            [] if pending is None else pending["plan"]["sources"] + pending["plan"]["desired"]
        )
        obligations = ([] if ledger is None else ledger["obligations"]) + (
            [] if pending is None else pending["plan"]["obligations"]
        )
        selected = keys(previous + current + pending_sources + future) | {
            (i["link"]["name"], i["prefix"]) for i in obligations
        }
        names = {e["name"] for e in previous + current + pending_sources + future} | {
            i["link"]["name"] for i in obligations
        }
        return {
            "configured": cfg is not None,
            "status": status,
            "state_digest": state_digest,
            "previous": previous,
            "current": current,
            "pending_digest": digest(pending) if pending else None,
            "pending_sources": _deduplicate(pending_sources),
            "history_digest": digest(ledger) if ledger else None,
            "obligations": _deduplicate(obligations),
            "kernel": kernel(
                runner, names, selected, boot, {i["link"]["ifindex"] for i in obligations}
            ),
        }

    def verify(
        self, cfg: dict, runner, boot: str, *, obligations: list[dict] | None = None
    ) -> list[dict]:
        desired = projection(cfg)
        if obligations is None:
            ledger = self.ledger()
            obligations = ledger["obligations"] if ledger else []
        if not obligations:
            return []
        selected = {(i["link"]["name"], i["prefix"]) for i in obligations}
        observed = kernel(
            runner,
            {k[0] for k in selected},
            selected,
            boot,
            {i["link"]["ifindex"] for i in obligations},
        )
        retained = _retained(obligations, observed, desired)
        for item in retained:
            key = (item["link"]["name"], item["prefix"])
            require(
                not any(_relevant(row, {key}) for row in observed["routes"]),
                "ordinary_retired_route_present",
            )
        return retained

    def cleanup(self, operation, cfg: dict, runner, boot: str) -> list[dict]:
        envelope = self.envelope(operation.check())
        plan = RouteRetirementPlan(envelope["plan"])
        require(plan.payload["desired"] == projection(cfg), "ordinary_route_desired_changed")
        require(envelope["approved"] or not plan.retirement, "ordinary_route_approval_required")
        for item in plan.payload["deletions"]:
            operation.check()
            link, row = item["link"], item["route"]
            key = (link["name"], row["dst"])
            observed = kernel(runner, {key[0]}, {key}, boot, {link["ifindex"]})
            require(
                boot == link["boot"] and _link(observed, key[0], link["if_id"]) == link,
                "ordinary_route_interface_changed",
            )
            if not observed["routes"]:
                continue
            require(observed["routes"] == [row], "ordinary_route_changed")
            result = runner(
                [
                    "ip",
                    "-4",
                    "route",
                    "del",
                    "unicast",
                    row["dst"],
                    "table",
                    "254",
                    "proto",
                    "3",
                    "scope",
                    "link",
                    "metric",
                    "0",
                    "tos",
                    "0",
                    "dev",
                    key[0],
                ],
                capture_output=True,
                text=True,
            )
            require(result.returncode == 0, "ordinary_route_delete_failed")
            after = kernel(runner, {key[0]}, {key}, boot)
            require(not after["routes"], "ordinary_route_delete_unverified")
        return self.verify(cfg, runner, boot, obligations=plan.payload["obligations"])

    def commit(self, operation: dict, obligations: list[dict]) -> str:
        value = {
            "schema": STORE_SCHEMA,
            "binding": self.binding(operation),
            "obligations": _deduplicate(obligations),
        }
        _write(self.history, value)
        return digest(value)
