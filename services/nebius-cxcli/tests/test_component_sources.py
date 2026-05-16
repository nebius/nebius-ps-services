from __future__ import annotations

import json
from copy import deepcopy
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

_VALID_ED25519_PUBLIC_KEY = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAABAgMEBQYHCAkKCwwNDg8QERITFBUWFxgZGhscHR4f demo@example"
)

_NEBIUS_CPU_ONLY_AFFINITY = {
    "nodeAffinity": {
        "requiredDuringSchedulingIgnoredDuringExecution": {
            "nodeSelectorTerms": [
                {
                    "matchExpressions": [
                        {
                            "key": "nebius.com/gpu",
                            "operator": "NotIn",
                            "values": ["true"],
                        }
                    ]
                }
            ]
        }
    }
}


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
    _write_catalog_file(
        path,
        {
            "cli": {
                "flux": {
                    "version": "v2.8.0",
                },
                "terraform": {
                    "version": "1.14.1",
                },
            },
            "components": {
                "infra": {
                    module_name: {
                        "source": {
                            "portable": (
                                "git::https://github.com/example/infra.git//modules/"
                                f"{module_name}?ref=v1.2.3"
                            ),
                            "local": (
                                f"platform-infra/modules/{module_name}"
                                if local_source is None
                                else local_source
                            ),
                        },
                        "ui": {
                            "title": f"{module_name} module",
                            "enabled": True,
                        },
                    }
                },
                "apps": {
                    "demo-app": {
                        "source": _portable_chart_source(
                            repo="https://example.invalid/charts",
                            chart="demo-app",
                            version="1.0.0",
                        ),
                        "release": {
                            "namespace": "demo",
                            "name": "demo-app",
                        },
                        "ui": {
                            "enabled": False,
                        },
                    },
                },
            },
        },
    )


def _catalog(
    *,
    infra: dict[str, object] | None = None,
    apps: dict[str, object] | None = None,
    cli: dict[str, object] | None = None,
    shared: dict[str, object] | None = None,
    observability: dict[str, object] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "components": {
            "infra": infra or {},
            "apps": apps or {},
        }
    }
    if cli is not None:
        payload["cli"] = cli
    if shared is not None:
        payload["shared"] = shared
    if observability is not None:
        payload["observability"] = observability
    return payload


def _split_component_cli_settings(
    payload: dict[str, object],
) -> tuple[dict[str, object], dict[str, object]]:
    sources = deepcopy(payload)
    settings: dict[str, object] = {}
    for key in ("cli", "observability"):
        if key in sources:
            settings[key] = sources.pop(key)
    components = sources.get("components")
    if isinstance(components, dict):
        settings_components: dict[str, object] = {}
        for scope in ("infra", "apps"):
            scope_components = components.get(scope)
            if not isinstance(scope_components, dict):
                continue
            settings_scope: dict[str, object] = {}
            for component_id, component in scope_components.items():
                if isinstance(component, dict) and "cli" in component:
                    settings_scope[str(component_id)] = {"cli": component.pop("cli")}
            if settings_scope:
                settings_components[scope] = settings_scope
        if settings_components:
            settings["components"] = settings_components
    return sources, settings


def _write_catalog_file(path: Path, payload: dict[str, object]) -> None:
    sources, settings = _split_component_cli_settings(payload)
    path.write_text(yaml.safe_dump(sources, sort_keys=False), encoding="utf-8")
    settings_path = path.with_name("component_cli_settings.yaml")
    if settings:
        settings_path.write_text(yaml.safe_dump(settings, sort_keys=False), encoding="utf-8")
    elif settings_path.exists():
        settings_path.unlink()


def _portable_chart_source(*, repo: str, chart: str, version: str = "") -> dict[str, object]:
    portable: dict[str, object] = {
        "repo": repo,
        "chart": chart,
    }
    if version:
        portable["version"] = version
    return {"portable": portable}


def test_bundled_catalog_exposes_soperator_and_nfs_local_sources() -> None:
    sources = load_component_sources(source_profile=SourceProfile.LOCAL)

    nfs = next(module for module in sources.tf_modules if module.module == "nfs")
    assert nfs.local_source is not None
    assert nfs.local_source.endswith("platform-infra/modules/nfs")

    soperator = next(chart for chart in sources.helm_charts if chart.name == "soperator")
    assert soperator.source.path is not None
    assert soperator.source.path.endswith("helm-charts/soperator")
    assert soperator.namespace == "soperator"
    assert soperator.release_name == "soperator"
    assert soperator.release_timeout == "90m"
    assert soperator.release_install_after == ()
    assert soperator.wizard_fields is not None
    assert soperator.wizard_fields["profile"]["default"] == "nebius-gpu-v1"
    assert soperator.wizard_fields["profile"]["materialize_default"] is True
    assert soperator.wizard_fields["profile"]["options"] == {"from": "soperator_nodesets_profiles"}
    assert soperator.wizard_fields["values.partitionProfile"]["default"] == "shape-default"
    assert soperator.wizard_fields["values.partitionProfile"]["materialize_default"] is True
    assert soperator.wizard_fields["values.partitionProfile"]["options"] == {
        "from": "soperator_partition_profiles",
        "args": {"default": "shape-default"},
    }
    assert soperator.mk8s_gpu.install_after == (
        "cert-manager",
        "nvidia-network-operator",
        "nvidia-gpu-operator",
    )
    assert soperator.soperator_nodesets.default == "nebius-gpu-v1"
    assert set(soperator.soperator_nodesets.profiles) >= {
        "nebius-cpu-v1",
        "nebius-gpu-v1",
        "nebius-mixed-v1",
    }
    profile = soperator.soperator_nodesets.profiles["nebius-gpu-v1"]
    assert profile["mk8s"]["use_generic_gpu_node_groups"] is True
    assert profile["mk8s"]["worker_nodesets"][0]["max_nodes_per_group"] == 100
    assert profile["mk8s"]["worker_nodesets"][0]["node_groups_input"] == "gpu_node_groups"
    assert profile["chart"]["values"]["partitionConfiguration"]["partitions"][0]["name"] == "gpu"
    assert "with-debug-long" in profile["chart"]["partition_profiles"]
    mixed_profile = soperator.soperator_nodesets.profiles["nebius-mixed-v1"]
    assert "Mixed CPU+GPU workers" in mixed_profile["wizard"]["label"]
    assert [worker["nodeset_name"] for worker in mixed_profile["mk8s"]["worker_nodesets"]] == [
        "worker-cpu",
        "worker-gpu",
    ]
    assert [node["name"] for node in mixed_profile["chart"]["values"]["nodesets"]] == [
        "worker-cpu",
        "worker-gpu",
    ]
    assert "with-debug-long" in mixed_profile["chart"]["partition_profiles"]
    assert "with-h100-infiniband-debug-long" in mixed_profile["chart"]["partition_profiles"]
    assert "nfs" not in profile["mk8s"]["node_groups"]

    notifier = next(chart for chart in sources.helm_charts if chart.name == "soperator-notifier")
    assert notifier.source.path is not None
    assert notifier.source.path.endswith("helm-charts/soperator-notifier")
    assert notifier.namespace == "soperator"
    assert notifier.release_name == "soperator-notifier"
    assert notifier.release_timeout == "10m"
    assert notifier.release_install_after == ("soperator", "cert-manager")
    assert notifier.wizard_fields["values.slack.mode"]["default"] == "existing-webhook"
    assert notifier.defaults[0].target_path == "values.slack.mode"
    assert notifier.defaults[0].value == "existing-webhook"


def _kubernetes_agent_validation_enabled() -> bool:
    return True


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

    monkeypatch.setattr(
        "nebius_cxcli.component_sources.DEFAULT_COMPONENT_SOURCES_FILE", default_file
    )
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


def test_component_sources_shared_admin_ssh_public_key_accepts_relative_file_path(
    tmp_path: Path,
) -> None:
    key_path = tmp_path / "id_rsa.pub"
    key_path.write_text(
        "ssh-rsa "
        "AAAAB3NzaC1yc2EAAAADAQABAAAAgAEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEB"
        "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBA"
        "QEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEB "
        "demo@example\n",
        encoding="utf-8",
    )
    sources_file = tmp_path / "component_sources.yaml"
    _write_catalog_file(
        sources_file,
        _catalog(
            shared={
                "admin_ssh": {
                    "user_name": "ubuntu",
                    "public_key": "./id_rsa.pub",
                }
            }
        ),
    )

    loaded = load_component_sources(explicit=sources_file, source_profile=SourceProfile.PORTABLE)
    assert loaded.shared["admin_ssh"]["public_key"].startswith("ssh-rsa ")


def test_component_sources_reject_invalid_shared_admin_ssh_user_name(tmp_path: Path) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    _write_catalog_file(
        sources_file,
        _catalog(
            shared={
                "admin_ssh": {
                    "user_name": "BAD USER",
                }
            }
        ),
    )

    with pytest.raises(ValueError, match="shared.admin_ssh.user_name"):
        load_component_sources(explicit=sources_file, source_profile=SourceProfile.PORTABLE)


def test_component_sources_profile_resolution_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    set_component_sources_profile_override(None)

    assert resolve_component_sources_profile() == SourceProfile.PORTABLE

    monkeypatch.setenv("NEBIUS_CXCLI_COMPONENT_SOURCES_PROFILE", "local")
    assert resolve_component_sources_profile() == SourceProfile.LOCAL

    set_component_sources_profile_override(SourceProfile.PORTABLE)
    assert resolve_component_sources_profile() == SourceProfile.PORTABLE
    assert resolve_component_sources_profile(explicit=SourceProfile.LOCAL) == SourceProfile.LOCAL


def test_load_component_sources_reads_tf_modules_and_helm_entries(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    sources_file = tmp_path / "component-sources.yaml"
    _write_catalog_file(
        sources_file,
        _catalog(
            cli={
                "flux": {
                    "version": "v2.8.0",
                },
                "terraform": {
                    "version": "1.14.1",
                },
            },
            shared={
                "admin_ssh": {
                    "user_name": "ubuntu",
                    "public_key": _VALID_ED25519_PUBLIC_KEY,
                },
            },
            infra={
                "wireguard-gw": {
                    "source": {
                        "portable": (
                            "git::https://github.com/example/infra.git//modules/"
                            "wireguard-gw?ref=v1.2.3"
                        ),
                        "local": "platform-infra/modules/wireguard-gw",
                    },
                    "ui": {
                        "title": "WireGuard VPN gateway",
                        "group": "Network",
                        "enabled": True,
                    },
                    "wizard": {
                        "inputs.subnet_id": {
                            "options": {
                                "from": "project_subnets",
                            }
                        },
                    },
                    "defaults": {
                        "inputs.instance_count": 1,
                        "inputs.ssh_user_name": "shared.admin_ssh.user_name",
                    },
                    "status": {
                        "kind": "nebius.compute.instance",
                        "parent_input": "parent_id",
                        "name_input": "name",
                    },
                },
            },
            apps={
                "gateway-helm": {
                    "source": _portable_chart_source(
                        repo="oci://docker.io/envoyproxy",
                        chart="gateway-helm",
                        version="1.4.2",
                    ),
                    "release": {
                        "namespace": "envoy-gateway-system",
                        "name": "envoy-gateway",
                        "timeout": "10m",
                    },
                    "ui": {
                        "title": "Envoy Gateway",
                        "group": "Platform",
                        "enabled": True,
                    },
                    "defaults": {
                        "values.replicaCount": 2,
                    },
                }
            },
        ),
    )

    monkeypatch.setenv("NEBIUS_CXCLI_COMPONENT_SOURCES_FILE", str(sources_file))
    set_component_sources_file_override(None)
    reset_component_sources_cache()

    loaded = load_component_sources()
    assert loaded.cli.flux.version == "v2.8.0"
    assert loaded.cli.flux.release_timeout == "5m"
    assert loaded.cli.terraform.version == "1.14.1"
    assert loaded.tf_modules[0].module == "wireguard-gw"
    assert (
        loaded.tf_modules[0].source
        == "git::https://github.com/example/infra.git//modules/wireguard-gw?ref=v1.2.3"
    )
    assert (
        loaded.tf_modules[0].portable_source
        == "git::https://github.com/example/infra.git//modules/wireguard-gw?ref=v1.2.3"
    )
    assert loaded.tf_modules[0].local_source == "platform-infra/modules/wireguard-gw"
    assert (
        loaded.tf_modules[0].metadata_source
        == "git::https://github.com/example/infra.git//modules/wireguard-gw?ref=v1.2.3"
    )
    assert loaded.tf_modules[0].description == "WireGuard VPN gateway"
    assert loaded.tf_modules[0].group == "Network"
    assert loaded.tf_modules[0].enable is True
    assert loaded.tf_modules[0].validation_profile == "wireguard_gw"
    assert loaded.tf_modules[0].wizard_fields == {
        "inputs.subnet_id": {
            "options": {
                "from": "project_subnets",
            }
        }
    }
    assert loaded.tf_modules[0].defaults[0].target_path == "inputs.instance_count"
    assert loaded.tf_modules[0].defaults[0].value == 1
    assert loaded.tf_modules[0].defaults[0].kind == "literal"
    assert loaded.tf_modules[0].defaults[1].target_path == "inputs.ssh_user_name"
    assert loaded.tf_modules[0].defaults[1].kind == "shared"
    assert loaded.tf_modules[0].defaults[1].source_path == "shared.admin_ssh.user_name"
    output_by_name = {output.name: output for output in loaded.tf_modules[0].outputs}
    assert output_by_name["cluster_id"].kind == "terraform_output"
    assert output_by_name["cluster_id"].source_path == "cluster_id"
    assert loaded.tf_modules[0].handoff is None
    assert loaded.tf_modules[0].status is not None
    assert loaded.tf_modules[0].status.kind == "nebius.compute.instance"
    assert loaded.tf_modules[0].status.parent_input == "parent_id"
    assert loaded.tf_modules[0].status.name_input == "name"

    assert loaded.helm_charts[0].name == "gateway-helm"
    assert loaded.helm_charts[0].chart_name == "gateway-helm"
    assert loaded.helm_charts[0].repo == "oci://docker.io/envoyproxy"
    assert loaded.helm_charts[0].namespace == "envoy-gateway-system"
    assert loaded.helm_charts[0].release_name == "envoy-gateway"
    assert loaded.helm_charts[0].release_timeout == "10m"
    assert loaded.helm_charts[0].group == "Platform"
    assert loaded.helm_charts[0].enable is True
    assert loaded.helm_charts[0].defaults[0].target_path == "values.replicaCount"
    assert loaded.helm_charts[0].defaults[0].value == 2


def test_app_release_timeout_inherits_global_flux_default(tmp_path: Path) -> None:
    sources_file = tmp_path / "component-sources.yaml"
    _write_catalog_file(
        sources_file,
        _catalog(
            cli={
                "flux": {
                    "version": "v2.8.0",
                    "release_timeout": "15m",
                },
                "terraform": {"version": "1.14.1"},
            },
            apps={
                "demo-app": {
                    "source": _portable_chart_source(
                        repo="https://example.invalid/charts",
                        chart="demo-app",
                        version="1.0.0",
                    ),
                    "release": {
                        "namespace": "demo",
                        "name": "demo-app",
                    },
                }
            },
        ),
    )

    loaded = load_component_sources(explicit=sources_file)

    assert loaded.cli.flux.release_timeout == "15m"
    assert loaded.helm_charts[0].release_timeout == "15m"


def test_load_component_sources_adds_builtin_mk8s_handoff(tmp_path: Path) -> None:
    sources_file = tmp_path / "component-sources.yaml"
    _write_catalog_file(
        sources_file,
        _catalog(
            infra={
                "mk8s": {
                    "source": {
                        "portable": "git::https://github.com/example/infra.git//modules/mk8s?ref=v1.2.3",
                    },
                    "status": {
                        "kind": "nebius.mk8s.cluster",
                        "name_input": "cluster_name",
                    },
                }
            }
        ),
    )

    loaded = load_component_sources(explicit=sources_file)

    output_by_name = {output.name: output for output in loaded.tf_modules[0].outputs}
    assert output_by_name["cluster_id"].kind == "terraform_output"
    assert output_by_name["cluster_id"].source_path == "cluster_id"
    assert loaded.tf_modules[0].validation_profile == "mk8s_cluster"
    assert loaded.tf_modules[0].handoff is not None
    assert loaded.tf_modules[0].handoff.cluster_id_output_name == "cluster_id"
    assert loaded.tf_modules[0].handoff.access_kind == "input"
    assert loaded.tf_modules[0].handoff.access_source_path == "inputs.mk8s_cluster_public_endpoint"


def test_load_component_sources_rejects_public_validation_field(tmp_path: Path) -> None:
    sources_file = tmp_path / "component-sources.yaml"
    _write_catalog_file(
        sources_file,
        _catalog(
            infra={
                "mk8s": {
                    "source": {
                        "portable": "git::https://github.com/example/infra.git//modules/mk8s?ref=v1.2.3",
                    },
                    "validation": "mk8s_cluster",
                }
            }
        ),
    )

    with pytest.raises(
        ValueError,
        match=r"components\.infra\.mk8s has unsupported field\(s\): validation",
    ):
        load_component_sources(explicit=sources_file)


def test_load_component_sources_rejects_cli_settings_inside_source_catalog(
    tmp_path: Path,
) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    sources_file.write_text(
        yaml.safe_dump(
            {
                "cli": {
                    "flux": {
                        "version": "v2.8.0",
                    }
                },
                "observability": {
                    "endpoints": {
                        "read": {},
                        "write": {},
                    }
                },
                "components": {
                    "infra": {
                        "mk8s": {
                            "source": {
                                "portable": "git::https://github.com/example/infra.git//modules/mk8s?ref=v1.2.3",
                            },
                            "cli": {
                                "boot_disk_defaults": {},
                            },
                        }
                    },
                    "apps": {},
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match=r"component_sources root has unsupported field\(s\): cli, observability",
    ):
        load_component_sources(explicit=sources_file)


def test_load_component_sources_rejects_cli_settings_for_unknown_component(
    tmp_path: Path,
) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    sources_file.write_text(
        yaml.safe_dump(
            {
                "components": {
                    "infra": {},
                    "apps": {},
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    sources_file.with_name("component_cli_settings.yaml").write_text(
        yaml.safe_dump(
            {
                "components": {
                    "infra": {
                        "mk8s": {
                            "cli": {
                                "boot_disk_defaults": {},
                            }
                        }
                    }
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match=r"component_cli_settings\.components\.infra references unknown component\(s\): mk8s",
    ):
        load_component_sources(explicit=sources_file)


def test_load_bundled_cli_settings_missing_file_names_cli_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    prefix_dir = tmp_path / "prefix"
    prefix_dir.mkdir()
    missing_default = tmp_path / "missing-component_cli_settings.yaml"
    monkeypatch.setattr(
        component_sources.importlib_resources,
        "files",
        lambda _name: package_dir,
    )
    monkeypatch.setattr(component_sources.sys, "prefix", str(prefix_dir))
    monkeypatch.setattr(component_sources, "DEFAULT_COMPONENT_CLI_SETTINGS_FILE", missing_default)

    with pytest.raises(
        FileNotFoundError,
        match="Bundled component CLI settings file is missing",
    ):
        component_sources._load_bundled_cli_settings()


def test_load_component_sources_rejects_release_name_alias_for_helm_chart(tmp_path: Path) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    _write_catalog_file(
        sources_file,
        _catalog(
            apps={
                "gateway-helm": {
                    "source": _portable_chart_source(
                        repo="oci://docker.io/envoyproxy",
                        chart="gateway-helm",
                        version="1.4.2",
                    ),
                    "release": {
                        "namespace": "envoy-gateway-system",
                        "release-name": "envoy-gateway",
                    },
                }
            }
        ),
    )

    with pytest.raises(
        ValueError,
        match=r"components\.apps\.gateway-helm release has unsupported field\(s\): release-name",
    ):
        load_component_sources(explicit=sources_file)


def test_load_component_sources_parses_instance_qualified_input_binding(tmp_path: Path) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    _write_catalog_file(
        sources_file,
        _catalog(
            infra={
                "mk8s": {
                    "source": {
                        "portable": "git::https://example.invalid/repo.git//mk8s?ref=v1.0.0",
                    }
                }
            },
            apps={
                "demo-app": {
                    "source": _portable_chart_source(
                        repo="https://example.invalid/charts",
                        chart="demo-app",
                        version="1.0.0",
                    ),
                    "release": {
                        "namespace": "demo",
                        "name": "demo-app",
                    },
                    "input": {
                        "values.global.clusterId": "mk8s@mk8s-blue.cluster_id",
                    },
                }
            },
        ),
    )

    loaded = load_component_sources(explicit=sources_file)

    binding = loaded.helm_charts[0].input_bindings[0]
    assert binding.target_path == "values.global.clusterId"
    assert binding.source_component_id == "mk8s"
    assert binding.source_instance_id == "mk8s-blue"
    assert binding.source_output_name == "cluster_id"


def test_load_component_sources_rejects_invalid_status_watcher_block(tmp_path: Path) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    _write_catalog_file(
        sources_file,
        _catalog(
            infra={
                "managed-postgresql": {
                    "source": {
                        "portable": "git::https://example.invalid/repo.git//managed-postgresql?ref=v1.0.0",
                    },
                    "status": {
                        "name_input": "name",
                    },
                }
            }
        ),
    )

    with pytest.raises(ValueError, match="status.kind is required"):
        load_component_sources(explicit=sources_file)


def test_load_component_sources_rejects_legacy_resource_kind_field(tmp_path: Path) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    _write_catalog_file(
        sources_file,
        _catalog(
            infra={
                "managed-postgresql": {
                    "source": {
                        "portable": "git::https://example.invalid/repo.git//managed-postgresql?ref=v1.0.0",
                    },
                    "resource_kind": "nebius.msp.postgresql.cluster",
                }
            }
        ),
    )

    with pytest.raises(
        ValueError,
        match=r"components\.infra\.managed-postgresql has unsupported field\(s\): resource_kind",
    ):
        load_component_sources(explicit=sources_file)


def test_load_component_sources_expands_builtin_wizard_profile(tmp_path: Path) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    _write_catalog_file(
        sources_file,
        _catalog(
            infra={
                "managed-postgresql": {
                    "source": {
                        "portable": "git::https://example.invalid/repo.git//managed-postgresql?ref=v1.0.0",
                    },
                    "wizard_profile": "managed-postgresql",
                }
            }
        ),
    )

    loaded = load_component_sources(explicit=sources_file)
    module = loaded.tf_modules[0]

    assert module.wizard_fields == {
        "inputs.network_id": {
            "options": {
                "from": "project_networks",
            }
        },
        "inputs.tier": {
            "sources": [
                {
                    "source": "static",
                    "values": ["small", "medium", "large"],
                }
            ]
        },
    }


def test_load_component_sources_merges_profile_and_explicit_wizard_override(tmp_path: Path) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    _write_catalog_file(
        sources_file,
        _catalog(
            infra={
                "managed-postgresql": {
                    "source": {
                        "portable": "git::https://example.invalid/repo.git//managed-postgresql?ref=v1.0.0",
                    },
                    "wizard_profile": "managed-postgresql",
                    "wizard": {
                        "inputs.network_id": {
                            "options": {
                                "from": "project_networks",
                                "filter_regex": "^vpcnetwork-",
                            }
                        }
                    },
                }
            }
        ),
    )

    loaded = load_component_sources(explicit=sources_file)
    module = loaded.tf_modules[0]

    assert module.wizard_fields == {
        "inputs.network_id": {
            "options": {
                "from": "project_networks",
                "filter": "^vpcnetwork-",
            }
        },
        "inputs.tier": {
            "sources": [
                {
                    "source": "static",
                    "values": ["small", "medium", "large"],
                }
            ]
        },
    }


def test_load_component_sources_rejects_profile_name_that_does_not_match_component_id(
    tmp_path: Path,
) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    _write_catalog_file(
        sources_file,
        _catalog(
            infra={
                "managed-postgresql": {
                    "source": {
                        "portable": "git::https://example.invalid/repo.git//managed-postgresql?ref=v1.0.0",
                    },
                    "wizard_profile": "mk8s",
                }
            }
        ),
    )

    with pytest.raises(
        ValueError,
        match="wizard_profile must match component id 'managed-postgresql' when set",
    ):
        load_component_sources(explicit=sources_file)


def test_load_component_sources_rejects_unknown_wizard_profile(tmp_path: Path) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    _write_catalog_file(
        sources_file,
        _catalog(
            infra={
                "managed-postgresql": {
                    "source": {
                        "portable": "git::https://example.invalid/repo.git//managed-postgresql?ref=v1.0.0",
                    },
                    "wizard_profile": "unknown-profile",
                }
            }
        ),
    )

    with pytest.raises(ValueError, match="wizard_profile 'unknown-profile' is unknown"):
        load_component_sources(explicit=sources_file)


def test_load_component_sources_falls_back_to_bundled_default(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    missing_default = tmp_path / "missing-default.yaml"
    missing_user = tmp_path / "missing-user.yaml"
    missing_global = tmp_path / "missing-global.yaml"

    monkeypatch.setattr(
        "nebius_cxcli.component_sources.DEFAULT_COMPONENT_SOURCES_FILE", missing_default
    )
    monkeypatch.setattr("nebius_cxcli.component_sources.USER_COMPONENT_SOURCES_FILE", missing_user)
    monkeypatch.setattr(
        "nebius_cxcli.component_sources.GLOBAL_COMPONENT_SOURCES_FILE", missing_global
    )
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


def test_load_component_sources_rejects_runtime_block(tmp_path: Path) -> None:
    sources_file = tmp_path / "component-sources.yaml"
    _write_catalog_file(
        sources_file,
        _catalog(
            infra={
                "mk8s": {
                    "source": {
                        "portable": "git::https://github.com/example/infra.git//modules/mk8s?ref=v1.2.3",
                    },
                    "runtime": {
                        "values": {
                            "access": "external",
                        },
                    },
                }
            }
        ),
    )

    with pytest.raises(
        ValueError, match=r"components\.infra\.mk8s has unsupported field\(s\): runtime"
    ):
        load_component_sources(explicit=sources_file)


def test_load_component_sources_rejects_grafana_dashboard_signal_missing_locator(
    tmp_path: Path,
) -> None:
    sources_file = tmp_path / "component-sources.yaml"
    _write_catalog_file(
        sources_file,
        _catalog(
            apps={
                "grafana": {
                    "source": _portable_chart_source(
                        repo="https://example.invalid/charts",
                        chart="grafana",
                        version="1.0.0",
                    ),
                    "release": {
                        "namespace": "observability",
                        "name": "grafana",
                    },
                    "defaults": {
                        "values.dashboards.nebius-kubernetes.kubernetes-logs-from-loki": {
                            "revision": 1,
                            "datasource": "Nebius Logs",
                        }
                    },
                    "cli": {
                        "dashboard_signals": {
                            "logs": "nebius-kubernetes/kubernetes-logs-from-loki",
                        }
                    },
                }
            }
        ),
    )

    with pytest.raises(
        ValueError,
        match=(
            r"components\.apps\.grafana\.defaults\.values\.dashboards\.nebius-kubernetes"
            r"\.kubernetes-logs-from-loki must declare gnetId plus uid or "
            r"dashboard JSON with a top-level uid"
        ),
    ):
        load_component_sources(explicit=sources_file)


def test_load_component_sources_rejects_grafana_dashboard_signal_invalid_json(
    tmp_path: Path,
) -> None:
    sources_file = tmp_path / "component-sources.yaml"
    _write_catalog_file(
        sources_file,
        _catalog(
            apps={
                "grafana": {
                    "source": _portable_chart_source(
                        repo="https://example.invalid/charts",
                        chart="grafana",
                        version="1.0.0",
                    ),
                    "release": {
                        "namespace": "observability",
                        "name": "grafana",
                    },
                    "defaults": {
                        "values.dashboards.nebius-kubernetes.kubernetes-logs-from-loki": {
                            "json": "{",
                            "datasource": "Nebius Logs",
                        }
                    },
                    "cli": {
                        "dashboard_signals": {
                            "logs": "nebius-kubernetes/kubernetes-logs-from-loki",
                        }
                    },
                }
            }
        ),
    )

    with pytest.raises(
        ValueError,
        match=r"values\.dashboards\.nebius-kubernetes\.kubernetes-logs-from-loki\.json "
        r"must be valid dashboard JSON",
    ):
        load_component_sources(explicit=sources_file)


def test_load_component_sources_materializes_grafana_dashboard_signal_json_file(
    tmp_path: Path,
) -> None:
    dashboard_file = tmp_path / "kubernetes-logs.json"
    dashboard_file.write_text(
        json.dumps({"uid": "cxcli-test-logs", "title": "Logs", "panels": []}),
        encoding="utf-8",
    )
    sources_file = tmp_path / "component-sources.yaml"
    _write_catalog_file(
        sources_file,
        _catalog(
            observability={
                "endpoints": {
                    "read": {
                        "logs_loki_read": {
                            "label": "Logs read",
                            "template": "https://example.invalid/projects/{project_id}",
                        }
                    },
                    "write": {},
                }
            },
            apps={
                "grafana": {
                    "source": _portable_chart_source(
                        repo="https://example.invalid/charts",
                        chart="grafana",
                        version="1.0.0",
                    ),
                    "release": {
                        "namespace": "observability",
                        "name": "grafana",
                    },
                    "defaults": {
                        "values.dashboards.nebius-kubernetes.kubernetes-logs-from-loki": {
                            "json_file": "kubernetes-logs.json",
                            "datasource": "Nebius Logs",
                        }
                    },
                    "cli": {
                        "datasources": {
                            "logs": {
                                "name": "Nebius Logs",
                                "uid": "nebius-logs",
                                "type": "loki",
                                "read_endpoint": "logs_loki_read",
                            }
                        },
                        "dashboard_signals": {
                            "logs": "nebius-kubernetes/kubernetes-logs-from-loki",
                        },
                    },
                }
            },
        ),
    )

    loaded = load_component_sources(explicit=sources_file)
    chart = next(item for item in loaded.helm_charts if item.name == "grafana")
    dashboard = next(
        item
        for item in chart.defaults
        if item.target_path == "values.dashboards.nebius-kubernetes.kubernetes-logs-from-loki"
    ).value
    binding = chart.grafana.dashboard_signals[0]

    assert dashboard["datasource"] == "Nebius Logs"
    assert "json_file" not in dashboard
    assert json.loads(dashboard["json"])["uid"] == "cxcli-test-logs"
    assert binding.dashboard_uid == "cxcli-test-logs"
    assert binding.read_endpoint == "logs_loki_read"


def test_load_component_sources_materializes_custom_grafana_dashboard_json_file_paths(
    tmp_path: Path,
) -> None:
    relative_dashboard_file = tmp_path / "dashboards" / "myk8slogs-dash.json"
    relative_dashboard_file.parent.mkdir()
    relative_dashboard_file.write_text(
        json.dumps({"uid": "custom-relative-logs", "title": "Logs", "panels": []}),
        encoding="utf-8",
    )
    absolute_dashboard_file = tmp_path / "absolute-metrics.json"
    absolute_dashboard_file.write_text(
        json.dumps({"uid": "custom-absolute-metrics", "title": "Metrics", "panels": []}),
        encoding="utf-8",
    )
    sources_file = tmp_path / "component-sources.yaml"
    _write_catalog_file(
        sources_file,
        _catalog(
            observability={
                "endpoints": {
                    "read": {
                        "logs_loki_read": {
                            "label": "Logs read",
                            "template": "https://example.invalid/logs/{project_id}",
                        },
                        "metrics_user_read": {
                            "label": "Metrics read",
                            "template": "https://example.invalid/metrics/{project_id}",
                        },
                    },
                    "write": {},
                }
            },
            apps={
                "grafana": {
                    "source": _portable_chart_source(
                        repo="https://example.invalid/charts",
                        chart="grafana",
                        version="1.0.0",
                    ),
                    "release": {
                        "namespace": "observability",
                        "name": "grafana",
                    },
                    "defaults": {
                        "values.dashboards": {
                            "myfolder": {
                                "kubernetes-mylogs": {
                                    "datasource": "Nebius Logs",
                                    "json_file": "./dashboards/myk8slogs-dash.json",
                                },
                                "kubernetes-mymetrics": {
                                    "datasource": "Nebius User Metrics",
                                    "json_file": str(absolute_dashboard_file),
                                },
                            }
                        }
                    },
                    "cli": {
                        "datasources": {
                            "logs": {
                                "name": "Nebius Logs",
                                "uid": "nebius-logs",
                                "type": "loki",
                                "read_endpoint": "logs_loki_read",
                            },
                            "user-metrics": {
                                "name": "Nebius User Metrics",
                                "uid": "nebius-user-metrics",
                                "type": "prometheus",
                                "read_endpoint": "metrics_user_read",
                            },
                        },
                    },
                }
            },
        ),
    )

    loaded = load_component_sources(explicit=sources_file)
    chart = next(item for item in loaded.helm_charts if item.name == "grafana")
    dashboards = next(
        item for item in chart.defaults if item.target_path == "values.dashboards"
    ).value["myfolder"]

    assert json.loads(dashboards["kubernetes-mylogs"]["json"])["uid"] == ("custom-relative-logs")
    assert json.loads(dashboards["kubernetes-mymetrics"]["json"])["uid"] == (
        "custom-absolute-metrics"
    )
    assert "json_file" not in dashboards["kubernetes-mylogs"]
    assert "json_file" not in dashboards["kubernetes-mymetrics"]


def test_load_component_sources_rejects_gnet_dashboard_without_uid(
    tmp_path: Path,
) -> None:
    sources_file = tmp_path / "component-sources.yaml"
    _write_catalog_file(
        sources_file,
        _catalog(
            apps={
                "grafana": {
                    "source": _portable_chart_source(
                        repo="https://example.invalid/charts",
                        chart="grafana",
                        version="1.0.0",
                    ),
                    "release": {
                        "namespace": "observability",
                        "name": "grafana",
                    },
                    "defaults": {
                        "values.dashboards.nebius.service-dashboard": {
                            "gnetId": 123,
                            "revision": 1,
                            "datasource": "Nebius Services",
                        }
                    },
                    "cli": {
                        "datasources": {
                            "service-metrics": {
                                "name": "Nebius Services",
                                "uid": "nebius-service-metrics",
                                "type": "prometheus",
                                "read_endpoint": "metrics_service_provider_read",
                            }
                        },
                    },
                }
            }
        ),
    )

    with pytest.raises(
        ValueError,
        match=(
            r"components\.apps\.grafana\.defaults\.values\.dashboards\.nebius"
            r"\.service-dashboard\.uid is required for gnetId dashboards"
        ),
    ):
        load_component_sources(explicit=sources_file)


def test_load_component_sources_rejects_any_grafana_dashboard_unknown_datasource(
    tmp_path: Path,
) -> None:
    sources_file = tmp_path / "component-sources.yaml"
    _write_catalog_file(
        sources_file,
        _catalog(
            apps={
                "grafana": {
                    "source": _portable_chart_source(
                        repo="https://example.invalid/charts",
                        chart="grafana",
                        version="1.0.0",
                    ),
                    "release": {
                        "namespace": "observability",
                        "name": "grafana",
                    },
                    "defaults": {
                        "values.dashboards.nebius.service-dashboard": {
                            "gnetId": 123,
                            "revision": 1,
                            "uid": "service-dashboard",
                            "datasource": "Missing Datasource",
                        }
                    },
                    "cli": {
                        "datasources": {
                            "service-metrics": {
                                "name": "Nebius Services",
                                "uid": "nebius-service-metrics",
                                "type": "prometheus",
                                "read_endpoint": "metrics_service_provider_read",
                            }
                        },
                    },
                }
            }
        ),
    )

    with pytest.raises(
        ValueError,
        match=(
            r"components\.apps\.grafana\.defaults\.values\.dashboards\.nebius"
            r"\.service-dashboard\.datasource references 'Missing Datasource'"
        ),
    ):
        load_component_sources(explicit=sources_file)


def test_load_component_sources_rejects_mixed_grafana_dashboard_provider_modes(
    tmp_path: Path,
) -> None:
    sources_file = tmp_path / "component-sources.yaml"
    _write_catalog_file(
        sources_file,
        _catalog(
            apps={
                "grafana": {
                    "source": _portable_chart_source(
                        repo="https://example.invalid/charts",
                        chart="grafana",
                        version="1.0.0",
                    ),
                    "release": {
                        "namespace": "observability",
                        "name": "grafana",
                    },
                    "defaults": {
                        "values.dashboards.nebius.service-dashboard": {
                            "gnetId": 123,
                            "revision": 1,
                            "uid": "service-dashboard",
                            "datasource": "Nebius Services",
                        },
                        "values.dashboards.nebius.kubernetes-logs-from-loki": {
                            "json": json.dumps(
                                {
                                    "uid": "cxcli-test-logs",
                                    "title": "Logs",
                                    "panels": [],
                                }
                            ),
                            "datasource": "Nebius Services",
                        },
                    },
                    "cli": {
                        "datasources": {
                            "service-metrics": {
                                "name": "Nebius Services",
                                "uid": "nebius-service-metrics",
                                "type": "prometheus",
                                "read_endpoint": "metrics_service_provider_read",
                            }
                        },
                    },
                }
            }
        ),
    )

    with pytest.raises(
        ValueError,
        match=(
            r"components\.apps\.grafana\.defaults\.values\.dashboards\.nebius "
            r"must not mix Grafana\.com gnetId dashboards with dashboard JSON"
        ),
    ):
        load_component_sources(explicit=sources_file)


def test_load_component_sources_rejects_grafana_dashboard_json_and_json_file(
    tmp_path: Path,
) -> None:
    dashboard_file = tmp_path / "kubernetes-logs.json"
    dashboard_file.write_text(
        json.dumps({"uid": "cxcli-test-logs", "title": "Logs", "panels": []}),
        encoding="utf-8",
    )
    sources_file = tmp_path / "component-sources.yaml"
    _write_catalog_file(
        sources_file,
        _catalog(
            apps={
                "grafana": {
                    "source": _portable_chart_source(
                        repo="https://example.invalid/charts",
                        chart="grafana",
                        version="1.0.0",
                    ),
                    "release": {
                        "namespace": "observability",
                        "name": "grafana",
                    },
                    "defaults": {
                        "values.dashboards.nebius-kubernetes.kubernetes-logs-from-loki": {
                            "json": json.dumps({"uid": "inline"}),
                            "json_file": "kubernetes-logs.json",
                            "datasource": "Nebius Logs",
                        }
                    },
                }
            }
        ),
    )

    with pytest.raises(
        ValueError,
        match=(
            r"components\.apps\.grafana\.defaults\.values\.dashboards\.nebius-kubernetes"
            r"\.kubernetes-logs-from-loki must not declare both json and json_file"
        ),
    ):
        load_component_sources(explicit=sources_file)


def test_load_component_sources_rejects_grafana_logout_timeout_never(tmp_path: Path) -> None:
    sources_file = tmp_path / "component-sources.yaml"
    _write_catalog_file(
        sources_file,
        _catalog(
            apps={
                "grafana": {
                    "source": _portable_chart_source(
                        repo="https://example.invalid/charts",
                        chart="grafana",
                        version="1.0.0",
                    ),
                    "release": {
                        "namespace": "observability",
                        "name": "grafana",
                    },
                    "cli": {
                        "logout-timeout": "never",
                    },
                }
            }
        ),
    )

    with pytest.raises(
        ValueError,
        match=(
            r"components\.apps\.grafana\.cli\.logout-timeout must be "
            r"a Grafana duration"
        ),
    ):
        load_component_sources(explicit=sources_file)


def test_load_component_sources_rejects_nested_grafana_cli_namespace(tmp_path: Path) -> None:
    sources_file = tmp_path / "component-sources.yaml"
    _write_catalog_file(
        sources_file,
        _catalog(
            apps={
                "grafana": {
                    "source": _portable_chart_source(
                        repo="https://example.invalid/charts",
                        chart="grafana",
                        version="1.0.0",
                    ),
                    "release": {
                        "namespace": "observability",
                        "name": "grafana",
                    },
                    "cli": {
                        "grafana": {
                            "logout-timeout": "20m",
                        }
                    },
                }
            }
        ),
    )

    with pytest.raises(
        ValueError,
        match=r"components\.apps\.grafana\.cli has unsupported field\(s\): grafana",
    ):
        load_component_sources(explicit=sources_file)


def test_load_component_sources_rejects_grafana_datasource_unknown_read_endpoint(
    tmp_path: Path,
) -> None:
    sources_file = tmp_path / "component-sources.yaml"
    _write_catalog_file(
        sources_file,
        _catalog(
            apps={
                "grafana": {
                    "source": _portable_chart_source(
                        repo="https://example.invalid/charts",
                        chart="grafana",
                        version="1.0.0",
                    ),
                    "release": {
                        "namespace": "observability",
                        "name": "grafana",
                    },
                    "cli": {
                        "datasources": {
                            "future": {
                                "name": "Future Read API",
                                "uid": "future-read-api",
                                "type": "prometheus",
                                "read_endpoint": "future_read",
                            }
                        }
                    },
                }
            }
        ),
    )

    with pytest.raises(
        ValueError,
        match=(
            r"components\.apps\.grafana\.cli\.datasources\.future"
            r"\.read_endpoint references 'future_read', but that read endpoint "
            r"is not declared under observability\.endpoints\.read"
        ),
    ):
        load_component_sources(explicit=sources_file)


def test_load_component_sources_allows_grafana_datasource_bound_to_catalog_read_endpoint(
    tmp_path: Path,
) -> None:
    sources_file = tmp_path / "component-sources.yaml"
    _write_catalog_file(
        sources_file,
        _catalog(
            observability={
                "endpoints": {
                    "read": {
                        "future_read": {
                            "label": "Future read API",
                            "template": "https://read.example.invalid/projects/{project_id}",
                            "include_when": ["metrics"],
                        }
                    }
                }
            },
            infra={
                "mk8s": {
                    "source": {
                        "portable": (
                            "git::https://github.com/example/infra.git//modules/mk8s?ref=v1.2.3"
                        )
                    },
                    "ui": {"enabled": True},
                    "cli": {
                        "observability": {
                            "primary_agent": {
                                "kind": "kubernetes_agent",
                                "chart_component_id": "nebius-observability-agent",
                                "validation": _kubernetes_agent_validation_enabled(),
                            },
                        }
                    },
                }
            },
            apps={
                "grafana": {
                    "source": _portable_chart_source(
                        repo="https://example.invalid/charts",
                        chart="grafana",
                        version="1.0.0",
                    ),
                    "release": {
                        "namespace": "observability",
                        "name": "grafana",
                    },
                    "cli": {
                        "datasources": {
                            "future": {
                                "name": "Future Read API",
                                "uid": "future-read-api",
                                "type": "prometheus",
                                "read_endpoint": "future_read",
                            }
                        }
                    },
                }
            },
        ),
    )

    loaded = load_component_sources(explicit=sources_file)

    grafana = next(item for item in loaded.helm_charts if item.name == "grafana")
    assert grafana.grafana.datasources[0].read_endpoint == "future_read"


def test_load_component_sources_parses_mk8s_observability_validation_switch(
    tmp_path: Path,
) -> None:
    sources_file = tmp_path / "component-sources.yaml"
    _write_catalog_file(
        sources_file,
        _catalog(
            infra={
                "mk8s": {
                    "source": {
                        "portable": (
                            "git::https://github.com/example/infra.git//modules/mk8s?ref=v1.2.3"
                        )
                    },
                    "ui": {"enabled": True},
                    "cli": {
                        "observability": {
                            "primary_agent": {
                                "kind": "kubernetes_agent",
                                "chart_component_id": "nebius-observability-agent",
                                "validation": False,
                            },
                        }
                    },
                }
            },
        ),
    )

    loaded = load_component_sources(explicit=sources_file)

    mk8s = next(module for module in loaded.tf_modules if module.module == "mk8s")
    assert mk8s.observability.validation.enabled is False
    assert mk8s.observability.validation.helmrelease_ready_condition == "Ready"
    assert mk8s.observability.validation.pod_failure_sample_limit == 5


def test_load_component_sources_rejects_component_local_observability_endpoints(
    tmp_path: Path,
) -> None:
    sources_file = tmp_path / "component-sources.yaml"
    _write_catalog_file(
        sources_file,
        _catalog(
            infra={
                "mk8s": {
                    "source": {
                        "portable": (
                            "git::https://github.com/example/infra.git//modules/mk8s?ref=v1.2.3"
                        )
                    },
                    "ui": {"enabled": True},
                    "cli": {
                        "observability": {
                            "primary_agent": {
                                "kind": "kubernetes_agent",
                                "chart_component_id": "nebius-observability-agent",
                                "validation": _kubernetes_agent_validation_enabled(),
                            },
                            "endpoints": {
                                "read": {
                                    "future_read": {
                                        "label": "Future read API",
                                        "template": (
                                            "https://read.example.invalid/projects/{project_id}"
                                        ),
                                    }
                                }
                            },
                        }
                    },
                }
            },
        ),
    )

    with pytest.raises(
        ValueError,
        match=(
            r"components\.infra\.mk8s\.cli\.observability has unsupported field\(s\): "
            r"endpoints"
        ),
    ):
        load_component_sources(explicit=sources_file)


def test_load_component_sources_allows_service_observability_for_non_agent_infra(
    tmp_path: Path,
) -> None:
    sources_file = tmp_path / "component-sources.yaml"
    _write_catalog_file(
        sources_file,
        _catalog(
            infra={
                "object-storage": {
                    "source": {
                        "portable": (
                            "git::https://github.com/example/infra.git//modules/object-storage?ref=v1.2.3"
                        )
                    },
                    "ui": {"enabled": True},
                    "cli": {
                        "observability": {
                            "service_metrics": {
                                "buckets": {
                                    "future_storage": {
                                        "label": "Future storage metrics",
                                    }
                                }
                            },
                            "service_logs": {
                                "buckets": {
                                    "future_logs": {
                                        "label": "Future service logs",
                                        "include_when": ["inputs.audit_enabled"],
                                    }
                                }
                            },
                        }
                    },
                }
            },
        ),
    )

    loaded = load_component_sources(explicit=sources_file)

    object_storage = next(item for item in loaded.tf_modules if item.module == "object-storage")
    assert [bucket.name for bucket in object_storage.observability.service_metrics] == [
        "future_storage"
    ]
    assert object_storage.observability.service_logs[0].include_when == ("inputs.audit_enabled",)


def test_load_component_sources_falls_back_to_repo_default_catalog_when_bundled_missing(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    default_file = tmp_path / "component_sources.yaml"
    missing_user = tmp_path / "missing-user.yaml"
    missing_global = tmp_path / "missing-global.yaml"
    missing_prefix = tmp_path / "missing-prefix"

    monkeypatch.setattr(
        "nebius_cxcli.component_sources.DEFAULT_COMPONENT_SOURCES_FILE", default_file
    )
    monkeypatch.setattr("nebius_cxcli.component_sources.USER_COMPONENT_SOURCES_FILE", missing_user)
    monkeypatch.setattr(
        "nebius_cxcli.component_sources.GLOBAL_COMPONENT_SOURCES_FILE", missing_global
    )
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


def test_bundled_object_storage_declares_bucket_status_watcher() -> None:
    loaded = load_component_sources(
        explicit=Path(__file__).resolve().parents[1] / "component_sources.yaml"
    )
    object_storage = next(item for item in loaded.tf_modules if item.module == "object-storage")

    assert object_storage.validation_profile == ""
    assert object_storage.status is not None
    assert object_storage.status.kind == "nebius.storage.bucket"
    assert object_storage.status.parent_input == "parent_id"
    assert object_storage.status.name_input == "name"
    assert [bucket.name for bucket in object_storage.observability.service_metrics] == [
        "sp_storage"
    ]
    assert object_storage.observability.service_logs == ()


def test_bundled_mysterybox_declares_secret_status_watcher() -> None:
    loaded = load_component_sources(
        explicit=Path(__file__).resolve().parents[1] / "component_sources.yaml"
    )
    mysterybox = next(item for item in loaded.tf_modules if item.module == "mysterybox")

    assert mysterybox.status is not None
    assert mysterybox.status.kind == "nebius.mysterybox.secret"
    assert mysterybox.status.parent_input == "parent_id"
    assert mysterybox.status.name_input == "secrets"


def test_bundled_vm_like_modules_declare_compute_instance_status_watchers() -> None:
    loaded = load_component_sources(
        explicit=Path(__file__).resolve().parents[1] / "component_sources.yaml"
    )
    vm = next(item for item in loaded.tf_modules if item.module == "vm")
    wireguard = next(item for item in loaded.tf_modules if item.module == "wireguard-gw")
    ssh = next(item for item in loaded.tf_modules if item.module == "ssh-jumphost")

    assert vm.validation_profile == "vm_instance"
    assert vm.status is not None
    assert vm.status.kind == "nebius.compute.instance"
    assert vm.status.parent_input == "parent_id"
    assert vm.status.name_input == "name"

    assert wireguard.status is not None
    assert wireguard.status.kind == "nebius.compute.instance"
    assert wireguard.status.parent_input == "parent_id"
    assert wireguard.status.name_input == "name"

    assert ssh.status is not None
    assert ssh.status.kind == "nebius.compute.instance"
    assert ssh.status.parent_input == "parent_id"
    assert ssh.status.name_input == "name"


def test_bundled_wireguard_gw_declares_runtime_defaults_in_catalog() -> None:
    loaded = load_component_sources(
        explicit=Path(__file__).resolve().parents[1] / "component_sources.yaml"
    )
    wireguard = next(item for item in loaded.tf_modules if item.module == "wireguard-gw")
    defaults = {
        default.target_path: default.value
        for default in wireguard.defaults
        if default.kind == "literal"
    }

    assert defaults["inputs.wireguard_tunnel_cidr"] == "10.8.0.1/22"
    assert defaults["inputs.wireguard_listen_port"] == 51820
    assert defaults["inputs.client_default_dns"] == ["1.1.1.1", "1.0.0.1"]


def test_bundled_wireguard_uses_gateway_id_without_legacy_jumphost_id() -> None:
    loaded = load_component_sources(
        explicit=Path(__file__).resolve().parents[1] / "component_sources.yaml"
    )
    modules = {item.module: item for item in loaded.tf_modules}
    legacy_id = "wireguard-" + "jumphost"

    assert "wireguard-gw" in modules
    assert legacy_id not in modules
    assert legacy_id not in modules["wireguard-gw"].portable_source
    assert legacy_id not in str(modules["wireguard-gw"].local_source)


def test_bundled_global_observability_declares_public_endpoint_templates() -> None:
    loaded = load_component_sources(
        explicit=Path(__file__).resolve().parents[1] / "component_sources.yaml"
    )
    read = {endpoint.key: endpoint for endpoint in loaded.observability.endpoints.read}
    write = {endpoint.key: endpoint for endpoint in loaded.observability.endpoints.write}

    assert write["metrics_otlp_write"].template == (
        "https://write.monitoring.{region}.nebius.cloud/projects/"
        "{project_id}/opentelemetry/v1/metrics"
    )
    assert write["metrics_otlp_write"].label == "Metrics write (OTLP HTTP/protobuf)"
    assert write["metrics_otlp_write"].include_when == ("kubernetes_metrics",)
    assert write["metrics_prometheus_remote_write"].template == (
        "https://write.monitoring.{region}.nebius.cloud/projects/"
        "{project_id}/prometheus/api/v1/write"
    )
    assert write["metrics_prometheus_remote_write"].include_when == (
        "kubernetes_metrics",
    )
    assert write["logs_otlp_write"].template == ("https://write.logging.{region}.nebius.cloud")
    assert write["logs_agent_grpc_write"].template == (
        "dns:///write.logging.{region}.nebius.cloud:443"
    )
    assert write["logs_agent_grpc_write"].include_when == (
        "kubernetes_logs",
    )
    assert read["metrics_service_provider_read"].template == (
        "https://read.monitoring.api.nebius.cloud/projects/{project_id}/service-provider/prometheus"
    )
    assert read["logs_loki_read"].template == (
        "https://read.logging.api.nebius.cloud/projects/{project_id}"
    )
    assert read["traces_tempo_read"].template == (
        "https://read.tracing.api.nebius.cloud/projects/{project_id}/tempo"
    )
    assert read["metrics_federate_read"].bucket_placeholder == "<service-provider>"


def test_bundled_vm_observability_declares_service_buckets() -> None:
    loaded = load_component_sources(
        explicit=Path(__file__).resolve().parents[1] / "component_sources.yaml"
    )
    vm = next(item for item in loaded.tf_modules if item.module == "vm")

    assert vm.observability.mode == "monitoring_agent"
    assert not hasattr(vm.observability, "standalone_collector")
    assert [bucket.name for bucket in vm.observability.service_metrics] == ["compute", "nbs"]
    assert [bucket.name for bucket in vm.observability.service_logs] == ["sp_serial"]


def test_bundled_gpu_operator_observability_declares_dcgm_node_policy() -> None:
    loaded = load_component_sources(
        explicit=Path(__file__).resolve().parents[1] / "component_sources.yaml"
    )
    gpu_operator = next(item for item in loaded.helm_charts if item.name == "nvidia-gpu-operator")

    target = gpu_operator.observability.metric_targets[0]

    assert target.job_name == "cxcli-nvidia-dcgm-exporter"
    assert target.discovery == "prometheus_annotations"
    assert dict(target.required_gpu_node_labels) == {
        "nvidia.com/gpu.deploy.operands": "true",
        "nvidia.com/gpu.deploy.dcgm-exporter": "true",
        "nvidia.com/gpu.deploy.operator-validator": "true",
        "nvidia.com/gpu.deploy.device-plugin": "false",
        "nvidia.com/gpu.deploy.gpu-feature-discovery": "false",
    }
    assert dict(target.required_gpu_node_selector) == {"nebius.com/gpu": "true"}
    assert target.required_gpu_node_label_stack_sources == ("nebius_image",)
    assert [item.name for item in gpu_operator.mk8s_gpu.default_sets] == [
        "nebius_image_gpu_nfd",
    ]
    gpu_nfd_default_set = gpu_operator.mk8s_gpu.default_sets[0]
    defaults = {item.target_path: item.value for item in gpu_nfd_default_set.defaults}
    assert defaults["values.node-feature-discovery.worker.affinity"]["nodeAffinity"][
        "requiredDuringSchedulingIgnoredDuringExecution"
    ]["nodeSelectorTerms"][0]["matchExpressions"][0] == {
        "key": "nebius.com/gpu",
        "operator": "In",
        "values": ["true"],
    }
    gpu_nfd_rule = next(
        rule
        for rule in gpu_operator.mk8s_gpu.rules
        if rule.gpu_stack_source == "nebius_image" and rule.gpu_cluster_enabled is False
    )
    assert gpu_nfd_rule.defaults_from == ("nebius_image_gpu_nfd",)


def test_bundled_network_operator_declares_single_nfd_owner_policy() -> None:
    loaded = load_component_sources(
        explicit=Path(__file__).resolve().parents[1] / "component_sources.yaml"
    )
    network_operator = next(
        item for item in loaded.helm_charts if item.name == "nvidia-network-operator"
    )
    defaults = {item.target_path: item.value for item in network_operator.defaults}

    assert defaults["values.operator.affinity"] == _NEBIUS_CPU_ONLY_AFFINITY
    assert [item.name for item in network_operator.mk8s_gpu.default_sets] == [
        "network_operator_nfd",
        "driverful_infiniband_node_selection",
    ]
    default_sets = {
        item.name: {default.target_path: default.value for default in item.defaults}
        for item in network_operator.mk8s_gpu.default_sets
    }
    assert default_sets["network_operator_nfd"] == {
        "values.nfd.enabled": True,
        "values.nfd.deployNodeFeatureRules": True,
    }
    assert default_sets["driverful_infiniband_node_selection"][
        "values.node-feature-discovery.worker.affinity"
    ]["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]["nodeSelectorTerms"][0][
        "matchExpressions"
    ][0] == {
        "key": "nebius.com/driverful",
        "operator": "In",
        "values": ["true"],
    }

    gpu_cluster_rule = next(
        rule
        for rule in network_operator.mk8s_gpu.rules
        if rule.gpu_cluster_enabled is True and rule.gpu_stack_source == ""
    )
    assert gpu_cluster_rule.auto_enable is True
    assert gpu_cluster_rule.defaults_from == ("network_operator_nfd",)

    b200_rule = next(
        rule
        for rule in network_operator.mk8s_gpu.rules
        if rule.gpu_stack_source == "operator_managed"
        and rule.match_platforms == ("gpu-b200-sxm", "gpu-b200-sxm-a")
    )
    assert b200_rule.auto_enable is True
    assert b200_rule.defaults_from == ("network_operator_nfd",)

    driverful_cluster_rule = next(
        rule
        for rule in network_operator.mk8s_gpu.rules
        if rule.gpu_stack_source == "nebius_image" and rule.gpu_cluster_enabled is True
    )
    assert driverful_cluster_rule.defaults_from == ("driverful_infiniband_node_selection",)


def test_component_sources_rejects_invalid_observability_gpu_node_label_stack_source(
    tmp_path: Path,
) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    _write_catalog_file(
        sources_file,
        _catalog(
            apps={
                "gpu-operator": {
                    "source": _portable_chart_source(
                        repo="https://example.invalid/charts",
                        chart="gpu-operator",
                        version="1.0.0",
                    ),
                    "release": {
                        "namespace": "gpu-system",
                        "name": "gpu-operator",
                    },
                    "ui": {"enabled": False},
                    "cli": {
                        "observability": {
                            "metric_targets": [
                                {
                                    "job_name": "cxcli-nvidia-dcgm-exporter",
                                    "discovery": {
                                        "kind": "prometheus_annotations",
                                        "service_name": "nvidia-dcgm-exporter",
                                    },
                                    "managed_gpu_node_policy": {
                                        "stack_sources": [
                                            "driverful",
                                        ]
                                    },
                                }
                            ]
                        }
                    },
                }
            }
        ),
    )

    with pytest.raises(
        ValueError,
        match=(
            "components\\.apps\\.gpu-operator\\.cli\\.observability\\.metric_targets"
            "\\[0\\]\\.managed_gpu_node_policy\\.stack_sources\\[0\\] must be "
            "'nebius_image' or 'operator_managed'"
        ),
    ):
        load_component_sources(explicit=sources_file)


def test_bundled_mk8s_declares_optional_wizard_field_override() -> None:
    loaded = load_component_sources(
        explicit=Path(__file__).resolve().parents[1] / "component_sources.yaml"
    )
    mk8s = next(item for item in loaded.tf_modules if item.module == "mk8s")

    assert mk8s.mk8s_gpu.validations.nccl.rdma_mpi_extra_args == (
        "-x",
        "NCCL_DMABUF_ENABLE=1",
    )
    assert mk8s.wizard_fields == {
        "inputs.subnet_id": {
            "options": {
                "from": "project_subnets",
            }
        },
        "inputs.k8s_version": {
            "options": {
                "from": "mk8s_control_plane_versions",
                "auto_select_first": True,
            }
        },
        "inputs.cpu_nodes_platform": {
            "options": {
                "from": "mk8s_compatible_platforms",
                "args": {"platform_prefix": "cpu-"},
            }
        },
        "inputs.gpu_nodes_platform": {
            "options": {
                "from": "mk8s_compatible_platforms",
                "args": {"platform_prefix": "gpu-"},
            }
        },
        "inputs.cpu_nodes_preset": {
            "options": {
                "from": "compute_platform_presets",
                "args": {"platform_path": "inputs.cpu_nodes_platform"},
            }
        },
        "inputs.cpu_nodes_os": {
            "options": {
                "from": "mk8s_node_group_os_values",
                "args": {"platform_path": "inputs.cpu_nodes_platform"},
                "auto_select_first": True,
            },
            "prompt": False,
        },
        "inputs.gpu_nodes_preset": {
            "options": {
                "from": "compute_platform_presets",
                "args": {
                    "platform_path": "inputs.gpu_nodes_platform",
                    "gpu_cluster_required_path": "inputs.infiniband_fabric",
                },
                "auto_select_single": True,
            }
        },
        "inputs.gpu_stack_source": {
            "sources": [
                {
                    "source": "static",
                    "values": [
                        {
                            "value": "nebius_image",
                            "label": (
                                "nebius_image  (Nebius GPU image includes the host "
                                "NVIDIA driver/toolkit; GPU Operator does not "
                                "install them)"
                            ),
                        },
                        {
                            "value": "operator_managed",
                            "label": (
                                "operator_managed  (base OS image; GPU Operator "
                                "installs and manages the NVIDIA driver/toolkit)"
                            ),
                        },
                    ],
                }
            ]
        },
        "inputs.infiniband_fabric": {
            "options": {
                "from": "mk8s_infiniband_fabrics",
                "args": {
                    "platform_path": "inputs.gpu_nodes_platform",
                    "preset_path": "inputs.gpu_nodes_preset",
                },
                "skip_prompt_if_no_choices": True,
            }
        },
        "inputs.gpu_nodes_os": {
            "options": {
                "from": "mk8s_node_group_os_values",
                "args": {
                    "platform_path": "inputs.gpu_nodes_platform",
                    "stack_preset_path": "inputs.gpu_stack_preset",
                },
                "auto_select_first": True,
            },
            "prompt": False,
        },
        "inputs.gpu_stack_preset": {
            "options": {
                "from": "mk8s_gpu_stack_presets",
                "args": {"platform_path": "inputs.gpu_nodes_platform"},
            },
            "prompt": False,
        },
        "inputs.cpu_nodes_boot_disk_type": {
            "options": {
                "from": "compute_boot_disk_types",
                "auto_select_first": True,
            },
        },
        "inputs.gpu_nodes_boot_disk_type": {
            "options": {
                "from": "compute_boot_disk_types",
                "auto_select_first": True,
            },
        },
        "deploy.targets[].validations.mk8s_gpu.operator_readiness.enabled": {
            "default": True,
        },
        "deploy.targets[].validations.mk8s_gpu.gpu_visibility.enabled": {
            "default": True,
        },
        "deploy.targets[].validations.mk8s_gpu.gpu_visibility.max_nodes": {
            "default": 3,
        },
        "deploy.targets[].validations.mk8s_gpu.nccl.enabled": {
            "default": True,
        },
        "deploy.targets[].validations.mk8s_gpu.nccl.max_nodes": {
            "default": 8,
        },
        "deploy.targets[].validations.mk8s_gpu.nccl.average_bus_bandwidth_threshold_gbps": {
            "default": 300,
        },
        "deploy.targets[].validations.mk8s_gpu.health_checker.enabled": {
            "default": False,
        },
        "deploy.targets[].observability.enabled": {
            "default": False,
        },
        "deploy.targets[].observability.kubernetes.logs.enabled": {
            "default": True,
        },
        "deploy.targets[].observability.kubernetes.logs.collect_agent_logs": {
            "default": False,
            "prompt": False,
        },
        "deploy.targets[].observability.kubernetes.logs.excluded_namespaces": {
            "default": ["kube-system"],
            "prompt": False,
        },
        "deploy.targets[].observability.kubernetes.metrics.enabled": {
            "default": True,
        },
        "deploy.targets[].observability.kubernetes.metrics.collect_agent_metrics": {
            "default": False,
            "prompt": False,
        },
        "deploy.targets[].observability.kubernetes.metrics.collect_k8s_cluster_metrics": {
            "default": True,
        },
        "deploy.targets[].observability.kubernetes.metrics.excluded_namespaces": {
            "default": ["kube-system"],
            "prompt": False,
        },
        "deploy.targets[].observability.kubernetes.traces.enabled": {
            "default": True,
        },
        "deploy.targets[].secrets.mysterybox.enabled": {
            "default": True,
            "materialize_default": True,
        },
        "deploy.targets[].secrets.mysterybox.store_name": {
            "default": "nebius-mysterybox-shared",
            "prompt": False,
        },
        "deploy.targets[].secrets.mysterybox.api_domain": {
            "default": "api.nebius.cloud:443",
            "prompt": False,
        },
        "deploy.targets[].secrets.mysterybox.credentials_secret.name": {
            "default": "nebius-mysterybox-shared-creds",
            "prompt": False,
        },
        "deploy.targets[].secrets.mysterybox.credentials_secret.namespace": {
            "default": "external-secrets",
            "prompt": False,
        },
        "deploy.targets[].secrets.mysterybox.credentials_secret.key": {
            "default": "credentials.json",
            "prompt": False,
        },
        "deploy.targets[].secrets.mysterybox.allow_all_namespaces": {
            "default": True,
            "materialize_default": True,
        },
        "deploy.targets[].secrets.mysterybox.refresh_interval": {
            "default": "15m",
            "materialize_default": True,
        },
        "deploy.targets[].secrets.mysterybox.sync_namespaces": {
            "default": ["default"],
            "type_hint": "list(string)",
            "prompt_complex": True,
            "materialize_default": True,
            "required": True,
        },
        "inputs.mk8s_cluster_overrides": {
            "prompt": False,
        },
        "inputs.mk8s_cpu_node_group_overrides": {
            "prompt": False,
        },
        "inputs.mk8s_gpu_node_group_overrides": {
            "prompt": False,
        },
        "inputs.gpu_clusters": {
            "prompt": False,
        },
        "inputs.node_groups": {
            "prompt": False,
        },
    }
    boot_disks = loaded.compute.boot_disk_defaults
    assert tuple(choice.value for choice in boot_disks.disk_types) == (
        "NETWORK_SSD",
        "NETWORK_SSD_NON_REPLICATED",
        "NETWORK_SSD_IO_M3",
    )
    assert tuple(choice.allocation_unit_gib for choice in boot_disks.disk_types) == (1, 93, 93)
    assert tuple(choice.explicit_encryption_supported for choice in boot_disks.disk_types) == (
        False,
        True,
        True,
    )
    assert boot_disks.cpu.default_type == "NETWORK_SSD"
    assert boot_disks.gpu.default_type == "NETWORK_SSD"
    assert tuple(rule.size_gib for rule in boot_disks.cpu.rules) == (64, 93, 128, 186)
    assert tuple(rule.size_gib for rule in boot_disks.gpu.rules) == (256, 512, 1023)


def test_bundled_cert_manager_enables_chart_crds_by_default() -> None:
    loaded = load_component_sources(
        explicit=Path(__file__).resolve().parents[1] / "component_sources.yaml"
    )
    cert_manager = next(item for item in loaded.helm_charts if item.name == "cert-manager")
    defaults = {item.target_path: item.value for item in cert_manager.defaults}

    assert defaults["values.crds.enabled"] is True
    assert defaults["values.replicaCount"] == 2
    assert defaults["values.affinity"] == _NEBIUS_CPU_ONLY_AFFINITY
    assert defaults["values.webhook.replicaCount"] == 2
    assert defaults["values.webhook.affinity"] == _NEBIUS_CPU_ONLY_AFFINITY
    assert defaults["values.cainjector.replicaCount"] == 2
    assert defaults["values.cainjector.affinity"] == _NEBIUS_CPU_ONLY_AFFINITY
    assert defaults["values.startupapicheck.affinity"] == _NEBIUS_CPU_ONLY_AFFINITY


def test_bundled_mysterybox_uses_profile_and_no_webhook_chart() -> None:
    loaded = load_component_sources(
        explicit=Path(__file__).resolve().parents[1] / "component_sources.yaml"
    )
    mysterybox = next(item for item in loaded.tf_modules if item.module == "mysterybox")

    assert not any(item.name == "mysterybox-webhook" for item in loaded.helm_charts)
    assert mysterybox.wizard_fields == {
        "inputs.secrets": {
            "type_hint": "list(object({}))",
            "prompt_complex": True,
            "required": True,
        },
        "inputs.payload_values": {
            "prompt": False,
        },
    }


def test_bundled_cpu_only_charts_avoid_nebius_gpu_nodes_by_default() -> None:
    loaded = load_component_sources(
        explicit=Path(__file__).resolve().parents[1] / "component_sources.yaml"
    )
    charts = {item.name: item for item in loaded.helm_charts}

    grafana_defaults = {item.target_path: item.value for item in charts["grafana"].defaults}
    gateway_defaults = {item.target_path: item.value for item in charts["gateway-helm"].defaults}
    external_dns_defaults = {
        item.target_path: item.value for item in charts["external-dns"].defaults
    }
    external_secrets_defaults = {
        item.target_path: item.value for item in charts["external-secrets"].defaults
    }
    n8n_defaults = {item.target_path: item.value for item in charts["n8n"].defaults}

    assert "values.replicas" not in grafana_defaults
    assert grafana_defaults["values.affinity"] == _NEBIUS_CPU_ONLY_AFFINITY
    assert "values.image.registry" not in grafana_defaults
    assert "values.image.repository" not in grafana_defaults
    assert "values.image.tag" not in grafana_defaults
    grafana_settings = charts["grafana"].grafana
    assert grafana_settings.org_id == 1
    assert grafana_settings.logout_timeout == "20m"
    assert grafana_settings.admin_secret.secret_name == "nebius-cxcli-grafana-admin"
    assert grafana_settings.admin_secret.user == "admin"
    assert grafana_settings.admin_secret.user_key == "admin-user"
    assert grafana_settings.admin_secret.password_key == "admin-password"
    assert grafana_settings.read_token.env == "NEBIUS_OBSERVABILITY_STATIC_TOKEN"
    assert grafana_settings.read_token.secret_name == "nebius-cxcli-grafana-observability-read"
    assert grafana_settings.read_token.key == "token"
    assert {item.signal: item.query for item in grafana_settings.explore_queries} == {
        "metrics": 'count({__name__=~".+"})',
        "logs": '{__bucket__="default"}',
    }
    datasources = {item.key: item for item in grafana_settings.datasources}
    assert {
        key: {
            "name": datasource.name,
            "uid": datasource.uid,
            "type": datasource.datasource_type,
            "read_endpoint": datasource.read_endpoint,
            "is_default": datasource.is_default,
            "description": datasource.description,
        }
        for key, datasource in datasources.items()
    } == {
        "service-metrics": {
            "name": "Nebius Services",
            "uid": "nebius-service-metrics",
            "type": "prometheus",
            "read_endpoint": "metrics_service_provider_read",
            "is_default": True,
            "description": (
                "Nebius/provider service metrics for cloud resources, the cxcli GPU dashboard, "
                "and service dashboard examples."
            ),
        },
        "user-metrics": {
            "name": "Nebius User Metrics",
            "uid": "nebius-user-metrics",
            "type": "prometheus",
            "read_endpoint": "metrics_user_read",
            "is_default": False,
            "description": (
                "Customer/user-ingested Prometheus metrics, including Kubernetes "
                "metrics written by the Nebius observability agent."
            ),
        },
        "logs": {
            "name": "Nebius Logs",
            "uid": "nebius-logs",
            "type": "loki",
            "read_endpoint": "logs_loki_read",
            "is_default": False,
            "description": "",
        },
        "traces": {
            "name": "Nebius Traces",
            "uid": "nebius-traces",
            "type": "tempo",
            "read_endpoint": "traces_tempo_read",
            "is_default": False,
            "description": "",
        },
    }
    dashboard_signals = {binding.signal: binding for binding in grafana_settings.dashboard_signals}
    assert {
        signal: {
            "folder": binding.folder,
            "dashboard": binding.dashboard,
            "gnet_id": binding.gnet_id,
            "dashboard_uid": binding.dashboard_uid,
            "datasource": binding.datasource,
            "read_endpoint": binding.read_endpoint,
        }
        for signal, binding in dashboard_signals.items()
    } == {
        "metrics": {
            "folder": "nebius-kubernetes",
            "dashboard": "kubernetes-cluster-monitoring",
            "gnet_id": 0,
            "dashboard_uid": "cxcli-kubernetes-metrics",
            "datasource": "Nebius User Metrics",
            "read_endpoint": "metrics_user_read",
        },
        "logs": {
            "folder": "nebius-kubernetes",
            "dashboard": "kubernetes-logs-from-loki",
            "gnet_id": 0,
            "dashboard_uid": "cxcli-kubernetes-logs",
            "datasource": "Nebius Logs",
            "read_endpoint": "logs_loki_read",
        },
        "traces": {
            "folder": "nebius-kubernetes",
            "dashboard": "kubernetes-traces",
            "gnet_id": 0,
            "dashboard_uid": "cxcli-kubernetes-traces",
            "datasource": "Nebius Traces",
            "read_endpoint": "traces_tempo_read",
        },
    }
    grafana_service_dashboards = grafana_defaults["values.dashboards"]["nebius"]
    grafana_dashboards = grafana_defaults["values.dashboards"]["nebius-kubernetes"]
    grafana_vm_dashboards = grafana_defaults["values.dashboards"]["nebius-vm"]
    for binding in dashboard_signals.values():
        assert binding.folder == "nebius-kubernetes"
        dashboard = grafana_dashboards[binding.dashboard]
        dashboard_json = json.loads(dashboard["json"])
        assert dashboard_json["uid"] == binding.dashboard_uid
        assert dashboard["datasource"] == binding.datasource
        assert "json_file" not in dashboard
    assert (
        "query_result(count by (kubernetes_io_hostname) "
        '(container_cpu_usage_seconds_total{\\"k8s.cluster.id\\"=~\\"$Cluster\\",pod!=\\"\\"}))'
        in grafana_dashboards["kubernetes-cluster-monitoring"]["json"]
    )
    assert "container_cpu_cfs_throttled_periods_total" in grafana_dashboards[
        "kubernetes-cluster-monitoring"
    ]["json"]
    assert "container_memory_failures_total" in grafana_dashboards[
        "kubernetes-cluster-monitoring"
    ]["json"]
    assert "container_fs_reads_bytes_total" in grafana_dashboards[
        "kubernetes-cluster-monitoring"
    ]["json"]
    assert "apiserver_request_total" in grafana_dashboards["kubernetes-cluster-monitoring"][
        "json"
    ]
    assert (
        'DCGM_FI_DEV_GPU_UTIL{job=\\"nebius-observability-agent\\",mk8s_cluster_id=~\\"$Cluster\\",instance_id=~\\"$GpuNode\\"'
        in grafana_dashboards["kubernetes-gpu"]["json"]
    )
    assert "DCGM_FI_DEV_XID_ERRORS" in grafana_dashboards["kubernetes-gpu"]["json"]
    assert grafana_dashboards["kubernetes-gpu"]["datasource"] == "Nebius Services"
    assert "kube_pod_info" not in grafana_dashboards["kubernetes-cluster-monitoring"]["json"]
    assert (
        'label_values({__bucket__=\\"default\\", k8s_cluster_id=~\\"$Cluster\\"}, k8s_namespace_name)'
        in grafana_dashboards["kubernetes-logs-from-loki"]["json"]
    )
    assert "{}" in grafana_dashboards["kubernetes-traces"]["json"]
    assert "{ duration > 1s }" in grafana_dashboards["kubernetes-traces"]["json"]
    assert json.loads(grafana_vm_dashboards["vm-metrics"]["json"])["uid"] == "cxcli-vm-metrics"
    assert json.loads(grafana_vm_dashboards["vm-logs"]["json"])["uid"] == "cxcli-vm-logs"
    assert grafana_vm_dashboards["vm-metrics"]["datasource"] == "Nebius Services"
    assert grafana_vm_dashboards["vm-logs"]["datasource"] == "Nebius Logs"
    assert "node_cpu_seconds_total" in grafana_vm_dashboards["vm-metrics"]["json"]
    assert "cxcli-vm-collector" not in grafana_vm_dashboards["vm-logs"]["json"]
    assert "__bucket__=~\\\"$Bucket\\\"" in grafana_vm_dashboards["vm-logs"]["json"]
    assert {
        dashboard["datasource"]
        for key, dashboard in grafana_service_dashboards.items()
        if key.startswith("nebius-")
    } == {"Nebius Services"}
    assert {
        key: dashboard["uid"]
        for key, dashboard in grafana_service_dashboards.items()
        if key.startswith("nebius-")
    } == {"nebius-disk": "nebius-disk-user-stats"}
    envoy_proxy = next(
        item for item in grafana_defaults["values.extraObjects"] if item["kind"] == "EnvoyProxy"
    )
    assert envoy_proxy["spec"]["provider"]["kubernetes"]["envoyDeployment"]["replicas"] == 2
    assert (
        envoy_proxy["spec"]["provider"]["kubernetes"]["envoyDeployment"]["pod"]["affinity"]
        == _NEBIUS_CPU_ONLY_AFFINITY
    )
    assert gateway_defaults["values.deployment.pod.affinity"] == _NEBIUS_CPU_ONLY_AFFINITY
    assert gateway_defaults["values.deployment.replicas"] == 2
    assert gateway_defaults["values.certgen.job.affinity"] == _NEBIUS_CPU_ONLY_AFFINITY
    assert external_dns_defaults["values.affinity"] == _NEBIUS_CPU_ONLY_AFFINITY
    assert external_secrets_defaults["values.replicaCount"] == 2
    assert external_secrets_defaults["values.leaderElect"] is True
    assert external_secrets_defaults["values.global.affinity"] == _NEBIUS_CPU_ONLY_AFFINITY
    assert external_secrets_defaults["values.webhook.replicaCount"] == 2
    assert external_secrets_defaults["values.certController.replicaCount"] == 2
    assert n8n_defaults["values.main.affinity"] == _NEBIUS_CPU_ONLY_AFFINITY
    assert n8n_defaults["values.worker.affinity"] == _NEBIUS_CPU_ONLY_AFFINITY
    assert n8n_defaults["values.webhook.affinity"] == _NEBIUS_CPU_ONLY_AFFINITY


def test_bundled_managed_postgresql_uses_wizard_profile() -> None:
    loaded = load_component_sources(
        explicit=Path(__file__).resolve().parents[1] / "component_sources.yaml"
    )
    module = next(item for item in loaded.tf_modules if item.module == "managed-postgresql")

    assert [bucket.name for bucket in module.observability.service_metrics] == ["msp"]
    assert [bucket.name for bucket in module.observability.service_logs] == ["sp_postgres"]
    assert module.wizard_fields == {
        "inputs.network_id": {
            "options": {
                "from": "project_networks",
            }
        },
        "inputs.tier": {
            "sources": [
                {
                    "source": "static",
                    "values": ["small", "medium", "large"],
                }
            ]
        },
    }


def test_bundled_vm_and_jump_hosts_use_component_scoped_wizard_profiles() -> None:
    loaded = load_component_sources(
        explicit=Path(__file__).resolve().parents[1] / "component_sources.yaml"
    )

    vm = next(item for item in loaded.tf_modules if item.module == "vm")
    wireguard = next(item for item in loaded.tf_modules if item.module == "wireguard-gw")
    ssh = next(item for item in loaded.tf_modules if item.module == "ssh-jumphost")

    assert vm.wizard_fields == {
        "inputs.subnet_id": {
            "options": {
                "from": "project_subnets",
            }
        },
        "inputs.platform": {
            "options": {
                "from": "compute_platforms",
            }
        },
        "inputs.preset": {
            "options": {
                "from": "compute_platform_presets",
                "args": {"platform_path": "inputs.platform"},
            }
        },
        "inputs.source_image_family": {
            "options": {
                "from": "compute_public_image_families",
                "args": {"platform_path": "inputs.platform"},
                "auto_select_first": True,
            }
        },
        "inputs.boot_disk_type": {
            "options": {
                "from": "compute_boot_disk_types",
                "auto_select_first": True,
            }
        },
        "inputs.public_ip_mode": {
            "sources": [
                {
                    "source": "static",
                    "values": ["dynamic", "none", "static", "allocation"],
                }
            ]
        },
        "inputs.gpu_cluster_infiniband_fabric": {
            "options": {
                "from": "mk8s_infiniband_fabrics",
                "args": {
                    "platform_path": "inputs.platform",
                    "preset_path": "inputs.preset",
                },
                "skip_prompt_if_no_choices": True,
            }
        },
        "deploy.observability.enabled": {
            "default": False,
        },
        "deploy.observability.vm.logs.enabled": {
            "default": True,
        },
        "deploy.observability.vm.logs.systemd_units": {
            "default": [],
        },
        "inputs.boot_disk_existing_id": {
            "prompt": False,
        },
        "inputs.boot_disk_block_size_bytes": {
            "prompt": False,
        },
        "inputs.source_image_id": {
            "prompt": False,
        },
        "inputs.boot_disk_device_id": {
            "prompt": False,
        },
        "inputs.public_ip_allocation_id": {
            "prompt": False,
        },
        "inputs.private_ip_allocation_id": {
            "prompt": False,
        },
        "inputs.security_group_ids": {
            "prompt": False,
        },
        "inputs.hostname": {
            "prompt": False,
        },
        "inputs.cloud_init_user_data_override": {
            "prompt": False,
        },
        "inputs.stopped": {
            "prompt": False,
        },
        "inputs.labels": {
            "prompt": False,
        },
        "inputs.data_disks": {
            "prompt": False,
        },
        "inputs.existing_data_disks": {
            "prompt": False,
        },
        "inputs.filesystems": {
            "prompt": False,
        },
        "inputs.recovery_policy": {
            "prompt": False,
        },
        "inputs.gpu_cluster_id": {
            "prompt": False,
        },
        "inputs.gpu_cluster_name": {
            "prompt": False,
        },
        "inputs.container_entrypoint": {
            "prompt": False,
        },
        "inputs.container_args": {
            "prompt": False,
        },
        "inputs.container_env": {
            "prompt": False,
        },
        "inputs.container_ports": {
            "prompt": False,
        },
        "inputs.container_mounts": {
            "prompt": False,
        },
    }
    assert wireguard.wizard_fields == {
        "inputs.subnet_id": {
            "options": {
                "from": "project_subnets",
            }
        },
        "inputs.platform": {
            "options": {
                "from": "compute_platforms",
            }
        },
        "inputs.preset": {
            "options": {
                "from": "compute_platform_presets",
                "args": {"platform_path": "inputs.platform"},
            }
        },
        "inputs.source_image_family": {
            "options": {
                "from": "compute_public_image_families",
                "args": {"platform_path": "inputs.platform"},
                "auto_select_first": True,
            }
        },
        "inputs.boot_disk_type": {
            "options": {
                "from": "compute_boot_disk_types",
                "auto_select_first": True,
            }
        },
        "inputs.wireguard_tunnel_cidr": {
            "materialize_default": True,
        },
        "inputs.boot_disk_block_size_bytes": {
            "prompt": False,
        },
        "inputs.endpoint_host": {
            "prompt": False,
        },
        "inputs.clients": {
            "prompt": False,
        },
        "inputs.labels": {
            "prompt": False,
        },
    }
    assert ssh.wizard_fields == {
        "inputs.subnet_id": {
            "options": {
                "from": "project_subnets",
            }
        },
        "inputs.platform": {
            "options": {
                "from": "compute_platforms",
            }
        },
        "inputs.preset": {
            "options": {
                "from": "compute_platform_presets",
                "args": {"platform_path": "inputs.platform"},
            }
        },
        "inputs.source_image_family": {
            "options": {
                "from": "compute_public_image_families",
                "args": {"platform_path": "inputs.platform"},
                "auto_select_first": True,
            }
        },
        "inputs.boot_disk_type": {
            "options": {
                "from": "compute_boot_disk_types",
                "auto_select_first": True,
            }
        },
        "inputs.allowed_cidrs": {
            "default_from": {
                "from": "operator_public_ip_cidr",
            },
            "type_hint": "list(string)",
            "materialize_default": True,
        },
        "inputs.boot_disk_block_size_bytes": {
            "prompt": False,
        },
        "inputs.labels": {
            "prompt": False,
        },
    }


def test_bundled_object_storage_uses_wizard_profile() -> None:
    loaded = load_component_sources(
        explicit=Path(__file__).resolve().parents[1] / "component_sources.yaml"
    )
    module = next(item for item in loaded.tf_modules if item.module == "object-storage")

    assert module.wizard_fields == {
        "inputs.versioning_policy": {
            "sources": [
                {
                    "source": "static",
                    "values": ["DISABLED", "ENABLED", "SUSPENDED"],
                }
            ]
        },
        "inputs.object_audit_logging": {
            "sources": [
                {
                    "source": "static",
                    "values": ["NONE", "MUTATE_ONLY", "ALL"],
                }
            ]
        },
    }


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
    wireguard = next(item for item in loaded.tf_modules if item.module == "wireguard-gw")
    ssh = next(item for item in loaded.tf_modules if item.module == "ssh-jumphost")
    assert wireguard.source == "../../platform-infra/modules/wireguard-gw"
    assert ssh.source == "../../platform-infra/modules/ssh-jumphost"
    assert wireguard.metadata_source == str(
        (
            Path(__file__).resolve().parents[3]
            / "platform-infra/modules/wireguard-gw"
        ).resolve()
    )
    assert ssh.metadata_source == str(
        (Path(__file__).resolve().parents[3] / "platform-infra/modules/ssh-jumphost").resolve()
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
    loaded = load_component_sources(
        explicit=Path(__file__).resolve().parents[1] / "component_sources.yaml"
    )
    admin_ssh = loaded.shared.get("admin_ssh")
    assert isinstance(admin_ssh, dict)
    assert "public_key" not in admin_ssh
    for module_id in ("vm", "wireguard-gw", "ssh-jumphost"):
        module = next(item for item in loaded.tf_modules if item.module == module_id)
        default_targets = {default.target_path for default in module.defaults}
        assert "inputs.ssh_user_name" in default_targets
        assert "inputs.ssh_public_key" not in default_targets


def test_wireguard_gw_cloud_init_enforces_key_only_admin_access() -> None:
    template_path = (
        Path(__file__).resolve().parents[3]
        / "platform-infra"
        / "modules"
        / "wireguard-gw"
        / "wireguard-cloud-init.tftpl"
    )
    template = template_path.read_text(encoding="utf-8")

    assert "sudo: ALL=(ALL) NOPASSWD:ALL" not in template
    assert "/etc/sudoers.d/90-${ssh_user_name}" in template
    assert "${ssh_user_name} ALL=(ALL) NOPASSWD:ALL" in template
    assert "PasswordAuthentication no" in template
    assert "AuthenticationMethods publickey" in template
    assert "AllowTcpForwarding no" in template
    assert "fail2ban" in template
    assert 'run(["sshd", "-t"])' in template


def test_load_component_sources_explicit_missing_file_raises(tmp_path: Path) -> None:
    missing = tmp_path / "missing-explicit.yaml"
    with pytest.raises(ValueError, match="Component sources file not found"):
        load_component_sources(explicit=missing)


def test_load_component_sources_rejects_unsupported_config_bindings_field(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    sources_file = tmp_path / "component_sources.yaml"
    _write_catalog_file(
        sources_file,
        _catalog(
            cli={
                "flux": {
                    "version": "v2.8.0",
                }
            },
            infra={
                "wireguard-gw": {
                    "source": {
                        "portable": (
                            "git::https://github.com/example/infra.git//modules/"
                            "wireguard-gw?ref=v1.2.3"
                        ),
                        "local": "platform-infra/modules/wireguard-gw",
                    },
                    "config_bindings": {
                        "inputs.ssh_user_name": "shared.admin_ssh.user_name",
                    },
                }
            },
        ),
    )

    monkeypatch.setenv("NEBIUS_CXCLI_COMPONENT_SOURCES_FILE", str(sources_file))
    set_component_sources_file_override(None)
    reset_component_sources_cache()

    with pytest.raises(ValueError, match="has unsupported field\\(s\\): config_bindings"):
        load_component_sources()


def test_load_component_sources_rejects_invalid_flux_version(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    sources_file = tmp_path / "component_sources.yaml"
    _write_catalog_file(
        sources_file,
        _catalog(
            cli={
                "flux": {
                    "version": "latest",
                }
            }
        ),
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
    _write_catalog_file(
        sources_file,
        _catalog(
            cli={
                "terraform": {
                    "version": "latest",
                }
            }
        ),
    )

    monkeypatch.setenv("NEBIUS_CXCLI_COMPONENT_SOURCES_FILE", str(sources_file))
    set_component_sources_file_override(None)
    reset_component_sources_cache()

    with pytest.raises(ValueError, match="cli\\.terraform\\.version must be a semantic version"):
        load_component_sources()


def test_load_component_sources_parses_mk8s_gpu_cli_settings(tmp_path: Path) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    _write_catalog_file(
        sources_file,
        _catalog(
            infra={
                "mk8s": {
                    "source": {
                        "portable": "git::https://example.invalid/modules/mk8s?ref=main",
                        "local": "../../platform-infra/modules/mk8s",
                    },
                    "ui": {"enabled": True},
                    "cli": {
                        "gpu": {
                            "default_stack_source": "nebius_image",
                            "image_preferences": {
                                "preferred_gpu_stack_presets": ["cuda13.0", "cuda12.8"],
                                "preferred_os": ["ubuntu24.04", "ubuntu22.04"],
                            },
                            "validations": {
                                "operator_readiness": {
                                    "enabled_by_default": True,
                                    "timeout": "20m",
                                },
                                "gpu_visibility": {
                                    "enabled_by_default": True,
                                    "namespace": "gpu-validation",
                                    "image": "nvcr.io/example/vectoradd:latest",
                                    "timeout": "10m",
                                    "max_nodes": 4,
                                },
                                "nccl": {
                                    "enabled_by_default": True,
                                    "chart_component_id": "nccl-test",
                                    "timeout": "45m",
                                    "training_operator_manifest": "github.com/example/training-operator?ref=v1.0.0",
                                    "training_operator_namespace": "kubeflow",
                                    "average_bus_bandwidth_threshold_gbps": 300,
                                    "max_nodes": 6,
                                },
                            },
                        },
                        "observability": {
                            "primary_agent": {
                                "kind": "kubernetes_agent",
                                "chart_component_id": "nebius-observability-agent",
                                "validation": _kubernetes_agent_validation_enabled(),
                                "logs": {
                                    "default_enabled": True,
                                    "collect_agent_logs": False,
                                    "excluded_namespaces": ["kube-system"],
                                },
                                "metrics": {
                                    "default_enabled": True,
                                    "collect_agent_metrics": False,
                                    "collect_k8s_cluster_metrics": True,
                                    "excluded_namespaces": ["kube-system"],
                                },
                                "traces": {
                                    "default_enabled": True,
                                },
                            },
                        },
                    },
                }
            },
            apps={
                "gpu-operator": {
                    "source": _portable_chart_source(
                        repo="https://example.invalid/charts",
                        chart="gpu-operator",
                        version="1.0.0",
                    ),
                    "release": {
                        "namespace": "gpu-system",
                        "name": "gpu-operator",
                    },
                    "ui": {"enabled": False},
                    "cli": {
                        "mk8s_gpu_policy": {
                            "role": "gpu_operator",
                            "rules": [
                                {
                                    "auto_enable": True,
                                },
                                {
                                    "gpu_stack_source": "nebius_image",
                                    "defaults": {
                                        "values.driver.enabled": False,
                                        "values.toolkit.enabled": False,
                                        "values.driver.nvidiaDriverCRD.enabled": False,
                                    },
                                },
                                {
                                    "gpu_cluster_enabled": True,
                                    "defaults": {
                                        "values.nfd.enabled": False,
                                    },
                                },
                                {
                                    "gpu_stack_source": "operator_managed",
                                    "match_platforms": [
                                        "gpu-b200-sxm",
                                        "gpu-b200-sxm-a",
                                    ],
                                    "defaults": {
                                        "values.nfd.enabled": False,
                                    },
                                },
                                {
                                    "gpu_stack_source": "operator_managed",
                                    "defaults": {
                                        "values.driver.enabled": True,
                                        "values.toolkit.enabled": True,
                                        "values.driver.nvidiaDriverCRD.enabled": False,
                                    },
                                },
                            ],
                            "install_after": ["network-op"],
                        },
                        "observability": {
                            "metric_targets": [
                                {
                                    "job_name": "cxcli-nvidia-dcgm-exporter",
                                    "discovery": {
                                        "kind": "prometheus_annotations",
                                        "service_name": "nvidia-dcgm-exporter",
                                        "port": 9400,
                                    },
                                    "managed_gpu_node_policy": {
                                        "labels": {
                                            "nvidia.com/gpu.deploy.operands": "true",
                                            "nvidia.com/gpu.deploy.dcgm-exporter": "true",
                                        },
                                        "selector": {
                                            "nebius.com/gpu": "true",
                                        },
                                        "stack_sources": [
                                            "nebius_image",
                                        ],
                                    },
                                }
                            ]
                        },
                    },
                },
                "network-op": {
                    "source": _portable_chart_source(
                        repo="https://example.invalid/charts",
                        chart="network-operator",
                        version="1.0.0",
                    ),
                    "release": {
                        "namespace": "network-system",
                        "name": "network-operator",
                    },
                    "ui": {"enabled": False},
                    "cli": {
                        "mk8s_gpu_policy": {
                            "role": "network_operator",
                            "default_sets": {
                                "network_operator_nfd": {
                                    "values.nfd.enabled": True,
                                    "values.nfd.deployNodeFeatureRules": True,
                                },
                                "driverful_infiniband_node_selection": {
                                    "values.nodeAffinity": {
                                        "requiredDuringSchedulingIgnoredDuringExecution": {
                                            "nodeSelectorTerms": [
                                                {
                                                    "matchExpressions": [
                                                        {
                                                            "key": "feature.node.kubernetes.io/pci-15b3.present",
                                                            "operator": "In",
                                                            "values": ["true"],
                                                        }
                                                    ]
                                                }
                                            ]
                                        }
                                    }
                                },
                            },
                            "post_render_patch_sets": {
                                "rdma_shared_device_plugin": [
                                    {
                                        "target": {
                                            "group": "mellanox.com",
                                            "version": "v1alpha1",
                                            "kind": "NicClusterPolicy",
                                            "name": "nic-cluster-policy",
                                        },
                                        "patch": {
                                            "apiVersion": "mellanox.com/v1alpha1",
                                            "kind": "NicClusterPolicy",
                                            "metadata": {"name": "nic-cluster-policy"},
                                            "spec": {
                                                "rdmaSharedDevicePlugin": {
                                                    "image": "k8s-rdma-shared-dev-plugin",
                                                    "repository": "nvcr.io/nvidia/mellanox",
                                                    "version": "network-operator-v{chart_version}",
                                                    "config": (
                                                        '{"periodicUpdateInterval": 0, '
                                                        '"configList":[]}'
                                                    ),
                                                }
                                            },
                                        },
                                    }
                                ]
                            },
                            "rules": [
                                {
                                    "gpu_cluster_enabled": True,
                                    "auto_enable": True,
                                    "defaults_from": ["network_operator_nfd"],
                                },
                                {
                                    "gpu_stack_source": "operator_managed",
                                    "match_platforms": ["gpu-b200-sxm"],
                                    "auto_enable": True,
                                    "defaults_from": ["network_operator_nfd"],
                                },
                                {
                                    "gpu_stack_source": "nebius_image",
                                    "gpu_cluster_enabled": True,
                                    "defaults_from": ["driverful_infiniband_node_selection"],
                                    "post_render_patches_from": ["rdma_shared_device_plugin"],
                                },
                            ],
                        }
                    },
                },
                "nccl-test": {
                    "source": {
                        "local": {
                            "path": "../../helm-charts/nccl-test",
                        }
                    },
                    "release": {
                        "namespace": "nccl-test",
                        "name": "nccl-test",
                    },
                    "ui": {
                        "enabled": False,
                        "selectable": False,
                    },
                    "cli": {
                        "mk8s_gpu_policy": {
                            "rules": [
                                {
                                    "match_platforms": ["gpu-h100-sxm"],
                                    "defaults": {
                                        "values.image.repository": "registry.example/nccl",
                                        "values.image.tag": "latest",
                                    },
                                }
                            ]
                        }
                    },
                },
            },
        ),
    )

    loaded = load_component_sources(explicit=sources_file)
    mk8s = next(module for module in loaded.tf_modules if module.module == "mk8s")
    gpu_operator = next(chart for chart in loaded.helm_charts if chart.name == "gpu-operator")
    network_operator = next(chart for chart in loaded.helm_charts if chart.name == "network-op")

    assert mk8s.mk8s_gpu.default_stack_source == "nebius_image"
    assert mk8s.mk8s_gpu.image_preferences.preferred_gpu_stack_presets == (
        "cuda13.0",
        "cuda12.8",
    )
    assert network_operator.mk8s_gpu.role == "network_operator"
    assert [item.name for item in network_operator.mk8s_gpu.default_sets] == [
        "network_operator_nfd",
        "driverful_infiniband_node_selection",
    ]
    assert [item.target_path for item in network_operator.mk8s_gpu.default_sets[0].defaults] == [
        "values.nfd.enabled",
        "values.nfd.deployNodeFeatureRules",
    ]
    assert [item.target_path for item in network_operator.mk8s_gpu.default_sets[1].defaults] == [
        "values.nodeAffinity",
    ]
    assert [item.name for item in network_operator.mk8s_gpu.post_render_patch_sets] == [
        "rdma_shared_device_plugin",
    ]
    assert network_operator.mk8s_gpu.rules[0].gpu_cluster_enabled is True
    assert network_operator.mk8s_gpu.rules[0].auto_enable is True
    assert network_operator.mk8s_gpu.rules[0].defaults_from == ("network_operator_nfd",)
    assert network_operator.mk8s_gpu.rules[1].gpu_stack_source == "operator_managed"
    assert network_operator.mk8s_gpu.rules[1].match_platforms == ("gpu-b200-sxm",)
    assert network_operator.mk8s_gpu.rules[1].auto_enable is True
    assert network_operator.mk8s_gpu.rules[1].defaults_from == ("network_operator_nfd",)
    assert network_operator.mk8s_gpu.rules[2].gpu_stack_source == "nebius_image"
    assert network_operator.mk8s_gpu.rules[2].gpu_cluster_enabled is True
    assert network_operator.mk8s_gpu.rules[2].defaults_from == (
        "driverful_infiniband_node_selection",
    )
    assert network_operator.mk8s_gpu.rules[2].post_render_patches_from == (
        "rdma_shared_device_plugin",
    )
    assert (
        network_operator.mk8s_gpu.post_render_patch_sets[0].patches[0].target.kind
        == "NicClusterPolicy"
    )
    patch_text = network_operator.mk8s_gpu.post_render_patch_sets[0].patches[0].patch
    assert "kind: NicClusterPolicy" in patch_text
    assert "image: k8s-rdma-shared-dev-plugin" in patch_text
    assert "version: network-operator-v1.0.0" in patch_text
    assert '"periodicUpdateInterval": 0' in patch_text
    gpu_defaults_rule = next(
        rule
        for rule in gpu_operator.mk8s_gpu.rules
        if rule.gpu_stack_source == "nebius_image" and rule.defaults
    )
    assert [item.target_path for item in gpu_defaults_rule.defaults] == [
        "values.driver.enabled",
        "values.toolkit.enabled",
        "values.driver.nvidiaDriverCRD.enabled",
    ]
    gpu_cluster_nfd_rule = next(
        rule
        for rule in gpu_operator.mk8s_gpu.rules
        if rule.gpu_cluster_enabled is True and rule.defaults
    )
    assert [item.target_path for item in gpu_cluster_nfd_rule.defaults] == [
        "values.nfd.enabled",
    ]
    operator_managed_driver_rule = next(
        rule
        for rule in gpu_operator.mk8s_gpu.rules
        if rule.gpu_stack_source == "operator_managed"
        and not rule.match_platforms
        and rule.defaults
        and {item.target_path for item in rule.defaults}
        >= {
            "values.driver.enabled",
            "values.toolkit.enabled",
            "values.driver.nvidiaDriverCRD.enabled",
        }
    )
    assert {item.target_path: item.value for item in operator_managed_driver_rule.defaults} == {
        "values.driver.enabled": True,
        "values.toolkit.enabled": True,
        "values.driver.nvidiaDriverCRD.enabled": False,
    }
    operator_managed_b200_nfd_rule = next(
        rule
        for rule in gpu_operator.mk8s_gpu.rules
        if rule.gpu_stack_source == "operator_managed"
        and rule.match_platforms == ("gpu-b200-sxm", "gpu-b200-sxm-a")
    )
    assert [item.target_path for item in operator_managed_b200_nfd_rule.defaults] == [
        "values.nfd.enabled",
    ]
    assert mk8s.mk8s_gpu.validations.operator_readiness.timeout == "20m"
    assert mk8s.mk8s_gpu.validations.gpu_visibility.namespace == "gpu-validation"
    assert mk8s.mk8s_gpu.validations.gpu_visibility.max_nodes == 4
    assert mk8s.mk8s_gpu.validations.nccl.chart_component_id == "nccl-test"
    assert mk8s.mk8s_gpu.validations.nccl.max_nodes == 6
    assert mk8s.observability.mode == "kubernetes_agent"
    assert mk8s.observability.chart_component_id == "nebius-observability-agent"
    assert mk8s.observability.logs.excluded_namespaces == ("kube-system",)
    assert mk8s.observability.metrics.excluded_namespaces == ("kube-system",)
    assert mk8s.observability.validation.enabled is True
    assert mk8s.observability.validation.helmrelease_ready_condition == "Ready"
    assert mk8s.observability.validation.signal_value_paths["metrics"] == (
        "spec.values.config.metrics.enabled"
    )
    assert mk8s.observability.validation.cluster_metric_targets_path == (
        "spec.values.config.metrics.additionalTargets"
    )
    assert mk8s.observability.validation.daemonset_name == "o11y-agent"
    assert mk8s.observability.validation.pod_failure_sample_limit == 5
    assert mk8s.observability.validation.trace_otlp_service.port == 4317
    assert mk8s.observability.validation.trace_otlp_service.endpoint_slice_check_limit == 5
    assert gpu_operator.observability.metric_targets[0].job_name == "cxcli-nvidia-dcgm-exporter"
    assert gpu_operator.observability.metric_targets[0].discovery == "prometheus_annotations"
    assert dict(gpu_operator.observability.metric_targets[0].required_gpu_node_labels) == {
        "nvidia.com/gpu.deploy.operands": "true",
        "nvidia.com/gpu.deploy.dcgm-exporter": "true",
    }
    assert dict(gpu_operator.observability.metric_targets[0].required_gpu_node_selector) == {
        "nebius.com/gpu": "true",
    }
    assert gpu_operator.observability.metric_targets[0].required_gpu_node_label_stack_sources == (
        "nebius_image",
    )


def test_load_component_sources_parses_vm_cli_settings(tmp_path: Path) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    _write_catalog_file(
        sources_file,
        _catalog(
            infra={
                "vm": {
                    "source": {
                        "portable": "git::https://example.invalid/modules/vm?ref=main",
                        "local": "../../platform-infra/modules/vm",
                    },
                    "ui": {"enabled": False},
                    "cli": {
                        "observability": {
                            "primary_agent": {
                                "kind": "monitoring_agent",
                                "metrics": {
                                    "default_enabled": True,
                                },
                                "logs": {
                                    "default_enabled": False,
                                    "systemd_units": ["sshd.service"],
                                },
                            },
                        },
                    },
                }
            },
        ),
    )

    loaded = load_component_sources(explicit=sources_file)
    vm = next(module for module in loaded.tf_modules if module.module == "vm")

    assert vm.observability.mode == "monitoring_agent"
    assert vm.observability.metrics.enabled_by_default is True
    assert vm.observability.logs.enabled_by_default is False
    assert vm.observability.logs.systemd_units == ("sshd.service",)


def test_load_component_sources_rejects_vm_image_preferences(tmp_path: Path) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    _write_catalog_file(
        sources_file,
        _catalog(
            infra={
                "vm": {
                    "source": {
                        "portable": "git::https://example.invalid/modules/vm?ref=main",
                        "local": "../../platform-infra/modules/vm",
                    },
                    "cli": {
                        "image_preferences": {
                            "preferred_cpu_image_families": ["ubuntu24.04-driverless"],
                        },
                    },
                }
            },
        ),
    )

    with pytest.raises(
        ValueError,
        match=r"components\.infra\.vm\.cli has unsupported field\(s\): image_preferences",
    ):
        load_component_sources(explicit=sources_file)


def test_load_component_sources_rejects_vm_public_ingest_observability_settings(
    tmp_path: Path,
) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    _write_catalog_file(
        sources_file,
        _catalog(
            infra={
                "vm": {
                    "source": {
                        "portable": "git::https://example.invalid/modules/vm?ref=main",
                        "local": "../../platform-infra/modules/vm",
                    },
                    "ui": {"enabled": False},
                    "cli": {
                        "observability": {
                            "primary_agent": {
                                "kind": "monitoring_agent",
                            },
                            "public_ingest": {
                                "default_enabled": True,
                            },
                        },
                    },
                }
            },
        ),
    )

    with pytest.raises(
        ValueError,
        match=r"components\.infra\.vm\.cli\.observability has unsupported field\(s\): "
        r"public_ingest",
    ):
        load_component_sources(explicit=sources_file)


def test_load_component_sources_rejects_top_level_infra_observability_signals(
    tmp_path: Path,
) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    _write_catalog_file(
        sources_file,
        _catalog(
            infra={
                "vm": {
                    "source": {
                        "portable": "git::https://example.invalid/modules/vm?ref=main",
                        "local": "../../platform-infra/modules/vm",
                    },
                    "ui": {"enabled": False},
                    "cli": {
                        "observability": {
                            "primary_agent": {
                                "kind": "monitoring_agent",
                            },
                            "logs": {
                                "default_enabled": False,
                            },
                        },
                    },
                }
            },
        ),
    )

    with pytest.raises(
        ValueError,
        match=r"components\.infra\.vm\.cli\.observability has unsupported field\(s\): logs",
    ):
        load_component_sources(explicit=sources_file)


def test_load_component_sources_resolves_local_nccl_chart_source(tmp_path: Path) -> None:
    chart_dir = tmp_path / "helm-charts" / "nccl-test"
    chart_dir.mkdir(parents=True)
    (chart_dir / "Chart.yaml").write_text(
        "apiVersion: v2\nname: nccl-test\nversion: 0.1.0\n",
        encoding="utf-8",
    )
    (chart_dir / "values.yaml").write_text("image: {}\n", encoding="utf-8")
    (chart_dir / "templates").mkdir()
    sources_file = tmp_path / "component_sources.yaml"
    _write_catalog_file(
        sources_file,
        _catalog(
            infra={
                "mk8s": {
                    "source": {
                        "portable": "git::https://example.invalid/modules/mk8s?ref=main",
                        "local": "../../platform-infra/modules/mk8s",
                    },
                    "ui": {"enabled": True},
                    "cli": {
                        "gpu": {
                            "validations": {
                                "nccl": {
                                    "enabled_by_default": True,
                                    "chart_component_id": "nccl-test",
                                    "timeout": "45m",
                                    "training_operator_manifest": "github.com/example/training-operator?ref=v1.0.0",
                                    "training_operator_namespace": "kubeflow",
                                }
                            }
                        }
                    },
                }
            },
            apps={
                "nccl-test": {
                    "source": {
                        "local": {
                            "path": "./helm-charts/nccl-test",
                        }
                    },
                    "release": {
                        "namespace": "nccl-test",
                        "name": "nccl-test",
                    },
                    "ui": {
                        "enabled": False,
                        "selectable": False,
                    },
                }
            },
        ),
    )

    loaded = load_component_sources(explicit=sources_file, source_profile=SourceProfile.LOCAL)
    mk8s = next(module for module in loaded.tf_modules if module.module == "mk8s")
    nccl_chart = next(chart for chart in loaded.helm_charts if chart.name == "nccl-test")

    assert mk8s.mk8s_gpu.validations.nccl.chart_component_id == "nccl-test"
    assert nccl_chart.path == str(chart_dir.resolve())


def test_load_component_sources_rejects_invalid_mk8s_gpu_app_role_value(tmp_path: Path) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    _write_catalog_file(
        sources_file,
        _catalog(
            apps={
                "gpu-operator": {
                    "source": _portable_chart_source(
                        repo="https://example.invalid/charts",
                        chart="gpu-operator",
                        version="1.0.0",
                    ),
                    "release": {
                        "namespace": "gpu-system",
                        "name": "gpu-operator",
                    },
                    "ui": {"enabled": False},
                    "cli": {
                        "mk8s_gpu_policy": {
                            "role": "device_plugin",
                        }
                    },
                }
            },
        ),
    )

    with pytest.raises(
        ValueError,
        match="components\\.apps\\.gpu-operator\\.cli\\.mk8s_gpu_policy\\.role must be one of: gpu_operator, network_operator, health_checker",
    ):
        load_component_sources(explicit=sources_file)


def test_load_component_sources_rejects_legacy_mk8s_gpu_app_cli_key(tmp_path: Path) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    _write_catalog_file(
        sources_file,
        _catalog(
            apps={
                "gpu-operator": {
                    "source": _portable_chart_source(
                        repo="https://example.invalid/charts",
                        chart="gpu-operator",
                        version="1.0.0",
                    ),
                    "release": {
                        "namespace": "gpu-system",
                        "name": "gpu-operator",
                    },
                    "ui": {"enabled": False},
                    "cli": {
                        "mk8s_gpu": {
                            "role": "gpu_operator",
                        }
                    },
                }
            },
        ),
    )

    with pytest.raises(
        ValueError,
        match="components\\.apps\\.gpu-operator\\.cli has unsupported field\\(s\\): mk8s_gpu",
    ):
        load_component_sources(explicit=sources_file)


def test_load_component_sources_rejects_empty_mk8s_gpu_rule(tmp_path: Path) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    _write_catalog_file(
        sources_file,
        _catalog(
            apps={
                "gpu-operator": {
                    "source": _portable_chart_source(
                        repo="https://example.invalid/charts",
                        chart="gpu-operator",
                        version="1.0.0",
                    ),
                    "release": {
                        "namespace": "gpu-system",
                        "name": "gpu-operator",
                    },
                    "ui": {"enabled": False},
                    "cli": {
                        "mk8s_gpu_policy": {
                            "role": "gpu_operator",
                            "rules": [
                                {
                                    "gpu_stack_source": "operator_managed",
                                }
                            ],
                        }
                    },
                }
            },
        ),
    )

    with pytest.raises(
        ValueError,
        match="components\\.apps\\.gpu-operator\\.cli\\.mk8s_gpu_policy\\.rules\\[0\\] must set auto_enable: true, defaults/defaults_from, and/or post_render_patches/post_render_patches_from",
    ):
        load_component_sources(explicit=sources_file)


def test_load_component_sources_rejects_unknown_mk8s_gpu_default_set_reference(
    tmp_path: Path,
) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    _write_catalog_file(
        sources_file,
        _catalog(
            apps={
                "network-op": {
                    "source": _portable_chart_source(
                        repo="https://example.invalid/charts",
                        chart="network-operator",
                        version="1.0.0",
                    ),
                    "release": {
                        "namespace": "network-system",
                        "name": "network-operator",
                    },
                    "ui": {"enabled": False},
                    "cli": {
                        "mk8s_gpu_policy": {
                            "role": "network_operator",
                            "rules": [
                                {
                                    "gpu_stack_source": "nebius_image",
                                    "defaults_from": ["missing-set"],
                                }
                            ],
                        }
                    },
                }
            },
        ),
    )

    with pytest.raises(
        ValueError,
        match="components\\.apps\\.network-op\\.cli\\.mk8s_gpu_policy\\.rules\\[0\\]\\.defaults_from references unknown default_set\\(s\\): missing-set",
    ):
        load_component_sources(explicit=sources_file)


def test_load_component_sources_rejects_unknown_mk8s_gpu_post_render_patch_set_reference(
    tmp_path: Path,
) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    _write_catalog_file(
        sources_file,
        _catalog(
            apps={
                "network-op": {
                    "source": _portable_chart_source(
                        repo="https://example.invalid/charts",
                        chart="network-operator",
                        version="1.0.0",
                    ),
                    "release": {
                        "namespace": "network-system",
                        "name": "network-operator",
                    },
                    "ui": {"enabled": False},
                    "cli": {
                        "mk8s_gpu_policy": {
                            "role": "network_operator",
                            "rules": [
                                {
                                    "gpu_stack_source": "nebius_image",
                                    "post_render_patches_from": ["missing-patch-set"],
                                }
                            ],
                        }
                    },
                }
            },
        ),
    )

    with pytest.raises(
        ValueError,
        match="components\\.apps\\.network-op\\.cli\\.mk8s_gpu_policy\\.rules\\[0\\]\\.post_render_patches_from references unknown post_render_patch_set\\(s\\): missing-patch-set",
    ):
        load_component_sources(explicit=sources_file)


def test_load_component_sources_rejects_chart_version_template_without_chart_version(
    tmp_path: Path,
) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    _write_catalog_file(
        sources_file,
        _catalog(
            apps={
                "network-op": {
                    "source": _portable_chart_source(
                        repo="https://example.invalid/charts",
                        chart="network-operator",
                    ),
                    "release": {
                        "namespace": "network-system",
                        "name": "network-operator",
                    },
                    "ui": {"enabled": False},
                    "cli": {
                        "mk8s_gpu_policy": {
                            "role": "network_operator",
                            "post_render_patch_sets": {
                                "rdma": [
                                    {
                                        "target": {
                                            "group": "mellanox.com",
                                            "version": "v1alpha1",
                                            "kind": "NicClusterPolicy",
                                            "name": "nic-cluster-policy",
                                        },
                                        "patch": {
                                            "apiVersion": "mellanox.com/v1alpha1",
                                            "kind": "NicClusterPolicy",
                                            "spec": {
                                                "rdmaSharedDevicePlugin": {
                                                    "version": ("network-operator-v{chart_version}")
                                                }
                                            },
                                        },
                                    }
                                ]
                            },
                            "rules": [
                                {
                                    "gpu_stack_source": "nebius_image",
                                    "post_render_patches_from": ["rdma"],
                                }
                            ],
                        }
                    },
                }
            },
        ),
    )

    with pytest.raises(
        ValueError,
        match=(
            r"components\.apps\.network-op\.cli\.mk8s_gpu_policy"
            r"\.post_render_patch_sets\.rdma\[0\]\.patch references "
            r"\{chart_version\} but source\.portable\.version is empty"
        ),
    ):
        load_component_sources(explicit=sources_file)


def test_validate_sources_resolves_relative_local_module_path_from_component_sources_file(
    monkeypatch, tmp_path: Path
) -> None:
    catalog_dir = tmp_path / "catalog"
    module_dir = catalog_dir / "modules" / "demo-module"
    module_dir.mkdir(parents=True, exist_ok=True)
    (module_dir / "main.tf").write_text('output "demo" { value = var.name }\n', encoding="utf-8")
    (module_dir / "variables.tf").write_text(
        'variable "name" { type = string }\n', encoding="utf-8"
    )
    (module_dir / "outputs.tf").write_text(
        "\n".join(
            [
                'output "cluster_id" { value = "cluster-123" }',
                'output "cluster_ca_certificate" { value = "ca-cert" }',
                'output "instance_id" { value = "instance-123" }',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (module_dir / "versions.tf").write_text(
        "\n".join(
            [
                "terraform {",
                '  required_version = ">= 1.10.0, < 2.0.0"',
                "  required_providers {",
                "    nebius = {",
                '      source = "terraform-provider.storage.eu-north1.nebius.cloud/nebius/nebius"',
                '      version = ">= 0.5.55, < 0.6.0"',
                "    }",
                "  }",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (module_dir / "README.md").write_text("# demo-module\n", encoding="utf-8")
    example_dir = module_dir / "examples" / "minimal"
    example_dir.mkdir(parents=True, exist_ok=True)
    (example_dir / "main.tf").write_text('module "demo" {}\n', encoding="utf-8")

    sources_file = catalog_dir / "component_sources.yaml"
    _write_catalog_file(
        sources_file,
        _catalog(
            infra={
                "demo-module": {
                    "source": {
                        "portable": "git::https://github.com/example/infra.git//modules/demo-module?ref=v1.2.3",
                        "local": "./modules/demo-module",
                    },
                    "ui": {
                        "enabled": True,
                    },
                }
            }
        ),
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
    (module_dir / "variables.tf").write_text(
        'variable "name" { type = string }\n', encoding="utf-8"
    )
    (module_dir / "outputs.tf").write_text(
        "\n".join(
            [
                'output "cluster_id" { value = "cluster-123" }',
                'output "cluster_ca_certificate" { value = "ca-cert" }',
                'output "instance_id" { value = "instance-123" }',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (module_dir / "versions.tf").write_text(
        "\n".join(
            [
                "terraform {",
                '  required_version = ">= 1.10.0, < 2.0.0"',
                "  required_providers {",
                "    nebius = {",
                '      source = "terraform-provider.storage.eu-north1.nebius.cloud/nebius/nebius"',
                '      version = ">= 0.5.55, < 0.6.0"',
                "    }",
                "  }",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (module_dir / "README.md").write_text("# demo-module\n", encoding="utf-8")
    example_dir = module_dir / "examples" / "minimal"
    example_dir.mkdir(parents=True, exist_ok=True)
    (example_dir / "main.tf").write_text('module "demo" {}\n', encoding="utf-8")

    sources_file = tmp_path / "component_sources.yaml"
    _write_catalog_file(
        sources_file,
        _catalog(
            infra={
                "demo-module": {
                    "source": {
                        "portable": "git::https://github.com/example/infra.git//modules/demo-module?ref=v1.2.3",
                        "local": str(module_dir),
                    },
                    "ui": {
                        "enabled": True,
                    },
                }
            }
        ),
    )

    monkeypatch.chdir(tmp_path)
    set_component_sources_file_override(sources_file)
    set_component_sources_profile_override(SourceProfile.LOCAL)
    reset_component_sources_cache()

    resolved_path, issues, warnings = _validate_component_sources_registry()

    assert resolved_path == sources_file
    assert issues == []
    assert warnings == []


def test_validate_sources_reports_module_contract_issues_for_missing_versions_and_provider_block(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module_dir = tmp_path / "demo-module"
    module_dir.mkdir(parents=True, exist_ok=True)
    (module_dir / "main.tf").write_text(
        "\n".join(
            [
                'provider "nebius" {}',
                'output "demo" {',
                "  value = var.name",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (module_dir / "variables.tf").write_text(
        'variable "name" { type = string }\n', encoding="utf-8"
    )

    sources_file = tmp_path / "component_sources.yaml"
    _write_catalog_file(
        sources_file,
        _catalog(
            infra={
                "demo-module": {
                    "source": {
                        "portable": "git::https://github.com/example/infra.git//modules/demo-module?ref=v1.2.3",
                        "local": str(module_dir),
                    },
                    "ui": {
                        "enabled": True,
                    },
                }
            }
        ),
    )

    monkeypatch.chdir(tmp_path)
    set_component_sources_file_override(sources_file)
    set_component_sources_profile_override(SourceProfile.LOCAL)
    reset_component_sources_cache()

    _resolved_path, issues, warnings = _validate_component_sources_registry()

    assert any("missing versions.tf" in issue for issue in issues)
    assert any("must not configure providers" in issue for issue in issues)
    assert any("missing README.md" in warning for warning in warnings)
    assert any("missing examples/" in warning for warning in warnings)


def test_validate_sources_reports_chart_contract_findings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    _write_catalog_file(
        sources_file,
        _catalog(
            apps={
                "gateway-helm": {
                    "source": _portable_chart_source(
                        repo="oci://docker.io/envoyproxy",
                        chart="gateway-helm",
                        version="1.4.2",
                    ),
                    "ui": {
                        "enabled": True,
                    },
                }
            }
        ),
    )

    monkeypatch.setattr(
        "nebius_cxcli.cli._resolve_helm_chart_validation_issues",
        lambda **_kwargs: (),
    )
    monkeypatch.setattr(
        "nebius_cxcli.cli.chart_cli_contract_findings",
        lambda **_kwargs: (
            ("materialized chart is missing Chart.yaml in /tmp/fake-chart",),
            (),
        ),
    )
    monkeypatch.chdir(tmp_path)
    set_component_sources_file_override(sources_file)
    reset_component_sources_cache()

    _resolved_path, issues, warnings = _validate_component_sources_registry()

    assert any("missing Chart.yaml" in issue for issue in issues)
    assert not any("missing README.md" in warning for warning in warnings)


def test_validate_sources_rejects_https_git_repo_module_source_without_git_prefix(
    monkeypatch, tmp_path: Path
) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    _write_catalog_file(
        sources_file,
        _catalog(
            infra={
                "demo-module": {
                    "source": {
                        "portable": "https://github.com/example/platform-modules.git//modules/demo?ref=v1.2.3",
                    },
                    "ui": {
                        "enabled": True,
                    },
                }
            }
        ),
    )

    monkeypatch.chdir(tmp_path)
    set_component_sources_file_override(sources_file)
    reset_component_sources_cache()

    _resolved_path, issues, warnings = _validate_component_sources_registry()

    assert warnings == []
    assert any(
        "is not supported as a plain HTTP(S) Terraform module source" in issue for issue in issues
    )
    assert any(
        "git::https://github.com/org/repo.git//modules/mk8s?ref=v1.2.3" in issue for issue in issues
    )


def test_validate_sources_rejects_registry_style_module_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    _write_catalog_file(
        sources_file,
        _catalog(
            infra={
                "demo-module": {
                    "source": {
                        "portable": "app.terraform.io/example/network/nebius",
                    },
                    "ui": {
                        "enabled": True,
                    },
                }
            }
        ),
    )

    monkeypatch.chdir(tmp_path)
    set_component_sources_file_override(sources_file)
    reset_component_sources_cache()

    _resolved_path, issues, warnings = _validate_component_sources_registry()

    assert warnings == []
    assert any(
        "module source 'app.terraform.io/example/network/nebius' is not supported" in issue
        for issue in issues
    )


def test_validate_sources_accepts_github_tree_chart_repo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sources_file = tmp_path / "component_sources.yaml"
    _write_catalog_file(
        sources_file,
        _catalog(
            apps={
                "n8n": {
                    "source": _portable_chart_source(
                        repo="https://github.com/example/charts/tree/main/charts/n8n",
                        chart="n8n",
                        version="1.2.3",
                    ),
                    "ui": {
                        "enabled": True,
                    },
                }
            }
        ),
    )

    class _FakeHelmClient:
        def show_chart(self, *, reference):  # type: ignore[no-untyped-def]
            assert reference.chart_repo == "https://github.com/example/charts/tree/main/charts/n8n"
            return {"name": "n8n", "version": "1.2.3"}

    monkeypatch.setattr("nebius_cxcli.cli.HelmClient", _FakeHelmClient)
    monkeypatch.setattr(
        "nebius_cxcli.cli.chart_cli_contract_findings",
        lambda **_kwargs: ((), ()),
    )
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
    _write_catalog_file(
        sources_file,
        _catalog(
            apps={
                "gateway-helm": {
                    "source": _portable_chart_source(
                        repo="oci://docker.io/envoyproxy",
                        chart="gateway-helm",
                        version="1.4.2",
                    ),
                    "ui": {
                        "enabled": True,
                    },
                }
            }
        ),
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
