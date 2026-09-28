"""State loss must fail before cloud creation, without guessing IAM ownership."""

from types import SimpleNamespace

import pytest
from grpc import StatusCode
from nebius.aio.service_error import RequestError, RequestStatusExtended

from nebius_cxcli import deployment_iam as iam


def plan(*roles, actions=None):
    return {
        "resource_changes": [
            {
                "address": f'module.cluster.nebius_iam_v1_service_account.node_group["{role}"]',
                "type": "nebius_iam_v1_service_account",
                "change": {
                    "actions": actions or ["create"],
                    "after": {
                        "parent_id": "project-test",
                        "name": f"cluster-{role}",
                    },
                },
            }
            for role in roles
        ]
    }


def service_error(code):
    return RequestError(
        RequestStatusExtended(
            code=code,
            message="secret-provider-payload",
            details=[],
            request_id="",
            trace_id="",
            service_errors=[],
        )
    )


def setup_sdk(monkeypatch, responses):
    from nebius.api.nebius.iam import v1 as api

    calls, closed = [], []
    sdk = SimpleNamespace(sync_close=lambda: closed.append(True))
    monkeypatch.setattr(iam, "init_nebius_sdk", lambda **kw: sdk)

    def get(request, **kwargs):
        calls.append((request.parent_id, request.name, kwargs))

        def wait():
            value = responses[request.name]
            if isinstance(value, Exception):
                raise value
            return SimpleNamespace(metadata=SimpleNamespace(**value))

        return SimpleNamespace(wait=wait)

    monkeypatch.setattr(
        api, "ServiceAccountServiceClient", lambda sdk: SimpleNamespace(get_by_name=get)
    )
    return calls, closed


def test_reports_every_collision_without_adopting(monkeypatch):
    roles = ("system", "controller", "login", "accounting", "worker")
    calls, closed = setup_sdk(
        monkeypatch,
        {
            f"cluster-{role}": {
                "id": f"sa-{role}",
                "name": f"cluster-{role}",
                "parent_id": "project-test",
            }
            for role in roles
        },
    )
    with pytest.raises(RuntimeError) as failure:
        iam.assert_service_account_names_available(plan(*roles))
    for role in roles:
        assert f'node_group["{role}"]' in str(failure.value)
        assert f"sa-{role}" in str(failure.value)
    assert "Restore the original Terraform state" in str(failure.value)
    assert len(calls) == 5 and closed == [True]
    assert all(call[2]["timeout"] == 20 and call[2]["retries"] == 0 for call in calls)


def test_only_typed_not_found_admits_create(monkeypatch):
    _, closed = setup_sdk(monkeypatch, {"cluster-worker": service_error(StatusCode.NOT_FOUND)})
    iam.assert_service_account_names_available(plan("worker"))
    assert closed == [True]


@pytest.mark.parametrize(
    "error",
    [
        service_error(StatusCode.PERMISSION_DENIED),
        service_error(StatusCode.UNAVAILABLE),
        TimeoutError("secret-provider-payload"),
        RuntimeError("NOT_FOUND secret-provider-payload"),
    ],
)
def test_inconclusive_lookup_fails_closed_without_provider_payload(monkeypatch, error):
    _, closed = setup_sdk(monkeypatch, {"cluster-worker": error})
    with pytest.raises(RuntimeError, match="Cannot verify IAM name availability") as failure:
        iam.assert_service_account_names_available(plan("worker"))
    assert "secret-provider-payload" not in str(failure.value)
    assert failure.value.__context__ is None
    assert closed == [True]


@pytest.mark.parametrize(
    "metadata",
    [
        {},
        {"id": "sa-worker", "parent_id": "other-project", "name": "cluster-worker"},
        {"id": "sa-worker", "parent_id": "project-test", "name": "other-name"},
    ],
)
def test_rejects_incomplete_or_conflicting_identity(monkeypatch, metadata):
    setup_sdk(monkeypatch, {"cluster-worker": metadata})
    with pytest.raises(RuntimeError, match="incomplete or conflicting identity"):
        iam.assert_service_account_names_available(plan("worker"))


@pytest.mark.parametrize("actions", [["no-op"], ["update"], ["delete"], ["delete", "create"]])
def test_existing_state_binding_needs_no_name_lookup(monkeypatch, actions):
    monkeypatch.setattr(iam, "init_nebius_sdk", lambda **kw: pytest.fail("unexpected SDK lookup"))
    iam.assert_service_account_names_available(plan("worker", actions=actions))


def test_unknown_project_fails_before_sdk_lookup(monkeypatch):
    value = plan("worker")
    value["resource_changes"][0]["change"]["after"]["parent_id"] = None
    monkeypatch.setattr(iam, "init_nebius_sdk", lambda **kw: pytest.fail("unexpected SDK lookup"))
    with pytest.raises(RuntimeError, match="without project, name and address"):
        iam.assert_service_account_names_available(value)


@pytest.mark.parametrize("recovering", [False, True])
def test_deployment_stops_before_apply_on_initial_admission_and_recovery(
    monkeypatch, tmp_path, recovering
):
    import io

    from rich.console import Console

    from nebius_cxcli.deployment_cli import DeployOptions, _CliDeploymentExecutor
    from nebius_cxcli.deployment_plan import DeploymentAction, DeploymentStageKind

    setup_sdk(
        monkeypatch,
        {
            "cluster-worker": {
                "id": "sa-worker",
                "parent_id": "project-test",
                "name": "cluster-worker",
            }
        },
    )

    def save_plan(*args, plan_file, **kwargs):
        plan_file.parent.mkdir(parents=True)
        plan_file.write_bytes(b"saved plan")

    executor = _CliDeploymentExecutor(
        config={},
        paths=SimpleNamespace(infra_dir=tmp_path),
        manifest={},
        options=DeployOptions(),
        lease=SimpleNamespace(assert_held=lambda: None),
    )
    executor.cli = SimpleNamespace(
        progress_console=Console(file=io.StringIO()),
        _config_has_enabled_infra_components=lambda _: True,
        terraform_plan=save_plan,
        terraform_show_json=lambda *a, **kw: plan("worker"),
        _deploy_generated_artifacts=lambda *a, **kw: pytest.fail("unexpected apply"),
    )
    executor.plan = SimpleNamespace(action=DeploymentAction.INSTALL, target_ref=None)
    with pytest.raises(RuntimeError, match="existing service accounts"):
        if recovering:
            executor.execute(
                SimpleNamespace(stage=SimpleNamespace(name=DeploymentStageKind.RECONCILE)),
                recovering=True,
            )
        else:
            executor.admit(executor.plan)
