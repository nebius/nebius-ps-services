"""Declared fixture setup, executed by the authentic predecessor interpreter."""

import argparse
import inspect
import json
import shlex
import subprocess
from importlib import metadata
from pathlib import Path

import yaml


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("static", "bgp"), required=True)
    parser.add_argument("--topology", choices=("ordinary", "ha"), required=True)
    parser.add_argument("--source-wheel", type=Path, required=True)
    args = parser.parse_args()

    # Docker initially supplies the link; explicitly hand its unchanged address
    # and gateway to networkd before declaring the predecessor checkpoint.
    def command(*argv):
        return subprocess.check_output(argv, text=True)

    link = json.loads(command("ip", "-j", "-4", "addr", "show", "eth0"))[0]
    addresses = [
        f"{a['local']}/{a['prefixlen']}" for a in link["addr_info"] if a["scope"] == "global"
    ]
    gateway = json.loads(command("ip", "-j", "route", "show", "default"))[0]["gateway"]
    network = Path("/etc/netplan/50-cloud-init.yaml")
    network.parent.mkdir(exist_ok=True)
    network.write_text(
        yaml.safe_dump(
            {
                "network": {
                    "version": 2,
                    "renderer": "networkd",
                    "ethernets": {
                        "eth0": {
                            "addresses": addresses,
                            "routes": [{"to": "default", "via": gateway}],
                        }
                    },
                }
            }
        )
    )
    network.chmod(0o600)
    command("ip", "route", "flush", "default")
    command("netplan", "apply")
    from nebius_vpngw.agent.state_store import StateStore
    from nebius_vpngw.config_loader import merge_with_peer_configs
    from nebius_vpngw.deploy.vm_manager import VMManager

    fixture = json.loads(Path("/fixture/previous_release_0_6_0_ordinary.json").read_text())
    local = fixture["cases"][args.mode]["local_config"]
    plan = merge_with_peer_configs(local, [])
    instance = next(iter(plan.iter_instance_configs()))
    config = yaml.safe_load(instance.config_yaml)
    root = Path("/etc/nebius-vpngw")
    root.mkdir(mode=0o700, exist_ok=True)
    (root / "config-resolved.yaml").write_text(instance.config_yaml)
    (root / "config-resolved.yaml").chmod(0o600)
    StateStore(root / "last-applied.json").save_last_applied(config)
    # Render the predecessor's own bootstrap; do not copy candidate assets into it.
    options = {"local_prefixes": ["10.10.0.0/16"]}
    if "vm_ha" in inspect.signature(VMManager._build_cloud_init).parameters:
        options["vm_ha"] = args.topology == "ha"
    if "ingress" in inspect.signature(VMManager._build_cloud_init).parameters:
        from nebius_vpngw import ingress_policy

        options["ingress"] = ingress_policy.from_config(config)
    cloud = yaml.safe_load(VMManager._build_cloud_init(VMManager.__new__(VMManager), **options))
    owned = ("/etc/systemd/system/", "/usr/local/bin/", "/usr/lib/tmpfiles.d/")
    files = {}
    for item in cloud["write_files"]:
        path = Path(item["path"])
        if str(path).startswith(owned):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(item["content"])
            path.chmod(int(str(item.get("permissions", "0644")), 8))
            files[str(path)] = __import__("hashlib").sha256(path.read_bytes()).hexdigest()
    # Record checkpoint bytes emitted by the old codec, without claiming a
    # cloud-owned HA deployment or admitting any forwarding in this fixture.
    if metadata.version("nebius-vpngw") == "0.6.0":
        from nebius_vpngw.agent.main import controller_checkpoint_to_dict
    else:
        from nebius_vpngw.agent.vm_ha_checkpoint import controller_checkpoint_to_dict
    from nebius_vpngw.agent.vm_ha_controller import ControllerCheckpoint

    checkpoint = controller_checkpoint_to_dict(ControllerCheckpoint(sequence=7))
    Path("/qualification/predecessor-checkpoint.json").write_text(json.dumps(checkpoint))
    if args.topology == "ha":
        # 0.6.0's cloud-init predates its HA switch. Install the original release's
        # HA assets through that release's own wheel installer in every HA case.
        from nebius_vpngw.deploy.ssh_push import (
            _VM_HA_SERVICE_ASSET_DESTINATIONS,
            SSHPush,
        )

        subprocess.run(
            shlex.split(SSHPush._install_vm_ha_assets_from_artifact_command(str(args.source_wheel))),
            check=True, timeout=30,
        )
        for _name, destination, _mode in _VM_HA_SERVICE_ASSET_DESTINATIONS:
            files[destination] = __import__("hashlib").sha256(Path(destination).read_bytes()).hexdigest()
        # These exact local lock/fence records are fixture setup. They represent
        # no cloud ownership or successful deployment of a serving HA pair.
        state = Path("/var/lib/nebius-vpngw/vm-ha")
        (state / "mtls").mkdir(parents=True, mode=0o700, exist_ok=True)
        Path("/run/nebius-vpngw").mkdir(mode=0o700, exist_ok=True)
        expected = {
            "cluster_id": "fixture-cluster",
            "node_id": "fixture-node",
            "generation_id": "d" * 64,
            "operation_id": "e" * 64,
            "host_id": "fixture-host",
        }
        records = {
            "controller-checkpoint.json": checkpoint,
            "apply.lock": {
                "schema": "nebius-vpngw/vm-ha-apply-lock-v2",
                "apply_locked": True,
                **{k: v for k, v in expected.items() if k != "host_id"},
            },
            "guard.json": {
                "schema": "nebius-vpngw/vm-ha-status-v1",
                "data_plane_mode": "blocked",
                "guard_boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            },
        }
        command("sysctl", "-w", "net.ipv4.ip_forward=0")
        assert Path("/proc/sys/net/ipv4/ip_forward").read_text().strip() == "0"
        for name, value in records.items():
            path = state / name
            path.write_text(json.dumps(value))
            path.chmod(0o600)
        (root / "vm-ha-enabled").touch(mode=0o600)
        Path("/qualification/expected-lock.json").write_text(json.dumps(expected))
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    Path("/qualification/baseline.json").write_text(
        json.dumps(
            {
                "version": metadata.version("nebius-vpngw"),
                "mode": args.mode,
                "topology": args.topology,
                "assets": files,
                "checkpoint_schema": checkpoint["schema"],
                "packages": {d.metadata["Name"]: d.version for d in metadata.distributions()},
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
