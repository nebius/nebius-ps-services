from __future__ import annotations

from pathlib import Path

from nebius_cxcli.provider_options import ProviderOptionLookup


def test_provider_option_lookup_uses_plugin_for_unknown_provider(
    monkeypatch,
) -> None:
    def _plugin(**kwargs):
        if kwargs.get("provider") != "vendor_regions":
            return []
        return [{"value": "us-central1", "label": "US Central 1"}]

    monkeypatch.setenv(
        "NEBIUS_CXCLI_PROVIDER_OPTION_PLUGINS",
        "tests.test_provider_option_plugins:_plugin",
    )
    monkeypatch.setattr(
        "nebius_cxcli.provider_options._load_option_plugins",
        lambda _specs: (_plugin,),
    )

    lookup = ProviderOptionLookup()
    resolved = lookup.resolve(
        provider="vendor_regions",
        args={},
        payload={},
        field_path="client_info.nebius.region_id",
    )
    assert [choice.value for choice in resolved] == ["us-central1"]
    assert [choice.label for choice in resolved] == ["US Central 1"]


def test_provider_option_lookup_sdk_uses_shared_sdk_auth(monkeypatch) -> None:
    lookup = ProviderOptionLookup()
    captured: dict[str, object] = {}
    sdk = object()

    monkeypatch.setenv("NEBIUS_CXCLI_PROVIDER_SDK_CONFIG_FILE", "/tmp/provider-sdk-config.yaml")
    monkeypatch.setenv("NEBIUS_CXCLI_PROVIDER_AUTH_PROFILE", "dev")
    monkeypatch.setenv("NEBIUS_CXCLI_PROVIDER_AUTH_ENDPOINT", "api.example.invalid")
    monkeypatch.setattr(
        "nebius_cxcli.provider_options.init_nebius_sdk",
        lambda *, profile, endpoint, config_file, context: (
            captured.update(
                {
                    "profile": profile,
                    "endpoint": endpoint,
                    "config_file": config_file,
                    "context": context,
                }
            )
            or sdk
        ),
    )

    assert lookup._sdk_or_none() is sdk
    assert captured == {
        "profile": "dev",
        "endpoint": "api.example.invalid",
        "config_file": Path("/tmp/provider-sdk-config.yaml"),
        "context": "provider option lookup",
    }
