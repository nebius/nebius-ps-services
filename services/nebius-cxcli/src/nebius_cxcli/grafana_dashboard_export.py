"""Grafana dashboard export and component catalog attachment helpers."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from base64 import b64encode
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urljoin
from urllib.request import Request, urlopen

from .component_sources import load_component_sources, reset_component_sources_cache


class GrafanaApiError(RuntimeError):
    """Raised when the Grafana API returns an unusable response."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class GrafanaAuth:
    kind: str
    value: str
    source: str

    def authorization_header(self) -> str:
        if self.kind == "bearer":
            return f"Bearer {self.value}"
        if self.kind == "basic":
            return f"Basic {self.value}"
        raise RuntimeError(f"Unsupported Grafana auth kind: {self.kind}")


@dataclass(frozen=True)
class GrafanaFolder:
    uid: str
    title: str


@dataclass(frozen=True)
class GrafanaDashboard:
    uid: str
    title: str
    folder_uid: str
    folder_title: str


@dataclass(frozen=True)
class CatalogDatasource:
    name: str
    uid: str
    datasource_type: str


@dataclass(frozen=True)
class ExportedDashboard:
    uid: str
    title: str
    folder_uid: str
    folder_title: str
    catalog_folder: str
    dashboard_key: str
    datasource_name: str
    path: Path


_GRAFANA_INTERNAL_DATASOURCE_TYPES = {"grafana", "__expr__"}
_GRAFANA_INTERNAL_DATASOURCE_UIDS = {"-- Grafana --", "__expr__"}


def safe_slug(value: str, *, fallback: str = "item") -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", str(value or "").lower()).strip("-")
    return slug or fallback


def _json_request(base_url: str, path: str, auth: GrafanaAuth, *, timeout: int) -> object:
    request = Request(
        urljoin(base_url.rstrip("/") + "/", path),
        headers={
            "Accept": "application/json",
            "Authorization": auth.authorization_header(),
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        raise GrafanaApiError(
            f"Grafana API returned HTTP {exc.code} using {auth.source}",
            status_code=exc.code,
        ) from exc
    except URLError as exc:
        raise GrafanaApiError(f"Grafana API request failed: {exc.reason}") from exc
    try:
        return json.loads(body or "{}")
    except json.JSONDecodeError as exc:
        raise GrafanaApiError(
            "Grafana API returned invalid JSON. The URL may require a different "
            "Grafana API credential instead of a browser login redirect."
        ) from exc


def grafana_get_json(
    base_url: str,
    path: str,
    auth_candidates: Sequence[GrafanaAuth],
    *,
    timeout: int = 20,
) -> object:
    if not auth_candidates:
        raise RuntimeError(
            "No Grafana credentials available. Set GRAFANA_TOKEN, NEBIUS_IAM_TOKEN, "
            "use --token-env, or provide Basic auth with --username and --password-env."
        )
    rejected: list[str] = []
    for auth in auth_candidates:
        try:
            return _json_request(base_url, path, auth, timeout=timeout)
        except GrafanaApiError as exc:
            if exc.status_code in {401, 403}:
                rejected.append(auth.source)
                continue
            raise
    sources = ", ".join(rejected) if rejected else "configured credentials"
    raise GrafanaApiError(f"Grafana API rejected all configured credentials: {sources}")


def bearer_auth_candidates(
    *,
    token_env: str = "",
    env: Mapping[str, str] | None = None,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> list[GrafanaAuth]:
    resolved_env = env or os.environ
    candidates: list[GrafanaAuth] = []
    seen_tokens: set[str] = set()

    def add_token(token: str, source: str) -> None:
        token = str(token or "").strip()
        if not token or token in seen_tokens:
            return
        seen_tokens.add(token)
        candidates.append(GrafanaAuth(kind="bearer", value=token, source=source))

    add_token(str(resolved_env.get("GRAFANA_TOKEN") or ""), "GRAFANA_TOKEN")
    add_token(str(resolved_env.get("NEBIUS_IAM_TOKEN") or ""), "NEBIUS_IAM_TOKEN")
    try:
        cp = run(
            ["nebius", "iam", "get-access-token", "--format", "text"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        add_token(str(cp.stdout or ""), "nebius iam get-access-token")
    except Exception:
        pass
    if token_env:
        add_token(str(resolved_env.get(token_env) or ""), token_env)
    return candidates


def basic_auth_candidate(username: str, password: str, *, source: str = "Basic auth") -> GrafanaAuth:
    username = str(username or "").strip()
    password = str(password or "")
    if not username or not password:
        raise RuntimeError("Grafana Basic auth requires both username and password")
    encoded = b64encode(f"{username}:{password}".encode()).decode("ascii")
    return GrafanaAuth(kind="basic", value=encoded, source=source)


def list_folders(
    base_url: str,
    auth_candidates: Sequence[GrafanaAuth],
) -> tuple[GrafanaFolder, ...]:
    payload = grafana_get_json(base_url, "api/search?type=dash-folder&limit=5000", auth_candidates)
    if not isinstance(payload, list):
        raise GrafanaApiError("Grafana folder search API did not return a JSON array")
    folders: list[GrafanaFolder] = []
    for item in payload:
        if not isinstance(item, Mapping) or item.get("type") != "dash-folder":
            continue
        uid = str(item.get("uid") or "").strip()
        title = str(item.get("title") or "").strip()
        if uid:
            folders.append(GrafanaFolder(uid=uid, title=title or uid))
    return tuple(sorted(folders, key=lambda item: (item.title.casefold(), item.uid.casefold())))


def list_dashboards(
    base_url: str,
    auth_candidates: Sequence[GrafanaAuth],
    *,
    folder_uid: str,
    folder_title: str = "",
) -> tuple[GrafanaDashboard, ...]:
    query = urlencode({"folderUIDs": folder_uid, "type": "dash-db", "limit": "5000"})
    payload = grafana_get_json(base_url, f"api/search?{query}", auth_candidates)
    if not isinstance(payload, list):
        raise GrafanaApiError("Grafana dashboard search API did not return a JSON array")
    dashboards: list[GrafanaDashboard] = []
    for item in payload:
        if not isinstance(item, Mapping) or item.get("type") != "dash-db":
            continue
        uid = str(item.get("uid") or "").strip()
        title = str(item.get("title") or "").strip()
        if not uid:
            continue
        item_folder_uid = str(item.get("folderUid") or folder_uid).strip() or folder_uid
        item_folder_title = str(item.get("folderTitle") or folder_title).strip() or item_folder_uid
        dashboards.append(
            GrafanaDashboard(
                uid=uid,
                title=title or uid,
                folder_uid=item_folder_uid,
                folder_title=item_folder_title,
            )
        )
    return tuple(
        sorted(dashboards, key=lambda item: (item.title.casefold(), item.uid.casefold()))
    )


def dashboard_json(
    base_url: str,
    auth_candidates: Sequence[GrafanaAuth],
    *,
    dashboard_uid: str,
) -> dict[str, object]:
    payload = grafana_get_json(
        base_url,
        f"api/dashboards/uid/{quote(dashboard_uid, safe='')}",
        auth_candidates,
    )
    if not isinstance(payload, Mapping):
        raise GrafanaApiError("Grafana dashboard detail API did not return a JSON object")
    return normalize_dashboard_json(payload, source_label=f"Grafana dashboard {dashboard_uid}")


def normalize_dashboard_json(payload: object, *, source_label: str) -> dict[str, object]:
    if not isinstance(payload, Mapping):
        raise GrafanaApiError(f"{source_label} must be a JSON object")
    dashboard = payload["dashboard"] if isinstance(payload.get("dashboard"), Mapping) else payload
    if not isinstance(dashboard, Mapping):
        raise GrafanaApiError(f"{source_label} does not include dashboard JSON")
    exported = dict(dashboard)
    exported.pop("id", None)
    exported.pop("version", None)
    uid = str(exported.get("uid") or "").strip()
    if not uid:
        raise GrafanaApiError(f"{source_label} does not declare a top-level uid")
    return exported


def dashboard_json_from_file(path: Path) -> dict[str, object]:
    source_path = path.expanduser().resolve()
    if not source_path.exists() or not source_path.is_file():
        raise RuntimeError(f"Dashboard JSON file not found: {source_path}")
    try:
        payload = json.loads(source_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Dashboard JSON file is invalid JSON: {source_path}") from exc
    except OSError as exc:
        raise RuntimeError(f"Could not read dashboard JSON file: {source_path}") from exc
    return normalize_dashboard_json(payload, source_label=str(source_path))


def _iter_datasource_refs(value: object) -> Iterable[object]:
    if isinstance(value, Mapping):
        if "datasource" in value:
            yield value.get("datasource")
        for child in value.values():
            yield from _iter_datasource_refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_datasource_refs(child)


def _datasource_ref_fields(ref: object) -> tuple[str, str, str]:
    if isinstance(ref, str):
        text = ref.strip()
        return text, text, ""
    if isinstance(ref, Mapping):
        name = str(ref.get("name") or "").strip()
        uid = str(ref.get("uid") or "").strip()
        datasource_type = str(ref.get("type") or "").strip()
        return name, uid, datasource_type
    return "", "", ""


def _is_rewritable_datasource_ref(ref: object) -> bool:
    name, uid, datasource_type = _datasource_ref_fields(ref)
    if name.startswith("$") or uid.startswith("$") or datasource_type.startswith("$"):
        return True
    if uid in _GRAFANA_INTERNAL_DATASOURCE_UIDS:
        return False
    return datasource_type not in _GRAFANA_INTERNAL_DATASOURCE_TYPES


def select_catalog_datasource(
    dashboard: Mapping[str, object],
    datasources: Sequence[CatalogDatasource],
    *,
    requested: str = "",
) -> CatalogDatasource:
    if requested:
        matches = [
            item
            for item in datasources
            if requested == item.name or requested == item.uid or requested == item.datasource_type
        ]
        if len(matches) == 1:
            selected = matches[0]
            concrete_types = {
                datasource_type
                for _name, _uid, datasource_type in (
                    _datasource_ref_fields(ref) for ref in _iter_datasource_refs(dashboard)
                )
                if datasource_type and not datasource_type.startswith("$")
                and datasource_type not in _GRAFANA_INTERNAL_DATASOURCE_TYPES
            }
            if concrete_types and concrete_types != {selected.datasource_type}:
                raise RuntimeError(
                    "Dashboard uses mixed datasource types; automatic attach supports one "
                    "datasource type per dashboard. Export without --attach or split the dashboard."
                )
            return selected
        if matches:
            names = ", ".join(item.name for item in matches)
            raise RuntimeError(f"Datasource selector '{requested}' is ambiguous: {names}")
        raise RuntimeError(f"Datasource selector '{requested}' does not match a Grafana datasource")

    matched: list[CatalogDatasource] = []
    for ref in _iter_datasource_refs(dashboard):
        if not _is_rewritable_datasource_ref(ref):
            continue
        name, uid, datasource_type = _datasource_ref_fields(ref)
        if name.startswith("$") or uid.startswith("$"):
            continue
        matches = [
            item
            for item in datasources
            if (name and name == item.name)
            or (uid and uid == item.uid)
            or (
                datasource_type
                and datasource_type == item.datasource_type
                and sum(1 for ds in datasources if ds.datasource_type == datasource_type) == 1
            )
        ]
        for match in matches:
            if match not in matched:
                matched.append(match)
    if len(matched) == 1:
        return matched[0]
    if len(matched) > 1:
        names = ", ".join(item.name for item in matched)
        raise RuntimeError(
            "Dashboard references multiple catalog datasources. Automatic attach supports one "
            f"datasource per dashboard: {names}"
        )
    raise RuntimeError(
        "Dashboard datasource could not be matched to component_cli_settings.yaml. "
        "Pass --datasource with one configured Grafana datasource name."
    )


def rewrite_dashboard_datasources(dashboard: object, datasource: CatalogDatasource) -> object:
    if isinstance(dashboard, Mapping):
        rewritten: dict[str, object] = {}
        for key, value in dashboard.items():
            if key == "datasource" and _is_rewritable_datasource_ref(value):
                rewritten[key] = {"type": datasource.datasource_type, "uid": datasource.uid}
            else:
                rewritten[str(key)] = rewrite_dashboard_datasources(value, datasource)
        return rewritten
    if isinstance(dashboard, list):
        return [rewrite_dashboard_datasources(item, datasource) for item in dashboard]
    return dashboard


def catalog_datasources(component_sources_path: Path) -> tuple[str, tuple[CatalogDatasource, ...]]:
    reset_component_sources_cache()
    sources = load_component_sources(explicit=component_sources_path)
    for chart in sources.helm_charts:
        if chart.name == "grafana" or chart.grafana.datasources:
            datasources = tuple(
                CatalogDatasource(
                    name=item.name,
                    uid=item.uid,
                    datasource_type=item.datasource_type,
                )
                for item in chart.grafana.datasources
            )
            if datasources:
                return chart.name, datasources
    raise RuntimeError(
        "No Grafana app with configured datasources was found in the active component catalog."
    )


def write_dashboard_file(path: Path, dashboard: Mapping[str, object], *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise RuntimeError(f"Dashboard export already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(dict(dashboard), indent=2, sort_keys=False) + "\n"
    temp_path: Path | None = None
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        temp_path = Path(handle.name)
        handle.write(rendered)
    try:
        temp_path.replace(path)
    except Exception:
        if temp_path.exists():
            temp_path.unlink()
        raise


def _relative_json_file(path: Path, *, catalog_path: Path) -> str:
    rel = os.path.relpath(path.resolve(), catalog_path.resolve().parent)
    normalized = Path(rel).as_posix()
    if not normalized.startswith((".", "/")):
        normalized = f"./{normalized}"
    return normalized


def _rt_map(*items: tuple[str, object]):
    from ruamel.yaml.comments import CommentedMap

    mapping = CommentedMap()
    for key, value in items:
        mapping[key] = value
    return mapping


def _ensure_provider(
    providers: list[object],
    *,
    catalog_folder: str,
    folder_title: str,
) -> None:
    for provider in providers:
        if isinstance(provider, Mapping) and str(provider.get("name") or "") == catalog_folder:
            return
    providers.append(
        _rt_map(
            ("name", catalog_folder),
            ("orgId", 1),
            ("folder", folder_title or catalog_folder),
            ("folderUid", catalog_folder),
            ("type", "file"),
            ("disableDeletion", False),
            ("allowUiUpdates", True),
            ("options", _rt_map(("path", f"/var/lib/grafana/dashboards/{catalog_folder}"))),
        )
    )


def attach_dashboards_to_catalog(
    component_sources_path: Path,
    *,
    grafana_component_id: str,
    exports: Sequence[ExportedDashboard],
    overwrite: bool,
) -> None:
    if not exports:
        return
    try:
        from ruamel.yaml import YAML
        from ruamel.yaml.comments import CommentedMap
    except Exception as exc:  # pragma: no cover - dependency guard
        raise RuntimeError("ruamel.yaml is required to update component_sources.yaml") from exc

    yaml_rt = YAML()
    yaml_rt.preserve_quotes = True
    original = component_sources_path.read_text(encoding="utf-8")
    payload = yaml_rt.load(original)
    if not isinstance(payload, CommentedMap):
        raise RuntimeError("component_sources.yaml root must be a mapping")
    components = payload.setdefault("components", CommentedMap())
    apps = components.setdefault("apps", CommentedMap())
    grafana_app = apps.get(grafana_component_id)
    if not isinstance(grafana_app, CommentedMap):
        raise RuntimeError(f"components.apps.{grafana_component_id} must be a mapping")
    defaults = grafana_app.setdefault("defaults", CommentedMap())
    if not isinstance(defaults, CommentedMap):
        raise RuntimeError(f"components.apps.{grafana_component_id}.defaults must be a mapping")

    dashboard_providers = defaults.setdefault("values.dashboardProviders", CommentedMap())
    if not isinstance(dashboard_providers, CommentedMap):
        raise RuntimeError("values.dashboardProviders must be a mapping")
    providers_yaml = dashboard_providers.setdefault("dashboardproviders.yaml", CommentedMap())
    if not isinstance(providers_yaml, CommentedMap):
        raise RuntimeError("values.dashboardProviders.dashboardproviders.yaml must be a mapping")
    providers = providers_yaml.setdefault("providers", [])
    if not isinstance(providers, list):
        raise RuntimeError("values.dashboardProviders.dashboardproviders.yaml.providers must be a list")

    dashboards = defaults.setdefault("values.dashboards", CommentedMap())
    if not isinstance(dashboards, CommentedMap):
        raise RuntimeError("values.dashboards must be a mapping")

    for exported in exports:
        folder_dashboards = dashboards.setdefault(exported.catalog_folder, CommentedMap())
        if not isinstance(folder_dashboards, CommentedMap):
            raise RuntimeError(f"values.dashboards.{exported.catalog_folder} must be a mapping")
        for existing in folder_dashboards.values():
            if isinstance(existing, Mapping) and "gnetId" in existing:
                raise RuntimeError(
                    f"values.dashboards.{exported.catalog_folder} already contains Grafana.com "
                    "gnetId dashboards; choose a different --dashboard-folder for JSON exports."
                )
        if exported.dashboard_key in folder_dashboards and not overwrite:
            raise RuntimeError(
                f"values.dashboards.{exported.catalog_folder}.{exported.dashboard_key} "
                "already exists. Use --overwrite to replace it."
            )
        folder_dashboards[exported.dashboard_key] = _rt_map(
            ("datasource", exported.datasource_name),
            ("json_file", _relative_json_file(exported.path, catalog_path=component_sources_path)),
        )
        _ensure_provider(
            providers,
            catalog_folder=exported.catalog_folder,
            folder_title=exported.folder_title,
        )

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=component_sources_path.parent, delete=False
        ) as handle:
            temp_path = Path(handle.name)
            yaml_rt.dump(payload, handle)
        temp_path.replace(component_sources_path)
    except Exception:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()
        raise
    try:
        reset_component_sources_cache()
        load_component_sources(explicit=component_sources_path)
    except Exception:
        component_sources_path.write_text(original, encoding="utf-8")
        reset_component_sources_cache()
        raise
