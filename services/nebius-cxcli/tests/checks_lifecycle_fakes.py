"""Stateful transport fixture; exercises the real admission/lifecycle owners."""

import copy
import json
import shlex


def lifecycle_transport(cluster):
    cluster.partitions = {
        "gpu": {"PartitionName": "gpu", "State": "DOWN", "AllowGroups": "ALL"},
        "hidden": {
            "PartitionName": "hidden",
            "State": "DOWN",
            "AllowGroups": "ALL",
            "Hidden": "YES",
        },
    }
    cluster.configmaps["slurm-scripts"] = {
        "metadata": {"uid": "passive-config"},
        "data": {"checks.json": "[]", "check_runner.py": "# native fixture"},
    }
    cluster.pending = {}
    slurm = cluster.slurm
    kube = cluster.kube

    def run(command):
        if command == "scontrol show partition -o":
            return "\n".join(
                " ".join(f"{k}={v}" for k, v in row.items()) for row in cluster.partitions.values()
            )
        if command.startswith("scontrol update PartitionName="):
            fields = dict(token.split("=", 1) for token in shlex.split(command)[2:])
            cluster.partitions[fields.pop("PartitionName")].update(fields)
            cluster.writes.append(("partition", command))
            return ""
        if command == "getent group soperatorchecks":
            return "soperatorchecks:x:1001:"
        if command == "id -g soperatorchecks":
            return "1001"
        if command == "getent passwd":
            return "soperatorchecks:x:1001:1001::/home/soperatorchecks:/bin/bash"
        if command.startswith("squeue -r -h -t PENDING"):
            return "\n".join(f"{job}|alice|hidden" for job in cluster.pending)
        if command.startswith("scontrol show job "):
            return cluster.pending.get(command.split()[3], "")
        if command.startswith("scontrol hold "):
            job = command.split()[-1]
            cluster.pending[job] = (
                cluster.pending[job]
                .replace("Priority=1", "Priority=0")
                .replace("Reason=None", "Reason=JobHeldAdmin")
            )
            cluster.writes.append(("hold", job))
            return ""
        if command.startswith("scontrol release "):
            job = command.split()[-1]
            cluster.pending[job] = (
                cluster.pending[job]
                .replace("Priority=0", "Priority=1")
                .replace("Reason=JobHeldAdmin", "Reason=None")
            )
            cluster.writes.append(("release", job))
            return ""
        return slurm(command)

    def api(args, document):
        if args[:2] == ["get", "pod"]:
            return {
                "metadata": {"uid": args[2]},
                "spec": {"nodeName": args[2]},
                "status": {
                    "containerStatuses": [
                        {
                            "name": "slurmd",
                            "ready": True,
                            "containerID": "container",
                            "restartCount": 0,
                        }
                    ]
                },
            }
        if args[0] == "exec":
            expected = json.loads(args[-2])
            return {
                "worker": args[3],
                "hashes": copy.deepcopy(expected["hashes"]),
                "config": copy.deepcopy(expected["config"]),
                "boot": "boot",
                "running": [],
                "observedAt": 1,
            }
        return kube(args, document)

    cluster.slurm = run
    cluster.kube = api
