import json
import stat
from copy import deepcopy

import pytest
import yaml

from nebius_vpngw import pair_config as pair
from nebius_vpngw.config_template import DEFAULT_CONFIG_TEMPLATE
from nebius_vpngw.schema import VPNGatewayConfig


def seed(side, ha):
    config = yaml.safe_load(DEFAULT_CONFIG_TEMPLATE)
    config.update(tenant_id="tenant-test", project_id=f"project-{side}", region_id=f"region-{side}")
    config["gateway"]["local_prefixes"] = [f"10.{10 if side == 'a' else 20}.0.0/16"]
    config["gateway_group"]["instance_count"] = 2 if ha else 1
    config["gateway_group"]["vm_ha"] = {"enabled": ha}
    return config


@pytest.mark.parametrize("a,b", [(False, False), (False, True), (True, False), (True, True)])
@pytest.mark.parametrize("mode", ["static", "bgp"])
def test_every_owner_combination_is_reciprocal_and_schema_valid(tmp_path, a, b, mode):
    left, right = seed("a", a), seed("b", b)
    original = deepcopy((left, right))
    manifest_path = pair.create(tmp_path / "private", "regional", left, right, mode=mode)
    manifest = json.loads(manifest_path.read_bytes())
    configs = {
        side: yaml.safe_load((manifest_path.parent / f"{side}.config.yaml").read_bytes())
        for side in pair.SIDES
    }
    assert (left, right) == original
    assert len(manifest["paths"]) == (2 if a else 1) * (2 if b else 1)
    assert configs["a"]["gateway"]["local_asn"] != configs["b"]["gateway"]["local_asn"]
    assert 64512 <= configs["a"]["gateway"]["local_asn"] < 65535
    tunnels = {
        side: {t["name"]: t for t in configs[side]["connections"][0]["tunnels"]}
        for side in pair.SIDES
    }
    for path in manifest["paths"]:
        first, second = (tunnels[side][path["id"]] for side in pair.SIDES)
        assert first["inner_local_ip"] == second["inner_remote_ip"]
        assert first["inner_remote_ip"] == second["inner_local_ip"]
        assert first["psk"] == second["psk"]
        assert first["psk"] not in manifest_path.read_text()
        assert first["remote_public_ip"] == pair.endpoint("b", path["b"])
        assert second["remote_public_ip"] == pair.endpoint("a", path["a"])
        for side, peer in (("a", "b"), ("b", "a")):
            connection = configs[side]["connections"][0]
            assert connection["remote_prefixes"] == configs[peer]["gateway"]["local_prefixes"]
    for config in configs.values():
        for tunnel in config["connections"][0]["tunnels"]:
            tunnel["remote_public_ip"] = "192.0.2.1"
        VPNGatewayConfig.model_validate(config)
    for path in manifest_path.parent.iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(manifest_path.parent.stat().st_mode) == 0o700


def test_creation_resumes_without_regenerating_secrets(tmp_path, monkeypatch):
    left, right = seed("a", True), seed("b", True)
    directory = tmp_path / "private"
    publish = pair._publish_exact

    def interrupted(path, content):
        if path.name == "b.config.yaml":
            raise ConnectionError("before second configuration publication")
        publish(path, content)

    monkeypatch.setattr(pair, "_publish_exact", interrupted)
    with pytest.raises(ConnectionError):
        pair.create(directory, "regional", left, right, mode="bgp")
    saved = (directory / pair.ASSIGNMENTS).read_bytes()
    first = (directory / "a.config.yaml").read_bytes()
    monkeypatch.setattr(pair, "_publish_exact", publish)
    monkeypatch.setattr(pair.secrets, "token_urlsafe", lambda *a: pytest.fail("secret regenerated"))
    pair.create(directory, "regional", left, right, mode="bgp")
    assert (directory / pair.ASSIGNMENTS).read_bytes() == saved
    assert (directory / "a.config.yaml").read_bytes() == first
    before = {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in directory.iterdir()}
    pair.create(directory, "regional", left, right, mode="bgp")
    assert {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in directory.iterdir()} == before


def test_changed_intent_or_output_is_not_overwritten(tmp_path):
    left, right = seed("a", False), seed("b", False)
    directory = tmp_path / "private"
    pair.create(directory, "regional", left, right, mode="static")
    with pytest.raises(ValueError, match="seed intent changed"):
        pair.create(directory, "regional", left, right, mode="bgp")
    (directory / "b.config.yaml").write_text("operator changes")
    with pytest.raises(ValueError, match="configuration is malformed"):
        pair.create(directory, "regional", left, right, mode="static")
    assert (directory / "b.config.yaml").read_text() == "operator changes"


def test_overlap_and_ambiguous_topology_fail_before_publication(tmp_path):
    left, right = seed("a", True), seed("b", True)
    right["gateway"]["local_prefixes"] = ["10.10.1.0/24"]
    with pytest.raises(ValueError, match="overlap"):
        pair.create(tmp_path / "private", "regional", left, right, mode="static")
    assert not (tmp_path / "private").exists()
    right = seed("b", False)
    right["gateway_group"]["instance_count"] = 2
    with pytest.raises(ValueError, match="explicit two-member"):
        pair.create(tmp_path / "private", "regional", left, right, mode="static")


def test_schema_errors_do_not_expose_generated_credentials(tmp_path, monkeypatch):
    left, right = seed("a", False), seed("b", False)
    left["defaults"]["ike_version"] = 8
    monkeypatch.setattr(pair.secrets, "token_urlsafe", lambda *a: "never-print-this-secret")
    with pytest.raises(ValueError) as error:
        pair.create(tmp_path / "private", "regional", left, right, mode="bgp")
    assert "never-print-this-secret" not in str(error.value)
    assert "ike_version" in str(error.value)


def test_both_networks_must_be_prepared_before_binding_and_resume_is_exact(tmp_path, monkeypatch):
    path = pair.create(
        tmp_path / "private", "regional", seed("a", True), seed("b", False), mode="bgp"
    )
    manifest, configs = pair.load(path)
    configs["a"]["gateway_group"]["external_ips"] = [["192.0.2.10"], ["192.0.2.11"]]
    a = path.parent / "a.config.yaml"
    a.write_text(yaml.safe_dump(configs["a"], sort_keys=False))
    with pytest.raises(ValueError, match="Prepare every member"):
        pair.bind_endpoints(path)
    configs["b"]["gateway_group"]["external_ips"] = [["198.51.100.10"]]
    b = path.parent / "b.config.yaml"
    b.write_text(yaml.safe_dump(configs["b"], sort_keys=False))
    original_replace = pair.os.replace

    def interrupt(source, target):
        if target == b:
            raise ConnectionError("lost second binding publication")
        original_replace(source, target)

    monkeypatch.setattr(pair.os, "replace", interrupt)
    with pytest.raises(ConnectionError):
        pair.bind_endpoints(path)
    pair.load(path)
    with pytest.raises(ValueError, match="unresolved"):
        pair.load(path, resolved=True)
    monkeypatch.setattr(pair.os, "replace", original_replace)
    pair.bind_endpoints(path)
    assert pair.load(path, resolved=True)[0] == manifest
    before = (a.read_bytes(), b.read_bytes(), a.stat().st_mtime_ns, b.stat().st_mtime_ns)
    pair.bind_endpoints(path)
    pair.create(path.parent, "regional", seed("a", True), seed("b", False), mode="bgp")
    assert (a.read_bytes(), b.read_bytes(), a.stat().st_mtime_ns, b.stat().st_mtime_ns) == before


def test_status_admission_is_read_only_and_rejects_redirected_config(tmp_path):
    path = pair.create(
        tmp_path / "private", "regional", seed("a", False), seed("b", False), mode="static"
    )
    before = {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in path.parent.iterdir()}
    pair.load(path)
    assert {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in path.parent.iterdir()} == before
    manifest = json.loads(path.read_bytes())
    manifest["deployments"]["a"]["config"] = "../outside.yaml"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="reference is malformed"):
        pair.load(path)


@pytest.mark.parametrize("mode", ["static", "bgp"])
@pytest.mark.parametrize("ha", [False, True])
def test_pair_seed_does_not_reuse_probes_for_replaced_tunnel_identities(tmp_path, mode, ha):
    left, right = seed("a", ha), seed("b", ha)
    for config in (left, right):
        tunnel = config["connections"][0]["tunnels"][0]
        config["gateway"]["tunnel_probes"] = {
            tunnel["name"]: {
                "source_ip": tunnel["inner_local_ip"],
                "target_ip": tunnel["inner_remote_ip"],
            }
        }
    original = deepcopy((left, right))
    path = pair.create(tmp_path / "pair", "regional", left, right, mode=mode)
    manifest, generated = pair.load(path)
    assert all(not config["gateway"].get("tunnel_probes") for config in generated.values())
    before = {p.name: p.read_bytes() for p in path.parent.iterdir()}
    assert pair.create(path.parent, "regional", left, right, mode=mode) == path
    assert {p.name: p.read_bytes() for p in path.parent.iterdir()} == before
    assert (left, right) == original
