"""Provider boundary regressions using the installed SDK's real response types."""

from io import StringIO
from types import SimpleNamespace

import pytest
from nebius.api.nebius.common.v1 import ResourceMetadata
from nebius.api.nebius.compute.v1 import (
    ListPlatformsResponse,
    Platform,
    PlatformServiceClient,
    PlatformSpec,
    Preset,
    PresetResources,
)
from nebius.api.nebius.iam import v1 as iam_v1
from nebius.api.nebius.iam import v2 as iam_v2
from nebius.api.nebius.vpc.v1 import (
    IPv4PrivateSubnetPools,
    Subnet,
    SubnetCidr,
    SubnetPool,
    SubnetServiceClient,
    SubnetSpec,
    SubnetStatus,
    SubnetStatusPool,
)
from rich.console import Console

from nebius_cxcli import cli, soperator_public_discovery
from nebius_cxcli.mk8s_preflight import _subnet_pool_cidrs
from nebius_cxcli.provider_options import ProviderOptionLookup
from nebius_cxcli.soperator_install_progress import install_progress_scope
from nebius_cxcli.soperator_upgrade_progress import SoperatorUpgradeProgress


@pytest.mark.parametrize("mode", ["explicit", "prefix", "inherited", "omitted"])
def test_sdk_subnet_pool_status_preserves_cidrs_without_deprecation(monkeypatch, caplog, mode):
    private_pools = (
        None
        if mode == "omitted"
        else IPv4PrivateSubnetPools(
            use_network_pools=mode == "inherited",
            pools=[SubnetPool(cidrs=[SubnetCidr(cidr="/24")])] if mode == "prefix" else [],
        )
    )
    subnet = Subnet(
        metadata=ResourceMetadata(id="vpcsubnet-test"),
        spec=SubnetSpec(network_id="vpcnetwork-test", ipv4_private_pools=private_pools),
        status=SubnetStatus(
            ipv4_private_pools=[
                SubnetStatusPool(cidrs=["10.0.0.0/24"]),
                SubnetStatusPool(cidrs=["10.1.0.0/24"]),
            ]
        ),
    )
    lookup = ProviderOptionLookup()
    monkeypatch.setattr(lookup, "_sdk_or_none", lambda: object())
    monkeypatch.setattr(lookup, "_paged_list", lambda **kwargs: [subnet])
    monkeypatch.setattr(SubnetServiceClient, "__init__", lambda self, sdk: None)
    choices = lookup.resolve(
        provider="project_subnets", args={"project_id": "project-test"}, payload={}, field_path=""
    )
    expected_owned = ("10.0.0.0/24", "10.1.0.0/24") if mode in {"explicit", "prefix"} else ()
    assert choices[0].metadata["private_cidrs"] == expected_owned
    assert "10.0.0.0/24, 10.1.0.0/24" in choices[0].label
    assert _subnet_pool_cidrs(subnet) == expected_owned
    assert not [record for record in caplog.records if "deprecated" in record.getMessage()]


def _platform(name, preset, *, gpu=False):
    return Platform(
        metadata=ResourceMetadata(name=name),
        spec=PlatformSpec(
            presets=[
                Preset(
                    name=preset,
                    resources=PresetResources(
                        vcpu_count=8, memory_gibibytes=32, gpu_count=int(gpu)
                    ),
                    allow_gpu_clustering=gpu,
                )
            ]
        ),
    )


def test_project_inventory_serves_selected_and_previous_shapes_without_refetch(monkeypatch):
    calls = []

    def list_platforms(self, request, **kwargs):
        calls.append((request.parent_id, request.page_token))
        assert kwargs["retries"] == 0
        if not request.page_token:
            response = ListPlatformsResponse(
                items=[_platform("cpu-d3", "8vcpu-32gb")], next_page_token="next"
            )
        else:
            response = ListPlatformsResponse(
                items=[_platform("gpu-h200-sxm", "1gpu-8vcpu-32gb", gpu=True)]
            )
        return SimpleNamespace(wait=lambda: response)

    monkeypatch.setattr(PlatformServiceClient, "__init__", lambda self, sdk: None)
    monkeypatch.setattr(PlatformServiceClient, "list", list_platforms)
    monkeypatch.setattr(
        PlatformServiceClient, "get_by_name", lambda *args, **kwargs: pytest.fail("redundant fetch")
    )
    lookup = ProviderOptionLookup()
    monkeypatch.setattr(lookup, "_sdk_or_none", lambda: object())
    output = StringIO()
    progress = SoperatorUpgradeProgress(Console(file=output), prefix="Soperator install")
    with install_progress_scope(progress):
        choices = lookup.resolve(
            provider="compute_platforms",
            args={"project_id": "project-test"},
            payload={},
            field_path="",
        )
        assert [choice.value for choice in choices] == ["cpu-d3", "gpu-h200-sxm"]
        for _ in range(4):
            for platform, preset, expected in [
                ("cpu-d3", "8vcpu-32gb", (8, 32, 0)),
                ("gpu-h200-sxm", "1gpu-8vcpu-32gb", (8, 32, 1)),
                ("gpu-h100-sxm", "1gpu-8vcpu-32gb", None),
            ]:
                assert (
                    lookup.compute_platform_preset_resources(
                        project_id="project-test", platform_name=platform, preset_name=preset
                    )
                    == expected
                )
        assert (
            lookup.compute_platform_preset_allows_gpu_clustering(
                project_id="project-test",
                platform_name="gpu-h200-sxm",
                preset_name="1gpu-8vcpu-32gb",
            )
            is True
        )
    assert calls == [("project-test", ""), ("project-test", "next")]
    assert "FAILED" not in output.getvalue()
    assert len(output.getvalue().splitlines()) == 2


@pytest.mark.parametrize("failed_page", ["", "next"])
def test_failed_inventory_does_not_poison_preset_choices_or_cache_partial_pages(
    monkeypatch, failed_page
):
    calls = []
    failed = False

    def list_platforms(self, request, **kwargs):
        nonlocal failed
        calls.append(request.page_token)
        if request.page_token == failed_page and not failed:
            failed = True
            raise RuntimeError("private-provider-detail")
        response = (
            ListPlatformsResponse(items=[_platform("cpu-d3", "8vcpu-32gb")], next_page_token="next")
            if not request.page_token
            else ListPlatformsResponse(
                items=[_platform("gpu-h200-sxm", "1gpu-8vcpu-32gb", gpu=True)]
            )
        )
        return SimpleNamespace(wait=lambda: response)

    monkeypatch.setattr(PlatformServiceClient, "__init__", lambda self, sdk: None)
    monkeypatch.setattr(PlatformServiceClient, "list", list_platforms)
    monkeypatch.setattr(
        PlatformServiceClient, "get_by_name", lambda *args, **kwargs: pytest.fail("redundant fetch")
    )
    lookup = ProviderOptionLookup()
    monkeypatch.setattr(lookup, "_sdk_or_none", lambda: object())
    output = StringIO()
    progress = SoperatorUpgradeProgress(Console(file=output), prefix="Soperator install")
    args = {"project_id": "project-test", "platform": "cpu-d3"}
    with install_progress_scope(progress):
        assert (
            lookup.resolve(
                provider="compute_platform_presets", args=args, payload={}, field_path=""
            )
            == []
        )
        assert lookup.last_error()
        choices = lookup.resolve(
            provider="compute_platform_presets", args=args, payload={}, field_path=""
        )
    assert [choice.value for choice in choices] == ["8vcpu-32gb"]
    assert lookup.last_error() is None
    assert calls == (["", "", "next"] if not failed_page else ["", "next", "", "next"])
    assert "FAILED" in output.getvalue()
    assert "OK" in output.getvalue()
    assert "private-provider-detail" not in output.getvalue()


@pytest.fixture
def iam_v2_only(monkeypatch, caplog):
    """Exercise real v2 constructors; any retired service use is a regression."""

    def retired_client(*args, **kwargs):
        pytest.fail("Project/Tenant discovery must not instantiate IAM v1 clients")

    monkeypatch.setattr(iam_v1, "ProjectServiceClient", retired_client)
    monkeypatch.setattr(iam_v1, "TenantServiceClient", retired_client)
    yield
    assert not [record for record in caplog.records if record.name == "deprecation"]


def _assert_iam_v2_request(client, request, request_type, kwargs):
    assert client.__service_name__ in {
        "nebius.iam.v2.ProjectService",
        "nebius.iam.v2.TenantService",
    }
    assert isinstance(request, request_type)
    assert request.SerializeToString()
    assert kwargs["retries"] == 0
    assert all(kwargs[name] > 0 for name in ("timeout", "auth_timeout", "per_retry_timeout"))


@pytest.mark.parametrize(
    ("project_id", "parent_id", "valid"),
    [
        ("project-test", "tenant-test", True),
        ("project-other", "tenant-test", False),
        ("", "tenant-test", False),
        (" ", "tenant-test", False),
        ("project-test", "tenant-other", False),
        ("project-test", "", False),
    ],
)
def test_iam_v2_scope_requires_exact_project_identity(
    monkeypatch, iam_v2_only, project_id, parent_id, valid
):
    calls = []

    def get_project(self, request, **kwargs):
        _assert_iam_v2_request(self, request, iam_v2.GetProjectRequest, kwargs)
        calls.append(request.id)
        return SimpleNamespace(
            wait=lambda: iam_v2.Project(
                metadata=ResourceMetadata(id=project_id, parent_id=parent_id)
            )
        )

    monkeypatch.setattr(iam_v2.ProjectServiceClient, "get", get_project)
    monkeypatch.setattr(
        iam_v2.TenantServiceClient,
        "get",
        lambda *a, **kw: pytest.fail("Scope validation must work with project-only credentials"),
    )
    lookup = ProviderOptionLookup()
    monkeypatch.setattr(lookup, "_sdk_or_none", lambda: object())
    result = lookup.validate_tenant_project_scope(
        tenant_id="tenant-test", project_id="project-test"
    )
    assert result.valid is valid
    assert calls == ["project-test"]


def test_iam_v2_scope_failure_sanitizes_result_and_cli_diagnostic(monkeypatch, iam_v2_only):
    def failed_request():
        raise RuntimeError("private-provider-detail")

    def get_project(self, request, **kwargs):
        _assert_iam_v2_request(self, request, iam_v2.GetProjectRequest, kwargs)
        return SimpleNamespace(wait=failed_request)

    monkeypatch.setattr(iam_v2.ProjectServiceClient, "get", get_project)
    lookup = ProviderOptionLookup()
    monkeypatch.setattr(lookup, "_sdk_or_none", lambda: object())
    result = lookup.validate_tenant_project_scope(
        tenant_id="tenant-test", project_id="project-test"
    )
    assert not result.valid
    assert "not accessible" in result.message
    assert "private-provider-detail" not in result.message
    with pytest.raises(RuntimeError, match="Nebius scope validation failed") as caught:
        cli._validate_tenant_project_ids_or_prompt(
            tenant_id="tenant-test",
            project_id="project-test",
            interactive=False,
            provider_lookup=lookup,
        )
    assert "private-provider-detail" not in str(caught.value)


@pytest.mark.parametrize("tenant_access", [True, False])
@pytest.mark.parametrize("project_access", [True, False])
def test_iam_v2_optional_names_preserve_create_folder_naming(
    monkeypatch, iam_v2_only, tenant_access, project_access
):
    calls = []

    def get_tenant(self, request, **kwargs):
        _assert_iam_v2_request(self, request, iam_v2.GetTenantRequest, kwargs)
        calls.append(("tenant", request.id))
        if not tenant_access:
            raise PermissionError("private-provider-detail")
        return SimpleNamespace(
            wait=lambda: iam_v2.Tenant(metadata=ResourceMetadata(name="Test Tenant"))
        )

    def get_project(self, request, **kwargs):
        _assert_iam_v2_request(self, request, iam_v2.GetProjectRequest, kwargs)
        calls.append(("project", request.id))
        if not project_access:
            raise PermissionError("private-provider-detail")
        return SimpleNamespace(
            wait=lambda: iam_v2.Project(metadata=ResourceMetadata(name="Test Project"))
        )

    monkeypatch.setattr(iam_v2.TenantServiceClient, "get", get_tenant)
    monkeypatch.setattr(iam_v2.ProjectServiceClient, "get", get_project)
    lookup = ProviderOptionLookup()
    monkeypatch.setattr(lookup, "_sdk_or_none", lambda: object())
    folders = cli._resolve_create_target_folders(
        provider_lookup=lookup, tenant_id="tenant-test", project_id="project-test"
    )
    assert folders == (
        "test-tenant" if tenant_access else "tenant-test",
        "test-project" if project_access else "project-test",
    )
    assert calls == [("tenant", "tenant-test"), ("project", "project-test")]


def test_iam_v2_project_listing_paginates_sorts_and_caches_by_tenant(monkeypatch, iam_v2_only):
    calls = []

    def list_projects(self, request, **kwargs):
        _assert_iam_v2_request(self, request, iam_v2.ListProjectsRequest, kwargs)
        assert request.page_size == 999
        calls.append((request.parent_id, request.page_token))
        suffix = "a" if request.page_token else "b"
        return SimpleNamespace(
            wait=lambda: iam_v2.ListProjectsResponse(
                items=[
                    iam_v2.Project(
                        metadata=ResourceMetadata(
                            id=f"{request.parent_id}-{suffix}", name=f"Project {suffix}"
                        )
                    )
                ],
                next_page_token="" if request.page_token else "next",
            )
        )

    monkeypatch.setattr(iam_v2.ProjectServiceClient, "list", list_projects)
    lookup = ProviderOptionLookup()
    monkeypatch.setattr(lookup, "_sdk_or_none", lambda: object())
    for tenant in ("tenant-test", "tenant-test", "tenant-other"):
        choices = lookup.resolve(
            provider="tenant_projects", args={"tenant_id": tenant}, payload={}, field_path=""
        )
        assert [choice.value for choice in choices] == [f"{tenant}-a", f"{tenant}-b"]
        assert [choice.label for choice in choices] == [
            f"{tenant}-a  (Project a)",
            f"{tenant}-b  (Project b)",
        ]
    assert calls == [
        ("tenant-test", ""),
        ("tenant-test", "next"),
        ("tenant-other", ""),
        ("tenant-other", "next"),
    ]


@pytest.mark.parametrize("failure", ["denied", "repeated-token"])
def test_iam_v2_project_listing_does_not_cache_incomplete_pages(monkeypatch, iam_v2_only, failure):
    tokens = []
    should_fail = True

    def list_projects(self, request, **kwargs):
        _assert_iam_v2_request(self, request, iam_v2.ListProjectsRequest, kwargs)
        tokens.append(request.page_token)
        if request.page_token and should_fail and failure == "denied":
            raise PermissionError("private-provider-detail")
        return SimpleNamespace(
            wait=lambda: iam_v2.ListProjectsResponse(
                items=[iam_v2.Project(metadata=ResourceMetadata(id="project-test"))]
                if not request.page_token
                else [],
                next_page_token="next" if not request.page_token or should_fail else "",
            )
        )

    monkeypatch.setattr(iam_v2.ProjectServiceClient, "list", list_projects)
    lookup = ProviderOptionLookup()
    monkeypatch.setattr(lookup, "_sdk_or_none", lambda: object())
    args = {"tenant_id": "tenant-test"}
    assert lookup.resolve(provider="tenant_projects", args=args, payload={}, field_path="") == []
    assert lookup.last_error()
    should_fail = False
    choices = lookup.resolve(provider="tenant_projects", args=args, payload={}, field_path="")
    assert [choice.value for choice in choices] == ["project-test"]
    assert lookup.last_error() is None
    assert tokens == ["", "next", "", "next"]


@pytest.mark.parametrize(
    ("project_id", "parent_id", "cluster_parent", "error"),
    [
        ("project-test", "tenant-test", "project-test", None),
        ("", "tenant-test", "project-test", "project identity"),
        ("project-other", "tenant-test", "project-test", "project identity"),
        ("project-test", "", "project-test", "does not belong to tenant"),
        ("project-test", "tenant-other", "project-test", "does not belong to tenant"),
        ("project-test", "tenant-test", "project-other", "does not belong to project"),
    ],
)
def test_iam_v2_public_discovery_binds_scope_and_closes_sdk(
    monkeypatch, iam_v2_only, project_id, parent_id, cluster_parent, error
):
    closed = []
    calls = []

    def get_project(self, request, **kwargs):
        _assert_iam_v2_request(self, request, iam_v2.GetProjectRequest, kwargs)
        calls.append(request.id)
        return SimpleNamespace(
            wait=lambda: iam_v2.Project(
                metadata=ResourceMetadata(id=project_id, parent_id=parent_id)
            )
        )

    def get_cluster(cluster_id):
        assert project_id == "project-test" and parent_id == "tenant-test"
        return SimpleNamespace(metadata=ResourceMetadata(id=cluster_id, parent_id=cluster_parent))

    sdk = SimpleNamespace(sync_close=lambda: closed.append(True))
    monkeypatch.setattr(iam_v2.ProjectServiceClient, "get", get_project)
    monkeypatch.setattr(soperator_public_discovery, "init_nebius_sdk", lambda **kw: sdk)
    monkeypatch.setattr(
        soperator_public_discovery,
        "Mk8sKubernetesVersionExecutor",
        lambda sdk: SimpleNamespace(
            get_cluster=get_cluster, list_node_groups=lambda cluster_id: []
        ),
    )
    observe = soperator_public_discovery.SoperatorPublicDiscoveryRuntime.provider_observation
    kwargs = dict(tenant_id="tenant-test", project_id="project-test", cluster_id="cluster-test")
    if error:
        with pytest.raises(RuntimeError, match=error):
            observe(**kwargs)
    else:
        _, provider, errors = observe(**kwargs)
        assert provider["node_groups"] == []
        assert errors == []
    assert calls == ["project-test"]
    assert closed == [True]
