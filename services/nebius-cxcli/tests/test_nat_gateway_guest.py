from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

MODULE = Path(__file__).resolve().parents[3] / "platform-infra/modules/nat-gateway"


@pytest.fixture
def guest(monkeypatch):
    modules = []
    for name, filename in (("nat_security", "nat_security.py"), ("nat_guest_test", "nat_guest.py")):
        spec = importlib.util.spec_from_file_location(name, MODULE / filename)
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        modules.append(module)
    return modules[-1]


@pytest.mark.parametrize(
    "ban",
    [
        "",
        "-s 192.0.2.2/32 -j DROP",
        "-s 192.0.2.0/24 -j DROP",
        "-j ACCEPT",
        "-s 192.0.2.2/32 -j RETURN",
    ],
)
def test_exact_static_rules_allow_only_individual_dynamic_bans(guest, monkeypatch, ban):
    desired = guest.rules({"source_cidrs": ["10.0.0.0/8"]}, "eth0")
    hooks = [
        ("filter", "INPUT", "CXCLI_NAT_INPUT"),
        ("filter", "FORWARD", "CXCLI_NAT_FORWARD"),
        ("nat", "POSTROUTING", "CXCLI_NAT_POSTROUTING"),
    ]

    def run(*args, **kwargs):
        chain = args[-1]
        if chain == guest.BAN_CHAIN:
            content = f"-N {chain}\n" + (f"-A {chain} {ban}\n" if ban else "")
        elif any(parent == chain for _, parent, _ in hooks):
            target = next(target for _, parent, target in hooks if parent == chain)
            content = f"-A {chain} -j {target}\n"
        else:
            content = "\n".join(
                f"-A {chain} " + " ".join(rule) for _, name, rule in desired if name == chain
            )
        return NS(returncode=0, stdout=content)

    monkeypatch.setattr(guest, "run", run)
    assert guest.exact_rules(desired, hooks) is (ban in ("", "-s 192.0.2.2/32 -j DROP"))
    input_rules = [rule for _, chain, rule in desired if chain == "CXCLI_NAT_INPUT"]
    ban_index = next(i for i, rule in enumerate(input_rules) if rule[-1] == guest.BAN_CHAIN)
    assert input_rules[ban_index + 1] == ["-p", "tcp", "--dport", "22", "-j", "ACCEPT"]
    assert input_rules[-1] == ["-j", "DROP"]


def test_guest_rejects_lost_ban_chain_and_displaced_input_hook(guest, monkeypatch):
    desired = guest.rules({"source_cidrs": ["10.0.0.0/8"]}, "eth0")
    monkeypatch.setattr(guest, "run", lambda *args, **kwargs: NS(returncode=1, stdout=""))
    assert not guest.exact_rules(desired, [])

    def observed(*args, **kwargs):
        chain = args[-1]
        content = "\n".join(
            f"-A {chain} " + " ".join(rule) for _, name, rule in desired if name == chain
        )
        if chain == "INPUT":
            content = "-A INPUT -j ACCEPT\n-A INPUT -j CXCLI_NAT_INPUT"
        return NS(returncode=0, stdout=content)

    monkeypatch.setattr(guest, "run", observed)
    assert not guest.exact_rules(desired, [("filter", "INPUT", "CXCLI_NAT_INPUT")])
