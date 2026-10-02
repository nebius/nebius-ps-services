"""Preflight and version-guarded dashboard reconciliation, shared by all callers."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .grafana_api import DashboardOwnership, GrafanaClient, GrafanaError
from .grafana_dashboards import content_digest, map_datasources


@dataclass(frozen=True)
class DashboardPlan:
    dashboard: dict[str, Any]
    folder: str
    previous: dict[str, Any] | None
    unchanged: bool
    ownership: DashboardOwnership = DashboardOwnership()

    @property
    def uid(self) -> str:
        return str(self.dashboard["uid"])

    @property
    def digest(self) -> str:
        return content_digest(self.dashboard, self.folder)


def resource_digest(client: GrafanaClient, resource: dict[str, Any]) -> str:
    return content_digest(client.portable(resource), client.folder(resource))


def check_import_ownership(
    client: GrafanaClient,
    dashboards: Sequence[dict[str, Any]],
    *,
    progress: Callable[[str], None] = lambda _: None,
    overwrite: bool = False,
    folder: str = "",
    folder_explicit: bool = False,
    attach: bool = False,
) -> dict[str, dict[str, Any] | None]:
    """Reject known batch conflicts before asking for input or converting schemas."""
    previous: dict[str, dict[str, Any] | None] = {}
    for index, dashboard in enumerate(dashboards, 1):
        progress(f"Checking dashboard ownership {index} of {len(dashboards)}")
        uid = str(dashboard["uid"])
        if uid in previous:
            raise ValueError(f"Duplicate dashboard UID: {uid}")
        if dashboard.get("__elements"):
            raise ValueError(
                "Embedded library elements are not supported; export resolved Classic JSON"
            )
        resource = client.get(uid)
        ownership = client.admit_update(resource, overwrite=overwrite)
        if ownership.managed:
            if attach:
                raise GrafanaError("--attach cannot be used with a managed dashboard update")
            if (folder_explicit or folder) and folder != ownership.folder:
                raise GrafanaError("A managed dashboard must keep its provisioned folder")
        previous[uid] = resource
    return previous


def prepare_imports(
    client: GrafanaClient,
    dashboards: Sequence[dict[str, Any]],
    *,
    folder: str = "",
    mappings: Mapping[str, str] | None = None,
    overwrite: bool = False,
    progress: Callable[[str], None] = lambda _: None,
    folder_explicit: bool = False,
    attach: bool = False,
    expected_ownership: Mapping[str, DashboardOwnership] | None = None,
    preflight: Callable[[dict[str, dict[str, Any] | None]], None] = lambda _: None,
) -> list[DashboardPlan]:
    previous_by_uid = check_import_ownership(
        client,
        dashboards,
        progress=progress,
        overwrite=overwrite,
        folder=folder,
        folder_explicit=folder_explicit,
        attach=attach,
    )
    if expected_ownership is not None:
        for uid, resource in previous_by_uid.items():
            expected_ownership[uid].assert_current(resource)
    preflight(previous_by_uid)
    progress("Checking destination folder")
    client.folder_title(folder)
    progress("Loading available datasources")
    available = client.datasources()
    plans: list[DashboardPlan] = []
    for index, dashboard in enumerate(dashboards, 1):
        progress(f"Validating dashboard {index} of {len(dashboards)}")
        uid = str(dashboard["uid"])
        previous = previous_by_uid[uid]
        ownership = client.admit_update(previous, overwrite=overwrite)
        destination_folder = ownership.folder if ownership.managed else folder
        if destination_folder != folder:
            client.folder_title(destination_folder)
        mapped = map_datasources({**dashboard, "editable": True}, mappings or {}, available)
        desired = client.canonical(mapped, destination_folder)
        unchanged = False
        if previous is not None:
            desired_digest = content_digest(desired, destination_folder)
            unchanged = resource_digest(client, previous) == desired_digest
            if not unchanged:
                current = client.canonical(client.portable(previous), client.folder(previous))
                unchanged = content_digest(current, client.folder(previous)) == desired_digest
            if not unchanged and not overwrite:
                raise GrafanaError(f"Dashboard {uid} differs; use --overwrite to replace it")
        plans.append(DashboardPlan(desired, destination_folder, previous, unchanged, ownership))
    return plans


def execute_imports(
    client: GrafanaClient,
    plans: Sequence[DashboardPlan],
    *,
    checkpoint: Callable[[DashboardPlan, str, dict[str, Any] | None], None] = lambda *_: None,
    emit: Callable[[str], None] = print,
    progress: Callable[[str], None] = lambda _: None,
) -> dict[str, str]:
    results: dict[str, str] = {}
    for index, plan in enumerate(plans, 1):
        try:
            progress(f"Checking dashboard {index} of {len(plans)} before installation")
            current = client.get(plan.uid)
            plan.ownership.assert_current(current)
            # Readback resolves a lost successful response before any retry.
            if current is not None and resource_digest(client, current) == plan.digest:
                checkpoint(plan, "unchanged", current)
                results[plan.uid] = "unchanged"
                emit(f"Unchanged: {plan.uid}")
                continue
            if plan.unchanged:
                # Canonical migration may have made the older stored spec equal.
                if current == plan.previous:
                    checkpoint(plan, "unchanged", current)
                    results[plan.uid] = "unchanged"
                    emit(f"Unchanged: {plan.uid}")
                    continue
                raise GrafanaError("Dashboard changed after preflight")
            if (current is None) != (plan.previous is None) or (
                current is not None
                and plan.previous is not None
                and current["metadata"].get("resourceVersion")
                != plan.previous["metadata"].get("resourceVersion")
            ):
                raise GrafanaError("Dashboard changed after preflight")
            checkpoint(plan, "writing", current)
            try:
                progress(f"Installing dashboard {index} of {len(plans)}")
                written = client.write(
                    plan.dashboard, plan.folder, plan.previous, ownership=plan.ownership
                )
            except GrafanaError as exc:
                if exc.status is not None:
                    raise
                progress(f"Checking write outcome for dashboard {index} of {len(plans)}")
                observed = client.get(plan.uid)
                if observed is None or resource_digest(client, observed) != plan.digest:
                    raise GrafanaError(
                        "Write outcome is unresolved; rerun this import to inspect before retrying"
                    ) from None
                plan.ownership.assert_current(observed)
                written = observed
            plan.ownership.assert_current(written)
            progress(f"Verifying dashboard {index} of {len(plans)}")
            observed = client.get(plan.uid)
            if (
                observed is None
                or resource_digest(client, observed) != resource_digest(client, written)
                or resource_digest(client, observed) != plan.digest
            ):
                raise GrafanaError(
                    "Dashboard readback differs from the requested canonical content"
                )
            plan.ownership.assert_current(observed)
            checkpoint(plan, "installed", observed)
            results[plan.uid] = "installed"
            emit(f"Installed: {plan.uid}")
        except (GrafanaError, OSError, RuntimeError) as exc:
            results[plan.uid] = "failed"
            emit(f"Failed: {plan.uid}: {exc}")
            # Preserve successful work. The next invocation repeats preflight.
            break
    remaining = [plan.uid for plan in plans if plan.uid not in results]
    for uid in remaining:
        results[uid] = "pending"
        emit(f"Pending: {uid}")
    return results


def require_complete(results: Mapping[str, str]) -> None:
    if any(value in {"failed", "pending"} for value in results.values()):
        raise GrafanaError(
            "Dashboard import is incomplete; successful items were retained. Rerun the same command."
        )
