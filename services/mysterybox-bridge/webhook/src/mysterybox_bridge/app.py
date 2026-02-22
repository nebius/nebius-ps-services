from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from flask import Flask, jsonify, request
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from .config import Settings, SettingsError
from .iam import IAMError, SDKProvider
from .mysterybox_client import MysteryBoxClient, MysteryBoxClientError

REQUEST_COUNTER = Counter(
    "mysterybox_bridge_requests_total",
    "Total webhook requests received",
    ["endpoint", "status"],
)
REQUEST_DURATION = Histogram(
    "mysterybox_bridge_request_duration_seconds",
    "Webhook request duration",
    ["endpoint"],
)


@dataclass
class AppContext:
    settings: Settings
    mysterybox_client: MysteryBoxClient


class BridgeRuntimeError(RuntimeError):
    """Raised when bridge runtime cannot be initialized."""


def _request_auth_ok(settings: Settings) -> bool:
    if not settings.webhook_auth_header:
        return True
    provided = request.headers.get(settings.webhook_auth_header, "")
    return provided == (settings.webhook_auth_token or "")


def _request_param(name: str, body: dict[str, Any]) -> str | None:
    value = request.args.get(name)
    if value is not None and value.strip():
        return value.strip()
    raw = body.get(name)
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def _build_context(settings: Settings | None = None) -> AppContext:
    resolved_settings = settings or Settings.from_env()
    provider = SDKProvider(resolved_settings)
    sdk = provider.get_sdk()
    return AppContext(
        settings=resolved_settings,
        mysterybox_client=MysteryBoxClient(resolved_settings, sdk),
    )


def create_app(
    *,
    settings: Settings | None = None,
    mysterybox_client: MysteryBoxClient | None = None,
) -> Flask:
    app = Flask(__name__)

    if settings is None and mysterybox_client is None:
        context = _build_context()
    else:
        if settings is None:
            raise BridgeRuntimeError("settings must be provided when mysterybox_client is injected")
        if mysterybox_client is None:
            context = _build_context(settings)
        else:
            context = AppContext(settings=settings, mysterybox_client=mysterybox_client)

    logging.basicConfig(level=context.settings.log_level)
    logger = logging.getLogger("mysterybox-bridge")

    @app.get("/healthz")
    def healthz() -> tuple[str, int]:
        return "ok", 200

    @app.get("/readyz")
    def readyz() -> tuple[str, int]:
        try:
            context.mysterybox_client.readiness_check()
            return "ready", 200
        except MysteryBoxClientError as exc:
            logger.warning("readiness check failed: %s", exc)
            return "not ready", 503
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("unexpected readiness check failure: %s", exc)
            return "not ready", 503

    @app.get("/metrics")
    def metrics() -> tuple[bytes, int, dict[str, str]]:
        return generate_latest(), 200, {"Content-Type": CONTENT_TYPE_LATEST}

    @app.route(context.settings.secret_endpoint_path, methods=["GET", "POST"])
    def get_secret() -> Any:
        endpoint = context.settings.secret_endpoint_path

        if not _request_auth_ok(context.settings):
            REQUEST_COUNTER.labels(endpoint=endpoint, status="403").inc()
            return jsonify({"error": "forbidden"}), 403

        body = request.get_json(silent=True) or {}
        with REQUEST_DURATION.labels(endpoint=endpoint).time():
            secret_reference = _request_param("secret", body)
            payload_key = _request_param("key", body)
            version = _request_param("version", body)

            if not secret_reference or not payload_key:
                REQUEST_COUNTER.labels(endpoint=endpoint, status="400").inc()
                return (
                    jsonify(
                        {
                            "error": "missing required parameters",
                            "required": ["secret", "key"],
                        }
                    ),
                    400,
                )

            try:
                value = context.mysterybox_client.get_value(
                    secret_reference=secret_reference,
                    key=payload_key,
                    version=version,
                )
            except MysteryBoxClientError as exc:
                logger.warning("mysterybox read failed: %s", exc)
                REQUEST_COUNTER.labels(endpoint=endpoint, status="502").inc()
                return jsonify({"error": str(exc)}), 502
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception("unexpected bridge error")
                REQUEST_COUNTER.labels(endpoint=endpoint, status="500").inc()
                return jsonify({"error": f"unexpected error: {exc}"}), 500

            REQUEST_COUNTER.labels(endpoint=endpoint, status="200").inc()
            return jsonify({"value": value}), 200

    return app


def main() -> None:
    try:
        settings = Settings.from_env()
        app = create_app(settings=settings)
    except (SettingsError, IAMError, BridgeRuntimeError) as exc:
        raise SystemExit(f"Startup error: {exc}") from exc

    app.run(host=settings.listen_host, port=settings.listen_port)


if __name__ == "__main__":
    main()
