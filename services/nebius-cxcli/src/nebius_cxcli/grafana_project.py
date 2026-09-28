"""Project desired state and separately atomic optional catalog attachment."""

from __future__ import annotations

import copy
import hashlib
import io
import json
from collections.abc import Callable, Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from ruamel.yaml import YAML

from .grafana_api import DashboardOwnership, GrafanaError
from .grafana_dashboards import (
    IMPORTS_FIELD,
    IMPORTS_PROVIDER,
    content_digest,
    json_bytes,
    read_dashboard,
    validate_import_declarations,
)
from .grafana_import import DashboardPlan
from .ordinary_apps import protected_config_digest
from .project_bundle_transaction import ProjectBundleTransaction, normalize_project_bundle_target


def digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def publication_identity(kind: str, path: Path, target: str = "") -> str:
    return digest(json_bytes(["grafana-publication/v1", kind, str(path.absolute()), target]))


def recover_import_publication(config_path: Path, target: str, catalog: Path | None = None) -> None:
    """Replay only a committed local publication owned by this import destination."""
    from .soperator_operation_lock import SoperatorOperationLocalLock

    owned = {config_path.parent: {publication_identity("import", config_path, target)}}
    locks = {config_path.parent / ".nebius-cxcli" / "config.lock"}
    if catalog is not None:
        catalog = catalog.expanduser().absolute()
        owned.setdefault(catalog.parent, set()).add(publication_identity("attach", catalog))
        locks.add(catalog.parent / ".nebius-cxcli" / "grafana-catalog.lock")
    with ExitStack() as stack:
        for lock in sorted(locks):
            stack.enter_context(SoperatorOperationLocalLock(lock))
        for root, generations in owned.items():
            transaction = ProjectBundleTransaction(root)
            # Validate journal ownership and staged bytes without replaying a
            # different writer. Recovery rechecks this identity under its lock.
            pending = transaction.recovery_staged_files()
            if pending:
                generation = json.loads(transaction.journal_path.read_bytes()).get(
                    "generationSha256"
                )
                if generation not in generations:
                    raise RuntimeError(
                        "Pending project generation differs from this dashboard operation"
                    )
                transaction.recover_expected_generation(generation)


def grafana_row(payload: Mapping[str, Any], target: str) -> dict[str, Any]:
    from .observability import _grafana_app_id

    rows = [
        row
        for row in payload.get("apps", {}).get("charts", [])
        if row.get("id") == _grafana_app_id()
        and row.get("instance_id") == target
        and row.get("enabled")
    ]
    if len(rows) != 1:
        raise ValueError("Select a target with exactly one enabled Grafana app")
    return rows[0]


def declarations(
    payload: Mapping[str, Any], target: str, *, replay_only: bool = False
) -> list[dict[str, Any]]:
    row = grafana_row(payload, target)
    validate_import_declarations(row, grafana_id=str(row["id"]))
    return copy.deepcopy(
        [
            entry
            for entry in row.get(IMPORTS_FIELD, [])
            if not replay_only or entry.get("replay", True)
        ]
    )


def assert_saved_import_ownership(
    entries: Mapping[str, Mapping[str, Any]],
    ownership: Mapping[str, DashboardOwnership],
    *,
    identity: Mapping[str, Any],
    namespace: str,
) -> None:
    for uid, owner in ownership.items():
        entry = entries.get(uid)
        if entry is None:
            continue
        if entry.get("replay", True) == owner.managed:
            raise GrafanaError(
                "Saved dashboard ownership mode differs from Grafana; resolve its owner"
            )
        if owner.managed and entry.get("management_sha256") != owner.fingerprint(
            identity, namespace, uid
        ):
            raise GrafanaError("Saved dashboard management provenance differs from Grafana")


def assert_import_ownership(
    payload: Mapping[str, Any],
    target: str,
    ownership: Mapping[str, DashboardOwnership],
    *,
    identity: Mapping[str, Any],
    namespace: str,
) -> None:
    assert_saved_import_ownership(
        {entry["uid"]: entry for entry in declarations(payload, target)},
        ownership,
        identity=identity,
        namespace=namespace,
    )
    assert_catalog_ownership(
        payload, target, {uid for uid, owner in ownership.items() if not owner.managed}
    )


def declared_dashboards(
    payload: Mapping[str, Any], root: Path, target: str
) -> list[tuple[dict[str, Any], str]]:
    result = []
    for entry in declarations(payload, target):
        path = normalize_project_bundle_target(root, root / entry["json_file"])
        dashboard = read_dashboard(path)
        if dashboard["uid"] != entry["uid"]:
            raise ValueError("Declared dashboard UID differs from its JSON file")
        if content_digest(dashboard, entry.get("folder_uid", "")) != entry["sha256"]:
            raise ValueError("Declared dashboard content changed; run grafana import --overwrite")
        result.append((dashboard, entry.get("folder_uid", "")))
    return result


def exclude_linked_catalog_dashboards(values: dict[str, Any], row: Mapping[str, Any]) -> None:
    """An explicit API owner can suppress only its own catalog attachment."""
    if IMPORTS_PROVIDER in values.get("dashboards", {}):
        raise ValueError(f"Catalog provider {IMPORTS_PROVIDER} is reserved for API import assets")
    entries = [entry for entry in row.get(IMPORTS_FIELD, []) if entry.get("replay", True)]
    if not entries:
        return
    by_uid = {entry["uid"]: entry for entry in entries}
    for folder, dashboards in list(values.get("dashboards", {}).items()):
        if not isinstance(dashboards, dict):
            continue
        for key, item in list(dashboards.items()):
            if not isinstance(item, dict):
                continue
            raw = item.get("json")
            uid = str(item.get("uid") or "")
            if isinstance(raw, str):
                uid = str(json.loads(raw).get("uid") or "")
            if uid not in by_uid:
                continue
            if by_uid[uid].get("catalog_key") != f"{folder}/{key}":
                raise ValueError(
                    f"Dashboard {uid} has conflicting API and catalog provisioning owners"
                )
            if not raw or "gnetId" in item:
                raise ValueError("API attachment cannot replace a Grafana.com provisioning owner")
            del dashboards[key]
        if not dashboards:
            del values["dashboards"][folder]


def assert_catalog_ownership(payload: Mapping[str, Any], target: str, uids: set[str]) -> None:
    from .observability import _grafana_catalog_managed_values, _set_path_value

    values: dict[str, Any] = {}
    for key, value in _grafana_catalog_managed_values().items():
        if key.startswith("values."):
            _set_path_value(values, key.removeprefix("values."), copy.deepcopy(value))
    row = grafana_row(payload, target)
    exclude_linked_catalog_dashboards(values, row)
    for dashboards in values.get("dashboards", {}).values():
        for item in dashboards.values():
            uid = str(item.get("uid") or "")
            if item.get("json"):
                uid = str(json.loads(item["json"]).get("uid") or "")
            if uid in uids:
                raise ValueError(
                    f"Dashboard {uid} belongs to catalog file provisioning; update its source"
                )


def dump_yaml(payload: object) -> bytes:
    serializer = YAML()
    serializer.preserve_quotes = True
    stream = io.StringIO()
    serializer.dump(payload, stream)
    return stream.getvalue().encode()


@dataclass
class ProjectImport:
    root: Path
    target: str
    receipt_path: Path
    receipt: dict[str, Any]
    assert_authority: Callable[[], None]
    config_path: Path
    config_postimage: bytes

    def checkpoint(self, plan: DashboardPlan, status: str, resource: dict[str, Any] | None) -> None:
        self.assert_authority()
        old = self.receipt_path.read_bytes()
        if old != json_bytes(self.receipt):
            raise RuntimeError("Dashboard progress receipt changed during import")
        if status == "unchanged" and self.receipt["dashboards"][plan.uid].get("status") in {
            "installed",
            "unchanged",
        }:
            return
        self.receipt["dashboards"][plan.uid].update(
            status=status,
            resource_version=resource["metadata"].get("resourceVersion") if resource else None,
        )
        if json_bytes(self.receipt) == old:
            return
        ProjectBundleTransaction(self.root).commit(
            {self.receipt_path: json_bytes(self.receipt)},
            expected_preimages={self.receipt_path: digest(old)},
            generation_sha256=publication_identity("import", self.config_path, self.target),
        )
        self.assert_authority()


def publish_import(
    *,
    root: Path,
    config_path: Path,
    target: str,
    plans: Sequence[DashboardPlan],
    identity: Mapping[str, Any],
    namespace: str,
    overwrite: bool,
    catalog_keys: Mapping[str, str] | None,
    assert_authority: Callable[[], None],
    expected_config: bytes,
) -> ProjectImport:
    assert_authority()
    if config_path.read_bytes() != expected_config:
        raise RuntimeError("Configuration changed during dashboard preflight")
    serializer = YAML()
    serializer.preserve_quotes = True
    payload = serializer.load(expected_config)
    before = copy.deepcopy(payload)
    row = grafana_row(payload, target)
    existing = {item["uid"]: item for item in row.get(IMPORTS_FIELD, [])}
    before_entries = copy.deepcopy(existing)
    validate_import_declarations(row, grafana_id=str(row["id"]))
    assert_saved_import_ownership(
        existing,
        {plan.uid: plan.ownership for plan in plans},
        identity=identity,
        namespace=namespace,
    )
    updates: dict[Path, bytes] = {}
    receipt_path = root / ".grafana-imports" / f"{target}.json"
    transaction = ProjectBundleTransaction(root)
    destinations = [
        normalize_project_bundle_target(root, root / f"grafana_dashboards/{target}/{plan.uid}.json")
        for plan in plans
    ]
    preimages = transaction.snapshot_preimages(
        [config_path, receipt_path, *destinations], read_only=True
    )
    if preimages[config_path].content != expected_config:
        raise RuntimeError("Configuration changed during dashboard preflight")
    for plan in plans:
        relative = f"grafana_dashboards/{target}/{plan.uid}.json"
        path = normalize_project_bundle_target(root, root / relative)
        content = json_bytes(plan.dashboard)
        old_content = preimages[path].content
        if old_content is not None and old_content != content and not overwrite:
            raise ValueError(f"Saved dashboard {plan.uid} differs; use --overwrite")
        entry: dict[str, Any] = {
            "uid": plan.uid,
            "json_file": relative,
            "folder_uid": plan.folder,
            "sha256": plan.digest,
        }
        if plan.ownership.managed:
            if catalog_keys is not None:
                raise GrafanaError("--attach cannot be used with a managed dashboard update")
            entry["replay"] = False
            entry["management_sha256"] = plan.ownership.fingerprint(identity, namespace, plan.uid)
        elif existing.get(plan.uid, {}).get("catalog_key"):
            entry["catalog_key"] = existing[plan.uid]["catalog_key"]
        if catalog_keys and plan.uid in catalog_keys:
            entry["catalog_key"] = catalog_keys[plan.uid]
        existing[plan.uid] = entry
        if old_content != content:
            updates[path] = content
    row[IMPORTS_FIELD] = [existing[key] for key in sorted(existing)]
    validate_import_declarations(row, grafana_id=str(row["id"]))
    if protected_config_digest(payload) != protected_config_digest(before):
        raise RuntimeError("Dashboard publication changed protected configuration")
    if payload != before:
        updates[config_path] = dump_yaml(payload)
    receipt: dict[str, Any] = {
        "schema": "nebius-cxcli-grafana-import/v1",
        "target": target,
        "identity": dict(identity),
        "namespace": namespace,
        "dashboards": {
            plan.uid: {
                "desired_sha256": plan.digest,
                "previous_version": plan.previous["metadata"].get("resourceVersion")
                if plan.previous
                else None,
                "status": "pending",
                **(
                    {"management_sha256": plan.ownership.fingerprint(identity, namespace, plan.uid)}
                    if plan.ownership.managed
                    else {}
                ),
            }
            for plan in plans
        },
    }
    old_receipt = preimages[receipt_path].content
    if old_receipt is None and any(
        not entry.get("replay", True)
        for entry in before_entries.values()
        if entry["uid"] in receipt["dashboards"]
    ):
        raise GrafanaError(
            "Saved managed dashboard receipt is missing; restore its import provenance"
        )
    if old_receipt is not None:
        prior = json.loads(old_receipt)
        if prior.get("identity") != dict(identity) or prior.get("namespace") != namespace:
            raise RuntimeError("Saved dashboard receipt belongs to a different Grafana destination")
        for plan in plans:
            pending = prior.get("dashboards", {}).get(plan.uid, {})
            if (
                pending or not before_entries.get(plan.uid, {}).get("replay", True)
            ) and pending.get("management_sha256") != receipt["dashboards"][plan.uid].get(
                "management_sha256"
            ):
                raise GrafanaError(
                    "Saved dashboard receipt management provenance differs from Grafana"
                )
            if (
                pending.get("desired_sha256") == plan.digest
                and pending.get("status") in {"pending", "writing"}
                and not plan.unchanged
                and pending.get("previous_version")
                != receipt["dashboards"][plan.uid]["previous_version"]
            ):
                raise RuntimeError(
                    f"Dashboard {plan.uid} changed since the interrupted import; review remote changes before changing intent"
                )
        unchanged = {plan.uid for plan in plans if plan.unchanged}
        for uid, entry in prior.get("dashboards", {}).items():
            if (
                uid not in receipt["dashboards"]
                or uid in unchanged
                and entry.get("desired_sha256") == receipt["dashboards"][uid]["desired_sha256"]
                and entry.get("status") in {"installed", "unchanged"}
            ):
                receipt["dashboards"][uid] = entry
    if old_receipt is None or json.loads(old_receipt) != receipt:
        updates[receipt_path] = json_bytes(receipt)
    if updates:
        assert_authority()
        transaction.commit(
            updates,
            expected_preimages={path: preimages[path].sha256 for path in updates},
            generation_sha256=publication_identity("import", config_path, target),
        )
    return ProjectImport(
        root,
        target,
        receipt_path,
        receipt,
        assert_authority,
        config_path,
        updates.get(config_path, expected_config),
    )


@dataclass
class CatalogAttachment:
    path: Path
    updates: dict[Path, bytes]
    preimages: dict[Path, str]
    keys: dict[str, str]

    def publish(self, assert_authority: Callable[[], None]) -> None:
        from .component_sources import reset_component_sources_cache
        from .soperator_operation_lock import SoperatorOperationLocalLock

        assert_authority()
        with SoperatorOperationLocalLock(
            self.path.parent / ".nebius-cxcli" / "grafana-catalog.lock"
        ):
            transaction = ProjectBundleTransaction(self.path.parent)
            # An identical completed publication is an idempotent attachment.
            if all(
                path.is_file() and path.read_bytes() == data for path, data in self.updates.items()
            ):
                return
            transaction.commit(
                self.updates,
                expected_preimages=self.preimages,
                generation_sha256=publication_identity("attach", self.path),
            )
        reset_component_sources_cache()
        assert_authority()


def prepare_attachment(
    path: Path,
    plans: Sequence[DashboardPlan],
    *,
    folder_title: str,
    overwrite: bool,
    org_id: int = 1,
) -> CatalogAttachment:
    if any(plan.ownership.managed for plan in plans):
        raise GrafanaError("--attach cannot be used with a managed dashboard update")
    from . import component_sources as sources
    from .observability import _grafana_app_id

    path = path.expanduser().absolute()
    if path.is_symlink() or not path.is_file():
        raise ValueError("Catalog must be an existing non-symlink file")
    normalize_project_bundle_target(path.parent, path)
    transaction = ProjectBundleTransaction(path.parent)
    transaction.recover_expected_generation(publication_identity("attach", path))
    original = path.read_bytes()
    preimages = {path: digest(original)}
    serializer = YAML()
    serializer.preserve_quotes = True
    payload = serializer.load(original)
    app = payload["components"]["apps"][_grafana_app_id()]
    defaults = app.setdefault("defaults", {})
    dashboards = defaults.setdefault("values.dashboards", {})
    providers = (
        defaults.setdefault("values.dashboardProviders", {})
        .setdefault("dashboardproviders.yaml", {})
        .setdefault("providers", [])
    )
    updates: dict[Path, bytes] = {}
    keys: dict[str, str] = {}
    for plan in plans:
        folder_key = "cxcli-" + (
            hashlib.sha256(plan.folder.encode()).hexdigest()[:24] if plan.folder else "imports"
        )
        folder = dashboards.setdefault(folder_key, {})
        if any("gnetId" in item for item in folder.values()):
            raise ValueError("Catalog provider mixes JSON and Grafana.com imports")
        relative = f"grafana_dashboards/{folder_key}/{plan.uid}.json"
        json_path = normalize_project_bundle_target(path.parent, path.parent / relative)
        candidate = {"json_file": relative}
        if plan.uid in folder and folder[plan.uid] != candidate and not overwrite:
            raise ValueError("Catalog dashboard entry differs; use --overwrite")
        pre = transaction.snapshot_preimages([json_path], read_only=True)[json_path]
        preimages[json_path] = pre.sha256
        if pre.content is not None and pre.content != json_bytes(plan.dashboard) and not overwrite:
            raise ValueError("Catalog dashboard JSON differs; use --overwrite")
        folder[plan.uid] = candidate
        updates[json_path] = json_bytes(plan.dashboard)
        keys[plan.uid] = f"{folder_key}/{plan.uid}"
        provider = {
            "name": folder_key,
            "orgId": org_id,
            "folder": folder_title,
            "folderUid": plan.folder,
            "type": "file",
            "disableDeletion": False,
            "allowUiUpdates": False,
            "options": {"path": f"/var/lib/grafana/dashboards/{folder_key}"},
        }
        old = next((item for item in providers if item.get("name") == folder_key), None)
        if old is not None and old != provider:
            raise ValueError("Catalog provider has different folder/ownership settings")
        if old is None:
            providers.append(provider)
    # Validate the complete candidate via the catalog owner before publication.
    check = yaml.safe_load(dump_yaml(payload))
    for plan in plans:
        folder_key, key = keys[plan.uid].split("/")
        check["components"]["apps"][_grafana_app_id()]["defaults"]["values.dashboards"][folder_key][
            key
        ] = {"json": json_bytes(plan.dashboard).decode()}
    cli_path = sources.resolve_component_cli_settings_file(component_sources_file=path)
    parsed = sources._parse_sources_payload(
        check,
        cli_settings_payload=yaml.safe_load(cli_path.read_bytes()),
        source_profile=sources.resolve_component_sources_profile(),
        source_root=path.parent,
        cli_source_root=cli_path.parent,
    )
    chart = sources.helm_chart_source_by_id(_grafana_app_id(), sources=parsed)
    assert chart is not None
    # A UID has one catalog owner, even when --overwrite permits content updates.
    # Inspect normalized defaults so inline/file sources and dotted paths agree.
    for (folder_key, key), item in sources._grafana_dashboard_defaults(chart.defaults).items():
        uid = str(json.loads(item["json"])["uid"] if item.get("json") else item.get("uid", ""))
        if uid in keys and keys[uid] != f"{folder_key}/{key}":
            raise ValueError(
                f"Dashboard {uid} already belongs to catalog entry {folder_key}/{key}; "
                "resolve its catalog ownership explicitly before attaching elsewhere"
            )
    updates[path] = dump_yaml(payload)
    if path.read_bytes() != original:
        raise RuntimeError("Catalog changed during dashboard preflight")
    return CatalogAttachment(path, updates, preimages, keys)
