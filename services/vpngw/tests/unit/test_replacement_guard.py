import ast

import pytest
import yaml

from nebius_vpngw.deploy.replacement_guard import GUARD, UNITS, render


def projection():
    return {
        "account": {
            "username": "operator",
            "home": "/home/operator",
            "uid": 1001,
            "gid": 1001,
            "shell": "/bin/sh",
            "paths": ["/home/operator/.ssh/authorized_keys"],
        },
        "files": {
            "/home/operator/.ssh/authorized_keys": {
                "content": 'from="192.0.2.0/24" ssh-ed25519 customer-key\n',
                "uid": 1001,
                "gid": 1001,
                "mode": 0o600,
            }
        },
        "operators": {},
    }


def test_guard_precedes_packages_preserves_authorization_and_survives_reboot():
    base = yaml.safe_dump(
        {
            "users": [{"name": "ubuntu", "ssh_authorized_keys": ["removed-bootstrap-key"]}],
            "ssh_authorized_keys": ["removed-global-key"],
            "write_files": [],
            "packages": ["strongswan", "frr"],
            "runcmd": [
                ["systemctl", "start", "frr"],
                ["systemctl", "unmask", "frr.service"],
                ["systemctl", "enable", "nebius-vpngw-agent"],
                ["systemctl", "restart", "ssh"],
            ],
        }
    )
    result = render(base, operation="a" * 32, authorization=projection())
    assert "removed-bootstrap-key" not in result and "removed-global-key" not in result
    document = yaml.safe_load(result)
    assert document["bootcmd"][0][:2] == ["/usr/bin/python3", "-c"]
    boot = document["bootcmd"][0][2]
    ast.parse(boot)
    assert str(GUARD) in boot and "net.ipv4.ip_forward=0" in boot
    assert all(unit in boot for unit in UNITS)
    assert "ConditionPathExists=!" in boot
    assert ["systemctl", "start", "frr"] not in document["runcmd"]
    assert ["systemctl", "unmask", "frr.service"] in document["runcmd"]
    assert ["systemctl", "restart", "ssh"] in document["runcmd"]
    preserved = next(
        item for item in document["write_files"] if item["path"].endswith("/authorized_keys")
    )
    assert preserved["content"] == projection()["files"][preserved["path"]]["content"]
    assert preserved["owner"] == "1001:1001" and preserved["permissions"] == "0600"
    assert document["users"][0]["name"] == "operator"


def test_foreign_authorization_path_cannot_be_restored():
    source = projection()
    source["files"]["/etc/sudoers"] = source["files"].pop("/home/operator/.ssh/authorized_keys")
    with pytest.raises(ValueError, match="outside"):
        render("write_files: []", operation="a" * 32, authorization=source)


def test_product_sysctl_never_transiently_enables_forwarding_during_preparation():
    document = yaml.safe_load(
        render(
            yaml.safe_dump(
                {
                    "write_files": [
                        {
                            "path": "/etc/sysctl.d/99-zzz-vpngw.conf",
                            "content": "net.ipv4.ip_forward = 1\nnet.ipv4.conf.all.forwarding=1\n",
                            "owner": "root:root",
                            "permissions": "0644",
                        }
                    ]
                }
            ),
            operation="a" * 64,
            authorization=projection(),
        )
    )
    entry = next(
        item for item in document["write_files"] if item["path"].endswith("/99-zzz-vpngw.conf")
    )
    assert "forward = 1" not in entry["content"] and "forwarding=1" not in entry["content"]
    assert "net.ipv4.ip_forward = 0" in entry["content"]
    assert document["users"][0]["shell"] == "/bin/sh"
