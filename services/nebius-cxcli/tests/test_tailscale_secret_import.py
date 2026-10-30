"""SDK-only import with immutable version recovery and no secret-bearing journal."""

import json
from contextlib import nullcontext
from types import SimpleNamespace as NS

import pytest

from nebius_cxcli import tailscale_secret_import as importer


@pytest.fixture
def sdk_case(tmp_path, monkeypatch):
    creates, payloads = [], {}
    operation = NS(id="operation-a", resource_id="version-a")

    def response(value):
        return NS(wait=lambda: value)

    def create(request, **kwargs):
        creates.append(request)
        payloads.update({entry.key: entry.string_value for entry in request.spec.payload})
        assert request.spec.set_primary is False
        return response(operation)

    def payload(request, **kwargs):
        assert request.version_id == "version-a" and request.secret_id == "secret-a"
        return response(
            NS(
                version_id="version-a",
                data=[
                    NS(key=k, string_value=v, check_presence=lambda field: field == "string_value")
                    for k, v in payloads.items()
                ],
            )
        )

    version = NS(metadata=NS(id="version-a", parent_id="secret-a"))
    metadata = NS(metadata=NS(parent_id="project-a", name="oauth"), spec=NS(primary_version_id=""))
    monkeypatch.setattr(importer, "sdk_context", lambda *a: nullcontext(NS()))
    monkeypatch.setattr(
        "nebius.api.nebius.mysterybox.v1.SecretServiceClient",
        lambda sdk: NS(get=lambda *a, **kw: response(metadata)),
    )
    monkeypatch.setattr(
        "nebius.api.nebius.mysterybox.v1.SecretVersionServiceClient",
        lambda sdk: NS(create=create, get=lambda *a, **kw: response(version)),
    )
    monkeypatch.setattr(
        "nebius.api.nebius.mysterybox.v1.PayloadServiceClient", lambda sdk: NS(get=payload)
    )
    monkeypatch.setattr(
        "nebius_cxcli.nebius_api_helpers.wait_nebius_operation", lambda *a, **kw: None
    )
    ref = {
        "project_id": "project-a",
        "secret_id": "secret-a",
        "secret_name": "oauth",
        "component_instance": "ts-oauth",
    }
    source = tmp_path / "source"
    source.write_text("fixture-original")
    source.chmod(0o600)
    return NS(ref=ref, source=source, creates=creates, payloads=payloads, base=tmp_path)


def test_import_uses_validated_bytes_and_recovers_without_source(sdk_case):
    c = sdk_case
    checked = []

    def check(client_id, value):
        checked.append((client_id, value))
        c.source.write_text("fixture-changed")

    assert (
        importer.import_pair(
            c.ref, client_id="client-a", secret_ref="file:source", base=c.base, validate_pair=check
        )
        == "version-a"
    )
    assert c.payloads["client_secret"] == "fixture-original"
    assert checked == [("client-a", "fixture-original")] * 2
    c.source.unlink()
    assert (
        importer.import_pair(
            c.ref,
            client_id="client-a",
            secret_ref="file:source",
            base=c.base,
            validate_pair=lambda *a: checked.append(a),
        )
        == "version-a"
    )
    assert len(c.creates) == 1 and checked[-1] == ("client-a", "fixture-original")
    journals = list((c.base / ".nebius-cxcli/tailscale-import").glob("*.json"))
    assert len(journals) == 1
    assert "fixture-original" not in journals[0].read_text()
    assert json.loads(journals[0].read_text())["version_id"] == "version-a"


def test_rejected_credential_never_submits_version(sdk_case):
    c = sdk_case

    def denied(*args):
        raise RuntimeError("denied")

    with pytest.raises(RuntimeError, match="denied"):
        importer.import_pair(
            c.ref, client_id="client-a", secret_ref="file:source", base=c.base, validate_pair=denied
        )
    assert not c.creates
