from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from nebius_cxcli.component_sources import (
    load_component_sources,
    reset_component_sources_cache,
    resolve_component_sources_file,
    set_component_sources_file_override,
)


def _reset_sources_state() -> None:
    set_component_sources_file_override(None)
    reset_component_sources_cache()


def setup_function() -> None:
    _reset_sources_state()


def teardown_function() -> None:
    _reset_sources_state()


def _write_sources_file(path: Path, *, module_name: str) -> None:
    path.write_text(
        yaml.safe_dump(
            {
                "infra": {
                    "tf_modules": [
                        {
                            "module": module_name,
                            "source": f"platform-infra/modules/{module_name}",
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


def test_load_component_sources_reads_tf_modules_and_helm_entries(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    sources_file = tmp_path / "component-sources.yaml"
    sources_file.write_text(
        yaml.safe_dump(
            {
                "infra": {
                    "tf_modules": [
                        {
                            "module": "wireguard-jumphost",
                            "source": "platform-infra/modules/wireguard-jumphost",
                            "version": "1.2.3",
                            "group": "Network",
                            "enable": True,
                        }
                    ]
                },
                "apps": {
                    "helm_charts": [
                        {
                            "name": "gateway-helm",
                            "repo": "https://envoyproxy.github.io/gateway-helm",
                            "version": "1.4.2",
                            "namespace": "envoy-gateway-system",
                            "releasename": "envoy-gateway",
                            "group": "Platform",
                            "enable": True,
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
    assert loaded.tf_modules[0].module == "wireguard-jumphost"
    assert loaded.tf_modules[0].source == "platform-infra/modules/wireguard-jumphost"
    assert loaded.tf_modules[0].version == "1.2.3"
    assert loaded.tf_modules[0].group == "Network"
    assert loaded.tf_modules[0].enable is True

    assert loaded.helm_charts[0].name == "gateway-helm"
    assert loaded.helm_charts[0].repo == "https://envoyproxy.github.io/gateway-helm"
    assert loaded.helm_charts[0].namespace == "envoy-gateway-system"
    assert loaded.helm_charts[0].release_name == "envoy-gateway"
    assert loaded.helm_charts[0].group == "Platform"
    assert loaded.helm_charts[0].enable is True


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
    monkeypatch.setattr("nebius_cxcli.component_sources.sys.prefix", str(tmp_path))

    set_component_sources_file_override(None)
    reset_component_sources_cache()

    loaded = load_component_sources()
    assert loaded.tf_modules[0].module == "bundled-mod"


def test_load_component_sources_explicit_missing_file_raises(tmp_path: Path) -> None:
    missing = tmp_path / "missing-explicit.yaml"
    with pytest.raises(ValueError, match="Component sources file not found"):
        load_component_sources(explicit=missing)
