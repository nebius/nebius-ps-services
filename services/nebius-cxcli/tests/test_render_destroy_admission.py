from __future__ import annotations

import copy

import pytest

from destroy_fakes import SETTINGS, Store
from nebius_cxcli import cli
from nebius_cxcli.deployment_state import ObjectVersion
from nebius_cxcli.destroy_state import DestroyState


@pytest.mark.parametrize("status", ["running", "complete"])
def test_render_and_deploy_ignore_other_commands_local_destroy_record(monkeypatch, status):
    store = Store()
    state = DestroyState(store, SETTINGS)
    store.values[state.key] = ObjectVersion(
        {"schema": "nebius-cxcli.destroy.v3", "status": status}, "etag"
    )
    before = copy.deepcopy(store.values)

    def forbidden(*args, **kwargs):
        pytest.fail("Generic render/deploy must not read a previous destroy record")

    monkeypatch.setattr(DestroyState, "require_admission", forbidden)
    cli._require_soperator_lifecycle_scope({}, command="render")
    cli._require_soperator_lifecycle_scope({}, command="deploy")
    assert store.values == before and store.writes == []
