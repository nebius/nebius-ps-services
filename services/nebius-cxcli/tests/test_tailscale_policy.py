from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from nebius_cxcli import tailscale_policy
from test_tailscale import accept, row


@pytest.mark.parametrize("command", ["plan", "apply"])
@pytest.mark.parametrize("stored_id", [None, "secret-test", "different-secret"])
def test_policy_commands_resolve_linked_credentials_without_changing_intent(
    tmp_path, monkeypatch, command, stored_id
):
    import copy

    from nebius_cxcli import tailscale_api, tailscale_cli, tailscale_config
    from nebius_cxcli.component_sources import component_output_root_name

    value = row()
    ref = {"project_id": "project-test", "component_instance": "ts-oauth", "secret_name": "oauth"}
    if stored_id is not None:
        ref["secret_id"] = stored_id
    value["tailscale"]["auth"] = {"mode": "oauth", "secretstash": ref}
    accept(value)
    tailscale_config.validate(value)
    original = copy.deepcopy(value)
    outputs = {
        component_output_root_name("ts-oauth", "secret_ids"): {"value": {"oauth": "secret-test"}}
    }
    cli = NS(terraform_output_json=lambda *a, **k: outputs)
    paths = NS(project_dir=tmp_path, infra_dir=tmp_path / "infra")
    monkeypatch.setattr(tailscale_cli, "load", lambda *a: (cli, {}, paths, value))
    calls = []

    def oauth_pair(auth, **kwargs):
        assert auth["secretstash"]["secret_id"] == "secret-test"
        calls.append("credential-read")
        return "test-client", "test-secret", {}

    monkeypatch.setattr(tailscale_api, "oauth_pair", oauth_pair)
    monkeypatch.setattr(tailscale_api.TailscaleAPI, "authenticate_pair", lambda *a: None)

    def exercise(api, config, component, *args):
        api.authenticate()
        assert config == original["tailscale"]
        assert value == original
        return {"sha256": "reviewed", "status": "PASS"}

    monkeypatch.setattr(tailscale_policy, "make_plan", exercise)
    monkeypatch.setattr(tailscale_policy, "apply_plan", exercise)
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"sha256": "reviewed"}))

    def run():
        if command == "plan":
            tailscale_cli.policy_plan.__wrapped__(tmp_path / "config.yaml", plan, json_output=True)
        else:
            tailscale_cli.policy_apply.__wrapped__(
                tmp_path / "config.yaml", plan, "reviewed", json_output=True
            )

    if stored_id == "different-secret":
        with pytest.raises(RuntimeError, match="linked container identity.*changed"):
            run()
        assert not calls
    else:
        run()
        assert calls == ["credential-read"]
    assert value == original


@pytest.fixture
def native_helper(tmp_path, monkeypatch):
    go = shutil.which("go")
    if not go:
        pytest.skip("Native policy qualification requires the pinned Go toolchain")
    source = Path(tailscale_policy.__file__).with_name("hujson_helper")
    binary = tmp_path / "helper"
    subprocess.run(
        [go, "build", "-mod=readonly", "-trimpath", "-o", str(binary), "."],
        cwd=source,
        check=True,
        timeout=180,
    )
    monkeypatch.setattr(tailscale_policy, "helper", lambda: binary)


@pytest.mark.integration
def test_native_policy_preservation_idempotency_and_conditional_apply(native_helper):
    cfg = row()["tailscale"]
    cfg["policy"] = {
        "tag_owners": ["group:admins"],
        "rules": [
            {
                "sources": ["group:developers"],
                "destinations": ["10.20.0.0/24"],
                "protocol": "tcp",
                "ports": ["443"],
            }
        ],
    }
    original = '{ // administrator comment\n"future":9007199254740993, "routeSelection":"regional-failover", "grants":[],\n}'
    document, patch = tailscale_policy.proposal(original, cfg)
    assert "// administrator comment" in document
    assert '"future":9007199254740993' in document
    assert '"routeSelection":"regional-failover"' in document
    semantic = tailscale_policy.transform(document)["value"]
    assert semantic["future"] == 9007199254740993
    assert semantic["autoApprovers"]["routes"] == {"10.20.0.0/24": ["tag:router"]}
    assert tailscale_policy.proposal(document, cfg) == (document, [])
    state = {"document": original, "etag": "v1"}
    writes = []

    def request(method, path, value=None, *, raw=False, etag=""):
        if method == "GET":
            return state["document"], state["etag"]
        if path.endswith("/validate"):
            tailscale_policy.transform(value)
            return "{}", ""
        assert etag == state["etag"]
        writes.append(value)
        state.update(document=value, etag="v2")
        return "{}", "v2"

    api = NS(tailnet="/tailnet/example", request=request)
    plan = tailscale_policy.make_plan(api, cfg, "ts")
    assert plan["patch"] == patch and not writes
    assert tailscale_policy.apply_plan(api, cfg, "ts", plan)["status"] == "PASS"
    assert len(writes) == 1
    with pytest.raises(RuntimeError, match="changed since"):
        tailscale_policy.apply_plan(api, cfg, "ts", plan)
    assert len(writes) == 1


@pytest.mark.integration
def test_native_policy_rejects_duplicate_keys_and_conflicting_tag_owner(native_helper):
    with pytest.raises(RuntimeError, match="parsing"):
        tailscale_policy.transform('{"grants":[], "grants":[]}')
    cfg = row()["tailscale"]
    cfg["policy"] = {"tag_owners": ["group:new-owner"]}
    with pytest.raises(RuntimeError, match="different owners"):
        tailscale_policy.proposal(
            json.dumps({"tagOwners": {"tag:router": ["group:existing-owner"]}}), cfg
        )
