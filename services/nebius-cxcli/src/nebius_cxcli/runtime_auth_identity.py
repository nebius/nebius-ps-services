"""Strict runtime IAM validation with explicit operator-owned role reconciliation."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .iam_bootstrap import CIIdentityEnsureResult


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

    def verify(
        self,
        material: RuntimeIdentityMaterial,
        *,
        allow_mutation: bool = False,
        profile: str | None = None,
        endpoint: str | None = None,
        sdk_config_file: Path | None = None,
    ) -> None:
        """Check runtime authority; only explicit auth may reconcile project roles."""
        with self.project_lock(project_id=material.project_id, client_name=material.client_name):
            try:
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
                return
            except Exception:
                if not allow_mutation:
                    raise RuntimeError(
                        "Canonical runtime identity or project admin permissions could not be verified. "
                        "Run `nebius-cxcli auth --project-id "
                        + material.project_id
                        + "` with project IAM administrator authority before deployment."
                    ) from None
            try:
                with self.operator_environment(
                    project_id=material.project_id, client_name=material.client_name
                ):
                    identity = self.ensure_identity(
                        project_id=material.project_id,
                        service_account_name=self.service_account_name,
                        service_account_description="Canonical service account used by nebius-cxcli project automation",
                        role_ids=[self.role],
                        profile=profile,
                        endpoint=endpoint,
                        config_file=sdk_config_file,
                        prefer_operator_auth=True,
                        allow_mutation=True,
                        strict_managed_identity=True,
                        reconcile_managed_roles=True,
                        expected_service_account_id=material.service_account_id,
                    )
                    self.ensure_identity(
                        project_id=material.project_id,
                        service_account_name=self.service_account_name,
                        service_account_description="Canonical service account used by nebius-cxcli project automation",
                        role_ids=[self.role],
                        profile=profile,
                        endpoint=endpoint,
                        config_file=sdk_config_file,
                        prefer_operator_auth=True,
                        allow_mutation=False,
                        strict_managed_identity=True,
                        expected_service_account_id=material.service_account_id,
                    )
            except Exception:
                raise RuntimeError(
                    "Canonical project permission reconciliation failed. Verify operator IAM "
                    "authority and strict identity ownership, then rerun targeted auth."
                ) from None

            if identity.service_account_id != material.service_account_id:
                raise RuntimeError(
                    "Canonical runtime identity changed during permission reconciliation"
                )
