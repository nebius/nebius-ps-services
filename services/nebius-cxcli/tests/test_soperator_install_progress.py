from __future__ import annotations

from io import StringIO
from types import SimpleNamespace

import pytest
from rich.console import Console

from nebius_cxcli import iam_bootstrap, provider_options
from nebius_cxcli.soperator_install_progress import (
    install_phase,
    install_progress_active,
    install_progress_scope,
    install_progress_step,
)
from nebius_cxcli.soperator_upgrade_progress import SoperatorUpgradeProgress


@pytest.mark.parametrize("terminal", [False, True])
def test_install_progress_precedes_work_and_resets_after_nested_phases(terminal: bool) -> None:
    output = StringIO()
    console = Console(file=output, force_terminal=terminal, width=120, _environ={"TERM": "xterm"})
    progress = SoperatorUpgradeProgress(console, terminal=terminal, prefix="Soperator install")

    @install_progress_step("auth", "Preparing project authentication")
    def authenticate() -> str:
        assert "Preparing project authentication" in output.getvalue()
        with install_phase("create-sa", "Creating service account"):
            assert "Creating service account" in output.getvalue()
        return "ready"

    with install_progress_scope(progress):
        assert authenticate() == "ready"
        assert console._live_stack == []
    assert not install_progress_active()
    text = output.getvalue()
    assert "✓" in text if terminal else "Soperator install: OK" in text
    if not terminal:
        assert "\x1b" not in text
        assert len(text.splitlines()) == 4


@pytest.mark.parametrize("error", [RuntimeError("secret=do-not-display"), KeyboardInterrupt()])
def test_install_failure_is_not_success_and_leaves_no_active_context(error: BaseException) -> None:
    output = StringIO()
    console = Console(file=output, force_terminal=False, no_color=True)
    progress = SoperatorUpgradeProgress(console, prefix="Soperator install")
    with (
        pytest.raises(type(error)),
        install_progress_scope(progress),
        install_phase("auth", "Authenticating"),
    ):
        raise error
    assert "FAILED Authenticating" in output.getvalue()
    assert "OK" not in output.getvalue()
    assert "do-not-display" not in output.getvalue()
    assert not install_progress_active()
    assert console._live_stack == []


def test_shared_operation_has_no_install_output_outside_install() -> None:
    @install_progress_step("auth", "Authenticating")
    def operation(value: int) -> int:
        return value + 1

    assert operation(3) == 4
    with install_phase("shared", "Shared operation") as phase:
        assert phase is None


def test_broken_install_renderer_does_not_change_operation_result(monkeypatch) -> None:
    console = Console(file=StringIO(), force_terminal=True, _environ={"TERM": "xterm"})
    monkeypatch.setattr(
        console,
        "print",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("display failed")),
    )
    progress = SoperatorUpgradeProgress(console, prefix="Soperator install")
    with install_progress_scope(progress), install_phase("work", "Doing work"):
        result = 42
    assert result == 42
    assert not install_progress_active()


@pytest.mark.parametrize("exists", [False, True])
def test_service_account_progress_reports_creation_only_when_needed(exists):
    output = StringIO()
    progress = SoperatorUpgradeProgress(
        Console(file=output, force_terminal=False), prefix="Soperator install"
    )

    class ServiceAccounts:
        def get_by_name(self, _request):
            assert "Checking the canonical Nebius service account" in output.getvalue()
            return SimpleNamespace(
                wait=lambda: SimpleNamespace(
                    metadata=SimpleNamespace(id="serviceaccount-test" if exists else "")
                )
            )

        def create(self, _request):
            assert not exists
            assert "Creating the Nebius service account" in output.getvalue()
            return SimpleNamespace(wait=lambda: SimpleNamespace(resource_id="serviceaccount-test"))

    with install_progress_scope(progress):
        account_id, created = iam_bootstrap._ensure_service_account(
            service_accounts=ServiceAccounts(),
            project_id="project-test",
            service_account_name="nebius-cxcli-sa",
            service_account_description="test",
        )
    assert account_id == "serviceaccount-test"
    assert created is not exists
    assert ("Creating the Nebius service account" in output.getvalue()) is not exists


@pytest.mark.parametrize("valid", [False, True])
def test_provider_validation_reports_returned_outcome(monkeypatch, valid):
    from nebius.api.nebius.iam.v1 import ProjectServiceClient

    lookup = provider_options.ProviderOptionLookup()
    monkeypatch.setattr(lookup, "_sdk_or_none", lambda: object())
    monkeypatch.setattr(
        ProjectServiceClient,
        "__init__",
        lambda self, sdk: None,
    )
    monkeypatch.setattr(
        ProjectServiceClient,
        "get",
        lambda self, request, **kwargs: SimpleNamespace(
            wait=lambda: SimpleNamespace(
                metadata=SimpleNamespace(id="project-test", parent_id="tenant-test")
            )
        ),
    )
    output = StringIO()
    progress = SoperatorUpgradeProgress(Console(file=output), prefix="Soperator install")
    with install_progress_scope(progress):
        result = lookup.validate_tenant_project_scope(
            tenant_id="tenant-test" if valid else "", project_id="project-test"
        )
    assert result.valid is valid
    assert f"{'OK' if valid else 'FAILED'} Validating the Nebius project" in output.getvalue()
    assert ("OK Validating" in output.getvalue()) is valid


@pytest.mark.parametrize(
    ("method", "kwargs", "cache", "key"),
    [
        (
            "_resolve_capacity_resource_advice_inventory",
            {"tenant_id": "tenant-test"},
            "_capacity_resource_advice_cache",
            "tenant-test",
        ),
        (
            "_resolve_compute_platform_preset_inventory",
            {"project_id": "project-test", "platform_name": "platform-test"},
            "_compute_platform_preset_cache",
            ("project-test", "platform-test"),
        ),
        (
            "_resolve_compute_public_image_family_inventory",
            {"region_id": "region-test", "platform_name": "platform-test"},
            "_compute_public_image_family_cache",
            ("region-test", "platform-test"),
        ),
        (
            "_resolve_mk8s_compatibility_items",
            {"version": "1.35"},
            "_mk8s_compatibility_cache",
            "1.35",
        ),
        *[
            (
                f"_resolve_{kind}",
                {"args": {"project_id": "project-test"}, "payload": {}, "field_path": ""},
                "_cache",
                (kind, "project-test", None)
                if kind == "project_subnets"
                else (kind, "project-test"),
            )
            for kind in ("project_subnets", "project_filesystems", "project_networks")
        ],
    ],
)
def test_cached_provider_lookups_do_not_emit_progress(monkeypatch, method, kwargs, cache, key):
    lookup = provider_options.ProviderOptionLookup()
    getattr(lookup, cache)[key] = ()
    monkeypatch.setattr(lookup, "_sdk_or_none", lambda: pytest.fail("cache hit must not use SDK"))
    output = StringIO()
    progress = SoperatorUpgradeProgress(Console(file=output), prefix="Soperator install")
    with install_progress_scope(progress):
        for _ in range(3):
            assert getattr(lookup, method)(**kwargs) == ()
    assert output.getvalue() == ""


def test_caught_provider_error_reports_failure_without_changing_fallback(monkeypatch):
    lookup = provider_options.ProviderOptionLookup()
    monkeypatch.setattr(lookup, "_sdk_or_none", lambda: object())
    output = StringIO()

    def fail_lookup(*args, **kwargs):
        assert "START Checking available compute capacity" in output.getvalue()
        raise RuntimeError("private-provider-detail")

    monkeypatch.setattr(provider_options, "list_capacity_resource_advice", fail_lookup)
    progress = SoperatorUpgradeProgress(Console(file=output), prefix="Soperator install")
    with install_progress_scope(progress):
        assert lookup._resolve_capacity_resource_advice_inventory(tenant_id="tenant-test") == ()
    assert lookup._last_error == "capacity resource advice lookup failed: private-provider-detail"
    assert "FAILED Checking available compute capacity" in output.getvalue()
    assert "OK" not in output.getvalue()
    assert "private-provider-detail" not in output.getvalue()


def test_unavailable_provider_does_not_claim_lookup_success(monkeypatch):
    lookup = provider_options.ProviderOptionLookup()
    monkeypatch.setattr(lookup, "_sdk_or_none", lambda: None)
    output = StringIO()
    progress = SoperatorUpgradeProgress(Console(file=output), prefix="Soperator install")
    with install_progress_scope(progress):
        assert lookup._resolve_capacity_resource_advice_inventory(tenant_id="tenant-test") == ()
    assert output.getvalue() == ""


def test_empty_provider_inventory_is_successful_and_cached(monkeypatch):
    lookup = provider_options.ProviderOptionLookup()
    monkeypatch.setattr(lookup, "_sdk_or_none", lambda: object())
    calls = []
    monkeypatch.setattr(
        provider_options,
        "list_capacity_resource_advice",
        lambda sdk, **kwargs: calls.append(kwargs) or (),
    )
    output = StringIO()
    progress = SoperatorUpgradeProgress(Console(file=output), prefix="Soperator install")
    with install_progress_scope(progress):
        for _ in range(3):
            assert lookup._resolve_capacity_resource_advice_inventory(tenant_id="tenant-test") == ()
    assert calls == [{"parent_id": "tenant-test"}]
    assert output.getvalue().splitlines() == [
        "Soperator install: START Checking available compute capacity",
        "Soperator install: OK Checking available compute capacity",
    ]


def test_partial_name_lookup_reports_each_remote_outcome(monkeypatch):
    from nebius.api.nebius.iam.v1 import ProjectServiceClient, TenantServiceClient

    lookup = provider_options.ProviderOptionLookup()
    monkeypatch.setattr(lookup, "_sdk_or_none", lambda: object())
    for client in (ProjectServiceClient, TenantServiceClient):
        monkeypatch.setattr(client, "__init__", lambda self, sdk: None)

    def fail_lookup(*args, **kwargs):
        raise RuntimeError("private-provider-detail")

    monkeypatch.setattr(TenantServiceClient, "get", fail_lookup)
    monkeypatch.setattr(
        ProjectServiceClient,
        "get",
        lambda self, request, **kwargs: SimpleNamespace(
            wait=lambda: SimpleNamespace(metadata=SimpleNamespace(name="Test project"))
        ),
    )
    output = StringIO()
    progress = SoperatorUpgradeProgress(Console(file=output), prefix="Soperator install")
    with install_progress_scope(progress):
        assert lookup.resolve_tenant_project_names(
            tenant_id="tenant-test", project_id="project-test"
        ) == ("", "Test project")
    assert output.getvalue().splitlines() == [
        "Soperator install: START Resolving the Nebius tenant name",
        "Soperator install: FAILED Resolving the Nebius tenant name",
        "Soperator install: START Resolving the Nebius project name",
        "Soperator install: OK Resolving the Nebius project name",
    ]


def test_broken_result_classifier_preserves_business_result():
    def fail_classifier(result):
        raise RuntimeError("private-result-detail")

    @install_progress_step("test", "Checking result", succeeded=fail_classifier)
    def operation():
        return 42

    output = StringIO()
    progress = SoperatorUpgradeProgress(Console(file=output), prefix="Soperator install")
    with install_progress_scope(progress):
        assert operation() == 42
    assert "SKIPPED Checking result" in output.getvalue()
    assert "OK" not in output.getvalue()
    assert "private-result-detail" not in output.getvalue()
