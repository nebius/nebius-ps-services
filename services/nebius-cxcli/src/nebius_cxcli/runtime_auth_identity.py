"""Strict runtime IAM validation with operator-owned managed-state reconciliation."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .iam_bootstrap import CIIdentityEnsureResult, ManagedIdentityDriftError


def _iam_read_permission_denied(exc: Exception) -> bool:
    from grpc import StatusCode
    from nebius.aio.service_error import RequestError

    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, RequestError):
            return current.status.code == StatusCode.PERMISSION_DENIED
        current = current.__cause__
    return False


class RuntimeIdentityMaterial(Protocol):
    @property
    def project_id(self) -> str: ...

    @property
    def client_name(self) -> str: ...

    @property
    def service_account_id(self) -> str: ...


@dataclass
class RuntimeIdentityVerifier:
    project_lock: Callable[..., AbstractContextManager[None]]
    canonical_environment: Callable[..., AbstractContextManager[None]]
    operator_environment: Callable[..., AbstractContextManager[None]]
    ensure_identity: Callable[..., CIIdentityEnsureResult]
    service_account_name: str
    role: str

    def _verify_canonical(self, material: RuntimeIdentityMaterial) -> None:
        with self.canonical_environment(material):
            identity = self.ensure_identity(
                project_id=material.project_id,
                service_account_name=self.service_account_name,
                service_account_description="Canonical service account used by nebius-cxcli project automation",
                role_ids=[self.role],
                profile=None,
                endpoint=None,
                config_file=None,
                prefer_operator_auth=False,
                allow_mutation=False,
                allow_cli_token=False,
                strict_managed_identity=True,
                expected_service_account_id=material.service_account_id,
            )
        if identity.service_account_id != material.service_account_id:
            raise RuntimeError("Canonical runtime identity changed")

    def verify(
        self,
        material: RuntimeIdentityMaterial,
        *,
        allow_mutation: bool = False,
        profile: str | None = None,
        endpoint: str | None = None,
        sdk_config_file: Path | None = None,
    ) -> None:
        """Reuse healthy authority; reconcile only classified managed IAM drift."""
        with self.project_lock(project_id=material.project_id, client_name=material.client_name):
            inspect_with_operator = False
            try:
                self._verify_canonical(material)
                return
            except ManagedIdentityDriftError:
                if not allow_mutation:
                    raise RuntimeError(
                        "Canonical runtime identity needs managed project permission reconciliation. "
                        "This authentication check is read-only; rerun a project command with "
                        "operator IAM administrator authority."
                    ) from None
            except Exception as exc:
                if not allow_mutation or not _iam_read_permission_denied(exc):
                    raise RuntimeError(
                        "Canonical runtime identity could not be verified. Check IAM availability "
                        "and managed identity ownership; no permission repair was attempted."
                    ) from None
                inspect_with_operator = True
            try:
                with self.operator_environment(
                    project_id=material.project_id, client_name=material.client_name
                ):
                    operator_options = dict(
                        project_id=material.project_id,
                        service_account_name=self.service_account_name,
                        service_account_description="Canonical service account used by nebius-cxcli project automation",
                        role_ids=[self.role],
                        profile=profile,
                        endpoint=endpoint,
                        config_file=sdk_config_file,
                        prefer_operator_auth=True,
                        strict_managed_identity=True,
                        expected_service_account_id=material.service_account_id,
                    )
                    if inspect_with_operator:
                        try:
                            self.ensure_identity(**operator_options, allow_mutation=False)
                        except ManagedIdentityDriftError:
                            pass
                        else:
                            raise RuntimeError(
                                "Canonical IAM denial has no managed drift to repair"
                            )
                    identity = self.ensure_identity(
                        **operator_options, allow_mutation=True, reconcile_managed_roles=True
                    )
                if identity.service_account_id != material.service_account_id:
                    raise RuntimeError("Canonical runtime identity changed during reconciliation")
            except Exception:
                raise RuntimeError(
                    "Automatic canonical project permission reconciliation failed. "
                    "Verify that the operator credentials have project IAM administrator "
                    "authority and that managed identity ownership is intact, then retry "
                    "the original command."
                ) from None

            try:
                self._verify_canonical(material)
            except Exception:
                raise RuntimeError(
                    "Canonical runtime permissions could not be verified after reconciliation. "
                    "No credentials were replaced; retry the original command once IAM "
                    "permissions are available."
                ) from None
