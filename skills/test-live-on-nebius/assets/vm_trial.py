"""Reviewed simple-VM adapter for cxcli and SDK qualification.

The host supplies discovered inputs and an authenticated guest evidence collector.
No imports perform cloud or process effects. Dependencies are sibling skill assets.
"""

import hashlib
import json
import os
import sys
import time
from datetime import timedelta
from pathlib import Path

from live_state import StateError
from sdk_actions import Actions

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "nebius/assets"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "nebius/scripts"))


def clients(sdk):
    from nebius.api.nebius.compute import v1 as compute
    from nebius.api.nebius.storage import v1 as storage
    from nebius.api.nebius.vpc import v1 as vpc

    return {
        "vm": (
            compute.InstanceServiceClient(sdk),
            compute.GetInstanceRequest,
            compute.DeleteInstanceRequest,
            compute.ListInstancesRequest,
        ),
        "disk": (
            compute.DiskServiceClient(sdk),
            compute.GetDiskRequest,
            compute.DeleteDiskRequest,
            compute.ListDisksRequest,
        ),
        "group": (
            vpc.SecurityGroupServiceClient(sdk),
            vpc.GetSecurityGroupRequest,
            vpc.DeleteSecurityGroupRequest,
            vpc.ListSecurityGroupsRequest,
        ),
        "bucket": (
            storage.BucketServiceClient(sdk),
            storage.GetBucketRequest,
            storage.DeleteBucketRequest,
            storage.ListBucketsRequest,
        ),
    }


def context(j):
    from sdk.runtime import owned_sdk

    project = j.data["plan"]["project"]
    credential = Path.home() / ".nebius" / f"codex-agent-authkey.{project}.json"
    return owned_sdk(parent_id=project, credentials_file=credential)


def remaining(deadline, maximum=300):
    result = min(maximum, deadline - time.monotonic())
    if result <= 0:
        raise StateError("DEADLINE_EXCEEDED")
    return result


def labels(j):
    return {"live-test-run": j.data["run_id"]}


def preflight(j, deadline):
    from inspect_quotas import _effective_rows, _list_quotas
    from nebius.api.nebius.common.v1 import GetByNameRequest
    from nebius.api.nebius.compute.v1 import PlatformServiceClient
    from nebius.api.nebius.iam.v2 import GetProjectRequest, ProjectServiceClient
    from nebius.api.nebius.vpc.v1 import GetSubnetRequest, SubnetServiceClient
    from sdk.runtime import rpc, verify_identity

    p = j.data["plan"]
    with context(j) as sdk:
        project = rpc(ProjectServiceClient(sdk).get, GetProjectRequest(id=p["project"]))
        verify_identity(project, parent_id=p["tenant"], resource_id=p["project"])
        if project.spec.region != p["region"]:
            raise StateError("REGION_MISMATCH")
        subnet = rpc(
            SubnetServiceClient(sdk).get, GetSubnetRequest(id=p["inputs"]["subnet_id"])
        )
        verify_identity(subnet, parent_id=p["project"])
        if subnet.spec.network_id != p["inputs"]["network_id"]:
            raise StateError("NETWORK_MISMATCH")
        platform = rpc(
            PlatformServiceClient(sdk).get_by_name,
            GetByNameRequest(parent_id=p["project"], name=p["inputs"]["platform"]),
        )
        matches = [
            x
            for x in platform.spec.presets
            if x.name == p["inputs"]["preset"]
            and x.resources.vcpu_count == 4
            and x.resources.memory_gibibytes == 16
            and x.resources.gpu_count == 0
        ]
        if len(matches) != 1:
            raise StateError("SHAPE_MISMATCH")
        rows = _effective_rows(
            _list_quotas(sdk, parent_id=p["tenant"], scope="tenant"),
            _list_quotas(sdk, parent_id=p["project"], scope="project"),
        )
        quota = {
            r["name"]: r["effective_available"]
            for r in rows
            if r["region"] == p["region"]
        }
        demand = {
            "compute.instance.non-gpu.vcpu": 4,
            "compute.instance.count": 1,
            "compute.disk.count": 1,
            "compute.disk.size.network-ssd": 40 * 2**30,
            "vpc.securitygroup.count": 1,
            "vpc.allocation.count": 1,
        }
        if p["engine"] == "cxcli":
            demand.update(
                {"storage.bucket.count": 1, "storage.bucket.size.standard": 2**30}
            )
        if any(quota.get(k) is None or quota[k] < n for k, n in demand.items()):
            if p["quota_mode"] == "request":
                j.evidence(
                    "quota_shortfall",
                    {
                        k: n
                        for k, n in demand.items()
                        if quota.get(k) is None or quota[k] < n
                    },
                )
                return "awaiting_quota"
            raise StateError("QUOTA_UNAVAILABLE")
        j.evidence("quota_checked", {k: quota[k] for k in demand})
        from sdk.runtime import collect_pages

        baseline = {}
        for kind, (client, _, _, List) in clients(sdk).items():
            baseline[kind] = [
                x.metadata.id
                for x in collect_pages(
                    client.list,
                    lambda token, List=List: List(
                        parent_id=p["project"], page_token=token
                    ),
                    timeout=remaining(deadline),
                )
            ]
        j.evidence("resource_baseline", baseline)
    remaining(deadline)
    return True


def cloud_init(j):
    import yaml

    marker = "NEBIUS_LIVE_" + j.data["run_id"]
    # Standard image Python needs no package downloads. Guest writes only a nonce
    # payload, checks CPU/RAM and root block device, and emits public evidence.
    script = """import hashlib,json,os,subprocess,tempfile
cpu=os.cpu_count()
mem=int(next(x.split()[1] for x in open('/proc/meminfo') if x.startswith('MemTotal:')))
root=subprocess.check_output(['findmnt','-n','-o','SOURCE','/']).decode().strip()
parent=subprocess.check_output(['lsblk','-ndo','PKNAME',root]).decode().strip()
disk=int(subprocess.check_output(['lsblk','-bdno','SIZE','/dev/'+parent]).decode().strip())
payload=b"live-nebius-checksum"
with tempfile.TemporaryFile() as f:
 f.write(payload);f.flush();os.fsync(f.fileno());f.seek(0);ok=hashlib.sha256(f.read()).digest()==hashlib.sha256(payload).digest()
result={'cpu':cpu,'memory_kib':mem,'disk_bytes':disk,'checksum':ok,'passed':cpu==4 and 14*2**20<=mem<=16*2**20 and disk==40*2**30 and ok}
print(MARKER+' '+json.dumps(result),flush=True)
""".replace("MARKER", repr(marker))
    config = {
        "ssh_pwauth": False,
        "disable_root": True,
        "users": [
            {
                "name": "ubuntu",
                "sudo": "ALL=(ALL) NOPASSWD:ALL",
                "shell": "/bin/bash",
                "ssh_authorized_keys": [j.data["plan"]["inputs"]["ssh_public_key"]],
            }
        ],
        "write_files": [
            {"path": "/opt/live-vm-probe.py", "permissions": "0644", "content": script}
        ],
        "runcmd": [["sh", "-c", "python3 /opt/live-vm-probe.py > /dev/ttyS0 2>&1"]],
    }
    return "#cloud-config\n" + yaml.safe_dump(config)


def source_digest(source):
    source = Path(source)
    files = sorted((source / "src/nebius_cxcli").rglob("*.py"))
    files += [source / "component_sources.yaml", source / "pyproject.toml"]
    files += sorted((source.parents[1] / "platform-infra/modules/vm").rglob("*"))
    digest = hashlib.sha256()
    for path in files:
        if path.is_file() and "__pycache__" not in path.parts:
            digest.update(
                str(path.relative_to(source.parents[1])).encode()
                + b"\0"
                + path.read_bytes()
            )
    return digest.hexdigest()


def native_digest(config):
    config = Path(config)
    paths = [config]
    generated = config.parent / "generated"
    paths += sorted(
        p
        for p in generated.rglob("*")
        if p.is_file()
        and ".terraform" not in p.parts
        and (
            p.suffix in {".tf", ".tfvars"}
            or p.name in {"nebius-cxcli-manifest.json", ".terraform.lock.hcl"}
        )
    )
    return {
        str(p.relative_to(config.parent)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in paths
    }


def cxcli(j, args, deadline):
    p = j.data["plan"]
    source = Path(p["cxcli_source"])
    if source_digest(source) != p["source_identity"]["cxcli_source_sha256"]:
        raise StateError("CXCLI_SOURCE_CHANGED")
    if args[0] in {"deploy", "destroy"} and native_digest(args[1]) != j.data[
        "evidence"
    ].get("native_inputs"):
        raise StateError("NATIVE_INPUTS_CHANGED")
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("NEBIUS_", "CODEX_NEBIUS_", "CXCLI_NEBIUS_", "AWS_"))
    }
    project = p["project"]
    env.update(
        {
            "CODEX_NEBIUS_PROJECT_ID": project,
            "NEBIUS_PROJECT_ID": project,
            "NEBIUS_PROFILE": "codex-agent-" + project,
            "NEBIUS_AUTH_CREDENTIALS_FILE": str(
                Path.home() / ".nebius" / f"codex-agent-authkey.{project}.json"
            ),
            "NEBIUS_CXCLI_TFSTATE_BUCKET_NAME": "live-test-" + j.data["run_id"],
            "NEBIUS_CXCLI_TFSTATE_OBJECT_KEY": "terraform.tfstate",
            "PYTHONUNBUFFERED": "1",
        }
    )
    # One profile only: runtime operator fallback cannot choose a human profile.
    # cxcli persists its own runtime account in its supported auth store.
    command = [
        str(source / ".venv/bin/nebius-cxcli"),
        "--source-profile",
        "local",
        *map(str, args),
    ]
    from process_group import run_owned

    result = run_owned(j, command, cwd=source, env=env, deadline=deadline)
    if result["code"]:
        j.evidence(
            "cxcli_failure",
            {
                "action": str(args[0]),
                "exit": result["code"],
                "captured_output_sha256": result["captured_output_sha256"],
            },
        )
        raise StateError("CXCLI_COMMAND_FAILED")


def provision(j, deadline):
    from nebius.api.nebius.common.v1 import ResourceMetadata
    from nebius.api.nebius.compute.v1 import (
        AttachedDiskSpec,
        CreateDiskRequest,
        CreateInstanceRequest,
        DiskSpec,
        ExistingDisk,
        InstanceSpec,
        IPAddress,
        NetworkInterfaceSpec,
        ResourcesSpec,
        SecurityGroup,
    )
    from nebius.api.nebius.storage.v1 import (
        BucketSpec,
        CreateBucketRequest,
        VersioningPolicy,
    )
    from nebius.api.nebius.vpc.v1 import CreateSecurityGroupRequest, SecurityGroupSpec

    p = j.data["plan"]
    inputs = p["inputs"]
    name = "live-test-" + j.data["run_id"][:12]
    actions = Actions(j)
    with context(j) as sdk:
        c = clients(sdk)
        sg = actions.create(
            "group",
            c["group"][0],
            CreateSecurityGroupRequest(
                metadata=ResourceMetadata(
                    parent_id=p["project"], name=name + "-sg", labels=labels(j)
                ),
                spec=SecurityGroupSpec(network_id=inputs["network_id"]),
            ),
            kind="group",
            timeout=remaining(deadline),
        )
        if p["engine"] == "sdk":
            disk = actions.create(
                "disk",
                c["disk"][0],
                CreateDiskRequest(
                    metadata=ResourceMetadata(
                        parent_id=p["project"], name=name + "-disk", labels=labels(j)
                    ),
                    spec=DiskSpec(
                        size_gibibytes=40,
                        type=DiskSpec.DiskType.NETWORK_SSD,
                        source_image_id=inputs["image_id"],
                        block_size_bytes=4096,
                    ),
                ),
                kind="disk",
                timeout=remaining(deadline),
            )
            actions.create(
                "vm",
                c["vm"][0],
                CreateInstanceRequest(
                    metadata=ResourceMetadata(
                        parent_id=p["project"], name=name, labels=labels(j)
                    ),
                    spec=InstanceSpec(
                        resources=ResourcesSpec(
                            platform=inputs["platform"], preset=inputs["preset"]
                        ),
                        boot_disk=AttachedDiskSpec(
                            existing_disk=ExistingDisk(id=disk),
                            attach_mode=AttachedDiskSpec.AttachMode.READ_WRITE,
                            device_id="boot",
                        ),
                        network_interfaces=[
                            NetworkInterfaceSpec(
                                name="eth0",
                                subnet_id=inputs["subnet_id"],
                                ip_address=IPAddress(),
                                security_groups=[SecurityGroup(id=sg)],
                            )
                        ],
                        cloud_init_user_data=cloud_init(j),
                    ),
                ),
                kind="vm",
                dependencies=["disk", "group"],
                timeout=remaining(deadline),
            )
            return
        actions.create(
            "bucket",
            c["bucket"][0],
            CreateBucketRequest(
                metadata=ResourceMetadata(
                    parent_id=p["project"],
                    name="live-test-" + j.data["run_id"],
                    labels=labels(j),
                ),
                spec=BucketSpec(
                    versioning_policy=VersioningPolicy.ENABLED, max_size_bytes=2**30
                ),
            ),
            kind="bucket",
            timeout=remaining(deadline),
        )
    root = j.directory / "deployment"
    cxcli(
        j,
        [
            "create",
            root,
            "--client-name",
            "live-test",
            "--tenant-id",
            p["tenant"],
            "--project-id",
            p["project"],
            "--region-id",
            p["region"],
            "--infra",
            "vm",
            "--app",
            "none",
            "--no-interactive",
        ],
        deadline,
    )
    configs = list(root.rglob("config.yaml"))
    if len(configs) != 1:
        raise StateError("CXCLI_CONFIG_AMBIGUOUS")
    config = configs[0]
    import yaml

    data = yaml.safe_load(config.read_text())
    entries = data["infra"]["components"]
    vm = [x for x in entries if x["id"] == "vm"]
    if len(vm) != 1:
        raise StateError("CXCLI_VM_AMBIGUOUS")
    vm[0]["instance_id"] = name
    vm[0]["inputs"].update(
        {
            "name": name,
            "network_id": inputs["network_id"],
            "subnet_id": inputs["subnet_id"],
            "platform": inputs["platform"],
            "preset": inputs["preset"],
            "ssh_public_key": inputs["ssh_public_key"],
            "source_image_id": inputs["image_id"],
            "source_image_family": None,
            "boot_disk_size_gib": 40,
            "boot_disk_type": "NETWORK_SSD",
            "public_ip_mode": "none",
            "security_group_ids": [sg],
            "labels": labels(j),
            "cloud_init_user_data_override": cloud_init(j),
            "boot_disk_deletion_protection": False,
        }
    )
    config.write_text(yaml.safe_dump(data, sort_keys=False))
    os.chmod(config, 0o600)
    j.evidence("cxcli_config", str(config))
    for args in (
        ["validate", config],
        ["render", config],
        ["validate-generated", config.parent / "generated"],
        ["terraform", "plan", config.parent / "generated"],
        ["deploy", config],
    ):
        if args[0] == "deploy":
            j.evidence("native_inputs", native_digest(config))
            j.evidence("cxcli_apply_started", True)
        cxcli(j, args, deadline)
    reconcile(j, deadline)


def reconcile(j, deadline):
    from nebius.api.nebius.common.v1 import GetOperationRequest
    from process_group import reconcile_owned
    from sdk.runtime import CloudError, collect_pages, rpc

    reconcile_owned(j)
    with context(j) as sdk:
        c = clients(sdk)
        for key, record in list(j.data["operations"].items()):
            if record["status"] not in {"intent", "accepted"}:
                continue
            if not record.get("operation_id"):
                if record["action"] != "create":
                    resource_key = record.get("resource_key")
                    resource = j.data["resources"].get(resource_key, {})
                    if (
                        not resource
                        or record.get("target_id") != resource["id"]
                        or resource["owner"] != "sdk"
                        or resource["kind"] != record["kind"]
                    ):
                        raise StateError("DELETE_RECEIPT_SCOPE")
                    client, Get, _, _ = c[record["kind"]]
                    try:
                        item = rpc(client.get, Get(id=resource["id"]))
                        gone = item.status.state.name == "DELETED"
                    except CloudError as exc:
                        if exc.code != "NOT_FOUND":
                            raise
                        gone = True
                    if not gone:
                        raise StateError("DELETE_RECEIPT_LOST")
                    record["resolution"] = "authoritative_absence"
                    j.terminal(key, True)
                    j.absent(resource_key)
                    continue
                from nebius.api.nebius.common.v1 import ListOperationsRequest

                client, _, _, List = c[record["kind"]]
                existing = collect_pages(
                    client.list,
                    lambda token, List=List, parent=record["parent"]: List(
                        parent_id=parent, page_token=token
                    ),
                    timeout=remaining(deadline),
                )
                baseline = set(
                    j.data["evidence"]
                    .get("resource_baseline", {})
                    .get(record["kind"], [])
                )
                matches = [
                    x
                    for x in existing
                    if x.metadata.parent_id == record["parent"]
                    and x.metadata.labels.get("live-test-run") == j.data["run_id"]
                    and x.metadata.labels.get("live-test-intent") == key
                    and x.metadata.id not in baseline
                ]
                if len(matches) != 1:
                    raise StateError("RECEIPT_LOST_RECONCILE_REQUIRED")
                resource = matches[0]
                ops = collect_pages(
                    client.operation_service().list,
                    lambda token, rid=resource.metadata.id: ListOperationsRequest(
                        resource_id=rid, page_token=token
                    ),
                    key=lambda x: x.id,
                    field="operations",
                    timeout=remaining(deadline),
                )
                if len(ops) != 1 or ops[0].resource_id != resource.metadata.id:
                    raise StateError("OPERATION_AMBIGUOUS")
                j.accepted(key, ops[0].id, resource.metadata.id)
                j.own(
                    key,
                    resource_id=resource.metadata.id,
                    kind=record["kind"],
                    parent=record["parent"],
                    owner="sdk",
                    dependencies=record["dependencies"],
                )
            op = rpc(
                c[record["kind"]][0].operation_service().get,
                GetOperationRequest(id=record["operation_id"]),
            )
            op.sync_wait(
                timeout=remaining(deadline),
                interval=2,
                poll_iteration_timeout=20,
                poll_per_retry_timeout=10,
                poll_retries=2,
                auth_timeout=20,
            )
            if not op.done():
                raise StateError("OPERATION_PENDING")
            if op.resource_id and record["action"] == "create":
                j.own(
                    key,
                    resource_id=op.resource_id,
                    kind=record["kind"],
                    parent=record["parent"],
                    owner="sdk",
                    dependencies=record["dependencies"],
                )
            j.terminal(key, op.successful())
        if j.data["plan"]["engine"] == "cxcli":
            for kind, deps in [("disk", []), ("vm", ["disk", "group"])]:
                client, _, _, List = c[kind]
                items = collect_pages(
                    client.list,
                    lambda token, List=List: List(
                        parent_id=j.data["plan"]["project"], page_token=token
                    ),
                    timeout=remaining(deadline),
                )
                matches = [
                    r
                    for r in items
                    if r.metadata.labels.get("live-test-run") == j.data["run_id"]
                ]
                if len(matches) > 1:
                    raise StateError("AMBIGUOUS_RUN_RESOURCE")
                if matches:
                    r = matches[0]
                    j.own(
                        kind,
                        resource_id=r.metadata.id,
                        kind=kind,
                        parent=r.metadata.parent_id,
                        owner="cxcli",
                        dependencies=deps,
                    )


def verify(j, deadline):
    from sdk.runtime import rpc, wait_ready

    with context(j) as sdk:
        c = clients(sdk)
        vm = c["vm"][0]
        rid = j.data["resources"]["vm"]["id"]
        item = wait_ready(
            lambda timeout: rpc(vm.get, c["vm"][1](id=rid), timeout=timeout),
            lambda x: x.status.state.name == "RUNNING",
            timeout=remaining(deadline),
        )
        disk = rpc(c["disk"][0].get, c["disk"][1](id=j.data["resources"]["disk"]["id"]))
        inputs = j.data["plan"]["inputs"]
        exact = (
            item.spec.resources.platform == inputs["platform"]
            and item.spec.resources.preset == inputs["preset"]
            and disk.spec.size_gibibytes == 40
            and disk.spec.type.name == "NETWORK_SSD"
        )
        j.evidence(
            "control_plane",
            {
                "passed": exact,
                "vcpu": 4,
                "memory_gib": 16,
                "disk_gib": 40,
                "disk_type": "NETWORK_SSD",
                "state": item.status.state.name,
            },
        )
        if not exact:
            return False
    # The host's authenticated collector may supply only scoped parsed assertions.
    # Missing guest evidence is never promoted to a pass from VM RUNNING alone.
    evidence = j.directory / "guest-evidence.json"
    wait_until = min(deadline, time.monotonic() + 120)
    while time.monotonic() < wait_until:
        if evidence.exists():
            payload = json.loads(evidence.read_text())
            if (
                payload.get("run_id") != j.data["run_id"]
                or payload.get("instance_id") != rid
                or payload.get("source") != "authenticated-serial-logs"
            ):
                raise StateError("GUEST_EVIDENCE_SCOPE")
            facts = payload.get("facts", {})
            passed = (
                facts.get("cpu") == 4
                and 14 * 2**20 <= facts.get("memory_kib", 0) <= 16 * 2**20
                and facts.get("disk_bytes") == 40 * 2**30
                and facts.get("checksum") is True
            )
            j.evidence(
                "guest", {"passed": passed, "source": "authenticated-serial-logs"}
            )
            return passed
        time.sleep(2)
    j.evidence("guest", {"passed": False, "status": "unavailable"})
    return False


def cleanup(j, deadline):
    from sdk.runtime import CloudError, rpc

    p = j.data["plan"]
    if j.data["evidence"].get("owned_process", {}).get("active"):
        raise StateError("WRITER_NOT_PROVEN_QUIESCENT")
    if p["engine"] == "cxcli" and j.data["evidence"].get("cxcli_apply_started"):
        config = Path(j.data["evidence"]["cxcli_config"])
        if (config.parent / "generated").exists():
            cxcli(j, ["destroy", config, "--yes"], deadline)
    with context(j) as sdk:
        c = clients(sdk)
        for key in ["vm", "disk", "group", "bucket"]:
            record = j.data["resources"].get(key)
            if not record or record["absent"]:
                continue
            client, Get, Delete, _ = c[record["kind"]]
            try:
                item = rpc(client.get, Get(id=record["id"]))
            except CloudError as exc:
                if exc.code == "NOT_FOUND":
                    j.absent(key)
                    continue
                raise
            if item.status.state.name == "DELETED":
                j.absent(key)
                continue
            if record["owner"] == "cxcli":
                raise StateError("CXCLI_RESOURCE_REMAINS")
            options = {"id": record["id"]}
            if key == "bucket":
                options["ttl"] = timedelta(0)
            Actions(j).delete(
                key, client, Delete(**options), item, timeout=remaining(deadline)
            )
            while time.monotonic() < deadline:
                try:
                    observed = rpc(client.get, Get(id=record["id"]))
                except CloudError as exc:
                    if exc.code == "NOT_FOUND":
                        j.absent(key)
                        break
                    raise
                if observed.status.state.name == "DELETED":
                    j.absent(key)
                    break
                time.sleep(2)
            else:
                raise StateError("DELETION_NOT_ABSENT")

    return True
