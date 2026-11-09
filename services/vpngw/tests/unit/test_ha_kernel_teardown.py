"""Kernel cleanup admits exact managed identities, never broad selectors."""

from __future__ import annotations

import copy

import pytest

from nebius_vpngw.agent.vm_ha.kernel_teardown import deletions
from nebius_vpngw.agent.vm_ha.tunnel_observation import xfrm_records
from nebius_vpngw.ha_tunnel_policy import ConnectionPolicy, TunnelPath


@pytest.fixture
def kernel_scope():
    path = TunnelPath(
        "path", "xfrm0", 100, True, frozenset({"10.1.0.0/16"}), "", ("%any", "192.0.2.2")
    )
    policy = ConnectionPolicy("site", "static", (path,), path.destinations)
    state = {
        "src": "192.0.2.1",
        "dst": "192.0.2.2",
        "if_id": 100,
        "proto": "esp",
        "mode": "tunnel",
        "reqid": 1,
        "spi": "0x1234",
    }
    rule = {
        "src": "0.0.0.0/0",
        "dst": "0.0.0.0/0",
        "if_id": 100,
        "proto": "esp",
        "mode": "tunnel",
        "reqid": 1,
        "dir": "out",
        "index": 9,
        "tmpl_src": "192.0.2.1",
        "tmpl_dst": "192.0.2.2",
    }
    links = [{"ifname": "xfrm0", "linkinfo": {"info_kind": "xfrm", "info_data": {"if_id": 100}}}]
    addresses = [{"addr_info": [{"local": "192.0.2.1"}]}]
    return policy, [state], [rule], links, addresses


def test_scoped_deletions_preserve_unrelated_kernel_objects(kernel_scope):
    policy, states, rules, links, addresses = kernel_scope
    states += [{**states[0], "if_id": 700, "spi": "0x9999"}]
    rules += [{**rules[0], "if_id": 700, "index": 17}]
    before = copy.deepcopy((states, rules))
    commands = deletions(policy, states, rules, links, addresses)
    assert commands == (
        (
            "ip",
            "xfrm",
            "state",
            "delete",
            "src",
            "192.0.2.1",
            "dst",
            "192.0.2.2",
            "proto",
            "esp",
            "spi",
            "0x1234",
        ),
        ("ip", "xfrm", "policy", "delete", "index", "9", "dir", "out", "if_id", "100"),
    )
    assert (states, rules) == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("src", "203.0.113.1"),
        ("dst", "203.0.113.2"),
        ("mode", "transport"),
        ("proto", "ah"),
        ("reqid", 0),
        ("reqid", True),
        ("spi", 0),
        ("spi", 2**32),
        ("mark", "0x1/0xffffffff"),
        ("ctx", "unknown"),
    ],
)
def test_kernel_sa_requires_unambiguous_managed_scope(kernel_scope, field, value):
    kernel_scope[1][0][field] = value
    with pytest.raises(ValueError):
        deletions(*kernel_scope)


@pytest.mark.parametrize(
    "field,value",
    [
        ("tmpl_src", "203.0.113.1"),
        ("tmpl_dst", "203.0.113.2"),
        ("index", 0),
        ("index", None),
        ("dir", "unknown"),
        ("mark", 0),
    ],
)
def test_kernel_policy_requires_exact_owned_identity(kernel_scope, field, value):
    kernel_scope[2][0][field] = value
    with pytest.raises(ValueError):
        deletions(*kernel_scope)


@pytest.mark.parametrize(
    "links",
    [
        [],
        [{"ifname": "xfrm0"}],
        [{"ifname": "xfrm0", "linkinfo": {"info_kind": "xfrm", "info_data": {"if_id": 101}}}],
    ],
)
def test_wrong_or_missing_xfrm_interface_blocks_cleanup(kernel_scope, links):
    policy, states, rules, _, addresses = kernel_scope
    with pytest.raises(ValueError):
        deletions(policy, states, rules, links, addresses)


def test_kernel_rekey_overlap_has_separate_exact_deletions(kernel_scope):
    kernel_scope[1].append({**kernel_scope[1][0], "spi": "0x1235"})
    commands = deletions(*kernel_scope)
    assert [c[-1] for c in commands if c[2] == "state"] == ["0x1234", "0x1235"]


def test_cross_interface_sa_identity_collision_blocks_cleanup(kernel_scope):
    kernel_scope[1].append({**kernel_scope[1][0], "if_id": 777})
    with pytest.raises(ValueError, match="shared outside"):
        deletions(*kernel_scope)


def test_unestablished_trap_template_is_scoped_but_wildcard_sa_is_not(kernel_scope):
    kernel_scope[2][0]["tmpl_src"] = "0.0.0.0"
    assert len(deletions(*kernel_scope)) == 2
    kernel_scope[1][0]["src"] = "0.0.0.0"
    with pytest.raises(ValueError):
        deletions(*kernel_scope)


def test_policy_text_retains_deletion_identity_and_ambiguous_mark():
    raw = """src 0.0.0.0/0 dst 0.0.0.0/0
    dir out priority 375423 ptype main
    index 9 mark 0x1/0xffffffff
    tmpl src 192.0.2.1 dst 192.0.2.2
        proto esp reqid 1 mode tunnel
    if_id 0x64
"""
    (record,) = xfrm_records(raw, policy=True)
    assert record["index"] == "9"
    assert record["mark"] == "0x1/0xffffffff"
    assert record["if_id"] == "0x64"


@pytest.mark.parametrize(
    "metadata",
    [
        "priority 0",
        "action allow index 723 priority 0 share any flag  (0x00000000)",
    ],
)
def test_socket_bypass_policy_is_not_a_managed_teardown_object(metadata):
    raw = f"src 0.0.0.0/0 dst 0.0.0.0/0\n    socket in {metadata}\n"
    assert xfrm_records(raw, policy=True) == []
    with pytest.raises(ValueError, match="ambiguous XFRM socket"):
        xfrm_records(raw + "    if_id 0x64\n", policy=True)


def test_statistics_policy_request_id_keeps_exact_scope(kernel_scope):
    raw = """src 0.0.0.0/0 dst 0.0.0.0/0
    dir out action allow index 9 priority 375423 share any flag (0x00000000)
    tmpl src 192.0.2.1 dst 192.0.2.2
        proto esp reqid 1(0x00000001) mode tunnel
    if_id 0x64
"""
    policy, states, _, links, addresses = kernel_scope
    rules = xfrm_records(raw, policy=True)
    assert rules[0]["reqid"] == "1"
    assert len(deletions(policy, states, rules, links, addresses)) == 2


@pytest.mark.parametrize("request_id", ["1(0x00000002)", "1(garbled)", "1(0x1)truncated"])
def test_statistics_policy_rejects_ambiguous_request_id(request_id):
    raw = f"""src 0.0.0.0/0 dst 0.0.0.0/0
    dir out index 9
    tmpl src 192.0.2.1 dst 192.0.2.2
        proto esp reqid {request_id} mode tunnel
    if_id 0x64
"""
    with pytest.raises(ValueError):
        xfrm_records(raw, policy=True)


def test_text_policy_rejects_multiple_templates_before_cleanup():
    raw = """src 0.0.0.0/0 dst 0.0.0.0/0
    dir out index 9
    tmpl src 192.0.2.1 dst 192.0.2.2
        proto esp reqid 1 mode tunnel
    tmpl src 203.0.113.1 dst 203.0.113.2
        proto esp reqid 2 mode tunnel
    if_id 0x64
"""
    with pytest.raises(ValueError, match="ambiguous XFRM policy template"):
        xfrm_records(raw, policy=True)
