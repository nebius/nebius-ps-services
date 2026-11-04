from __future__ import annotations

import copy

import pytest
import yaml

from nebius_cxcli.infra_render import _materialize_mk8s_node_group_labels
from nebius_cxcli.mk8s_placement import (
    TAINT_EFFECTS,
    placement_pod_spec,
    prompt_node_placement,
    validate_node_placement,
)


def answers(mode="both", effect="NO_SCHEDULE"):
    result = [("placement.rule", mode)]
    if mode in {"label", "both"}:
        result += [
            ("node_labels.<new>.key", "scheduling.example.com/pool"),
            ("node_labels.<new>.value", "task"),
            ("node_labels.add_another", False),
        ]
    if mode in {"taint", "both"}:
        result += [
            ("taints[0].key", "scheduling.example.com/pool"),
            ("taints[0].value", "task"),
            ("taints[0].effect", effect),
            ("taints.add_another", False),
        ]
    return result


def run_script(script, *, stop_at=None, status="quit"):
    pending = list(script)
    seen = []
    emitted = []

    def prompt(path, current, **kwargs):
        suffix, value = pending.pop(0)
        assert path == "group." + suffix
        seen.append((path, current, kwargs))
        if len(seen) - 1 == stop_at:
            return current, status
        return value, "ok"

    result = prompt_node_placement(
        prefix="group", group_name="task-runners", prompt=prompt, emit=emitted.append
    )
    if stop_at is None:
        assert not pending
    return result, seen, emitted


@pytest.mark.parametrize("mode", ["none", "label", "taint", "both"])
@pytest.mark.parametrize("effect", list(TAINT_EFFECTS))
def test_placement_modes_persist_native_fields_and_render(mode, effect):
    (fields, status), seen, emitted = run_script(answers(mode, effect))
    assert status == "ok"
    assert set(fields) == (
        {"node_labels"}
        if mode == "label"
        else {"taints"}
        if mode == "taint"
        else {"node_labels", "taints"}
        if mode == "both"
        else set()
    )
    assert yaml.safe_load(yaml.safe_dump(fields)) == fields
    validate_node_placement(fields, path="group")
    pod = placement_pod_spec(fields)
    if mode in {"taint", "both"}:
        assert fields["taints"][0]["effect"] == effect
        assert pod["tolerations"][0] == {
            "key": "scheduling.example.com/pool",
            "operator": "Equal",
            "value": "task",
            "effect": TAINT_EFFECTS[effect],
        }
    if mode == "both":
        assert seen[4][1] == "scheduling.example.com/pool"
        assert seen[5][1] == "task"
        assert "nodeSelector" in emitted[-1]
    inputs = {"node_groups": {"task-runners": {**fields, "gpu": True}}}
    _materialize_mk8s_node_group_labels(component_id="mk8s", module_inputs=inputs)
    labels = inputs["node_groups"]["task-runners"]["node_labels"]
    assert labels["nebius.com/node-group"] == "task-runners"
    assert labels["nebius.com/gpu"] == "true"
    if "node_labels" in fields:
        assert labels["scheduling.example.com/pool"] == "task"


@pytest.mark.parametrize("status", ["back", "quit"])
@pytest.mark.parametrize("index", range(8))
def test_cancel_discards_all_partial_placement(index, status):
    (fields, control), _seen, _emitted = run_script(answers(), stop_at=index, status=status)
    assert control == status
    assert fields == {}


@pytest.mark.parametrize(
    "fields",
    [
        {"node_labels": []},
        {"node_labels": {"bad/key/more": "x"}},
        {"node_labels": {"Invalid.Prefix/key": "x"}},
        {"node_labels": {"kubernetes.io/custom-pool": "x"}},
        {"node_labels": {"example.k8s.io/pool": "x"}},
        {"node_labels": {"pool": True}},
        {"node_labels": {"pool": "x" * 64}},
        {"taints": {}},
        {"taints": [{"key": "pool", "effect": "NO_SCHEDULE"}]},
        {"taints": [{"key": "pool", "value": "x", "effect": "NoSchedule"}]},
        {"taints": [{"key": "pool", "value": "x", "effect": "EFFECT_UNSPECIFIED"}]},
        {"taints": [{"key": "pool", "value": 1, "effect": "NO_SCHEDULE"}]},
        {"taints": [{"key": "pool", "value": "x", "effect": "NO_SCHEDULE"}] * 2},
    ],
)
def test_invalid_authored_fields_fail_with_field_path(fields):
    with pytest.raises(ValueError, match=r"node_groups.task\."):
        validate_node_placement(fields, path="node_groups.task")


def test_valid_empty_value_and_distinct_effects_are_preserved():
    group = {
        "node_labels": {"example.com/empty": ""},
        "taints": [{"key": "pool", "value": "", "effect": effect} for effect in TAINT_EFFECTS],
    }
    before = copy.deepcopy(group)
    validate_node_placement(group, path="group")
    assert group == before


def test_duplicate_labels_reprompt_and_multiple_entries_survive():
    script = [
        ("placement.rule", "label"),
        ("node_labels.<new>.key", "pool"),
        ("node_labels.<new>.value", "task"),
        ("node_labels.add_another", True),
        ("node_labels.<new>.key", "pool"),
        ("node_labels.<new>.key", "zone"),
        ("node_labels.<new>.value", "a"),
        ("node_labels.add_another", False),
    ]
    (fields, status), _seen, emitted = run_script(script)
    assert fields == {"node_labels": {"pool": "task", "zone": "a"}}
    assert status == "ok"
    assert any("repeats an existing label key" in line for line in emitted)
