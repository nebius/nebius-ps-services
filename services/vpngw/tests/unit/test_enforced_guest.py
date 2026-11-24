import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from nebius_vpngw.agent.enforced_routing import PROTOCOL, RULE_BASE, PolicyRouting, normalize_rule
from nebius_vpngw.ha_tunnel_policy import connection_policies


class Kernel:
    def __init__(self):
        self.rules = []
        self.routes = []
        self.links = []
        self.guard = []
        self.commands = []
        self.fail_add = False
        self.fail_route_delete = False

    def __call__(self, argv, **kwargs):
        self.commands.append(argv)
        value = []
        if argv[:6] == ["ip", "-j", "-4", "-details", "-N", "rule"]:
            value = sorted(self.rules, key=lambda r: r["priority"])
        elif argv[:4] == ["ip", "-j", "-4", "route"]:
            value = self.routes
        elif argv[:4] == ["ip", "-d", "-j", "link"]:
            value = self.links
        elif argv[:4] == ["ip", "-4", "route", "del"]:
            if self.fail_route_delete:
                return SimpleNamespace(returncode=1, stdout="")
            matches = [
                row
                for row in self.routes
                if row["dst"].replace("default", "0.0.0.0/0") == argv[4]
                and str(row["table"]) == argv[argv.index("table") + 1]
                and all(
                    flag not in argv or str(row.get(field, default)) == argv[argv.index(flag) + 1]
                    for flag, field, default in (
                        ("dev", "dev", None),
                        ("nhid", "nhid", None),
                        ("via", "gateway", None),
                        ("proto", "protocol", "boot"),
                        ("metric", "metric", 0),
                        ("scope", "scope", "global"),
                    )
                )
            ]
            assert len(matches) == 1, "Deletion must select exactly one native route"
            self.routes.remove(matches[0])
        elif argv[:3] == ["ip", "-4", "rule"]:
            if self.fail_add and argv[3] == "add":
                self.fail_add = False
                return SimpleNamespace(returncode=1, stdout="")
            row = {
                "priority": int(argv[argv.index("priority") + 1]),
                "src": argv[argv.index("from") + 1],
                "dst": argv[argv.index("to") + 1],
                "iif": argv[argv.index("iif") + 1],
                "protocol": PROTOCOL,
            }
            row.update(
                {"table": int(argv[argv.index("lookup") + 1])}
                if "lookup" in argv
                else {"action": "unreachable"}
            )
            if "suppress_prefixlength" in argv:
                row["suppress_prefixlength"] = int(argv[argv.index("suppress_prefixlength") + 1])
            if argv[3] == "add":
                self.rules.append(row)
            else:
                self.rules.remove(row)
        elif argv[:3] == ["nft", "-j", "list"]:
            value = {"nftables": self.guard}
        elif argv[:2] == ["nft", "-j"] and "--check" not in argv:
            value = json.loads(Path(argv[-1]).read_text())
            self.guard = [o["add"] for o in value["nftables"][2:]]
        return SimpleNamespace(returncode=0, stdout=json.dumps(value))


def test_native_default_rule_omits_zero_destination():
    assert normalize_rule(
        {
            "priority": RULE_BASE,
            "src": "10.0.1.0",
            "srclen": 24,
            "iif": "eth0",
            "table": "12345",
            "protocol": "196",
        }
    ) == {
        "priority": RULE_BASE,
        "src": "10.0.1.0/24",
        "dst": "0.0.0.0/0",
        "iif": "eth0",
        "table": 12345,
        "protocol": 196,
    }


@pytest.mark.parametrize("action", ["7", 7])
def test_native_numeric_unreachable_action(action):
    assert (
        normalize_rule(
            {
                "priority": RULE_BASE + 1,
                "src": "10.0.1.0",
                "srclen": 24,
                "dst": "198.18.0.0",
                "dstlen": 24,
                "iif": "eth0",
                "protocol": "196",
                "action": action,
            }
        )["action"]
        == "unreachable"
    )


def protection(tmp_path, sample_config, kernel):
    cfg = deepcopy(sample_config)
    cfg["connections"][0]["enforced_routing"] = {
        "source_subnets": ["10.0.1.0/24", "10.0.2.0/24"],
        "destinations": ["0.0.0.0/0"],
    }
    return cfg, PolicyRouting(
        tmp_path / "enforced.json", cluster="test", policies=connection_policies(cfg), run=kernel
    )


def test_persistent_rules_are_source_destination_and_ingress_scoped(tmp_path, sample_config):
    kernel = Kernel()
    cfg, owner = protection(tmp_path, sample_config, kernel)
    owner.install()
    owner.verify()
    assert len(kernel.rules) == 4
    assert {r["src"] for r in kernel.rules} == {"10.0.1.0/24", "10.0.2.0/24"}
    assert all(r["dst"] == "0.0.0.0/0" and r["iif"] == "eth0" for r in kernel.rules)
    assert sum(r.get("action") == "unreachable" for r in kernel.rules) == 2
    before = deepcopy(kernel.rules)
    cfg["connections"][0]["enforced_routing"]["source_subnets"] = ["10.0.3.0/24"]
    updated = PolicyRouting(
        owner.path, cluster="test", policies=connection_policies(cfg), run=kernel
    )
    updated.install()
    assert all(r in kernel.rules for r in before)
    assert len(updated.read()) == 3  # Old obligations remain until cloud retirement.
    kernel.rules.clear()  # Kernel reboot; protected ledger survives.
    updated.install()
    assert len(kernel.rules) == 6


def test_interrupted_rule_install_resumes_with_unchanged_ownership(tmp_path, sample_config):
    kernel = Kernel()
    _, owner = protection(tmp_path, sample_config, kernel)
    kernel.fail_add = True
    with pytest.raises(RuntimeError, match="mutation"):
        owner.install()
    assert owner.read() and kernel.guard
    owner.install()
    owner.verify()


def test_foreign_rule_and_table_rejected_before_mutation(tmp_path, sample_config):
    kernel = Kernel()
    _, owner = protection(tmp_path, sample_config, kernel)
    kernel.rules = [{"priority": RULE_BASE, "src": "all", "table": 42}]
    with pytest.raises(RuntimeError, match="unowned policy rule"):
        owner.install()
    assert not owner.path.exists()
    kernel.rules = []
    kernel.routes = [{"table": owner.policies[0].enforced_table, "dst": "default", "dev": "eth0"}]
    with pytest.raises(RuntimeError, match="unowned routes"):
        owner.install()
    assert not owner.path.exists()


def test_retirement_waits_for_explicit_cloud_completion(tmp_path, sample_config):
    kernel = Kernel()
    cfg, owner = protection(tmp_path, sample_config, kernel)
    owner.install()
    cfg["connections"][0].pop("enforced_routing")
    removed = PolicyRouting(
        owner.path, cluster="test", policies=connection_policies(cfg), run=kernel
    )
    removed.install()
    assert kernel.rules  # Configuration removal alone cannot remove protection.
    removed.retire()  # Caller has independently completed cloud restoration.
    assert not kernel.rules and not removed.read()
    removed.install()
    removed.verify()


def test_shared_ordinary_tier_retains_selected_guards_and_retires_independently(
    tmp_path, sample_config
):
    kernel = Kernel()
    cfg, _ = protection(tmp_path, sample_config, kernel)
    conn = cfg["connections"][0]
    conn["remote_prefixes"] = ["0.0.0.0/0", "198.18.0.0/24", "198.18.0.128/25"]
    conn["enforced_routing"]["destinations"] = list(conn["remote_prefixes"])
    owner = PolicyRouting(
        tmp_path / "scopes.json", cluster="test", policies=connection_policies(cfg), run=kernel
    )
    owner.install()
    ordinary = [r for r in kernel.rules if r["src"] == "0.0.0.0/0"]
    selected = [r for r in kernel.rules if r["src"] != "0.0.0.0/0"]
    assert len(ordinary) == 4
    assert max(r["priority"] for r in selected) < min(r["priority"] for r in ordinary)
    assert [r["suppress_prefixlength"] for r in sorted(ordinary, key=lambda r: r["priority"])] == [
        25,
        24,
        24,
        23,
    ]
    assert all(r["src"] == "0.0.0.0/0" and r["iif"] == "eth0" for r in ordinary)
    assert all(r["dst"] != "0.0.0.0/0" for r in ordinary)
    # Removing ordinary intent retains selected protection and the old ordinary
    # routing obligations until the caller commits the cloud transition.
    conn["remote_prefixes"] = []
    changed = PolicyRouting(
        owner.path, cluster="test", policies=connection_policies(cfg), run=kernel
    )
    changed.install()
    assert all(r in kernel.rules for r in ordinary)
    changed.retire()
    assert kernel.rules == selected
    changed.verify()


def test_native_suppressor_json_and_foreign_selector_are_exact():
    row = dict(
        priority=30016,
        src="all",
        dst="198.18.0.0/24",
        iif="eth0",
        table="254",
        protocol="196",
        suppress_prefixlen=24,
    )
    assert normalize_rule(row)["suppress_prefixlength"] == 24
    assert normalize_rule({**row, "suppress_ifgroup": 1}) == {}
    assert normalize_rule({**row, "suppress_prefixlength": 23}) == {}


def native_bgp_route(tmp_path, sample_config, *, mode="bgp"):
    kernel = Kernel()
    _, owner = protection(tmp_path, sample_config, kernel)
    owner.policies = tuple(replace(p, mode=mode) for p in owner.policies)
    path = owner.policies[0].paths[0]
    kernel.links = [
        {
            "ifname": path.interface,
            "linkinfo": {"info_kind": "xfrm", "info_data": {"if_id": hex(path.if_id)}},
        }
    ]
    owner.install()
    # Native iproute2 6.1 / FRR 10.7 route dump: a single resolved nexthop
    # carries its kernel object ID as well as the exact gateway and device.
    kernel.routes = [
        {
            "dst": "default",
            "nhid": 17,
            "gateway": path.peer,
            "dev": path.interface,
            "table": str(owner.policies[0].enforced_table),
            "protocol": "bgp",
            "metric": 20,
            "flags": [],
        }
    ]
    return owner, kernel


def test_native_bgp_nexthop_object_retains_exact_path_authority(tmp_path, sample_config):
    owner, kernel = native_bgp_route(tmp_path, sample_config)
    owner.verify()
    kernel.routes[0].pop("nhid")
    owner.verify()


def shared_prefix_transition(tmp_path, sample_config, old_mode, change):
    kernel = Kernel()
    _, owner = protection(tmp_path, sample_config, kernel)
    owner.policies = tuple(replace(p, mode=old_mode) for p in owner.policies)
    path = owner.policies[0].paths[0]
    new_path = replace(path, peer="169.254.250.2") if change == "peer" else path
    new_mode = ("bgp" if old_mode == "static" else "static") if change == "mode" else old_mode
    kernel.links = [
        {
            "ifname": path.interface,
            "linkinfo": {"info_kind": "xfrm", "info_data": {"if_id": path.if_id}},
        }
    ]
    owner.install()

    def route(mode, peer, nhid):
        row = {"dst": "default", "dev": path.interface, "table": owner.policies[0].enforced_table}
        if mode == "bgp":
            row.update(protocol="bgp", gateway=peer, nhid=nhid, metric=20)
        else:
            row["scope"] = "link"  # Native boot protocol and metric zero are omitted.
        return row

    old_route = route(old_mode, path.peer, 17)
    kernel.routes = [old_route]
    changed = PolicyRouting(
        owner.path,
        cluster="test",
        policies=tuple(replace(p, mode=new_mode, paths=(new_path,)) for p in owner.policies),
        run=kernel,
    )
    changed.install()
    successor = route(new_mode, new_path.peer, 18)
    kernel.routes.append(successor)
    return changed, kernel, old_route, successor


@pytest.mark.parametrize("old_mode,change", [("static", "mode"), ("bgp", "mode"), ("bgp", "peer")])
def test_retirement_preserves_exact_successor_route_not_just_shared_prefix(
    tmp_path, sample_config, old_mode, change
):
    owner, kernel, obsolete, successor = shared_prefix_transition(
        tmp_path, sample_config, old_mode, change
    )
    owner.retire()
    assert kernel.routes == [successor]
    assert obsolete not in kernel.routes
    assert owner.read().keys() == owner.desired().keys()
    owner.verify()
    deletion = next(c for c in kernel.commands if c[:4] == ["ip", "-4", "route", "del"])
    assert deletion[deletion.index("proto") + 1] == obsolete.get("protocol", "boot")
    assert deletion[deletion.index("metric") + 1] == str(obsolete.get("metric", 0))


def test_failed_shared_prefix_retirement_keeps_predecessor_authority(tmp_path, sample_config):
    owner, kernel, obsolete, successor = shared_prefix_transition(
        tmp_path, sample_config, "static", "mode"
    )
    previous = owner.read()
    kernel.fail_route_delete = True
    with pytest.raises(RuntimeError, match="mutation failed"):
        owner.retire()
    assert owner.read() == previous
    assert kernel.routes == [obsolete, successor]
    kernel.fail_route_delete = False
    owner.retire()
    assert kernel.routes == [successor]
    owner.verify()


@pytest.mark.parametrize(
    "change",
    [
        {"nhid": 0},
        {"nhid": -1},
        {"nhid": 2**32},
        {"nhid": True},
        {"nhid": "17"},
        {"dev": "eth0"},
        {"gateway": "198.18.1.1"},
        {"gateway": None},
        {"protocol": "static"},
        {"flags": ["linkdown"]},
        {"nexthops": []},
        {"encap": {}},
    ],
)
def test_native_nexthop_metadata_never_admits_foreign_routes(tmp_path, sample_config, change):
    owner, kernel = native_bgp_route(tmp_path, sample_config)
    kernel.routes[0].update(change)
    with pytest.raises(RuntimeError, match="unowned route"):
        owner.verify()


def test_static_route_does_not_gain_nexthop_object_authority(tmp_path, sample_config):
    owner, kernel = native_bgp_route(tmp_path, sample_config, mode="static")
    kernel.routes[0].update(protocol="boot")
    kernel.routes[0].pop("gateway")
    with pytest.raises(RuntimeError, match="unowned route"):
        owner.verify()


def test_selected_finite_route_cannot_fall_through_to_broader_route(tmp_path, sample_config):
    kernel = Kernel()
    cfg, owner = protection(tmp_path, sample_config, kernel)
    owner.install()  # Default scopes already have durable priorities.
    cfg["connections"][0]["enforced_routing"]["destinations"].append("198.18.0.0/24")
    updated = PolicyRouting(
        owner.path, cluster="test", policies=connection_policies(cfg), run=kernel
    )
    updated.install()
    for source in ("10.0.1.0/24", "10.0.2.0/24"):
        rules = [r for r in kernel.rules if r["src"] == source]
        finite = [r for r in rules if r["dst"] == "198.18.0.0/24"]
        broad = [r for r in rules if r["dst"] == "0.0.0.0/0"]
        assert max(r["priority"] for r in finite) < min(r["priority"] for r in broad)
        lookup = next(r for r in finite if "table" in r)
        assert lookup["suppress_prefixlength"] == 23
        assert any(r.get("action") == "unreachable" for r in finite)
    before = deepcopy(kernel.rules)
    kernel.rules.clear()
    updated.install()
    assert sorted(kernel.rules, key=str) == sorted(before, key=str)
