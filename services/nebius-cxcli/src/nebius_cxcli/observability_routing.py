"""Target-owned telemetry routing and Grafana query contracts.

Write destinations, collectors and query connections are deliberately independent.
Resolution never talks to a cluster or writes a config file. Native identities
are read from the verified frozen Soperator source.
"""

from __future__ import annotations

import copy
import hashlib
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from .component_sources import load_component_sources
from .deploy_targets import enabled_cluster_target_refs
from .runtime_config import to_plain_data

if TYPE_CHECKING:
    from .soperator_release_resolver import SoperatorObservabilityDefaults


@dataclass
class _NativeDefaultsScope:
    emit: Callable[[str], None] | None
    releases: dict[str, SoperatorObservabilityDefaults] = field(default_factory=dict)
    announced_targets: set[str] = field(default_factory=set)


_NATIVE_DEFAULTS: ContextVar[_NativeDefaultsScope | None] = ContextVar(
    "observability_native_defaults", default=None
)


@contextmanager
def native_defaults_scope(emit: Callable[[str], None] | None = None) -> Iterator[None]:
    """Reuse source-only data within one caller-owned operation; never admission."""
    if _NATIVE_DEFAULTS.get() is not None:
        yield
        return
    token = _NATIVE_DEFAULTS.set(_NativeDefaultsScope(emit))
    try:
        yield
    finally:
        _NATIVE_DEFAULTS.reset(token)


SIGNALS = ("metrics", "logs", "traces")
STORAGE_MODES = ("local", "remote", "both")
PROTOCOLS = ("otlp-grpc", "otlp-http")
STORE_APPS = {
    "metrics": "victoria-metrics-k8s-stack",
    "logs": "victoria-logs-single",
    "traces": "victoria-traces-single",
}
LOCAL_TYPES = {
    "metrics": "prometheus",
    "logs": "victoriametrics-logs-datasource",
    "traces": "jaeger",
}
REMOTE_TYPES = {"metrics": "prometheus", "logs": "loki", "traces": "tempo"}
WRITE_KEYS = {
    "metrics": "metrics_prometheus_remote_write",
    "logs": "logs_agent_grpc_write",
    "traces": "traces_otlp_grpc_write",
}
READ_KEYS = {
    "metrics": "metrics_user_read",
    "logs": "logs_loki_read",
    "traces": "traces_tempo_read",
}


def target_settings(payload: Any, target: str) -> dict[str, Any]:
    data = to_plain_data(payload)
    for row in data.get("deploy", {}).get("targets", []):
        if row.get("instance_id") == target:
            return copy.deepcopy(row.get("observability", {}).get("routing", {}))
    return {}


def routed_targets(payload: Any) -> tuple[str, ...]:
    data = to_plain_data(payload)
    return tuple(
        row["instance_id"]
        for row in data.get("deploy", {}).get("targets", [])
        if row.get("observability", {}).get("routing")
    )


def validate_observability_target_refs(
    payload: Mapping[str, Any], target_refs: frozenset[str] | None
) -> None:
    """Validate explicit materialization scope without resolving backend sources."""
    if target_refs is not None:
        unknown = target_refs - set(enabled_cluster_target_refs(payload))
        if unknown:
            target = sorted(unknown)[0]
            raise ValueError(
                f"Target {target!r} is not an enabled managed MK8s target in this config"
            )


def describe_target(payload: Mapping[str, Any], target: str) -> None:
    """Explain configured ownership once, including lookups during config loading."""
    scope = _NATIVE_DEFAULTS.get()
    if scope is None or scope.emit is None or target in scope.announced_targets:
        return
    row = app_row(payload, target, "soperator")
    if row:
        scope.emit(
            f"Target {target} is configured with Soperator {row['version']}. "
            "Its metrics and logs collectors will be reused."
        )
    else:
        scope.emit(
            f"Target {target}: managed MK8s. "
            "Telemetry backends will be configured for your storage choices."
        )
    scope.announced_targets.add(target)


def endpoint_defaults(payload: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    identity = payload.get("client_info", {}).get("nebius", {})
    substitutions = {
        "project_id": identity.get("project_id", ""),
        "region": identity.get("region_id", ""),
    }
    catalog = load_component_sources().observability.endpoints
    result: dict[str, dict[str, str]] = {}
    for signal in SIGNALS:
        write = next(item.template for item in catalog.write if item.key == WRITE_KEYS[signal])
        read = next(item.template for item in catalog.read if item.key == READ_KEYS[signal])
        result[signal] = {
            "write": write.format(**substitutions).replace("dns:///", "https://"),
            "read": read.format(**substitutions),
        }
    return result


def validate_url(value: str, *, label: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
        or parsed.query
    ):
        raise ValueError(f"{label} must be an HTTP(S) URL without credentials, query or fragment")
    return value.rstrip("/")


def _secret(value: Any, *, label: str) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {"name", "key"}:
        raise ValueError(f"{label} requires a Secret reference with name and key")
    if not all(
        isinstance(item, str) and re.fullmatch(r"[a-zA-Z0-9_.-]+", item) for item in value.values()
    ):
        raise ValueError(f"{label} contains an invalid Secret name or key")
    return dict(value)


def _validate_saved_settings(settings: Any) -> None:
    """Reject malformed or misspelled authored routing before resolving defaults."""

    def mapping(value: Any, keys: set[str], label: str) -> None:
        if not isinstance(value, Mapping):
            raise ValueError(f"{label} must be a mapping")
        if set(value) - keys:
            raise ValueError(
                f"{label} has unsupported fields: {', '.join(sorted(set(value) - keys))}"
            )

    mapping(
        settings,
        {
            *SIGNALS,
            "signals",
            "pushgateway",
            "datasources",
            "default_datasource",
            "default_datasource_explicit",
            "local_stores",
            "collectors",
            "grafana_access",
            "native_backends",
        },
        "routing",
    )
    for signal in SIGNALS:
        item = settings.get(signal, {})
        mapping(item, {"storage", "remote"}, f"routing.{signal}")
        remote = item.get("remote", {})
        mapping(remote, {"url", "protocol", "auth", "auth_secret"}, f"routing.{signal}.remote")
        for key in ("url", "protocol", "auth"):
            if key in remote and not isinstance(remote[key], str):
                raise ValueError(f"routing.{signal}.remote.{key} must be a string")
    for key in ("signals", "local_stores"):
        if key in settings and (
            not isinstance(settings[key], list)
            or any(not isinstance(item, str) or item not in SIGNALS for item in settings[key])
        ):
            raise ValueError(f"routing.{key} must be a list of supported signals")
    for key in ("pushgateway", "default_datasource_explicit"):
        if key in settings and not isinstance(settings[key], bool):
            raise ValueError(f"routing.{key} must be a boolean")
    if "default_datasource" in settings and not isinstance(settings["default_datasource"], str):
        raise ValueError("routing.default_datasource must be a name")
    if settings.get("grafana_access", "private") not in {"private", "existing"}:
        raise ValueError("routing.grafana_access must be private or existing")
    mapping(settings.get("collectors", {}), set(SIGNALS), "routing.collectors")
    mapping(
        settings.get("native_backends", {}),
        {"vmStack", "vmLogs", "opentelemetry"},
        "routing.native_backends",
    )
    for key, value in settings.get("native_backends", {}).items():
        mapping(value, {"releaseName", "namespace", "queryUrl"}, f"routing.native_backends.{key}")
    sources = settings.get("datasources", [])
    if not isinstance(sources, list):
        raise ValueError("routing.datasources must be a list")
    for item in sources:
        mapping(item, {"name", "type", "url", "auth", "auth_secret"}, "routing.datasources[]")


def resolve_settings(
    payload: Mapping[str, Any],
    target: str,
    *,
    overrides: Mapping[str, Any] | None = None,
    datasources: Sequence[tuple[str, str, str]] = (),
    require_read_connections: bool = True,
) -> dict[str, Any]:
    """Explicit values win over saved values; defaults only fill unset fields."""
    managed_targets = {
        row.get("instance_id")
        for row in payload.get("infra", {}).get("components", [])
        if row.get("id") == "mk8s" and row.get("enabled") is True
    }
    if target not in managed_targets:
        raise ValueError(f"Target {target!r} is not an enabled managed MK8s target in this config")
    saved = target_settings(payload, target)
    _validate_saved_settings(saved)
    result = copy.deepcopy(saved)
    flags = dict(overrides or {})
    enabled_signals = result.setdefault("signals", list(SIGNALS))
    if (
        not isinstance(enabled_signals, list)
        or not enabled_signals
        or len(set(enabled_signals)) != len(enabled_signals)
        or not set(enabled_signals) <= set(SIGNALS)
    ):
        raise ValueError("routing.signals must list unique supported signals")
    defaults = endpoint_defaults(payload)
    for signal in SIGNALS:
        settings = result.setdefault(signal, {})
        settings["storage"] = flags.get(f"{signal}_storage") or settings.get("storage", "local")
        if settings["storage"] not in STORAGE_MODES:
            raise ValueError(f"{signal}.storage must be local, remote or both")
        remote = settings.setdefault("remote", {})
        if flags.get(f"{signal}_remote") and flags[f"{signal}_remote"].rstrip("/") != remote.get(
            "url"
        ):
            remote.pop("auth", None)
            remote.pop("auth_secret", None)
        remote["url"] = validate_url(
            flags.get(f"{signal}_remote") or remote.get("url") or defaults[signal]["write"],
            label=f"{signal} remote write",
        )
        if signal != "metrics":
            remote["protocol"] = flags.get(f"{signal}_remote_protocol") or remote.get(
                "protocol", "otlp-grpc"
            )
            if remote["protocol"] not in PROTOCOLS:
                raise ValueError(f"{signal}.remote.protocol must be otlp-grpc or otlp-http")
            if remote["protocol"] == "otlp-grpc" and urlsplit(remote["url"]).path:
                raise ValueError(f"{signal}: OTLP gRPC URLs must not contain a path")
            if remote["protocol"] != "otlp-grpc" and remote["url"] == defaults[signal]["write"]:
                raise ValueError(f"{signal}: the default Nebius endpoint requires otlp-grpc")
        if remote.get("auth_secret") is not None:
            remote["auth_secret"] = _secret(remote["auth_secret"], label=f"{signal} write auth")
        if remote["url"] != defaults[signal]["write"] and remote.get("auth") == "nebius":
            raise ValueError(
                f"{signal}: Nebius credentials cannot be sent to a custom write endpoint"
            )
        remote.setdefault(
            "auth", "nebius" if remote["url"] == defaults[signal]["write"] else "none"
        )
        if remote["auth"] not in {"none", "nebius"}:
            raise ValueError(
                f"{signal}.remote.auth must be none or nebius; use auth_secret for custom authentication"
            )
    result["pushgateway"] = (
        flags.get("pushgateway")
        if flags.get("pushgateway") is not None
        else result.get("pushgateway", False)
    )
    if not isinstance(result["pushgateway"], bool):
        raise ValueError("pushgateway must be a boolean")
    connections = copy.deepcopy(result.get("datasources", []))
    for name, kind, url in datasources:
        item = {"name": name, "type": kind, "url": validate_url(url, label=f"datasource {name}")}
        existing = next((row for row in connections if row.get("name") == name), None)
        if existing is None:
            connections.append(item)
            continue
        if (existing.get("auth_secret") or existing.get("auth", "none") != "none") and any(
            existing.get(key) != item[key] for key in ("type", "url")
        ):
            raise ValueError(
                f"Datasource {name} has saved authentication; edit its URL/type and "
                "authentication together in config.yaml"
            )
        existing.update(item)
    result["datasources"] = connections
    if flags.get("default_datasource") == "":
        result.pop("default_datasource", None)
        result["default_datasource_explicit"] = False
    default = flags.get("default_datasource") or result.get("default_datasource")
    if flags.get("default_datasource"):
        result["default_datasource_explicit"] = True
    if not result.get("default_datasource_explicit", False):
        default = None
    default_signal = "metrics" if "metrics" in enabled_signals else enabled_signals[0]
    result["default_datasource"] = default or (
        default_signal + ("-remote" if result[default_signal]["storage"] == "remote" else "-local")
    )
    for connection in connections:
        if not all(
            isinstance(connection.get(key), str) and connection[key].strip()
            for key in ("name", "type", "url")
        ):
            raise ValueError("Each datasource requires name, type and URL")
        if connection["type"] not in {*LOCAL_TYPES.values(), *REMOTE_TYPES.values()}:
            raise ValueError(f"Unsupported managed datasource type {connection['type']!r}")
        for signal in SIGNALS:
            if connection["name"] in {f"{signal}-local", f"{signal}-remote"} and connection[
                "type"
            ] not in {LOCAL_TYPES[signal], REMOTE_TYPES[signal]}:
                raise ValueError(f"Datasource {connection['name']} has an incompatible signal type")
        validate_url(connection["url"], label=f"datasource {connection['name']}")
        _secret(connection.get("auth_secret"), label=f"datasource {connection['name']} auth")
        if connection.get("auth", "none") not in {"none", "nebius"}:
            raise ValueError(
                "Datasource auth must be none or nebius; use auth_secret for custom authentication"
            )
        if connection.get("auth") == "nebius" and connection["url"] not in {
            item["read"] for item in defaults.values()
        }:
            raise ValueError("Nebius credentials cannot be sent to a custom read endpoint")
    if len({item["name"] for item in connections}) != len(connections):
        raise ValueError("Datasource names must be unique")
    for signal in SIGNALS:
        remote = result[signal]["remote"]
        if (
            require_read_connections
            and signal in enabled_signals
            and result[signal]["storage"] != "local"
            and remote["url"] != defaults[signal]["write"]
        ):
            name = f"{signal}-remote"
            if not any(item["name"] == name for item in connections):
                raise ValueError(
                    f"Custom {signal} write URL requires --datasource {name} TYPE READ_URL"
                )
    result["local_stores"] = sorted(
        set(result.get("local_stores", []))
        | {signal for signal in enabled_signals if result[signal]["storage"] != "remote"}
    )
    return result


def save_settings(payload: dict[str, Any], target: str, settings: Mapping[str, Any]) -> None:
    rows = payload.setdefault("deploy", {}).setdefault("targets", [])
    row = next((item for item in rows if item.get("instance_id") == target), None)
    if row is None:
        row = {"instance_id": target}
        rows.append(row)
    row.setdefault("observability", {})["routing"] = copy.deepcopy(dict(settings))
    row["observability"]["enabled"] = True


def app_row(payload: Mapping[str, Any], target: str, component: str) -> dict[str, Any] | None:
    targets = enabled_cluster_target_refs(payload)
    return next(
        (
            row
            for row in payload.get("apps", {}).get("charts", [])
            if row.get("id") == component
            and row.get("enabled")
            and (
                row.get("target_ref")
                or row.get("instance_id")
                or (targets[0] if len(targets) == 1 else "")
            )
            == target
        ),
        None,
    )


def native_values(payload: Mapping[str, Any], target: str) -> dict[str, Any]:
    row = app_row(payload, target, "soperator")
    settings = target_settings(payload, target)
    return settings.get("native_backends", {}) or (
        row.get("values", {}).get("observability", {}) if row else {}
    )


def bind_native_backends(payload: dict[str, Any], target: str, settings: dict[str, Any]) -> None:
    """Resolve service identities from the exact upstream source, not assumed names."""
    row = app_row(payload, target, "soperator")
    if not row:
        return
    describe_target(payload, target)
    from .soperator_release_resolver import (
        current_frozen_soperator_release,
        resolve_soperator_observability_defaults,
    )
    from .soperator_values import with_frozen_observability, with_observability_defaults

    release = row["version"]
    frozen = current_frozen_soperator_release(release, target_ref=target)
    if frozen is not None:
        native = with_frozen_observability(row.get("values", {}), frozen)["observability"]
    else:
        scope = _NATIVE_DEFAULTS.get()
        verified = scope.releases.get(release) if scope is not None else None
        if verified is None:
            verified = resolve_soperator_observability_defaults(
                release, emit=scope.emit if scope is not None else None
            )
            if scope is not None:
                scope.releases[release] = verified
        native = with_observability_defaults(row.get("values", {}), verified.values)[
            "observability"
        ]
    if not native.get("enabled") or not all(
        native.get(key, {}).get("enabled") for key in ("vmStack", "vmLogs")
    ):
        raise ValueError(
            "Native observability stores and collectors must be enabled before configuring Grafana routing"
        )
    settings["native_backends"] = {
        key: {"releaseName": native[key]["releaseName"], "namespace": native[key]["namespace"]}
        for key in ("vmStack", "vmLogs")
    }
    stack = settings["native_backends"]["vmStack"]
    host = f"vmsingle-{helm_fullname(stack['releaseName'], 'victoria-metrics-k8s-stack')}.{stack['namespace']}.svc"
    destinations = (
        native["vmStack"]
        .get("values", {})
        .get("vmagent", {})
        .get("spec", {})
        .get("remoteWrite", [])
    )
    local = [
        item["url"]
        for item in destinations
        if (urlsplit(item["url"]).hostname or "").rstrip(".") in {host, host + ".cluster.local"}
    ]
    if len(local) != 1 or not local[0].endswith("/api/v1/write"):
        raise ValueError(
            "Frozen native VMSingle write endpoint is ambiguous; configure its native internal remoteWrite URL explicitly"
        )
    stack["queryUrl"] = local[0].removesuffix("/api/v1/write")
    settings["native_backends"]["opentelemetry"] = {
        "namespace": native["opentelemetry"]["namespace"]
    }


def helm_fullname(release: str, chart: str) -> str:
    return (release if chart in release else f"{release}-{chart}")[:63].rstrip("-")


def local_url(payload: Mapping[str, Any], target: str, signal: str) -> str:
    native = native_values(payload, target)
    if native and signal in {"metrics", "logs"}:
        if signal == "metrics":
            vm = native.get("vmStack", {})
            if vm.get("queryUrl"):
                return vm["queryUrl"]
            release = vm.get("releaseName", "metrics")
            name = (
                vm.get("overrideValues", {}).get("fullnameOverride")
                if isinstance(vm.get("overrideValues"), dict)
                else None
            )
            name = name or helm_fullname(release, "victoria-metrics-k8s-stack")
            return f"http://vmsingle-{name}.{vm.get('namespace', 'monitoring-system')}.svc:8428"
        logs = native.get("vmLogs", {})
        name = helm_fullname(logs.get("releaseName", "vm-logs"), "victoria-logs-single")
        return f"http://{name}-server.{logs.get('namespace', 'logs-system')}.svc:9428"
    row = app_row(payload, target, STORE_APPS[signal])
    if not row:
        raise ValueError(f"Missing owned {signal} store on target {target}")
    name = row.get("release-name") or STORE_APPS[signal]
    namespace = row.get("namespace") or "observability"
    if signal == "metrics":
        name = "vmsingle-" + (
            row.get("values", {}).get("fullnameOverride") or helm_fullname(name, STORE_APPS[signal])
        )
        port, path = 8428, ""
    else:
        name = row.get("values", {}).get("server", {}).get("fullnameOverride") or f"{name}-server"
        port, path = (9428, "") if signal == "logs" else (10428, "/select/jaeger")
    return f"http://{name}.{namespace}.svc:{port}{path}"


def connections(payload: Any, target: str) -> list[dict[str, Any]]:
    data = to_plain_data(payload)
    routing = target_settings(data, target)
    if not routing:
        return []
    defaults = endpoint_defaults(data)
    by_name: dict[str, dict[str, Any]] = {}
    for signal in routing.get("signals", SIGNALS):
        mode = routing[signal]["storage"]
        if mode != "remote":
            name = f"{signal}-local"
            by_name[name] = {
                "name": name,
                "type": LOCAL_TYPES[signal],
                "url": local_url(data, target, signal),
                "auth": "none",
            }
        if mode != "local":
            name = f"{signal}-remote"
            if routing[signal]["remote"]["url"] == defaults[signal]["write"]:
                by_name[name] = {
                    "name": name,
                    "type": REMOTE_TYPES[signal],
                    "url": defaults[signal]["read"],
                    "auth": "nebius",
                }
    for item in routing.get("datasources", []):
        by_name[item["name"]] = copy.deepcopy(item)
    default = routing["default_datasource"]
    if default not in by_name:
        raise ValueError(f"Default datasource {default!r} is not a configured datasource")
    for name, item in by_name.items():
        item["uid"] = "cxcli-" + hashlib.sha256(f"{target}/{name}".encode()).hexdigest()[:20]
        item["isDefault"] = name == default
    return list(by_name.values())


def collector_owners(payload: Mapping[str, Any], target: str) -> dict[str, str]:
    # Scoped loading leaves other targets' authored routing unmaterialized.
    # Policy inspection still needs validated defaults, without persisting them.
    routing = resolve_settings(payload, target)
    native = bool(app_row(payload, target, "soperator"))
    defaults = endpoint_defaults(payload)
    owners = {}
    for signal in routing.get("signals", SIGNALS):
        previous = routing.get("collectors", {}).get(signal)
        if native and signal in {"metrics", "logs"} and previous not in {None, "soperator"}:
            raise ValueError(
                f"{signal}: Soperator already owns this collector; explicit cutover is required"
            )
        if previous:
            allowed = (
                {"soperator", "nebius-observability-agent", STORE_APPS["metrics"]}
                if signal == "metrics"
                else {"soperator", "nebius-observability-agent", "opentelemetry-collector"}
            )
            if previous not in allowed or (
                previous == "soperator" and (not native or signal == "traces")
            ):
                raise ValueError(f"Invalid collector owner for {signal}: {previous}")
            if previous == "nebius-observability-agent" and (
                routing[signal]["storage"] != "remote"
                or routing[signal]["remote"]["url"] != defaults[signal]["write"]
            ):
                raise ValueError(
                    f"{signal}: existing Nebius collector needs an explicit cutover before changing destinations"
                )
            owners[signal] = previous
        elif native and signal in {"metrics", "logs"}:
            owners[signal] = "soperator"
        elif (
            routing[signal]["storage"] == "remote"
            and routing[signal]["remote"]["url"] == defaults[signal]["write"]
        ):
            owners[signal] = "nebius-observability-agent"
        else:
            owners[signal] = (
                STORE_APPS["metrics"] if signal == "metrics" else "opentelemetry-collector"
            )
    return owners


def required_apps(payload: Mapping[str, Any], target: str) -> set[str]:
    routing = resolve_settings(payload, target)
    owners = collector_owners(payload, target)
    result = {owner for owner in owners.values() if owner != "soperator"}
    for signal in routing.get("signals", SIGNALS):
        if routing[signal]["storage"] != "remote" and not (
            owners[signal] == "soperator" and signal in {"metrics", "logs"}
        ):
            result.add(STORE_APPS[signal])
    if owners.get("logs") == "opentelemetry-collector":
        result.add("opentelemetry-logs")
    if routing.get("pushgateway"):
        result.add("prometheus-pushgateway")
    return result


def ensure_routing_apps(
    payload: dict[str, Any], *, observability_target_refs: frozenset[str] | None = None
) -> bool:
    from .components import component_entries

    validate_observability_target_refs(payload, observability_target_refs)
    before = copy.deepcopy(payload)
    entries = {entry.id: entry for entry in component_entries("apps")}
    # Standalone backend Apps opt into their signal pipeline without adding Grafana.
    for target in enabled_cluster_target_refs(payload):
        if observability_target_refs is not None and target not in observability_target_refs:
            continue
        if target_settings(payload, target):
            continue
        signals = [
            signal
            for signal, component in STORE_APPS.items()
            if app_row(payload, target, component)
        ]
        pushgateway = app_row(payload, target, "prometheus-pushgateway") is not None
        if pushgateway and "metrics" not in signals:
            signals.insert(0, "metrics")
        if signals:
            settings = resolve_settings(payload, target)
            settings.update(signals=signals, pushgateway=pushgateway)
            save_settings(payload, target, settings)
    for target in routed_targets(payload):
        if observability_target_refs is not None and target not in observability_target_refs:
            continue
        settings = resolve_settings(payload, target)
        bind_native_backends(payload, target, settings)
        save_settings(payload, target, settings)
        settings["collectors"] = collector_owners(payload, target)
        if (
            app_row(payload, target, "opentelemetry-logs")
            and settings["collectors"].get("logs") != "opentelemetry-collector"
        ):
            raise ValueError(
                "Existing node log collector requires explicit cutover before changing its owner"
            )
        save_settings(payload, target, settings)
        for component in sorted(required_apps(payload, target)):
            if app_row(payload, target, component) is None:
                if component not in entries:
                    raise ValueError(f"Missing observability catalog component {component}")
                enable_app(payload, target, entries[component])
    return payload != before


def enable_app(payload: dict[str, Any], target: str, entry: Any) -> dict[str, Any]:
    """Enable an existing disabled row instead of creating a duplicate identity."""
    from .observability import _new_observability_app_row
    from .observability_backends import _merge

    rows = payload.setdefault("apps", {}).setdefault("charts", [])
    row = next(
        (
            row
            for row in rows
            if row.get("id") == entry.id
            and (
                (row.get("target_ref") or row.get("instance_id")) == target
                or (
                    not row.get("enabled")
                    and not row.get("target_ref")
                    and row.get("instance_id") in {None, entry.id}
                )
            )
        ),
        None,
    )
    defaults = _new_observability_app_row(entry)
    if row is None:
        row = defaults
        rows.append(row)
    else:
        _merge(defaults, row)
        row.clear()
        row.update(defaults)
    row.update(enabled=True, instance_id=target, target_ref=target)
    return row
