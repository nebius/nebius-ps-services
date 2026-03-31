from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import setuptools
import yaml


def _load_setup_module(monkeypatch) -> ModuleType:
    for env_name in (
        "NEBIUS_CXCLI_BUILD_COMPONENT_SOURCES_FILE",
        "NEBIUS_CXCLI_BUILD_RELEASE_REF",
        "RELEASE_REF",
        "NEBIUS_CXCLI_REF",
    ):
        monkeypatch.delenv(env_name, raising=False)
    monkeypatch.setattr(setuptools, "setup", lambda *args, **kwargs: None)
    setup_path = Path(__file__).resolve().parents[1] / "setup.py"
    spec = importlib.util.spec_from_file_location("nebius_cxcli_setup_test", setup_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_catalog(
    path: Path,
    portable_source: str,
    *,
    local_source: str | None = "../../platform-infra/modules/mk8s",
) -> None:
    path.write_text(
        yaml.safe_dump(
            {
                "infra": {
                    "tf_modules": [
                        {
                            "module": "mk8s",
                            "portable_source": portable_source,
                            "local_source": local_source,
                        }
                    ]
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def test_select_bundled_component_sources_prefers_portable_staged_catalog(monkeypatch, tmp_path: Path) -> None:
    module = _load_setup_module(monkeypatch)
    staged = tmp_path / "component_sources.yaml"
    _write_catalog(
        staged,
        "git::https://github.com/nebius/nebius-ps-services.git//platform-infra/modules/mk8s?ref=main",
    )

    selected = module._select_bundled_component_sources(tmp_path)

    assert selected == staged


def test_render_bundled_component_sources_rewrites_ref_from_build_env(
    monkeypatch, tmp_path: Path
) -> None:
    module = _load_setup_module(monkeypatch)
    catalog = tmp_path / "component_sources.yaml"
    _write_catalog(
        catalog,
        "git::https://github.com/nebius/nebius-ps-services.git//platform-infra/modules/mk8s?ref=main",
    )
    monkeypatch.setenv("NEBIUS_CXCLI_REF", "feature/test-portable-catalog")

    rendered = yaml.safe_load(module._render_bundled_component_sources(catalog))

    assert rendered["infra"]["tf_modules"][0]["portable_source"].endswith(
        "?ref=feature/test-portable-catalog"
    )
    assert "local_source" not in rendered["infra"]["tf_modules"][0]


def test_render_bundled_component_sources_prefers_release_ref_over_cli_ref(
    monkeypatch, tmp_path: Path
) -> None:
    module = _load_setup_module(monkeypatch)
    catalog = tmp_path / "component_sources.yaml"
    _write_catalog(
        catalog,
        "git::https://github.com/nebius/nebius-ps-services.git//platform-infra/modules/mk8s?ref=main",
    )
    monkeypatch.setenv("NEBIUS_CXCLI_REF", "feature/test-portable-catalog")
    monkeypatch.setenv("RELEASE_REF", "deadbeefcafebabe")

    rendered = yaml.safe_load(module._render_bundled_component_sources(catalog))

    assert rendered["infra"]["tf_modules"][0]["portable_source"].endswith("?ref=deadbeefcafebabe")


def test_select_bundled_component_sources_uses_root_catalog_by_default(
    monkeypatch, tmp_path: Path
) -> None:
    module = _load_setup_module(monkeypatch)
    staged = tmp_path / "component_sources.yaml"
    _write_catalog(staged, "git::https://github.com/example/infra.git//modules/mk8s?ref=v1.2.3")

    selected = module._select_bundled_component_sources(tmp_path)

    assert selected == staged


def test_select_bundled_component_sources_honors_explicit_build_override(
    monkeypatch, tmp_path: Path
) -> None:
    module = _load_setup_module(monkeypatch)
    override = tmp_path / "portable.yaml"
    _write_catalog(override, "git::https://github.com/example/infra.git//modules/mk8s?ref=v1.2.3")
    monkeypatch.setenv("NEBIUS_CXCLI_BUILD_COMPONENT_SOURCES_FILE", str(override))

    selected = module._select_bundled_component_sources(tmp_path)

    assert selected == override
