"""Causal regressions for the approved whole-source review repairs."""

import base64
import os

import pytest

from nebius_vpngw import cli
from nebius_vpngw.agent import strongswan_renderer
from nebius_vpngw.peer_parsers.importer import parse_text_document


def test_publication_never_clobbers_a_writer_at_the_publication_boundary(tmp_path, monkeypatch):
    path = tmp_path / "gateway.config.yaml"
    path.write_text("original\n")
    predecessor = cli._file_fingerprint(path)
    original_replace, original_link = os.replace, os.link

    def race(operation):
        def invoke(source, destination, *args, **kwargs):
            if destination == path:
                path.write_text("concurrent edit\n")
            return operation(source, destination, *args, **kwargs)

        return invoke

    monkeypatch.setattr(os, "replace", race(original_replace))
    monkeypatch.setattr(os, "link", race(original_link))
    with pytest.raises(OSError):
        cli._atomic_write_text(path, "candidate\n", expected_fingerprint=predecessor)
    assert path.read_text() == "concurrent edit\n"


def test_text_import_keeps_repeated_credentials_and_document_order():
    connection = parse_text_document(
        "psk: repeated-key\nremote public ip: 192.0.2.1\n"
        "crypto isakmp key repeated-key address 192.0.2.1\n"
        "psk: third-only-key\nremote public ip: 192.0.2.3\n"
    )
    assert [item["psk"] for item in connection["tunnels"]] == [
        "repeated-key",
        "repeated-key",
        "third-only-key",
    ]
    assert [item["remote_public_ip"] for item in connection["tunnels"]] == [
        "192.0.2.1",
        "192.0.2.1",
        "192.0.2.3",
    ]


@pytest.mark.parametrize(
    "secret", ['quote"backslash\\\n# }', "0snot-encoded", "0xdeadbeef", "秘密-key-123"]
)
def test_psk_render_preserves_literal_bytes_without_config_injection(sample_config, secret):
    sample_config["connections"][0]["tunnels"][0]["psk"] = secret
    rendered = {}
    strongswan_renderer.StrongSwanRenderer().render_and_apply(
        sample_config, activate=False, rendered_files=rendered
    )
    content = rendered[strongswan_renderer.SWANCTL_CONF]
    encoded = base64.b64encode(secret.encode("utf-8")).decode("ascii")
    assert f"    secret = 0s{encoded}\n" in content
    assert secret not in content
