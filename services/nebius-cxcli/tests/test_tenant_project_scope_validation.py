from __future__ import annotations

import pytest

import nebius_cxcli.cli as cli
from nebius_cxcli.components import ComponentEntry
from nebius_cxcli.provider_options import TenantProjectValidationResult


class _FakeLookup:
    def __init__(self, results: list[TenantProjectValidationResult]) -> None:
        self._results = results
        self.calls: list[tuple[str, str]] = []

    def validate_tenant_project_scope(
        self,
        *,
        tenant_id: str,
        project_id: str,
    ) -> TenantProjectValidationResult:
        self.calls.append((tenant_id, project_id))
        if self._results:
            return self._results.pop(0)
        return TenantProjectValidationResult(valid=True)


def test_validate_tenant_project_ids_non_interactive_fails() -> None:
    lookup = _FakeLookup(
        [TenantProjectValidationResult(valid=False, message="project mismatch", retryable=True)]
    )

    with pytest.raises(RuntimeError, match="Nebius scope validation failed"):
        cli._validate_tenant_project_ids_or_prompt(
            tenant_id="tenant-1",
            project_id="project-1",
            interactive=False,
            provider_lookup=lookup,  # type: ignore[arg-type]
        )


def test_validate_tenant_project_ids_interactive_reprompts_until_valid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lookup = _FakeLookup(
        [
            TenantProjectValidationResult(
                valid=False,
                message="project does not belong to tenant",
                retryable=True,
            ),
            TenantProjectValidationResult(valid=True),
        ]
    )

    prompts = iter(["tenant-2", "project-2"])
    monkeypatch.setattr(
        cli.typer,
        "prompt",
        lambda _text, default="": next(prompts) if default else next(prompts),
    )

    tenant_id, project_id = cli._validate_tenant_project_ids_or_prompt(
        tenant_id="tenant-1",
        project_id="project-1",
        interactive=True,
        provider_lookup=lookup,  # type: ignore[arg-type]
    )

    assert (tenant_id, project_id) == ("tenant-2", "project-2")
    assert lookup.calls == [
        ("tenant-1", "project-1"),
        ("tenant-2", "project-2"),
    ]


def test_parent_id_dynamic_choices_prefer_validated_project_id() -> None:
    entry = ComponentEntry(
        id="mk8s",
        scope="infra",
        config_path="infra.components.mk8s",
        description="mk8s",
        origin="custom",
        source="platform-infra/modules/mk8s",
    )
    payload = {
        "client_info": {"nebius": {"project_id": "project-123"}},
        "infra": {"components": [{"id": "mk8s", "enabled": True, "inputs": {"parent_id": ""}}]},
    }

    choices = cli._resolve_dynamic_field_choices(
        payload=payload,
        entry=entry,
        full_path_label="infra.components[0].inputs.parent_id",
        provider_lookup=None,
    )

    assert [item.value for item in choices] == ["project-123"]
    assert "from client_info.nebius.project_id" in choices[0].label


def test_seed_infra_project_scope_defaults_uses_client_project_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli,
        "module_variable_names",
        lambda _source: ("cluster_name", "parent_id", "project_id"),
    )
    payload = {
        "client_info": {"nebius": {"project_id": "project-456"}},
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "enabled": True,
                    "source": "../../platform-infra/modules/mk8s",
                    "inputs": {"cluster_name": "cl1"},
                }
            ]
        },
    }
    entries = (
        ComponentEntry(
            id="mk8s",
            scope="infra",
            config_path="infra.components.mk8s",
            description="mk8s",
            origin="custom",
            source="../../platform-infra/modules/mk8s",
        ),
    )

    cli._seed_infra_project_scope_defaults(payload=payload, infra_entries=entries)

    inputs = payload["infra"]["components"][0]["inputs"]
    assert inputs["parent_id"] == "project-456"
    assert inputs["project_id"] == "project-456"


def test_seed_infra_project_scope_defaults_does_not_copy_shared_ssh_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli,
        "module_variable_names",
        lambda _source: ("parent_id", "ssh_public_key"),
    )
    payload = {
        "client_info": {"nebius": {"project_id": "project-456"}},
        "shared": {
            "admin_ssh": {
                "user_name": "ubuntu",
                "public_key": "ssh-ed25519 AAAA-shared",
            }
        },
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "enabled": True,
                    "source": "../../platform-infra/modules/mk8s",
                    "inputs": {},
                }
            ],
        },
    }
    entries = (
        ComponentEntry(
            id="mk8s",
            scope="infra",
            config_path="infra.components.mk8s",
            description="mk8s",
            origin="custom",
            source="../../platform-infra/modules/mk8s",
        ),
    )

    cli._seed_infra_project_scope_defaults(payload=payload, infra_entries=entries)
    inputs = payload["infra"]["components"][0]["inputs"]
    assert inputs["parent_id"] == "project-456"
    assert "ssh_public_key" not in inputs
