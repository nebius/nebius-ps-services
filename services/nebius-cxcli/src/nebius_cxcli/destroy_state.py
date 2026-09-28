"""Backend authority and admission for an interrupted SDK destroy."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from .deployment_state import ConditionalObjectStore, DeploymentState
from .destroy import DestroyReceipt
from .paths import ProjectPaths
from .project_bundle_transaction import recover_project_bundle
from .terraform_backend import TerraformBackendSettings

_OWNER: ContextVar[tuple[str, str, str] | None] = ContextVar("destroy_owner", default=None)


def destroy_receipt_path(paths: ProjectPaths, cluster_id: str) -> Path:
    token = hashlib.sha256(cluster_id.encode("utf-8")).hexdigest()
    return paths.reports_dir / f"destroy-{token}.json"


class DestroyState:
    def __init__(
        self,
        store: ConditionalObjectStore,
        settings: TerraformBackendSettings,
        *,
        assert_held: Callable[[], object] = lambda: None,
    ) -> None:
        self.store = store
        self.assert_held = assert_held
        self.backend = DeploymentState(store, settings, assert_held=assert_held).prefix
        self.key = self.backend + "/destroy.json"
        self.etag: str | None = None
        self.receipt: DestroyReceipt | None = None

    def read(self) -> DestroyReceipt | None:
        record = self.store.read(self.key)
        self.etag = record.etag if record else None
        self.receipt = DestroyReceipt.from_payload(record.value) if record else None
        if self.receipt and self.receipt.approved["backend"] != self.backend:
            raise RuntimeError("Destroy receipt belongs to another backend")
        return self.receipt

    def save(self, receipt: DestroyReceipt) -> None:
        self.assert_held()
        if receipt.approved["backend"] != self.backend or not receipt.checkpoints:
            raise RuntimeError("Only approved backend-bound destroys can be published")
        previous = self.receipt
        if (
            previous
            and previous.status == "complete"
            and previous.approval_fingerprint == receipt.approval_fingerprint
        ):
            if previous != receipt:
                raise RuntimeError("Completed destroy authority cannot be reopened")
            return
        if previous and previous.status != "complete":
            if previous.approval_fingerprint != receipt.approval_fingerprint:
                raise RuntimeError("An active destroy cannot be replaced")
            if receipt.checkpoints[: len(previous.checkpoints)] != previous.checkpoints:
                raise RuntimeError("Destroy checkpoints cannot move backwards")
        if previous and previous.approval_fingerprint != receipt.approval_fingerprint:
            # Preserve the completed audit record before admitting another target.
            archive = self.backend + "/destroy-history/" + previous.approval_fingerprint[7:]
            existing = self.store.read(archive)
            if existing is None:
                self.store.write(archive, previous.as_payload(), etag=None)
            elif existing.value != previous.as_payload():
                raise RuntimeError("Completed destroy history conflicts")
        self.etag = self.store.write(self.key, receipt.as_payload(), etag=self.etag)
        self.receipt = receipt

    def require_admission(self) -> None:
        receipt = self.read()
        if receipt and receipt.status != "complete":
            expected = (self.backend, receipt.target_ref, receipt.approval_fingerprint)
            if _OWNER.get() != expected:
                raise RuntimeError(
                    f"Backend has an unfinished MK8s destroy for {receipt.target_ref}; "
                    "resume that exact destroy before other mutations"
                )


@contextmanager
def destroy_owner(state: DestroyState, target_ref: str, fingerprint: str) -> Iterator[None]:
    token = _OWNER.set((state.backend, target_ref, fingerprint))
    try:
        yield
    finally:
        _OWNER.reset(token)


def prepare_destroy_admission_auth(config: object) -> None:
    from . import cli
    from .runtime_config import to_plain_data, wrap_runtime_config

    payload = to_plain_data(config)
    runtime = wrap_runtime_config(payload) if isinstance(payload, dict) else config
    # Export cached canonical S3 credentials before invoking the backend transport.
    # The caller's preview context prohibits credential creation for dry-runs.
    cli._ensure_runtime_auth_material(runtime, need_terraform=True)
    cli._ensure_backend_s3_env_aliases()


@contextmanager
def existing_project_write_lease(config_path):
    """Fence publication using the existing source identity, never its candidate replacement."""
    from . import cli
    from .deployment_recovery import is_deployment_preview

    if is_deployment_preview():
        raise RuntimeError("Deployment preview cannot publish project configuration")
    if not config_path.exists():
        yield
        return
    before = config_path.read_bytes()
    config = cli.load_config(config_path, persist_normalized=False)
    paths = cli.resolve_project_paths(config_path)
    with cli._deployment_execution(
        config=config, paths=paths, target_ref="project", operation_id="config-publication"
    ) as lease:
        lease.assert_held()
        if config_path.read_bytes() != before:
            raise RuntimeError(
                "Project configuration changed while acquiring local execution ownership; rerun the command"
            )
        recover_project_bundle(config_path.absolute().parent)
        lease.assert_held()
        if config_path.read_bytes() != before:
            raise RuntimeError(
                "Project configuration was recovered from its committed generation; "
                "rerun the command to plan from the recovered source"
            )
        yield
