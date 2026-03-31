from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import pytest
import yaml

import nebius_cxcli.component_sources as component_sources
from nebius_cxcli.cli import _validate_component_sources_registry
from nebius_cxcli.component_sources import (
    ComponentOutput,
    SourceProfile,
    load_component_sources,
    reset_component_sources_cache,
    resolve_component_sources_file,
    resolve_component_sources_profile,
    set_component_sources_file_override,
    set_component_sources_profile_override,
)
from nebius_cxcli.runtime_introspection import reset_runtime_introspection_cache


def _reset_sources_state() -> None:
    set_component_sources_file_override(None)
    set_component_sources_profile_override(None)
    reset_component_sources_cache()
    reset_runtime_introspection_cache()


def setup_function() -> None:
    _reset_sources_state()


def teardown_function() -> None:
    _reset_sources_state()


@pytest.fixture(autouse=True)
def _clear_component_source_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NEBIUS_CXCLI_COMPONENT_SOURCES_FILE", raising=False)
    monkeypatch.delenv("NEBIUS_CXCLI_COMPONENT_SOURCES_PROFILE", raising=False)


@pytest.fixture(autouse=True)
def _stub_catalog_output_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        component_sources,
        "_discover_terraform_outputs",
        lambda _source: (
            ComponentOutput(
                name="cluster_id",
                kind="terraform_output",
                source_path="cluster_id",
                sensitive=False,
            ),
            ComponentOutput(
                name="cluster_ca_certificate",
                kind="terraform_output",
                source_path="cluster_ca_certificate",
                sensitive=True,
            ),
            ComponentOutput(
                name="instance_id",
                kind="terraform_output",
                source_path="instance_id",
                sensitive=False,
            ),
        ),
    )


def _write_sources_file(
    path: Path,
    *,
    module_name: str,
    local_source: str | None = None,
) -> None:
    path.write_text(
        yaml.safe_dump(
            {
                "cli": {
                    "flux": {
                        "version": "v2.8.0",
                    },
                    "terraform": {
                        "version": "1.14.1",
                    },
                },
                "infra": {
                    "tf_modules": [
                        {
                            "module": module_name,
                            "portable_source": (
                                "git::https://github.com/example/infra.git//modules/"
                                f"{module_name}?ref=v1.2.3"
                            ),
                            "local_source": (
                                f"platform-infra/modules/{module_name}"
                                if local_source is None
                                else local_source
                            ),
                            "description": f"{module_name} module",
                            "enable": True,
                        }
                    ]
                },
                "apps": {
                    "helm_charts": [
                        {
                            "name": "demo-app",
                            "repo": "https://example.invalid/charts",
                            "version": "1.0.0",
                            "namespace": "demo",
                            "releasename": "demo-app",
                            "enable": False,
                        }
                    ],
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _normalized_catalog_signature(path: Path) -> dict[str, object]:
    loaded = load_component_sources(explicit=path, source_profile=SourceProfile.PORTABLE)
    return {
        "cli": asdict(loaded.cli),
        "shared": loaded.shared,
        "tf_modules": [
            {
                key: value
                for key, value in asdict(module).items()
                if key not in {"source", "portable_source", "local_source", "metadata_source"}
            }
            for module in loaded.tf_modules
        ],
        "helm_charts": [asdict(chart) for chart in loaded.helm_charts],
    }


def test_component_sources_resolution_precedence(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    default_file = tmp_path / "default.yaml"
    global_file = tmp_path / "global.yaml"
    user_file = tmp_path / "user.yaml"
    env_file = tmp_path / "env.yaml"
    cli_file = tmp_path / "cli.yaml"
    explicit_file = tmp_path / "explicit.yaml"
    cwd_file = tmp_path / "component_sources.yaml"

    for file_path, module_name in (
        (default_file, "default-mod"),
        (global_file, "global-mod"),
        (user_file, "user-mod"),
        (env_file, "env-mod"),
        (cli_file, "cli-mod"),
        (explicit_file, "explicit-mod"),
        (cwd_file, "cwd-mod"),
    ):
        _write_sources_file(file_path, module_name=module_name)

    monkeypatch.setattr("nebius_cxcli.component_sources.DEFAULT_COMPONENT_SOURCES_FILE", default_file)
    monkeypatch.setattr("nebius_cxcli.component_sources.USER_COMPONENT_SOURCES_FILE", user_file)
    monkeypatch.setattr("nebius_cxcli.component_sources.GLOBAL_COMPONENT_SOURCES_FILE", global_file)

    reset_component_sources_cache()
    set_component_sources_file_override(None)
    monkeypatch.delenv("NEBIUS_CXCLI_COMPONENT_SOURCES_FILE", raising=False)

    assert resolve_component_sources_file(explicit=explicit_file) == explicit_file

    set_component_sources_file_override(cli_file)
    monkeypatch.setenv("NEBIUS_CXCLI_COMPONENT_SOURCES_FILE", str(env_file))
    assert resolve_component_sources_file() == cli_file

    set_component_sources_file_override(None)
    assert resolve_component_sources_file() == cwd_file

    cwd_file.unlink()
    assert resolve_component_sources_file() == env_file

    monkeypatch.delenv("NEBIUS_CXCLI_COMPONENT_SOURCES_FILE", raising=False)
    assert resolve_component_sources_file() == user_file

    user_file.unlink()
    assert resolve_component_sources_file() == global_file

    global_file.unlink()
    assert resolve_component_sources_file() == default_file


def test_component_sources_profile_resolution_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    set_component_sources_profile_override(None)

    assert resolve_component_sources_profile() == SourceProfile.PORTABLE

    monkeypatch.setenv("NEBIUS_CXCLI_COMPONENT_SOURCES_PROFILE", "local")
    assert resolve_component_sources_profile() == SourceProfile.LOCAL

    set_component_sources_profile_override(SourceProfile.PORTABLE)
    assert resolve_component_sources_profile() == SourceProfile.PORTABLE
    assert resolve_component_sources_profile(explicit=SourceProfile.LOCAL) == SourceProfile.LOCAL


def test_load_component_sources_reads_tf_modules_and_helm_entries(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    sources_file = tmp_path / "component-sources.yaml"
    sources_file.write_text(
        yaml.safe_dump(
            {
                "cli": {
                    "flux": {
                        "version": "v2.8.0",
                    },
                    "terraform": {
                        "version": "1.14.1",
                    },
                },
                "shared": {
                    "admin_ssh": {
                        "user_name": "ubuntu",
                        "public_key": "ssh-ed25519 AAAA demo",
                    }
                },
                "infra": {
                    "tf_modules": [
                        {
                            "module": "wireguard-jumphost",
                            "portable_source": (
                                "git::https://github.com/example/infra.git//modules/"
                                "wireguard-jumphost?ref=v1.2.3"
                            ),
                            "local_source": "platform-infra/modules/wireguard-jumphost",
                            "version": "1.2.3",
                            "group": "Network",
                            "enable": True,
                            "defaults": {
                                "inputs.instance_count": 1,
                                "inputs.ssh_user_name": "shared.admin_ssh.user_name",
                            },
                            "outputs": {
                                "terraform": {
                                    "cluster_id": "cluster_id",
                                },
                                "static": {
                                    "access": "external",
                                },
                            },
                            "handoff": {
                                "cluster_id": "cluster_id",
                                "access": "access",
                            },
                        }
                    ]
                },
                "apps": {
                    "helm_charts": [
                        {
                            "name": "gateway-helm",
                            "repo": "oci://docker.io/envoyproxy/gateway-helm",
                            "version": "1.4.2",
                            "namespace": "envoy-gateway-system",
                            "releasename": "envoy-gateway",
                            "group": "Platform",
                            "enable": True,
                            "defaults": {
                                "values.replicaCount": 2,
                            },
                        }
                    ],
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("NEBIUS_CXCLI_COMPONENT_SOURCES_FILE", str(sources_file))
    set_component_sources_file_override(None)
    reset_component_sources_cache()

    loaded = load_component_sources()
    assert loaded.cli.flux.version == "v2.8.0"
    assert loaded.cli.terraform.version == "1.14.1"
    assert loaded.tf_modules[0].module == "wireguard-jumphost"
    assert (
        loaded.tf_modules[0].source
        == "git::https://github.com/example/infra.git//modules/wireguard-jumphost?ref=v1.2.3"
    )
    assert (
        loaded.tf_modules[0].portable_source
        == "git::https://github.com/example/infra.git//modules/wireguard-jumphost?ref=v1.2.3"
    )
    assert loaded.tf_modules[0].local_source == "platform-infra/modules/wireguard-jumphost"
    assert (
        loaded.tf_modules[0].metadata_source
        == "git::https://github.com/example/infra.git//modules/wireguard-jumphost?ref=v1.2.3"
    )
    assert loaded.tf_modules[0].version == "1.2.3"
    assert loaded.tf_modules[0].group == "Network"
    assert loaded.tf_modules[0].enable is True
    assert loaded.tf_modules[0].defaults[0].target_path == "inputs.instance_count"
    assert loaded.tf_modules[0].defaults[0].value == 1
    assert loaded.tf_modules[0].defaults[0].kind == "literal"
    assert loaded.tf_modules[0].defaults[1].target_path == "inputs.ssh_user_name"
    assert loaded.tf_modules[0].defaults[1].kind == "shared"
    assert loaded.tf_modules[0].defaults[1].source_path == "shared.admin_ssh.user_name"
    assert loaded.tf_modules[0].outputs[0].name == "cluster_id"
    assert loaded.tf_modules[0].outputs[0].kind == "terraform_output"
    assert loaded.tf_modules[0].outputs[0].source_path == "cluster_id"
    assert loaded.tf_modules[0].outputs[1].name == "access"
    assert loaded.tf_modules[0].outputs[1].kind == "static"
    assert loaded.tf_modules[0].outputs[1].value == "external"
    assert loaded.tf_modules[0].handoff is not None
    assert loaded.tf_modules[0].handoff.cluster_id_output_name == "cluster_id"
    assert loaded.tf_modules[0].handoff.access_output_name == "access"

    assert loaded.helm_charts[0].name == "gateway-helm"
    assert loaded.helm_charts[0].repo == "oci://docker.io/envoyproxy/gateway-helm"
    assert loaded.helm_charts[0].namespace == "envoy-gateway-system"
    assert loaded.helm_charts[0].release_name == "envoy-gateway"
    assert loaded.helm_charts[0].group == "Platform"
    assert loaded.helm_charts[0].enable is True
    assert loaded.helm_charts[0].defaults[0].target_path == "values.replicaCount"
    assert loaded.helm_charts[0].defaults[0].value == 2


def test_load_component_sources_rejects_release_name_alias_for_helm_chart(tmp_path: Path) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    sources_file.write_text(
        yaml.safe_dump(
            {
                "infra": {"tf_modules": []},
                "apps": {
                    "helm_charts": [
                        {
                            "name": "gateway-helm",
                            "repo": "oci://docker.io/envoyproxy/gateway-helm",
                            "version": "1.4.2",
                            "namespace": "envoy-gateway-system",
                            "release-name": "envoy-gateway",
                        }
                    ]
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unsupported field\\(s\\): release-name"):
        load_component_sources(explicit=sources_file)


def test_load_component_sources_falls_back_to_bundled_default(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    missing_default = tmp_path / "missing-default.yaml"
    missing_user = tmp_path / "missing-user.yaml"
    missing_global = tmp_path / "missing-global.yaml"

    monkeypatch.setattr("nebius_cxcli.component_sources.DEFAULT_COMPONENT_SOURCES_FILE", missing_default)
    monkeypatch.setattr("nebius_cxcli.component_sources.USER_COMPONENT_SOURCES_FILE", missing_user)
    monkeypatch.setattr("nebius_cxcli.component_sources.GLOBAL_COMPONENT_SOURCES_FILE", missing_global)
    monkeypatch.delenv("NEBIUS_CXCLI_COMPONENT_SOURCES_FILE", raising=False)

    bundled_dir = tmp_path / "nebius_cxcli"
    bundled_dir.mkdir(parents=True, exist_ok=True)
    bundled_file = bundled_dir / "component_sources.yaml"
    _write_sources_file(bundled_file, module_name="bundled-mod")
    monkeypatch.setattr(
        "nebius_cxcli.component_sources.importlib_resources.files",
        lambda _package: bundled_dir,
    )

    set_component_sources_file_override(None)
    reset_component_sources_cache()

    loaded = load_component_sources()
    assert loaded.tf_modules[0].module == "bundled-mod"


def test_load_component_sources_falls_back_to_repo_default_catalog_when_bundled_missing(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    default_file = tmp_path / "component_sources.yaml"
    missing_user = tmp_path / "missing-user.yaml"
    missing_global = tmp_path / "missing-global.yaml"
    missing_prefix = tmp_path / "missing-prefix"

    monkeypatch.setattr("nebius_cxcli.component_sources.DEFAULT_COMPONENT_SOURCES_FILE", default_file)
    monkeypatch.setattr("nebius_cxcli.component_sources.USER_COMPONENT_SOURCES_FILE", missing_user)
    monkeypatch.setattr("nebius_cxcli.component_sources.GLOBAL_COMPONENT_SOURCES_FILE", missing_global)
    monkeypatch.setattr(
        "nebius_cxcli.component_sources.importlib_resources.files",
        lambda _package: missing_prefix,
    )
    monkeypatch.setattr("nebius_cxcli.component_sources.sys.prefix", str(missing_prefix))
    monkeypatch.delenv("NEBIUS_CXCLI_COMPONENT_SOURCES_FILE", raising=False)

    _write_sources_file(default_file, module_name="portable-mod")
    set_component_sources_file_override(None)
    reset_component_sources_cache()

    loaded = load_component_sources()
    assert loaded.tf_modules[0].module == "portable-mod"


def test_bundled_mk8s_outputs_preserve_sensitive_metadata() -> None:
    loaded = load_component_sources(
        explicit=Path(__file__).resolve().parents[1] / "component_sources.yaml"
    )
    mk8s = next(item for item in loaded.tf_modules if item.module == "mk8s")
    output_by_name = {output.name: output for output in mk8s.outputs}
    assert output_by_name["cluster_id"].sensitive is False
    assert output_by_name["cluster_ca_certificate"].sensitive is True


def test_default_profile_uses_portable_git_module_sources() -> None:
    loaded = load_component_sources(
        explicit=Path(__file__).resolve().parents[1] / "component_sources.yaml"
    )
    mk8s = next(item for item in loaded.tf_modules if item.module == "mk8s")
    assert (
        mk8s.source
        == "git::https://github.com/nebius/nebius-ps-services.git//platform-infra/modules/mk8s?ref=main"
    )


def test_local_profile_uses_local_source_when_available() -> None:
    loaded = load_component_sources(
        explicit=Path(__file__).resolve().parents[1] / "component_sources.yaml",
        source_profile=SourceProfile.LOCAL,
    )
    mk8s = next(item for item in loaded.tf_modules if item.module == "mk8s")
    assert mk8s.source == "../../platform-infra/modules/mk8s"
    assert mk8s.metadata_source == str(
        (Path(__file__).resolve().parents[3] / "platform-infra/modules/mk8s").resolve()
    )


def test_local_profile_falls_back_to_portable_source_when_local_source_is_missing(
    tmp_path: Path,
) -> None:
    catalog = tmp_path / "component_sources.yaml"
    _write_sources_file(catalog, module_name="mk8s", local_source="")

    loaded = load_component_sources(explicit=catalog, source_profile=SourceProfile.LOCAL)
    assert (
        loaded.tf_modules[0].source
        == "git::https://github.com/example/infra.git//modules/mk8s?ref=v1.2.3"
    )
    assert (
        loaded.tf_modules[0].metadata_source
        == "git::https://github.com/example/infra.git//modules/mk8s?ref=v1.2.3"
    )


def test_portable_profile_prefers_resolved_local_source_for_module_metadata(
    tmp_path: Path,
) -> None:
    catalog = tmp_path / "component_sources.yaml"
    module_dir = tmp_path / "platform-infra" / "modules" / "mk8s"
    module_dir.mkdir(parents=True, exist_ok=True)
    _write_sources_file(
        catalog,
        module_name="mk8s",
        local_source="platform-infra/modules/mk8s",
    )

    loaded = load_component_sources(explicit=catalog, source_profile=SourceProfile.PORTABLE)

    assert (
        loaded.tf_modules[0].source
        == "git::https://github.com/example/infra.git//modules/mk8s?ref=v1.2.3"
    )
    assert loaded.tf_modules[0].metadata_source == str(module_dir.resolve())


def test_shipped_catalogs_do_not_embed_jump_host_public_key_defaults() -> None:
    loaded = load_component_sources(explicit=Path(__file__).resolve().parents[1] / "component_sources.yaml")
    for module_id in ("wireguard-jumphost", "ssh-jumphost"):
        module = next(item for item in loaded.tf_modules if item.module == module_id)
        default_targets = {default.target_path for default in module.defaults}
        assert "inputs.ssh_user_name" in default_targets
        assert "inputs.ssh_public_key" not in default_targets


def test_load_component_sources_explicit_missing_file_raises(tmp_path: Path) -> None:
    missing = tmp_path / "missing-explicit.yaml"
    with pytest.raises(ValueError, match="Component sources file not found"):
        load_component_sources(explicit=missing)


def test_load_component_sources_rejects_unsupported_config_bindings_field(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    sources_file = tmp_path / "component_sources.yaml"
    sources_file.write_text(
        yaml.safe_dump(
            {
                "cli": {
                    "flux": {
                        "version": "v2.8.0",
                    }
                },
                "infra": {
                    "tf_modules": [
                        {
                            "module": "wireguard-jumphost",
                            "portable_source": (
                                "git::https://github.com/example/infra.git//modules/"
                                "wireguard-jumphost?ref=v1.2.3"
                            ),
                            "local_source": "platform-infra/modules/wireguard-jumphost",
                            "config_bindings": {
                                "inputs.ssh_user_name": "shared.admin_ssh.user_name",
                            },
                        }
                    ]
                },
                "apps": {"helm_charts": []},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("NEBIUS_CXCLI_COMPONENT_SOURCES_FILE", str(sources_file))
    set_component_sources_file_override(None)
    reset_component_sources_cache()

    with pytest.raises(ValueError, match="has unsupported field\\(s\\): config_bindings"):
        load_component_sources()


def test_load_component_sources_rejects_invalid_flux_version(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    sources_file = tmp_path / "component_sources.yaml"
    sources_file.write_text(
        yaml.safe_dump(
            {
                "cli": {
                    "flux": {
                        "version": "latest",
                    }
                },
                "infra": {"tf_modules": []},
                "apps": {"helm_charts": []},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("NEBIUS_CXCLI_COMPONENT_SOURCES_FILE", str(sources_file))
    set_component_sources_file_override(None)
    reset_component_sources_cache()

    with pytest.raises(ValueError, match="cli\\.flux\\.version must be a semantic version"):
        load_component_sources()


def test_load_component_sources_rejects_invalid_terraform_version(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    sources_file = tmp_path / "component_sources.yaml"
    sources_file.write_text(
        yaml.safe_dump(
            {
                "cli": {
                    "terraform": {
                        "version": "latest",
                    }
                },
                "infra": {"tf_modules": []},
                "apps": {"helm_charts": []},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("NEBIUS_CXCLI_COMPONENT_SOURCES_FILE", str(sources_file))
    set_component_sources_file_override(None)
    reset_component_sources_cache()

    with pytest.raises(ValueError, match="cli\\.terraform\\.version must be a semantic version"):
        load_component_sources()


def test_validate_sources_resolves_relative_local_module_path_from_component_sources_file(
    monkeypatch, tmp_path: Path
) -> None:
    catalog_dir = tmp_path / "catalog"
    module_dir = catalog_dir / "modules" / "demo-module"
    module_dir.mkdir(parents=True, exist_ok=True)
    (module_dir / "main.tf").write_text('output "demo" { value = var.name }\n', encoding="utf-8")
    (module_dir / "variables.tf").write_text('variable "name" { type = string }\n', encoding="utf-8")

    sources_file = catalog_dir / "component_sources.yaml"
    sources_file.write_text(
        yaml.safe_dump(
            {
                "infra": {
                    "tf_modules": [
                        {
                            "module": "demo-module",
                            "portable_source": "git::https://github.com/example/infra.git//modules/demo-module?ref=v1.2.3",
                            "local_source": "./modules/demo-module",
                            "enable": True,
                        }
                    ]
                },
                "apps": {"helm_charts": []},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(elsewhere)
    set_component_sources_file_override(sources_file)
    set_component_sources_profile_override(SourceProfile.LOCAL)
    reset_component_sources_cache()

    resolved_path, issues, warnings = _validate_component_sources_registry()

    assert resolved_path == sources_file
    assert issues == []
    assert warnings == []


def test_validate_sources_accepts_absolute_local_module_path(monkeypatch, tmp_path: Path) -> None:
    module_dir = tmp_path / "demo-module"
    module_dir.mkdir(parents=True, exist_ok=True)
    (module_dir / "main.tf").write_text('output "demo" { value = var.name }\n', encoding="utf-8")
    (module_dir / "variables.tf").write_text('variable "name" { type = string }\n', encoding="utf-8")

    sources_file = tmp_path / "component_sources.yaml"
    sources_file.write_text(
        yaml.safe_dump(
            {
                "infra": {
                    "tf_modules": [
                        {
                            "module": "demo-module",
                            "portable_source": "git::https://github.com/example/infra.git//modules/demo-module?ref=v1.2.3",
                            "local_source": str(module_dir),
                            "enable": True,
                        }
                    ]
                },
                "apps": {"helm_charts": []},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    set_component_sources_file_override(sources_file)
    set_component_sources_profile_override(SourceProfile.LOCAL)
    reset_component_sources_cache()

    resolved_path, issues, warnings = _validate_component_sources_registry()

    assert resolved_path == sources_file
    assert issues == []
    assert warnings == []


def test_validate_sources_rejects_https_git_repo_module_source_without_git_prefix(
    monkeypatch, tmp_path: Path
) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    sources_file.write_text(
        yaml.safe_dump(
            {
                "infra": {
                    "tf_modules": [
                        {
                            "module": "demo-module",
                            "portable_source": "https://github.com/example/platform-modules.git//modules/demo?ref=v1.2.3",
                            "enable": True,
                        }
                    ]
                },
                "apps": {"helm_charts": []},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    set_component_sources_file_override(sources_file)
    reset_component_sources_cache()

    _resolved_path, issues, warnings = _validate_component_sources_registry()

    assert warnings == []
    assert any("is not supported as a plain HTTP(S) Terraform module source" in issue for issue in issues)
    assert any("git::https://github.com/org/repo.git//modules/mk8s?ref=v1.2.3" in issue for issue in issues)


def test_validate_sources_rejects_registry_style_module_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    sources_file.write_text(
        yaml.safe_dump(
            {
                "infra": {
                    "tf_modules": [
                        {
                            "module": "demo-module",
                            "portable_source": "app.terraform.io/example/network/nebius",
                            "enable": True,
                        }
                    ]
                },
                "apps": {"helm_charts": []},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    set_component_sources_file_override(sources_file)
    reset_component_sources_cache()

    _resolved_path, issues, warnings = _validate_component_sources_registry()

    assert warnings == []
    assert any("module source 'app.terraform.io/example/network/nebius' is not supported" in issue for issue in issues)


def test_validate_sources_accepts_github_tree_chart_repo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    sources_file.write_text(
        yaml.safe_dump(
            {
                "infra": {"tf_modules": []},
                "apps": {
                    "helm_charts": [
                        {
                            "name": "n8n",
                            "repo": "https://github.com/example/charts/tree/main/charts/n8n",
                            "version": "1.2.3",
                            "enable": True,
                        }
                    ]
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    class _FakeHelmClient:
        def show_chart(self, *, reference):  # type: ignore[no-untyped-def]
            assert reference.chart_repo == "https://github.com/example/charts/tree/main/charts/n8n"
            return {"name": "n8n", "version": "1.2.3"}

    monkeypatch.setattr("nebius_cxcli.cli.HelmClient", _FakeHelmClient)
    monkeypatch.chdir(tmp_path)
    set_component_sources_file_override(sources_file)
    reset_component_sources_cache()
    reset_runtime_introspection_cache()

    resolved_path, issues, warnings = _validate_component_sources_registry()

    assert resolved_path == sources_file
    assert issues == []
    assert warnings == []


def test_validate_sources_fails_when_helm_is_required_but_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    sources_file.write_text(
        yaml.safe_dump(
            {
                "infra": {"tf_modules": []},
                "apps": {
                    "helm_charts": [
                        {
                            "name": "gateway-helm",
                            "repo": "oci://docker.io/envoyproxy/gateway-helm",
                            "version": "1.4.2",
                            "enable": True,
                        }
                    ]
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    class _FailingHelmClient:
        def __init__(self) -> None:
            raise RuntimeError("helm not found in PATH")

    monkeypatch.setattr("nebius_cxcli.cli.HelmClient", _FailingHelmClient)
    monkeypatch.chdir(tmp_path)
    set_component_sources_file_override(sources_file)
    reset_component_sources_cache()
    reset_runtime_introspection_cache()

    _resolved_path, issues, warnings = _validate_component_sources_registry()

    assert warnings == []
    assert any("requires helm for source validation" in issue for issue in issues)
