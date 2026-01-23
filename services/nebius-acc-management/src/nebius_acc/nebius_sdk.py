from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from nebius.aio.cli_config import Config as NebiusConfig
from nebius.aio.cli_config import ConfigError as NebiusConfigError
from nebius.aio.token.static import NoTokenInEnvError
from nebius.api.nebius.iam.v1 import (
    AccessPermitServiceClient,
    FederationCertificateServiceClient,
    FederationServiceClient,
    GroupMembershipServiceClient,
    GroupServiceClient,
    InvitationServiceClient,
    ProjectServiceClient,
    TenantUserAccountWithAttributesServiceClient,
)
from nebius.api.nebius.quotas.v1 import QuotaAllowanceServiceClient
from nebius.sdk import SDK

from .errors import ConfigError


@dataclass
class NebiusSdk:
    sdk: SDK
    projects: ProjectServiceClient
    groups: GroupServiceClient
    group_memberships: GroupMembershipServiceClient
    access_permits: AccessPermitServiceClient
    invitations: InvitationServiceClient
    quotas: QuotaAllowanceServiceClient
    federations: FederationServiceClient
    federation_certs: FederationCertificateServiceClient
    tenant_users: TenantUserAccountWithAttributesServiceClient

    @classmethod
    def from_config(
        cls,
        config_file: Path | None = None,
        profile: str | None = None,
        endpoint: str | None = None,
    ) -> NebiusSdk:
        try:
            config_kwargs: dict[str, object] = {}
            if config_file:
                config_kwargs["config_file"] = config_file
            if profile:
                config_kwargs["profile"] = profile
            if endpoint:
                config_kwargs["endpoint"] = endpoint
            config = NebiusConfig(**config_kwargs)
            sdk = SDK(config_reader=config)
        except (NebiusConfigError, NoTokenInEnvError) as exc:
            raise ConfigError(
                "Nebius credentials not found. Set NEBIUS_IAM_TOKEN or configure ~/.nebius/config.yaml."
            ) from exc
        return cls.from_sdk(sdk)

    @classmethod
    def from_sdk(cls, sdk: SDK) -> NebiusSdk:
        return cls(
            sdk=sdk,
            projects=ProjectServiceClient(sdk),
            groups=GroupServiceClient(sdk),
            group_memberships=GroupMembershipServiceClient(sdk),
            access_permits=AccessPermitServiceClient(sdk),
            invitations=InvitationServiceClient(sdk),
            quotas=QuotaAllowanceServiceClient(sdk),
            federations=FederationServiceClient(sdk),
            federation_certs=FederationCertificateServiceClient(sdk),
            tenant_users=TenantUserAccountWithAttributesServiceClient(sdk),
        )
