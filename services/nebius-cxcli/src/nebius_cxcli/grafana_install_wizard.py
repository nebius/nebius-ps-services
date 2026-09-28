"""Guided, configuration-only prompts for the shared Grafana installation flow."""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlsplit

import questionary
import typer

from .observability_routing import (
    LOCAL_TYPES,
    REMOTE_TYPES,
    SIGNALS,
    connections,
    endpoint_defaults,
    resolve_settings,
    save_settings,
    target_settings,
    validate_url,
)

_STORAGE_LABELS = {
    "local": "Local — store in this cluster",
    "remote": "Remote — send to external storage",
    "both": "Both — store here and send remotely",
}
_BACKENDS = {
    "prometheus": "Prometheus / VictoriaMetrics (metrics)",
    "victoriametrics-logs-datasource": "VictoriaLogs (logs)",
    "loki": "Loki (logs)",
    "jaeger": "Jaeger / VictoriaTraces (traces)",
    "tempo": "Tempo (traces)",
}
_LOCAL_BACKENDS = {"metrics": "VictoriaMetrics", "logs": "VictoriaLogs", "traces": "VictoriaTraces"}


def _answer(question: questionary.Question) -> Any:
    answer = question.ask()
    if answer is None:
        raise typer.Abort()
    return answer


def _select(message: str, choices: Mapping[str, str], default: str | None = None) -> str:
    return str(
        _answer(
            questionary.select(
                message,
                choices=[
                    questionary.Choice(label, value=value) for value, label in choices.items()
                ],
                default=default,
            )
        )
    )


def _text(message: str, *, default: str = "", validate: Callable[[str], str | bool]) -> str:
    while True:
        value = str(_answer(questionary.text(message, default=default, validate=validate))).strip()
        valid = validate(value)
        if valid is True:
            return value
        typer.echo(str(valid))


def _url(message: str, *, default: str = "", protocol: str | None = None) -> str:
    def validate(value: str) -> str | bool:
        try:
            url = validate_url(value.strip(), label=message)
        except ValueError as exc:
            return str(exc)
        if protocol == "otlp-grpc" and urlsplit(url).path:
            return "OTLP gRPC URLs must contain only the scheme and authority, without a path."
        return True

    return validate_url(_text(message, default=default, validate=validate), label=message)


def _authenticated(connection: Mapping[str, Any]) -> bool:
    return bool(connection.get("auth_secret") or connection.get("auth", "none") != "none")


class _RoutingWizard:
    def __init__(
        self,
        payload: dict[str, Any],
        target: str,
        settings: dict[str, Any],
        flags: Mapping[str, Any],
        locked_names: set[str],
        build_candidate: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> None:
        self.payload = payload
        self.target = target
        self.settings = copy.deepcopy(settings)
        # Preliminary resolution must not retain stores for choices not yet made.
        self.settings["local_stores"] = list(
            target_settings(payload, target).get("local_stores", [])
        )
        self.original = resolve_settings(payload, target, require_read_connections=False)
        self.flags = flags
        self.locked_names = locked_names
        self.build_candidate = build_candidate
        self.defaults = endpoint_defaults(payload)

    def resolved(self) -> dict[str, Any]:
        draft = copy.deepcopy(self.payload)
        save_settings(draft, self.target, self.settings)
        return resolve_settings(draft, self.target)

    def available(self) -> list[dict[str, Any]]:
        # Strict connections() needs a valid default. Use automatic only on the
        # inventory copy, retaining a stale explicit choice for mandatory repair.
        settings = self.resolved()
        settings["default_datasource_explicit"] = False
        return connections(self.build_candidate(settings), self.target)

    def storage(self) -> None:
        typer.echo(
            "Choose where collected telemetry is stored. Grafana query connections "
            "will be configured automatically for the selected standard backends."
        )
        for signal in SIGNALS:
            if self.flags.get(f"{signal}_storage") is None:
                typer.echo(f"Local {signal} storage uses {_LOCAL_BACKENDS[signal]}.")
                self.settings[signal]["storage"] = _select(
                    f"{signal.capitalize()} storage for {self.target}",
                    _STORAGE_LABELS,
                    self.settings[signal]["storage"],
                )

    def remote_destinations(self) -> None:
        for signal in SIGNALS:
            remote = self.settings[signal]["remote"]
            original = self.original[signal]["remote"]
            if original.get("auth_secret") and remote["url"] != original["url"]:
                self.write_auth_error(signal)
            if self.settings[signal]["storage"] == "local":
                continue
            standard = self.defaults[signal]["write"]
            url = remote["url"]
            if self.flags.get(f"{signal}_remote") is None:
                destination = _select(
                    f"Where should remote {signal} be sent?",
                    {
                        "nebius": "Nebius — configured project and region",
                        "custom": "Custom endpoint",
                    },
                    "nebius" if url == standard else "custom",
                )
            else:
                destination = "nebius" if url == standard else "custom"
            if signal != "metrics" and self.flags.get(f"{signal}_remote_protocol") is None:
                remote["protocol"] = (
                    "otlp-grpc"
                    if destination == "nebius"
                    else _select(
                        f"{signal.capitalize()} write protocol",
                        {"otlp-grpc": "OTLP gRPC", "otlp-http": "OTLP HTTP"},
                        remote["protocol"],
                    )
                )
            if destination == "nebius":
                url = standard
            elif self.flags.get(f"{signal}_remote") is None:
                if remote.get("protocol") == "otlp-http":
                    typer.echo(
                        f"Enter the full HTTP write endpoint, including /v1/{signal} if required."
                    )
                url = _url(
                    f"{signal.capitalize()} WRITE URL (receives collected telemetry)",
                    default=url if url != standard else "",
                    protocol=remote.get("protocol"),
                )
            if original.get("auth_secret") and (
                url != original["url"] or remote.get("protocol") != original.get("protocol")
            ):
                self.write_auth_error(signal)
            if url != remote["url"]:
                remote.pop("auth_secret", None)
                remote["auth"] = "nebius" if url == standard else "none"
            remote["url"] = url
            self.remote_read(signal, changed=url != original["url"])

    def write_auth_error(self, signal: str) -> None:
        raise ValueError(
            f"The {signal} write destination uses a configured Secret. Update "
            f"observability.routing.{signal}.remote URL/protocol and authentication "
            "together in config; the wizard will not drop or transfer its credentials."
        )

    def remote_read(self, signal: str, *, changed: bool) -> None:
        name = f"{signal}-remote"
        if name in self.locked_names:
            return
        existing = next(
            (item for item in self.settings["datasources"] if item["name"] == name), None
        )
        standard = self.settings[signal]["remote"]["url"] == self.defaults[signal]["write"]
        if existing and changed:
            typer.echo(
                f"The {signal} write destination changed. Review its Grafana query connection."
            )
            typer.echo(f"Saved {name}: {existing['type']} at {existing['url']}")
            choices = {
                "keep": "Keep the saved query connection",
                "edit": "Edit the query connection",
            }
            if standard:
                choices = {"automatic": "Use the automatic Nebius query connection", **choices}
            action = _select(
                f"Grafana read connection for remote {signal}",
                choices,
                "automatic" if standard else "edit",
            )
            if action == "automatic":
                if _authenticated(existing):
                    self.read_auth_error(name)
                self.settings["datasources"] = [
                    item for item in self.settings["datasources"] if item["name"] != name
                ]
            elif action == "edit":
                self.put(self.read_connection(name, existing=existing, signal=signal))
        elif not existing and not standard:
            typer.echo(f"Grafana needs a separate READ connection for custom remote {signal}.")
            typer.echo(f"Datasource name: {name} (automatic)")
            self.put(self.read_connection(name, signal=signal))

    def read_auth_error(self, name: str) -> None:
        raise ValueError(
            f"Datasource {name!r} uses configured authentication. Update its URL/type "
            "and authentication together in observability.routing.datasources in config; "
            "the wizard will not drop or transfer its credentials."
        )

    def backend(self, *, signal: str | None = None, default: str | None = None) -> str:
        choices = {
            kind: label
            for kind, label in _BACKENDS.items()
            if signal is None or kind in {LOCAL_TYPES[signal], REMOTE_TYPES[signal]}
        }
        if len(choices) == 1:
            return next(iter(choices))
        return _select("Datasource backend", choices, default)

    def read_connection(
        self,
        name: str,
        *,
        existing: dict[str, Any] | None = None,
        signal: str | None = None,
        kind: str | None = None,
    ) -> dict[str, Any]:
        if existing and _authenticated(existing):
            self.read_auth_error(name)
        item = copy.deepcopy(existing or {"name": name})
        for key in ("uid", "isDefault"):
            item.pop(key, None)
        item["type"] = kind or self.backend(signal=signal, default=item.get("type"))
        item["url"] = _url(f"{name} READ URL (queried by Grafana)", default=item.get("url", ""))
        return item

    def put(self, connection: dict[str, Any]) -> None:
        sources = self.settings["datasources"]
        for index, existing in enumerate(sources):
            if existing["name"] == connection["name"]:
                sources[index] = connection
                return
        sources.append(connection)

    def choose_default(self, sources: list[dict[str, Any]], *, required: bool = False) -> None:
        if self.flags.get("default_datasource") is not None:
            if required:
                raise ValueError(
                    f"Default datasource {self.flags['default_datasource']!r} is not a configured datasource"
                )
            return
        automatic = next(item["name"] for item in sources if item["isDefault"])
        choices = {"": f"Automatic metrics default ({automatic})"}
        choices.update(
            {item["name"]: f"{item['name']} — {_BACKENDS[item['type']]}" for item in sources}
        )
        default = (
            self.settings["default_datasource"]
            if self.settings.get("default_datasource_explicit")
            else ""
        )
        if default not in choices:
            typer.echo(
                f"The saved default {default!r} is unavailable after these storage changes. Choose a replacement."
            )
            default = ""
        selected = _select("Default Grafana datasource", choices, default)
        self.settings["default_datasource_explicit"] = bool(selected)
        self.settings["default_datasource"] = selected or automatic

    def preview(self) -> None:
        sources = self.available()
        default = self.resolved()["default_datasource"]
        if default not in {item["name"] for item in sources}:
            self.choose_default(sources, required=True)
            default = self.resolved()["default_datasource"]
        custom = {item["name"] for item in self.settings["datasources"]}
        typer.echo("Grafana will use these datasources:")
        for item in sources:
            origin = "configured override/addition" if item["name"] in custom else "automatic"
            suffix = "; default" if item["name"] == default else ""
            typer.echo(
                f"  {item['name']} → {_BACKENDS[item['type']]} ({item['type']}) [{origin}{suffix}]"
            )
            typer.echo(f"    Query URL: {item['url']}")
        typer.echo("No manual datasource setup is needed for these configured connections.")
        retained = set(self.settings["local_stores"]) & {
            signal for signal in SIGNALS if self.settings[signal]["storage"] == "remote"
        }
        if retained:
            typer.echo(f"Existing local storage is retained for: {', '.join(sorted(retained))}.")

    def add(self) -> None:
        sources = self.available()
        names = {item["name"] for item in sources}
        kind = self.backend()
        signal = next(
            signal for signal in SIGNALS if kind in {LOCAL_TYPES[signal], REMOTE_TYPES[signal]}
        )
        suggested = f"{signal}-extra"
        suffix = 2
        while suggested in names:
            suggested = f"{signal}-extra-{suffix}"
            suffix += 1

        def validate(value: str) -> str | bool:
            name = value.strip()
            if not name:
                return "Enter a datasource name."
            if name in names:
                return "This datasource already exists. Use Edit datasource to change it."
            for reserved_signal in SIGNALS:
                if name in {
                    f"{reserved_signal}-local",
                    f"{reserved_signal}-remote",
                } and kind not in {LOCAL_TYPES[reserved_signal], REMOTE_TYPES[reserved_signal]}:
                    return "This reserved name requires a compatible signal backend."
            return True

        name = _text("Datasource name", default=suggested, validate=validate)
        self.put(self.read_connection(name, kind=kind))

    def edit(self) -> None:
        sources = [item for item in self.available() if item["name"] not in self.locked_names]
        if not sources:
            typer.echo(
                "All datasource connections were supplied by command options and cannot be edited here."
            )
            return
        name = _select(
            "Edit datasource",
            {item["name"]: f"{item['name']} — {_BACKENDS[item['type']]}" for item in sources},
        )
        existing = next(item for item in sources if item["name"] == name)
        if _authenticated(existing):
            typer.echo(
                f"Datasource {name!r} uses configured authentication. Edit its URL/type and authentication together in observability.routing.datasources in config."
            )
            return
        signal = next(
            (signal for signal in SIGNALS if name in {f"{signal}-local", f"{signal}-remote"}), None
        )
        self.put(self.read_connection(name, existing=existing, signal=signal))

    def run(self) -> dict[str, Any]:
        self.storage()
        self.remote_destinations()
        self.preview()
        if _answer(questionary.confirm("Customize datasource connections?", default=False)):
            while True:
                choices = {"add": "Add datasource", "edit": "Edit datasource"}
                if self.flags.get("default_datasource") is None:
                    choices["default"] = "Choose default"
                choices["done"] = "Done"
                action = _select("Datasource customization", choices, "done")
                if action == "done":
                    break
                if action == "add":
                    self.add()
                elif action == "edit":
                    self.edit()
                elif action == "default":
                    self.choose_default(self.available())
                self.preview()
        if self.flags.get("pushgateway") is None:
            self.settings["pushgateway"] = _answer(
                questionary.confirm(
                    "Enable Pushgateway for results from short-lived jobs?",
                    default=self.settings["pushgateway"],
                )
            )
        return self.resolved()


def configure_routing(
    payload: dict[str, Any],
    target: str,
    *,
    settings: dict[str, Any],
    flags: Mapping[str, Any],
    locked_names: set[str],
    build_candidate: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    """Gather routing choices without mutating source state or accessing a cluster."""
    return _RoutingWizard(payload, target, settings, flags, locked_names, build_candidate).run()
