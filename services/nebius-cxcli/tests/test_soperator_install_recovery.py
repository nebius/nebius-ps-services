import copy

import pytest

from nebius_cxcli import cli
from nebius_cxcli.soperator_install_recovery import (
    archive_failed_receipt,
    recovery_provenance,
    validate_recovery_archive,
    validate_recovery_plan,
)


def change(address, actions, before=None, after=None):
    return {
        "address": address,
        "mode": "managed",
        "provider_name": "example",
        "type": "resource",
        "change": {
            "actions": actions,
            "after_unknown": {"id": True} if actions == ["create"] else {},
            "before": before,
            "after": {} if after is None and actions == ["create"] else after,
        },
    }


def plan(*changes):
    return {"resource_changes": list(changes)}


@pytest.fixture
def receipt():
    result = {
        "schema": cli._SOPERATOR_INSTALL_PLAN_SCHEMA,
        "status": "planned",
        "planGeneration": "sha256:" + "a" * 64,
        "inputs": {"terraformPlanSha256": "sha256:" + "b" * 64},
    }
    result["approvalFingerprint"] = cli._soperator_install_approval_fingerprint(result)
    result.update(
        status="failed", startedAt="2026-01-01", failedAt="2026-01-02", failureType="RuntimeError"
    )
    return result


def test_partial_apply_allows_remaining_iam_and_preserves_archive(tmp_path, receipt):
    original = plan(change("module.mk8s.cluster", ["create"]), change("iam.group", ["create"]))
    candidate = plan(
        change("module.mk8s.cluster", ["no-op"], {"id": "cluster-a"}, {"id": "cluster-a"}),
        change("iam.group", ["create"]),
    )
    recovery = recovery_provenance(receipt, original, candidate)
    archive_failed_receipt(tmp_path, receipt)
    archive_failed_receipt(tmp_path, receipt)
    assert validate_recovery_archive(tmp_path, recovery) == recovery
    archives = list((tmp_path / "soperator-install-history").glob("*.json"))
    assert len(archives) == 1
    assert archives[0].stat().st_mode & 0o777 == 0o600
    assert cli.read_owner_only_json(archives[0], label="test") == receipt
    corrupted = dict(receipt, failureType="Changed")
    cli._write_owner_only_json(archives[0], corrupted)
    with pytest.raises(RuntimeError, match="no longer matches"):
        validate_recovery_archive(tmp_path, recovery)


@pytest.mark.parametrize(
    "candidate",
    [
        plan(change("foreign", ["create"])),
        plan(change("cluster", ["delete"])),
        plan(change("cluster", ["delete", "create"])),
        plan(change("cluster", ["no-op"], {"id": "foreign"}, {"id": "foreign"})),
        plan(change("cluster", ["update"], {"id": "original"}, {"id": "changed"})),
        plan(change("cluster", ["create"])),
        plan(change("cluster", ["no-op"])),
        plan(
            change("cluster", ["no-op"], {"id": "original"}, {"id": "original"}),
            change("extra", ["create"]),
        ),
    ],
)
def test_recovery_rejects_scope_identity_and_destructive_changes(candidate):
    original = plan(change("cluster", ["update"], {"id": "original"}, {"id": "original"}))
    with pytest.raises(RuntimeError):
        validate_recovery_plan(original, candidate)


def test_recovery_accepts_noop_and_preserves_ids_across_another_replan():
    complete = plan(change("cluster", ["no-op"], {"id": "same"}, {"id": "same"}))
    assert validate_recovery_plan(complete, complete).startswith("sha256:")
    changed = copy.deepcopy(complete)
    changed["resource_changes"][0]["change"].update(before={"id": "other"}, after={"id": "other"})
    with pytest.raises(RuntimeError, match="previously known"):
        validate_recovery_plan(complete, changed)


def test_failed_receipt_replan_requires_exact_approved_binary(monkeypatch, tmp_path, receipt):
    paths = cli.resolve_project_paths(tmp_path / "config.yaml")
    authority = {
        "schema": receipt["schema"],
        "operationId": "op",
        "target": {"ref": "cluster"},
        "release": {},
        "inputs": {"configSha256": "config", "generatedManifestSha256": "manifest"},
    }
    receipt.update({k: v for k, v in authority.items() if k != "inputs"})
    receipt["inputs"].update(authority["inputs"])
    paths.infra_dir.mkdir(parents=True)
    binary, receipt_path = cli._soperator_install_plan_paths(paths)
    binary.write_bytes(b"approved")
    binary.chmod(0o600)
    receipt["inputs"]["terraformPlanSha256"] = cli._sha256_file(binary)
    receipt["approvalFingerprint"] = cli._soperator_install_approval_fingerprint(
        {**receipt, "status": "planned"}
    )
    cli._write_owner_only_json(receipt_path, receipt)
    monkeypatch.setattr(cli, "_soperator_install_plan_authority", lambda **_kw: authority)
    assert (
        cli._validate_soperator_install_replan_receipt(
            config={}, paths=paths, manifest={}, target_ref="cluster"
        )[2]
        == receipt
    )
    binary.write_bytes(b"changed")
    with pytest.raises(RuntimeError, match="exact previously approved"):
        cli._validate_soperator_install_replan_receipt(
            config={}, paths=paths, manifest={}, target_ref="cluster"
        )
    binary.write_bytes(b"approved")
    receipt["infraCompleteAt"] = "done"
    cli._write_owner_only_json(receipt_path, receipt)
    with pytest.raises(RuntimeError, match="completion checkpoint"):
        cli._validate_soperator_install_replan_receipt(
            config={}, paths=paths, manifest={}, target_ref="cluster"
        )


def test_recovery_provenance_changes_approval_and_requires_archive(tmp_path, receipt):
    original = plan(change("cluster", ["create"]))
    recovery = recovery_provenance(receipt, original, original)
    approved = {
        **receipt,
        "status": "planned",
        "inputs": {**receipt["inputs"], "recovery": recovery},
    }
    assert cli._soperator_install_approval_fingerprint(approved) != receipt["approvalFingerprint"]
    with pytest.raises(RuntimeError):
        validate_recovery_archive(tmp_path, recovery)


def test_failed_apply_replan_publishes_fresh_approval_and_resumes(monkeypatch, tmp_path):
    paths = cli.resolve_project_paths(tmp_path / "config.yaml")
    paths.infra_dir.mkdir(parents=True)
    binary, receipt_path = cli._soperator_install_plan_paths(paths)
    binary.write_bytes(b"old")
    binary.chmod(0o600)
    authority = {
        "schema": cli._SOPERATOR_INSTALL_PLAN_SCHEMA,
        "operationId": "operation",
        "target": {"ref": "cluster"},
        "release": {},
        "inputs": {"configSha256": "config", "generatedManifestSha256": "manifest"},
    }
    monkeypatch.setattr(
        cli, "_soperator_install_plan_authority", lambda **_kw: copy.deepcopy(authority)
    )
    previous = cli._soperator_install_plan_material(
        config_path=paths.config_path,
        paths=paths,
        manifest={},
        terraform_plan_path=binary,
        target_ref="cluster",
    )
    previous.update(status="failed", startedAt="start", failedAt="fail", failureType="RuntimeError")
    cli._write_owner_only_json(receipt_path, previous)
    original_plan = plan(change("cluster", ["create"]), change("iam", ["create"]))
    candidate = plan(
        change("cluster", ["no-op"], {"id": "same"}, {"id": "same"}), change("iam", ["create"])
    )
    monkeypatch.setattr(
        cli,
        "terraform_provider_schema_json",
        lambda *_a, **_kw: {
            "provider_schemas": {"example": {"resource_schemas": {"resource": {"block": {}}}}}
        },
    )
    monkeypatch.setattr(cli, "refresh_install_terraform_root", lambda *_a, **_kw: None)
    monkeypatch.setattr(cli, "_run_deploy_preflight", lambda *_a, **_kw: {})
    monkeypatch.setattr(cli, "_terraform_runtime_env", lambda *_a: {})
    monkeypatch.setattr(
        cli, "terraform_plan", lambda *_a, **kw: kw["plan_file"].write_bytes(b"new")
    )
    monkeypatch.setattr(
        cli,
        "terraform_show_json",
        lambda *_a, **kw: original_plan if kw["plan_file"] == binary else candidate,
    )
    scopes = []
    monkeypatch.setattr(
        cli, "_validate_soperator_install_terraform_plan_scope", lambda *_a, **kw: scopes.append(kw)
    )
    _, _, replacement = cli._replan_soperator_install(
        config={}, paths=paths, manifest={}, target_ref="cluster", expected_receipt=previous
    )
    assert scopes == [{"allow_unchanged_infrastructure": True}]
    assert replacement["approvalFingerprint"] != previous["approvalFingerprint"]
    assert replacement["status"] == "planned"
    assert "failedAt" not in replacement
    archive = next((paths.reports_dir / "soperator-install-history").glob("*.json"))
    assert cli.read_owner_only_json(archive, label="test") == previous
    assert (
        cli._load_soperator_install_plan(config={}, paths=paths, manifest={}, target_ref="cluster")[
            2
        ]
        == replacement
    )
    monkeypatch.setattr(cli, "terraform_show_json", lambda *_a, **_kw: candidate)
    _, _, refreshed = cli._replan_soperator_install(
        config={}, paths=paths, manifest={}, target_ref="cluster", expected_receipt=replacement
    )
    assert refreshed["inputs"]["recovery"] == replacement["inputs"]["recovery"]
    assert refreshed["approvalFingerprint"] != replacement["approvalFingerprint"]
    assert (
        cli._load_soperator_install_plan(config={}, paths=paths, manifest={}, target_ref="cluster")[
            2
        ]
        == refreshed
    )


def pending_grants():
    group = 'nebius_iam_v1_group.observability["worker"]'
    grant = 'nebius_iam_v1_access_permit.observability["worker"]'
    old = plan(
        change(group, ["no-op"], {"id": "group-a"}, {"id": "group-a"}),
        change(
            'nebius_iam_v1_access_permit.metrics["worker"]',
            ["create"],
            after={"parent_id": "group-a", "resource_id": "project-a", "role": "bad-metrics"},
        ),
        change(
            'nebius_iam_v1_access_permit.logs["worker"]',
            ["create"],
            after={"parent_id": "group-a", "resource_id": "project-a", "role": "bad-logs"},
        ),
    )
    new = plan(
        copy.deepcopy(old["resource_changes"][0]),
        change(
            grant,
            ["create"],
            after={"parent_id": "group-a", "resource_id": "project-a", "role": "editor"},
        ),
    )
    return old, new, frozenset({grant})


@pytest.mark.parametrize("field", ["role", "parent_id", "resource_id"])
@pytest.mark.parametrize("partially_created", [False, True])
def test_recovery_preserves_existing_permit_settings(field, partially_created):
    address = "nebius_iam_v1_access_permit.metrics"
    approved = {"parent_id": "group-a", "resource_id": "project-a", "role": "viewer"}
    live = {"id": "permit-a", **approved}
    original = (
        plan(change(address, ["create"], after=approved))
        if partially_created
        else plan(change(address, ["no-op"], live, live))
    )
    candidate = plan(change(address, ["update"], live, {**live, field: "unapproved"}))
    with pytest.raises(RuntimeError, match="previously approved resource settings"):
        validate_recovery_plan(original, candidate)


def test_uncreated_grants_can_receive_current_renderer_wiring():
    old, new, allowed = pending_grants()
    assert validate_recovery_plan(old, new, allowed_new_access_permits=allowed).startswith(
        "sha256:"
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "foreign-group",
        "foreign-project",
        "unknown-address",
        "already-created",
        "drop-only",
        "unowned-allowed-kind",
    ],
)
def test_grant_repair_rejects_scope_expansion_or_existing_grant_removal(mutation):
    old, new, allowed = pending_grants()
    item = new["resource_changes"][-1]
    if mutation == "foreign-group":
        item["change"]["after"]["parent_id"] = "foreign"
    elif mutation == "foreign-project":
        item["change"]["after"]["resource_id"] = "foreign"
    elif mutation == "unknown-address":
        item["address"] += "extra"
    elif mutation == "already-created":
        old["resource_changes"][-1]["change"]["before"] = {"id": "existing"}
    elif mutation == "drop-only":
        new["resource_changes"].pop()
    else:
        item["address"] = "module.mk8s.extra"
        allowed = frozenset({item["address"]})
    with pytest.raises(RuntimeError):
        validate_recovery_plan(old, new, allowed_new_access_permits=allowed)


def test_recovery_preserves_known_settings_while_resolving_computed_values():
    old = plan(change("cluster", ["create"], after={"preset": "fixed", "labels": {}}))
    new = plan(
        change(
            "cluster", ["no-op"], {"id": "same"}, {"id": "same", "preset": "fixed", "labels": {}}
        )
    )
    validate_recovery_plan(old, new)
    new["resource_changes"][0]["change"]["after"]["labels"]["extra"] = "drift"
    with pytest.raises(RuntimeError, match="approved resource settings"):
        validate_recovery_plan(old, new)


@pytest.fixture
def root_refresh(monkeypatch, tmp_path):
    import json

    from nebius_cxcli import soperator_install_recovery as recovery

    paths = cli.resolve_project_paths(tmp_path / "config.yaml")
    paths.infra_dir.mkdir(parents=True)
    paths.config_path.write_text("frozen-config")
    names = (
        "backend.tf",
        "versions.tf",
        "providers.tf",
        "variables.tf",
        "main.tf",
        "outputs.tf",
        "terraform.auto.tfvars.json",
    )
    contents = {name: b"unchanged" for name in names}
    contents["terraform.auto.tfvars.json"] = b"{}"
    for name, value in contents.items():
        (paths.infra_dir / name).write_bytes(value)
    manifest = {
        "render": {"source_profile": "portable", "module_sources": [], "terraform_tfvars": {}}
    }
    mp = recovery.manifest_path_for_generated_dir(paths.generated_dir)
    mp.write_text(json.dumps(manifest))
    monkeypatch.setattr(recovery, "rendered_module_sources", lambda *_a, **_kw: ())

    def render(_config, staged, **_kwargs):
        for name, value in contents.items():
            (staged.infra_dir / name).write_bytes(b"repaired" if name == "main.tf" else value)
        return [staged.infra_dir / name for name in names]

    monkeypatch.setattr(recovery, "render_terraform_artifacts", render)
    return recovery, paths, manifest, contents, render


def test_root_refresh_preserves_state_plan_inputs_manifest_and_flux(root_refresh):
    recovery, paths, manifest, _contents, _render = root_refresh
    protected = [
        paths.infra_dir / ".soperator-install.tfplan",
        paths.infra_dir / "terraform.tfstate",
        paths.flux_dir / "test.yaml",
        paths.reports_dir / "soperator-install-history" / "failure.json",
    ]
    for path in protected:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"retained")
    before = {
        p: p.read_bytes()
        for p in [
            paths.config_path,
            recovery.manifest_path_for_generated_dir(paths.generated_dir),
            *protected,
        ]
    }
    recovery.refresh_install_terraform_root({}, paths, manifest)
    assert (paths.infra_dir / "main.tf").read_bytes() == b"repaired"
    assert all(path.read_bytes() == content for path, content in before.items())
    recovery.refresh_install_terraform_root({}, paths, manifest)


@pytest.mark.parametrize("field", ["backend.tf", "terraform.auto.tfvars.json", "module-sources"])
def test_root_refresh_rejects_frozen_input_changes(root_refresh, field):
    recovery, paths, manifest, contents, _render = root_refresh
    if field == "module-sources":
        manifest["render"]["module_sources"] = [{"source": "foreign"}]
    else:
        contents[field] = b'{"changed": true}'
    with pytest.raises(RuntimeError, match="cannot change frozen"):
        recovery.refresh_install_terraform_root({}, paths, manifest)
    assert (paths.infra_dir / "main.tf").read_bytes() == b"unchanged"


def test_root_refresh_does_not_overwrite_a_concurrent_edit(monkeypatch, root_refresh):
    recovery, paths, manifest, _contents, renderer = root_refresh

    def concurrent(*args, **kwargs):
        files = renderer(*args, **kwargs)
        (paths.infra_dir / "main.tf").write_bytes(b"operator-edit")
        return files

    monkeypatch.setattr(recovery, "render_terraform_artifacts", concurrent)
    with pytest.raises(RuntimeError):
        recovery.refresh_install_terraform_root({}, paths, manifest)
    assert (paths.infra_dir / "main.tf").read_bytes() == b"operator-edit"


def test_failed_install_history_survives_later_render_generations(tmp_path):
    from nebius_cxcli.render import (
        promote_staged_generated_paths,
        render_replaceable_generated_files,
        staged_generated_paths,
    )

    paths = cli.resolve_project_paths(tmp_path / "config.yaml")
    history = paths.reports_dir / "soperator-install-history" / "failed.json"
    history.parent.mkdir(parents=True)
    history.write_bytes(b"retained-failure")
    history.chmod(0o600)
    assert history not in render_replaceable_generated_files(paths)
    staged = staged_generated_paths(paths)
    staged.flux_dir.mkdir(parents=True)
    (staged.flux_dir / "new.yaml").write_text("new")
    promote_staged_generated_paths(staged, paths)
    assert history.read_bytes() == b"retained-failure"
    assert history.stat().st_mode & 0o777 == 0o600


def test_recovery_excludes_computed_status_but_fences_inputs_and_identity():
    attributes = {
        "id": {"computed": True},
        "status": {"computed": True},
        "labels": {"optional": True, "computed": True},
        "attachments": {
            "optional": True,
            "nested_type": {
                "nesting_mode": "list",
                "attributes": {
                    "mount_tag": {"required": True},
                    "observed": {"computed": True},
                },
            },
        },
    }
    schema = {
        "provider_schemas": {
            "example": {
                "resource_schemas": {
                    "resource": {"block": {"attributes": attributes}},
                }
            }
        }
    }
    before = {
        "id": "same",
        "status": {"attachments": []},
        "labels": {"name": "lab"},
        "attachments": [{"mount_tag": "jail", "observed": "pending"}],
    }
    after = copy.deepcopy(before)
    after["status"]["attachments"] = ["node-a"]
    after["attachments"][0]["observed"] = "ready"
    old = plan(change("filesystem", ["no-op"], before, before))
    new = plan(change("filesystem", ["no-op"], after, after))
    with pytest.raises(RuntimeError, match="settings"):
        validate_recovery_plan(old, new)
    assert validate_recovery_plan(old, new, provider_schema=schema).startswith("sha256:")
    for key, value in (
        ("labels", {"name": "foreign"}),
        ("attachments", [{"mount_tag": "foreign", "observed": "ready"}]),
        ("id", "foreign"),
    ):
        altered = copy.deepcopy(new)
        altered["resource_changes"][0]["change"]["after"][key] = value
        with pytest.raises(RuntimeError):
            validate_recovery_plan(old, altered, provider_schema=schema)
    with pytest.raises(RuntimeError, match="schema for every"):
        validate_recovery_plan(old, new, provider_schema={"provider_schemas": {}})


def test_recovery_accepts_resolved_nested_unknown_defaults():
    old = plan(change("nodes", ["create"], after={"network": [{"subnet": "same"}]}))
    old["resource_changes"][0]["change"]["after_unknown"] = {
        "id": True,
        "network": [{"public_ip": True, "security_groups": True}],
    }
    observed = {
        "id": "node-a",
        "network": [{"subnet": "same", "public_ip": None, "security_groups": []}],
    }
    new = plan(change("nodes", ["no-op"], observed, observed))
    assert validate_recovery_plan(old, new).startswith("sha256:")
    new["resource_changes"][0]["change"]["after"]["network"][0]["subnet"] = "foreign"
    with pytest.raises(RuntimeError, match="settings"):
        validate_recovery_plan(old, new)
