"""Persistent boot admission for an uncommitted replacement candidate."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from .. import boot_policy

GUARD = Path("/etc/nebius-vpngw/replacement-guard.json")
RELEASED = Path("/etc/nebius-vpngw/replacement-released.json")
UNITS = (
    "strongswan-starter.service",
    "strongswan.service",
    "frr.service",
    "nebius-vpngw-agent.service",
    "nebius-vpngw-health-monitor.service",
    "nebius-vpngw-fix-routes.service",
    "nebius-vpngw-fix-routes.timer",
    "nebius-vpngw-vm-ha.service",
    "nebius-vpngw-vm-ha-peer.service",
    "nebius-vpngw-vm-ha-rearm.service",
    "nebius-vpngw-vm-ha-rearm.timer",
)


def render(cloud_init: str, *, operation: str, authorization: dict[str, Any]) -> str:
    """Preserve the public authorization projection; never seed a YAML bootstrap key.

    The guard runs during bootcmd, before package installation or runcmd, and on
    every reboot until the exact transaction publishes its release receipt.
    """
    if not re.fullmatch(r"[a-f0-9]{32,64}", operation):
        raise ValueError("Replacement operation identity is invalid")
    account = authorization["account"]
    if (
        not re.fullmatch(r"[a-z_][a-z0-9_-]{0,63}", account["username"])
        or type(account["uid"]) is not int
        or account["uid"] <= 0
        or type(account["gid"]) is not int
        or account["gid"] < 0
        or not Path(account["shell"]).is_absolute()
        or ".." in Path(account["shell"]).parts
        or not Path(account["home"]).is_absolute()
        or ".." in Path(account["home"]).parts
    ):
        raise ValueError("Replacement management account metadata is invalid")
    document = yaml.safe_load(cloud_init)
    # A late sysctl override is insufficient: an earlier forwarding=1 would
    # transiently admit traffic. Normalize the product-owned boot inputs before
    # cloud-init package hooks can restart systemd-sysctl or UFW.
    for entry in document.get("write_files", []):
        if entry["path"].lstrip("/") in boot_policy.OWNED:
            lines = entry["content"].splitlines()
            entry["content"] = (
                "\n".join(
                    line
                    for line in lines
                    if boot_policy._forward_setting(line) is None and line != boot_policy.HA_MARKER
                )
                + "\n"
                + boot_policy.HA_MARKER
                + "\nnet.ipv4.ip_forward = 0\n"
            )
    # Source authorization is complete and authoritative, including a removed
    # original bootstrap key and authorized_keys options controlled by customers.
    for user in document.get("users", []):
        if isinstance(user, dict):
            user.pop("ssh_authorized_keys", None)
    document.pop("ssh_authorized_keys", None)
    document["users"] = [
        {
            "name": account["username"],
            "uid": account["uid"],
            "homedir": account["home"],
            "shell": account["shell"],
            "lock_passwd": True,
            "sudo": "ALL=(ALL) NOPASSWD:ALL",
        }
    ]
    captured_paths = set(authorization["files"])
    files = [
        entry for entry in document.get("write_files", []) if entry["path"] not in captured_paths
    ]
    for path, item in authorization["files"].items():
        if item is None:
            continue
        if not Path(path).is_relative_to(Path(account["home"])):
            raise ValueError("Authorization projection is outside the management home")
        files.append(
            {
                "path": path,
                "content": item["content"],
                "permissions": f"{item['mode']:04o}",
                "owner": f"{item['uid']}:{item['gid']}",
            }
        )
    roster = {"schema": 1, "username": account["username"], "operators": authorization["operators"]}
    files.append(
        {
            "path": "/var/lib/nebius-vpngw/access/operators.json",
            "content": json.dumps(roster, sort_keys=True) + "\n",
            "permissions": "0600",
            "owner": "root:root",
        }
    )
    files.append(
        {
            "path": "/var/lib/nebius-vpngw/access/replacement.json",
            "content": json.dumps({"operation": operation}, sort_keys=True, separators=(",", ":"))
            + "\n",
            "permissions": "0600",
            "owner": "root:root",
        }
    )
    # The new sshd must resolve exactly the captured authorization files. Refuse
    # ambiguous path syntax instead of producing a different login policy.
    if any(re.search(r'[\s"\\\x00-\x1f]', path) for path in account["paths"]):
        raise ValueError("Replacement authorization paths contain unsupported characters")
    for entry in files:
        if entry["path"] == "/etc/ssh/sshd_config":
            text = entry["content"]
            text = re.sub(r"(?im)^\s*AuthorizedKeysFile\s+.*$", "", text)
            entry["content"] = "AuthorizedKeysFile " + " ".join(account["paths"]) + "\n" + text
    files.append(
        {
            "path": "/etc/sysctl.d/99-zzzz-vpngw-replacement.conf",
            "content": "net.ipv4.ip_forward=0\nnet.ipv6.conf.all.forwarding=0\n",
            "permissions": "0644",
            "owner": "root:root",
        }
    )
    # bootcmd is independent of the package being installed. All product writers
    # remain condition-gated across package postinst hooks, cloud-init, and later boots.
    boot = (
        """import grp,json,os,pwd,subprocess
from pathlib import Path
account=ACCOUNT
operation=OPERATION
released=Path(RELEASED)
guard=Path(GUARD)
if released.exists():
    if json.loads(released.read_text()) != {"operation":operation}: raise SystemExit("foreign replacement release")
else:
    access=Path('/var/lib/nebius-vpngw/access')
    access.mkdir(parents=True,mode=0o700,exist_ok=True)
    os.chmod(access,0o700)
    guard.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(guard,os.O_CREAT|os.O_WRONLY|os.O_TRUNC|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"w") as stream: json.dump({"operation":operation},stream);stream.flush();os.fsync(stream.fileno())
    for unit in UNITS:
        directory=Path("/etc/systemd/system")/(unit+".d")
        directory.mkdir(parents=True,exist_ok=True)
        (directory/"00-vpngw-replacement.conf").write_text("[Unit]\\nConditionPathExists=!"+str(guard)+"\\n")
    subprocess.run(["systemctl","daemon-reload"],check=True)
    subprocess.run(["sysctl","-w","net.ipv4.ip_forward=0","net.ipv6.conf.all.forwarding=0"],check=True)
    try: group=grp.getgrgid(account["gid"])
    except KeyError:
        subprocess.run(["groupadd","--gid",str(account["gid"]),account["username"]],check=True)
    try: user=pwd.getpwnam(account["username"])
    except KeyError:
        subprocess.run(["useradd","--uid",str(account["uid"]),"--gid",str(account["gid"]),"--home-dir",account["home"],"--create-home","--shell",account["shell"],account["username"]],check=True)
        user=pwd.getpwnam(account["username"])
    if (user.pw_uid,user.pw_gid,user.pw_dir,user.pw_shell)!=(account["uid"],account["gid"],account["home"],account["shell"]):
        raise SystemExit("replacement management account differs from source")
""".replace("ACCOUNT", repr(account))
        .replace("OPERATION", repr(operation))
        .replace("RELEASED", repr(str(RELEASED)))
        .replace("GUARD", repr(str(GUARD)))
        .replace("UNITS", repr(UNITS))
    )
    document["bootcmd"] = [["/usr/bin/python3", "-c", boot], *document.get("bootcmd", [])]
    commands = []
    for command in document.get("runcmd", []):
        text = command if isinstance(command, str) else " ".join(map(str, command))
        guarded_service = any(unit.removesuffix(".service") in text for unit in UNITS)
        if (
            "systemctl" in text
            and guarded_service
            and any(word in text for word in ("start", "enable"))
        ):
            continue
        commands.append(command)
    commands.append(
        ["install", "-d", "-o", "root", "-g", "root", "-m", "0700", "/var/lib/nebius-vpngw/access"]
    )
    document["runcmd"] = commands
    document["write_files"] = files
    return "#cloud-config\n" + yaml.safe_dump(document, sort_keys=False)
