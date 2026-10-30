from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from nebius_cxcli.ssh_host_identity import _pair, generate_host_identity


def test_guest_http_client_identifies_itself_without_changing_authorization(monkeypatch):
    import io
    import urllib.error

    path = (
        Path(__file__).resolve().parents[3]
        / "platform-infra/modules/wireguard-gw/wireguard_access.py"
    )
    spec = importlib.util.spec_from_file_location("wireguard_guest_http", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class Endpoint:
        def open(self, request, *, timeout):
            # The live API edge rejects urllib's default client before IAM evaluation.
            if request.get_header("User-agent") != "nebius-cxcli-wireguard/1":
                raise urllib.error.HTTPError(request.full_url, 403, "Rejected client", {}, None)
            assert request.get_header("Authorization") == "Bearer fixture-token"
            assert timeout == 20
            return io.BytesIO(b'{"versionId":"fixture-version","data":[]}')

    def opener(*handlers):
        assert handlers[0].proxies == {}
        assert isinstance(handlers[1], module.NoRedirect)
        return Endpoint()

    monkeypatch.setattr(module.urllib.request, "build_opener", opener)
    assert module.fetch(
        "https://api.nebius.cloud/mysterybox/v1/payloads",
        {"Authorization": "Bearer fixture-token"},
    ) == {"versionId": "fixture-version", "data": []}


@pytest.fixture
def guest(tmp_path, monkeypatch):
    path = (
        Path(__file__).resolve().parents[3]
        / "platform-infra/modules/wireguard-gw/wireguard_access.py"
    )
    spec = importlib.util.spec_from_file_location("wireguard_guest_access", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "STATE", tmp_path / "state")
    monkeypatch.setattr(module, "HOST", tmp_path / "host")
    module.STATE.mkdir(mode=0o700)
    monkeypatch.setattr(module, "authorized", lambda config: tmp_path / "authorized_keys")
    # Exercise real Ed25519 validation; test runner is unprivileged, unlike the root guest.
    monkeypatch.setattr(module, "public", lambda path: _pair(path.read_bytes())["ed25519_public"])
    host, login = generate_host_identity(), generate_host_identity()
    config = dict(
        generation="generation",
        ssh_user="ubuntu",
        host_public_key=host["ed25519_public"],
        login_public_key=login["ed25519_public"],
        service_account_id="account",
        host_secret_id="secret",
        host_version_id="version",
    )

    def fetch(url, headers):
        if url.startswith("http:"):
            assert headers == {"Metadata": "true"}
            return {"service_account_id": "account", "token": {"access_token": "fixture-token"}}
        assert (
            url
            == "https://api.nebius.cloud/mysterybox/v1/payloads?secretId=secret&versionId=version"
        )
        assert headers == {"Authorization": "Bearer fixture-token"}
        return {
            "versionId": "version",
            "data": [{"key": key, "stringValue": value} for key, value in host.items()],
        }

    monkeypatch.setattr(module, "fetch", fetch)
    return module, config, host, login


def test_bootstrap_installs_expected_pair_before_login_and_reboot_needs_no_network(
    guest, monkeypatch
):
    module, config, host, login = guest
    module.bootstrap(config)
    assert module.HOST.read_text() == host["ed25519_private"]
    assert module.HOST.stat().st_mode & 0o777 == 0o600
    assert module.authorized(config).read_text() == login["ed25519_public"] + "\n"
    rotated = generate_host_identity()["ed25519_public"]
    module.atomic(module.authorized(config), rotated + "\n")
    monkeypatch.setattr(module, "fetch", lambda *a: pytest.fail("reboot fetched secret"))
    module.bootstrap(config)
    assert module.authorized(config).read_text() == rotated + "\n"


@pytest.mark.parametrize(
    "failure", ["outage", "wrong-version", "wrong-public", "wrong-private", "wrong-sa"]
)
def test_bootstrap_failure_never_authorizes_an_administrator(guest, monkeypatch, failure):
    module, config, _, _ = guest
    original = module.fetch

    def fetch(url, headers):
        if failure == "outage":
            raise TimeoutError
        result = original(url, headers)
        if failure == "wrong-sa" and url.startswith("http:"):
            result["service_account_id"] = "other"
        if url.startswith("https:"):
            if failure == "wrong-version":
                result["versionId"] = "other"
            for entry in result["data"]:
                if failure == "wrong-public" and entry["key"] == "ed25519_public":
                    entry["stringValue"] = generate_host_identity()["ed25519_public"]
                if failure == "wrong-private" and entry["key"] == "ed25519_private":
                    entry["stringValue"] = generate_host_identity()["ed25519_private"]
        return result

    monkeypatch.setattr(module, "fetch", fetch)
    with pytest.raises((ValueError, TimeoutError)):
        module.bootstrap(config)
    assert not module.authorized(config).exists()
    assert not (module.STATE / "generation").exists()
    # The same frozen input can recover after availability/permission repair.
    monkeypatch.setattr(module, "fetch", original)
    module.bootstrap(config)
    assert module.authorized(config).exists()


def test_rotation_retry_cannot_restore_retired_key(guest):
    module, config, _, login = guest
    module.bootstrap(config)
    candidate = generate_host_identity()["ed25519_public"]
    request = {"operation": "11111111-1111-1111-1111-111111111111", "public_key": candidate}
    module.rotate(config, "stage", request)
    assert set(module.authorized(config).read_text().splitlines()) == {
        candidate,
        login["ed25519_public"],
    }
    module.rotate(config, "retire", request)
    module.rotate(config, "stage", request)
    module.bootstrap(config)
    assert module.authorized(config).read_text() == candidate + "\n"
    module.rotate(config, "finish", request)
    assert json.loads((module.STATE / "rotation.json").read_text())["phase"] == "complete"


def test_template_has_no_private_payload_and_masks_socket_activation():
    root = Path(__file__).resolve().parents[3] / "platform-infra/modules/wireguard-gw"
    template = (root / "wireguard-cloud-init.tftpl").read_text()
    assert "ssh_authorized_keys:" not in template and "ssh_keys:" not in template
    assert "systemctl mask ssh.socket" in template
    assert "ssh.socket.d" not in template
    assert "Requires=nebius-wireguard-access.service" in template
    assert "Restart=on-failure" in template
    assert template.index("bootstrap --clients-file") < template.index(
        "nebius-wireguard-access setup-complete"
    )
    assert "ssh_host_keys" not in (root / "main.tf").read_text()


def test_superseded_rotation_cannot_be_replayed_after_newer_rotation(guest):
    module, config, _, _ = guest
    module.bootstrap(config)
    first = {
        "operation": "11111111-1111-1111-1111-111111111111",
        "public_key": generate_host_identity()["ed25519_public"],
    }
    second = {
        "operation": "22222222-2222-2222-2222-222222222222",
        "public_key": generate_host_identity()["ed25519_public"],
    }
    for request in (first, second):
        for phase in ("stage", "retire", "finish"):
            module.rotate(config, phase, request)
    for phase in ("stage", "retire", "finish"):
        with pytest.raises(ValueError, match="Superseded"):
            module.rotate(config, phase, first)
    assert module.authorized(config).read_text() == second["public_key"] + "\n"


@pytest.mark.integration
def test_real_terraform_template_contains_exact_public_helper_and_valid_scripts(tmp_path):
    import base64
    import gzip
    import shutil
    import subprocess

    import yaml

    root = Path(__file__).resolve().parents[3] / "platform-infra/modules/wireguard-gw"
    helper = (root / "wireguard_access.py").read_text()
    params = {
        "ssh_user_name": "ubuntu",
        "access_config_json": "{}",
        "network_helper": (root / "wireguard_network.py").read_text().replace("\n", "\n      "),
        "access_helper": helper.replace("\n", "\n      "),
        "wireguard_config_json": "{}",
        "bootstrap_clients_json": "[]",
    }
    raw_expression = f"templatefile({json.dumps(str(root / 'wireguard-cloud-init.tftpl'))}, {json.dumps(params)})"
    for name in (
        "cloud-init.tf",
        "wireguard-cloud-init.tftpl",
        "wireguard_access.py",
        "wireguard_network.py",
    ):
        shutil.copy2(root / name, tmp_path / name)
    defaults = {
        "ssh_user_name": "ubuntu",
        "access_bootstrap": {
            "generation": "11111111-1111-1111-1111-111111111111",
            "service_account_id": "serviceaccount-example",
            "host_secret_id": "mbsec-example",
            "host_version_id": "mbsecver-example",
            "host_public_key": "ssh-ed25519 " + "A" * 68,
            "login_public_key": "ssh-ed25519 " + "B" * 68,
        },
        "wireguard_tunnel_cidr": "10.8.0.1/22",
        "wireguard_listen_port": 51820,
        "nat_mode": True,
        "wireguard_mtu": None,
        "endpoint_host": None,
        "local_subnets": ["10.0.0.0/16"],
        "client_default_dns": ["1.1.1.1", "1.0.0.1"],
        "client_default_persistent_keepalive": 25,
        "clients": [],
    }
    (tmp_path / "variables.tf.json").write_text(
        json.dumps({"variable": {key: {"default": value} for key, value in defaults.items()}})
    )
    expression = (
        "jsonencode({rendered=local.cloud_init_user_data, "
        f"original=local.wireguard_cloud_config, raw={raw_expression}}})\n"
    )
    result = subprocess.run(
        ["terraform", "console"],
        cwd=tmp_path,
        input=expression,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    values = json.loads(json.loads(result.stdout))
    rendered = values["rendered"]
    assert rendered.startswith("#cloud-config\n")
    assert len(rendered.encode()) <= 32768
    assert len(values["raw"].encode()) > 32768, "Exercise the provider's observed failure"
    assert (
        "cloud_init_user_data_override = var.bootstrap_user_data != null ? var.bootstrap_user_data : local.cloud_init_user_data"
        in (root / "main.tf").read_text()
    )
    cloud = yaml.safe_load(rendered)
    for entry in cloud["write_files"]:
        assert entry.pop("encoding") == "gz+b64"
        entry["content"] = gzip.decompress(base64.b64decode(entry["content"])).decode()
    assert cloud == values["original"], "Compression must preserve every boot directive and file"
    files = {entry["path"]: entry["content"] for entry in cloud["write_files"]}
    assert files["/usr/local/sbin/nebius-wireguard-access"].strip() == helper.strip()
    for path, content in files.items():
        if content.startswith("#!/usr/bin/env python3"):
            compile(content, path, "exec")
        if content.startswith("#!/usr/bin/env bash"):
            checked = subprocess.run(["bash", "-n"], input=content, text=True, capture_output=True)
            assert checked.returncode == 0, checked.stderr
    assert "PRIVATE KEY" not in rendered
    assert "ssh_authorized_keys" not in cloud["users"][0]
