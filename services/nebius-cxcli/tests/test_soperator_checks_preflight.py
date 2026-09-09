"""The checks handoff and staged executor must bind the same target writers."""

import copy

import pytest
import yaml

from nebius_cxcli import soperator_checks_preflight as preflight
from nebius_cxcli.soperator_checks import SoperatorChecksExecution


@pytest.fixture
def rendered_writers(tmp_path, monkeypatch):
    child = {"namespace": "flux-system", "releaseName": "cxcli-product"}
    monkeypatch.setattr(
        preflight, "rendered_soperator_graph_contract", lambda _: {"releases": [child]}
    )
    outer = {
        "kind": "HelmRelease",
        "metadata": {"namespace": "flux-system", "name": "custom-umbrella", "uid": "outer"},
        "spec": {
            "values": {"slurmCluster": {"enabled": True}},
            "postRenderers": [
                {
                    "kustomize": {
                        "patches": [
                            {
                                "patch": yaml.safe_dump(
                                    [
                                        {
                                            "op": "replace",
                                            "path": "/metadata/name",
                                            "value": "cxcli-product",
                                        }
                                    ]
                                )
                            }
                        ]
                    }
                }
            ],
        },
    }
    path = tmp_path / "helmrelease.yaml"
    path.write_text(yaml.safe_dump(outer))
    return path, outer


def test_inline_umbrella_transfers_to_target_graph(rendered_writers):
    path, outer = rendered_writers
    writers = preflight._soperator_checks_target_writers(path.parent)
    assert writers == (("flux-system", "custom-umbrella"), ("flux-system", "cxcli-product"))
    runner = SoperatorChecksExecution.__new__(SoperatorChecksExecution)
    runner.state = {"sourceWriters": [{"kind": "helmrelease", **outer["metadata"]}]}
    runner._get = lambda *_: outer
    runner._save = lambda: None
    runner.complete_source_handoff(writers)


@pytest.mark.parametrize("ambiguous", [False, True])
def test_missing_or_ambiguous_umbrella_fails_handoff(rendered_writers, ambiguous):
    path, outer = rendered_writers
    if ambiguous:
        other = copy.deepcopy(outer)
        other["metadata"]["name"] = "second-umbrella"
        path.write_text(yaml.safe_dump_all([outer, other]))
    else:
        path.write_text("")
    with pytest.raises(ValueError, match="exactly one target outer"):
        preflight._soperator_checks_target_writers(path.parent)


def test_unrelated_inline_app_is_not_a_target_writer(rendered_writers):
    path, outer = rendered_writers
    other = {
        "kind": "HelmRelease",
        "metadata": {"namespace": "apps", "name": "ordinary"},
        "spec": {"values": {"enabled": True}},
    }
    path.write_text(yaml.safe_dump_all([outer, other]))
    assert ("apps", "ordinary") not in preflight._soperator_checks_target_writers(path.parent)
