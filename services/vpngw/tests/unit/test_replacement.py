from dataclasses import replace

import pytest

from nebius_vpngw.deploy import replacement as workflow


def plan():
    return workflow.Plan(
        project_id="project-test",
        gateway_name="gateway-test",
        operation_id="a" * 32,
        image_id="computeimage-test",
        desired={"platform": "cpu-d3", "disk_gb": 200},
        configuration_sha256="b" * 64,
        artifact_sha256="c" * 64,
        members=(
            workflow.Member(
                logical_name="gateway-test-0",
                compute_id="compute-old",
                physical_name="gateway-test-0",
                revision=1,
                disk_id="disk-attached",
                owned_disk=True,
                compute_sha256="d" * 64,
                authorization_sha256="e" * 64,
                configured_policy_sha256="f" * 64,
                security_groups=({"name": "eth0", "effective_ids": ["sg-customer"]},),
                public_allocations=("allocation-public",),
                primary_allocations=("allocation-private",),
                candidate_name="gateway-test-next",
                candidate_disk_name="gateway-test-next-boot",
            ),
        ),
        routes=(),
    )


class Ports:
    def __init__(self, fail=None):
        self.effects = []
        self.fail = fail
        self.settled = True

    def settle(self, journal):
        if not self.settled:
            raise RuntimeError("accepted operation is unresolved")

    def verify_authority(self, journal, *, step):
        assert journal.state["transaction"]["plan"]["members"][0]["compute_id"] == "compute-old"

    def execute(self, journal, *, step, effect_id):
        self.effects.append((step, effect_id))
        if self.fail == step:
            raise RuntimeError("interrupted")
        return {"verified_step": step}

    def verify(self, journal, *, step, result):
        assert result["verified_step"] == step

    def promote(self, journal, *, effect_id):
        self.effects.append(("promote", effect_id))

    def finish(self, journal):
        assert journal.state["transaction"]["member"] == len(
            journal.state["transaction"]["plan"]["members"]
        )


@pytest.mark.parametrize("boundary", workflow.STEPS)
def test_each_interrupted_phase_resumes_same_intent_without_retiring_early(tmp_path, boundary):
    desired = plan()
    ports = Ports(fail=boundary)
    with workflow.Journal(
        desired.project_id, desired.gateway_name, root=tmp_path / "state"
    ) as journal:
        journal.start(desired, workflow.digest(desired.mapping()))
        with pytest.raises(RuntimeError, match="interrupted"):
            workflow.run(journal, ports)
        assert journal.state["transaction"]["phase"] == "pending"
        assert journal.state["transaction"]["pending"] == boundary
        original_effect = ports.effects[-1]
    ports.fail = None
    with workflow.Journal(
        desired.project_id, desired.gateway_name, root=tmp_path / "state"
    ) as journal:
        workflow.run(journal, ports)
        assert journal.state["transaction"]["phase"] == "complete"
        assert original_effect in ports.effects[:-1] or boundary == workflow.STEPS[-1]
        assert ports.effects.count(original_effect) == 2
    effects = [item[0] for item in ports.effects]
    assert effects.index("retire") > effects.index("verify-serving")


def test_unsettled_operation_blocks_every_later_effect(tmp_path):
    desired = plan()
    ports = Ports()
    ports.settled = False
    with workflow.Journal(
        desired.project_id, desired.gateway_name, root=tmp_path / "state"
    ) as journal:
        journal.start(desired, workflow.digest(desired.mapping()))
        with pytest.raises(RuntimeError, match="unresolved"):
            workflow.run(journal, ports)
    assert ports.effects == []


def test_bundle_is_private_scoped_and_rejects_secrets(tmp_path):
    desired = plan()
    with workflow.Journal(
        desired.project_id, desired.gateway_name, root=tmp_path / "source"
    ) as journal:
        journal.start(desired, workflow.digest(desired.mapping()))
        bundle = journal.bundle
        assert bundle.stat().st_mode & 0o777 == 0o600
    with workflow.Journal(
        desired.project_id, desired.gateway_name, root=tmp_path / "destination"
    ) as journal:
        journal.import_bundle(bundle)
        assert journal.state["transaction"]["plan"]["operation_id"] == desired.operation_id
    with (
        workflow.Journal(
            desired.project_id, "different-gateway", root=tmp_path / "foreign"
        ) as journal,
        pytest.raises(ValueError, match="different deployment"),
    ):
        journal.import_bundle(bundle)
    with pytest.raises(ValueError, match="metadata"):
        replace(desired, desired={"private_key": "forbidden"}).mapping()


def test_different_approval_cannot_replace_pending_work(tmp_path):
    desired = plan()
    with workflow.Journal(
        desired.project_id, desired.gateway_name, root=tmp_path / "state"
    ) as journal:
        journal.start(desired, workflow.digest(desired.mapping()))
        changed = replace(desired, image_id="computeimage-different")
        with pytest.raises(RuntimeError, match="different replacement"):
            journal.start(changed, workflow.digest(changed.mapping()))


@pytest.mark.parametrize("after_exchange", [False, True])
def test_checkpoint_crash_recovers_the_same_portable_receipt(tmp_path, monkeypatch, after_exchange):
    from nebius_vpngw.deploy import operator_guest

    desired = plan()
    directory = tmp_path / "state"
    exchange = operator_guest._exchange

    class ProcessDied(BaseException):
        pass

    with workflow.Journal(desired.project_id, desired.gateway_name, root=directory) as journal:
        journal.start(desired, workflow.digest(desired.mapping()))
        assert journal.path == journal.bundle

        def interrupted(source, destination):
            if after_exchange:
                exchange(source, destination)
            raise ProcessDied()

        monkeypatch.setattr(operator_guest, "_exchange", interrupted)
        journal.state["transaction"]["results"]["receipt"] = {
            "cloud_operation_id": "operation-accepted"
        }
        with pytest.raises(ProcessDied):
            journal.save()
    monkeypatch.setattr(operator_guest, "_exchange", exchange)
    with workflow.Journal(desired.project_id, desired.gateway_name, root=directory) as journal:
        assert journal.state["transaction"]["results"]["receipt"] == {
            "cloud_operation_id": "operation-accepted"
        }
        assert not (directory / ".recovery.json.vpngw-recovery").exists()


def test_replacement_rejects_group_readable_canonical_state(tmp_path):
    desired = plan()
    directory = tmp_path / "state"
    with workflow.Journal(desired.project_id, desired.gateway_name, root=directory) as journal:
        journal.start(desired, workflow.digest(desired.mapping()))
        journal.path.chmod(0o640)
    with (
        pytest.raises(ValueError, match="private"),
        workflow.Journal(desired.project_id, desired.gateway_name, root=directory),
    ):
        pass


def test_cloud_receipt_survives_timeout_and_settles_without_resubmission(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from nebius.api.nebius.common import v1 as common
    from nebius.api.nebius.compute.v1 import StopInstanceRequest

    from nebius_vpngw.deploy.replacement_cloud import Cloud

    desired = plan()
    events = []
    failed = [True]

    class Operation:
        id = "operation-test"
        resource_id = "compute-old"

        def sync_wait(self, **kwargs):
            if failed[0]:
                raise TimeoutError("accepted but unconfirmed")

        def successful(self):
            return True

    def submit(request, **kwargs):
        events.append(("submit", request.id, kwargs["metadata"]))
        return SimpleNamespace(wait=lambda: Operation())

    def lookup(request, **kwargs):
        events.append(("lookup", request.id))
        return SimpleNamespace(wait=lambda: Operation())

    monkeypatch.setattr(common, "OperationServiceClient", lambda _: SimpleNamespace(get=lookup))
    with workflow.Journal(
        desired.project_id, desired.gateway_name, root=tmp_path / "state"
    ) as journal:
        journal.start(desired, workflow.digest(desired.mapping()))
        with pytest.raises(TimeoutError):
            Cloud(object(), journal).mutate(
                "stop-old", StopInstanceRequest(id="compute-old"), submit
            )
        assert (
            next(iter(journal.state["transaction"]["operations"].values()))["status"] == "accepted"
        )
    failed[0] = False
    with workflow.Journal(
        desired.project_id, desired.gateway_name, root=tmp_path / "state"
    ) as journal:
        cloud = Cloud(object(), journal)
        cloud.settle()
        assert (
            cloud.mutate("stop-old", StopInstanceRequest(id="compute-old"), submit) == "compute-old"
        )
        with pytest.raises(RuntimeError, match="request changed"):
            cloud.mutate("stop-old", StopInstanceRequest(id="compute-foreign"), submit)
    assert [event[0] for event in events] == ["submit", "lookup"]


def test_candidate_has_captured_security_groups_in_create_request(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from nebius.api.nebius.compute import v1 as compute

    from nebius_vpngw.deploy import security_groups
    from nebius_vpngw.deploy.replacement_cloud import Cloud

    desired = plan()
    groups = [
        {
            "name": "eth0",
            "subnet_id": "subnet-captured",
            "network_id": "network-captured",
            "explicit_ids": [],
            "effective_ids": ["sg-original-default"],
        }
    ]
    requests = []
    checked = []
    monkeypatch.setattr(
        compute, "InstanceServiceClient", lambda _: SimpleNamespace(create=object())
    )
    monkeypatch.setattr(
        security_groups,
        "verify_candidate",
        lambda _sdk, _instance, expected: checked.append(expected),
    )
    with workflow.Journal(
        desired.project_id, desired.gateway_name, root=tmp_path / "state"
    ) as journal:
        journal.start(desired, workflow.digest(desired.mapping()))
        cloud = Cloud(object(), journal)

        def mutate(label, request, submit):
            requests.append(request)
            return "compute-candidate"

        monkeypatch.setattr(cloud, "mutate", mutate)
        monkeypatch.setattr(
            cloud,
            "get_instance",
            lambda _: SimpleNamespace(
                metadata=SimpleNamespace(labels=cloud.labels("gateway-test-0", "candidate")),
                spec=SimpleNamespace(
                    boot_disk=SimpleNamespace(existing_disk=SimpleNamespace(id="disk-candidate"))
                ),
            ),
        )
        cloud.create_candidate(
            name="gateway-next",
            logical="gateway-test-0",
            disk_id="disk-candidate",
            public_allocation="allocation-temp",
            desired={"platform": "cpu-d3", "preset": "4vcpu-16gb"},
            security_groups=groups,
            cloud_init="#cloud-config\n",
        )
    assert len(requests) == 1
    nic = requests[0].spec.network_interfaces[0]
    assert nic.subnet_id == "subnet-captured"
    assert [group.id for group in nic.security_groups] == ["sg-original-default"]
    assert nic.public_ip_address.allocation_id == "allocation-temp"
    assert checked[0][0].create_ids == ("sg-original-default",)
