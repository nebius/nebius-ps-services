"""Original-source preservation and whole-archive privacy; synthetic inputs only."""

import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
import yaml

from nebius_vpngw.support_bundle import collector
from nebius_vpngw.support_bundle.addresses import AddressAliases, contains_public_address
from nebius_vpngw.support_bundle.source_yaml import SourceYaml, unsafe_yaml


def sanitized(raw: bytes) -> bytes:
    source = SourceYaml.read(raw)
    clean, _ = source.redact(AddressAliases())
    assert not unsafe_yaml(clean, source.literals)
    return clean


def test_exact_source_bytes_preserved_except_sensitive_values():
    raw = (
        "\ufeff# café; keep spacing\r\nunknown: {odd: 123}\r\n"
        "duplicate: first\r\nduplicate: second # keep\r\n"
        'psk: "synthetic-secret" # explanation\r\n'
        "remote_public_ip: 8.8.8.8\r\nlocal: 10.0.0.1\r\n"
        "extension: [unrecognized, values]\r\n"
    ).encode()
    assert sanitized(raw) == raw.replace(b"synthetic-secret", b"REDACTED_SECRET").replace(
        b"8.8.8.8", b"PUBLIC_IPV4_001"
    )


@pytest.mark.parametrize("style", ["|", "|+", "|-", ">", ">+", ">-"])
def test_multiline_secrets_preserve_header_lines_indentation_and_next_key(style):
    raw = f"psk: {style} # keep\r\n  synthetic-one\r\n  synthetic-two\r\nnext: keep\r\n".encode()
    expected = raw.replace(b"synthetic-one", b"REDACTED_SECRET").replace(
        b"synthetic-two", b"REDACTED_SECRET"
    )
    assert sanitized(raw) == expected
    assert yaml.safe_load(expected)["next"] == "keep"


def test_alias_merge_duplicates_and_unknown_fields_do_not_hide_secrets():
    raw = (
        b'value: &shared "synthetic-secret"\n'
        b"defaults: &defaults {psk: *shared, strange: keep}\n"
        b"connection: {<<: *defaults, psk: other-secret}\n"
        b"psk: final-secret\npsk: ${ENV_PSK}\n"
        b"# note synthetic-secret remains a known literal\n"
    )
    clean = sanitized(raw)
    expected = raw
    for secret in (b"synthetic-secret", b"other-secret", b"final-secret"):
        expected = expected.replace(secret, b"REDACTED_SECRET")
    assert clean == expected


def test_escaped_values_are_matched_without_reserializing_yaml():
    raw = b'psk: "synthetic-secret"\nextra: "keep synthetic\\x2dsecret after"\nurl: "https://8\\u002e8.8.8:443/path"\n'
    assert sanitized(raw) == (
        b'psk: "REDACTED_SECRET"\nextra: "keep REDACTED_SECRET after"\n'
        b'url: "https://PUBLIC_IPV4_001:443/path"\n'
    )


def test_comments_and_private_material_are_targeted():
    raw = (
        b"# plain operational note\n# password=synthetic-comment # keep\n"
        b"unknown:\n  api_key: synthetic-api-value\n"
        b"extra: |\n  -----BEGIN PRIVATE KEY-----\n  synthetic-key\n"
        b"  -----END PRIVATE KEY-----\n"
    )
    clean = sanitized(raw)
    assert b"synthetic" not in clean
    assert clean.startswith(b"# plain operational note\n# password=REDACTED_SECRET # keep\n")
    assert len(clean.splitlines()) == len(raw.splitlines())


@pytest.mark.parametrize(
    "value",
    [
        "password=synthetic-secret",
        "psk: synthetic-secret",
        '--password="synthetic secret with spaces"',
        "Bearer synthetic-token",
    ],
)
def test_embedded_credentials_in_free_scalar_text_are_removed(value):
    raw = yaml.safe_dump({"note": "keep " + value}).encode()
    clean = sanitized(raw)
    assert b"synthetic" not in clean
    assert b"keep" in clean


def test_references_nonsecret_fields_and_no_secret_fetch(monkeypatch):
    monkeypatch.setenv("ENV_PSK", "must-not-be-read")
    raw = b"psk: ${ENV_PSK}\nssh_private_key_path: /local/key\nprivate_prefix: 10.0.0.0/8\n"
    assert sanitized(raw) == raw


@pytest.mark.parametrize(
    "key", ["secret_key", "aws_secret_access_key", "private-key", "privateKey", "accessToken"]
)
def test_credential_name_variants_are_redacted(key):
    raw = f"{key}: synthetic-credential-value\n".encode()
    assert unsafe_yaml(raw)
    assert sanitized(raw) == raw.replace(b"synthetic-credential-value", b"REDACTED_SECRET")


def test_credential_metadata_references_are_preserved():
    raw = (
        b"private_key_path: /local/key\nprivateKeySha256: abc123\n"
        b"secret_key: ${ENV_SECRET}\naccessToken: $ENV_TOKEN\n"
    )
    assert sanitized(raw) == raw


def test_ordinary_long_text_redaction_remains_bounded():
    text = "ordinary " * 6000 + "completed\n"
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            "import sys; from nebius_vpngw.support_bundle.redaction import redact_text; "
            "value=sys.stdin.read(); assert redact_text(value)['content'] == value",
        ],
        input=text,
        text=True,
        capture_output=True,
        timeout=3,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_block_header_comment_secret_is_removed_without_losing_header():
    raw = b"psk: | # password=synthetic-header\n  synthetic-body\nnext: keep\n"
    assert sanitized(raw) == raw.replace(b"synthetic-header", b"REDACTED_SECRET").replace(
        b"synthetic-body", b"REDACTED_SECRET"
    )


def test_quoted_multiword_cli_secret_in_scalar():
    raw = b"note: 'tool --token \"synthetic one two\"'\n"
    clean = sanitized(raw)
    assert all(word not in clean for word in (b"synthetic", b"one", b"two"))


@pytest.mark.parametrize(
    "value",
    [
        "10.0.0.1",
        "172.16.0.0/12",
        "192.168.1.1",
        "100.64.1.1",
        "169.254.10.1",
        "127.0.0.1",
        "0.0.0.0",
        "0.0.0.0/0",
        "::/0",
        "::1",
        "fe80::1",
        "fd01::1",
        "ff02::1",
        "224.0.0.1",
        "192.0.2.1",
        "2001:db8::1",
    ],
)
def test_nonpublic_evidence_is_unchanged(value):
    assert AddressAliases().text(value) == value
    assert not contains_public_address(value)


@pytest.mark.parametrize(
    "value",
    [
        "8.8.8.8",
        "8.8.8.8:443",
        "https://8.8.8.8/a",
        "8.8.8.8.",
        "8.8.8.0/24",
        "peer:8.8.8.8",
        "8.8.8.8/0",
        "ip:2606:4700::1111",
        "2606:4700::1111/0",
        "8.8.8.8/999",
        "10.0.0.1[local]...8.8.8.8[peer]",
        "fd00::1[local]...2606:4700::1111[peer]",
        "[2606:4700::1111]:443",
        "2606:4700::/32",
        "::ffff:8.8.8.8",
    ],
)
def test_public_addresses_are_aliased_and_scannable(value):
    assert contains_public_address(value)
    clean = AddressAliases().text(value)
    assert "PUBLIC_IPV" in clean
    assert not contains_public_address(clean)


def test_equivalent_addresses_and_key_collisions():
    aliases = AddressAliases()
    assert aliases.text("2606:4700::1111") == aliases.text("2606:4700:0:0:0:0:0:1111")
    with pytest.raises(ValueError, match="collision"):
        aliases.value({"2606:4700::1111": 1, "2606:4700:0:0:0:0:0:1111": 2})
    assert collector.unsafe_export("x.json", b'{"8.8.8.8": 1}', ())
    assert collector.unsafe_export(
        "x.json", b'{"synthetic-key-secret": 1}', ("synthetic-key-secret",)
    )


def test_yaml_key_collision_is_rejected_but_original_duplicates_are_preserved():
    raw = b"addresses: {8.8.8.8: one, PUBLIC_IPV4_001: two}\n"
    with pytest.raises(ValueError, match="collision"):
        SourceYaml.read(raw).redact(AddressAliases())
    raw = b"addresses: {8.8.8.8: one, 8.8.8.8: two}\n"
    assert sanitized(raw).count(b"PUBLIC_IPV4_001") == 2


@pytest.mark.parametrize("raw", [b"\xff", b"x: &cycle [*cycle]", b'x: "unfinished'])
def test_unsafe_source_never_falls_back_to_raw(raw):
    with pytest.raises(ValueError):
        SourceYaml.read(raw)
    assert unsafe_yaml(raw)


def config_file(tmp_path: Path, monkeypatch) -> tuple[Path, bytes]:
    monkeypatch.setenv("HOME", str(tmp_path))
    raw = (
        b"# keep this customer comment\nproject_id: project-example\n"
        b"gateway_group:\n  name: example\n  instance_count: 1\n"
        b"  region: region-example\n  external_ips: [[8.8.8.8]]\n"
        b"connections:\n  - name: connection\n    tunnels:\n"
        b"      - name: tunnel\n        psk: synthetic-nested-psk\n"
        b"        remote_public_ip: 9.9.9.9\n"
        b"unknown: {keep: all-values}\n"
    )
    path = tmp_path / "customer.yaml"
    path.write_bytes(raw)
    return path, raw


def runner(request, _timeout):
    action = request["action"]
    if action == "artifact":
        return [{"probe": "selected-artifact", "data": {}, "outcome": "ok"}], "ok"
    if action == "cloud":
        return [], "ok"
    return [
        {
            "probe": "status" if action == "status" else "host/resources",
            "data": {"address": "8.8.8.8"},
            "outcome": "ok",
            "text": {
                "stdout": {
                    "content": "ordinary synthetic-nested-psk peer 10.0.0.1[local]...9.9.9.9[peer]\n"
                }
            },
        }
    ], "ok"


def test_real_nested_psk_and_original_yaml_survive_whole_archive_pipeline(tmp_path, monkeypatch):
    path, original = config_file(tmp_path, monkeypatch)
    output, _ = collector.collect(path, output=tmp_path / "bundle.zip", runner=runner)
    assert path.read_bytes() == original
    with zipfile.ZipFile(output) as archive:
        contents = {name: archive.read(name) for name in archive.namelist()}
    header = json.loads(contents["manifest.json"])
    assert header["schema"] == "nebius-vpngw/support-bundle-v4"
    assert header["redaction_policy"] == "nebius-vpngw/redaction-v2"
    yaml_name = "operator/customer-config.redacted.yaml"
    clean = contents[yaml_name]
    expected = (
        original.replace(b"synthetic-nested-psk", b"REDACTED_SECRET")
        .replace(b"8.8.8.8", b"PUBLIC_IPV4_001")
        .replace(b"9.9.9.9", b"PUBLIC_IPV4_002")
    )
    assert clean == expected
    for name, data in contents.items():
        assert not collector.unsafe_export(name, data, ("synthetic-nested-psk",))
        assert all(
            secret not in data for secret in (b"8.8.8.8", b"9.9.9.9", b"synthetic-nested-psk")
        )
    for row in header["probes"] + header["reports"]:
        if row["file"] in contents:
            assert row["sha256"] == hashlib.sha256(contents[row["file"]]).hexdigest()
            assert row["bytes"] == len(contents[row["file"]])
    assert yaml_name.encode() in contents["evidence-index.json"]


def test_extension_credentials_are_removed_from_every_archive_member(tmp_path, monkeypatch):
    path, original = config_file(tmp_path, monkeypatch)
    original += b"extension: {aws_secret_access_key: synthetic-credential-value}\n"
    path.write_bytes(original)
    output, _ = collector.collect(path, output=tmp_path / "bundle.zip", runner=runner)
    assert path.read_bytes() == original
    with zipfile.ZipFile(output) as archive:
        assert b"extension: {aws_secret_access_key: REDACTED_SECRET}" in archive.read(
            "operator/customer-config.redacted.yaml"
        )
        assert all(b"synthetic-credential-value" not in archive.read(n) for n in archive.namelist())


def test_comment_only_source_change_is_visible(tmp_path, monkeypatch):
    path, original = config_file(tmp_path, monkeypatch)

    def changing_runner(request, timeout):
        path.write_bytes(original + b"# concurrent edit\n")
        return runner(request, timeout)

    output, code = collector.collect(path, output=tmp_path / "changed.zip", runner=changing_runner)
    with zipfile.ZipFile(output) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        row = next(r for r in manifest["probes"] if r["file"].endswith(".yaml"))
        assert row["outcome"] == "changed" and row["source_changed"]
        assert b"concurrent edit" not in archive.read(row["file"])
    assert code == 1


def test_unsupported_snapshot_is_omitted_with_explicit_partial_evidence(tmp_path, monkeypatch):
    path, original = config_file(tmp_path, monkeypatch)
    path.write_bytes(original.decode().encode("utf-16"))
    output, code = collector.collect(path, output=tmp_path / "unsupported.zip", runner=runner)
    with zipfile.ZipFile(output) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        row = next(r for r in manifest["probes"] if r["file"].endswith(".yaml"))
        assert row["outcome"] == "invalid-data"
        assert row["file"] not in archive.namelist()
    assert code == 1


@pytest.mark.parametrize("value", ["null", "42", "wrong-type"])
def test_malformed_nonscope_tunnels_remain_available_for_diagnosis(tmp_path, monkeypatch, value):
    path, _ = config_file(tmp_path, monkeypatch)
    raw = (
        "project_id: project-example\ngateway_group: {name: example, instance_count: 1}\n"
        f"connections: [{{name: example, tunnels: {value}}}]\n"
    ).encode()
    path.write_bytes(raw)
    output, _ = collector.collect(path, output=tmp_path / "malformed.zip", runner=runner)
    with zipfile.ZipFile(output) as archive:
        assert archive.read("operator/customer-config.redacted.yaml") == raw
