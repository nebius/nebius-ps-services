"""Lost IAM replies never become name adoption or a new credential identity."""

from types import SimpleNamespace as NS

import nebius.api.nebius.iam.v1 as iam
import pytest
from grpc import StatusCode
from nebius.aio.service_error import RequestError, RequestStatusExtended
from nebius.api.nebius.common import v1 as common
from test_vm_ha_managed_credentials import _credential_set

from nebius_vpngw import vm_ha_managed_credentials as managed
from nebius_vpngw import vpngw_sa
from nebius_vpngw.apply_approval import digest
from nebius_vpngw.deploy import credential_enrollment
from nebius_vpngw.deploy.apply_command import CommandJournal
from nebius_vpngw.deploy.apply_plan import CommandPlan, Effect
from nebius_vpngw.deploy.credential_enrollment import REQUESTS, EnrollmentJournal
from nebius_vpngw.deploy.replacement_cloud import message_digest


def missing():
    return RequestError(RequestStatusExtended(StatusCode.NOT_FOUND, "missing", [], "", "", []))


class Service:
    def __init__(self, graph, label):
        self.graph, self.label = graph, label
        self.item = None

    def get_by_name(self, request):
        if self.item is None:
            raise missing()
        assert self.item.metadata.parent_id == request.parent_id
        assert self.item.metadata.name == request.name
        return NS(wait=lambda: self.item)

    def get(self, request, **_options):
        if self.item is None or self.item.metadata.id != request.id:
            raise missing()
        return NS(wait=lambda: self.item)

    def list(self, request):
        return NS(
            wait=lambda: NS(items=[] if self.item is None else [self.item], next_page_token="")
        )

    def list_members(self, request):
        return NS(
            wait=lambda: NS(
                memberships=[] if self.item is None else [self.item], next_page_token=""
            )
        )

    def list_by_account(self, request, **_options):
        return self.list(request)

    def operation_service(self):
        def get(request, **_options):
            operation = self.graph.operations[self.label]
            assert request.id == operation.id
            return NS(wait=lambda: operation)

        return NS(get=get)

    def create(self, request, **options):
        assert options["retries"] == 0
        key = options["metadata"]
        signature = message_digest(request)
        self.graph.calls.append((self.label, signature, key))
        if self.label in self.graph.operations:
            assert self.graph.requests[self.label] == (signature, key)
            return NS(wait=lambda: self.graph.operations[self.label])
        self.graph.requests[self.label] = (signature, key)
        kind = REQUESTS[self.label].removeprefix("Create").removesuffix("Request")
        metadata = common.ResourceMetadata()
        metadata.__pb2_message__.CopyFrom(request.metadata.__pb2_message__)
        metadata.id = self.label + "-created"
        self.item = getattr(iam, kind)(metadata=metadata, spec=request.spec)
        op = NS(id="operation-" + self.label, resource_id=metadata.id, successful=lambda: True)

        def finish(**_options):
            if self.graph.fail == (self.label, "accepted"):
                self.graph.fail = None
                raise TimeoutError("accepted operation waiter interrupted")

        op.sync_wait = finish
        self.graph.operations[self.label] = op
        if self.graph.fail == (self.label, "reply"):
            self.graph.fail = None
            raise TimeoutError("accepted creation reply lost")
        return NS(wait=lambda: op)


@pytest.fixture
def graph(tmp_path, monkeypatch, cached_managed_key_validation):
    graph = NS(fail=None, calls=[], requests={}, operations={}, sync_close=lambda: None)
    graph.services = {label: Service(graph, label) for label in REQUESTS}
    for label, request in REQUESTS.items():
        kind = request.removeprefix("Create").removesuffix("Request")
        monkeypatch.setattr(
            iam, kind + "ServiceClient", lambda _sdk, label=label: graph.services[label]
        )
    monkeypatch.setattr(vpngw_sa, "_init_client", lambda *_: (graph, True))
    monkeypatch.setattr(vpngw_sa, "_cli_access_token", lambda **_: "ephemeral-fixture-token")
    journal = EnrollmentJournal("project-a", "gateway-a", root=tmp_path / "journal")

    def preflight(nodes, *, source_path, **_):
        return _credential_set(source_path, node_ids=tuple(nodes))

    def inspect():
        return managed.inspect_managed_vm_ha_credentials(
            project_id="project-a",
            gateway_name="gateway-a",
            node_ids=("a", "b"),
            home=tmp_path,
            preflight=preflight,
            iam_inspector=lambda *args, **kwargs: vpngw_sa.inspect_vm_ha_service_account_identity(
                *args, **{**kwargs, "client_factory": lambda *_: (graph, True)}
            ),
            client_factory=lambda *_: (graph, True),
            enrollment=journal,
        )

    def execute(plan):
        return managed.ensure_managed_vm_ha_credentials(
            plan,
            node_ids=("a", "b"),
            tenant_id=None,
            region_id=None,
            client_factory=lambda *_: (graph, True),
            preflight=preflight,
            enrollment=journal,
        )

    return graph, journal, inspect, execute


@pytest.mark.parametrize("label", REQUESTS)
@pytest.mark.parametrize("boundary", ["reply", "accepted"])
def test_each_uncertain_iam_creation_resumes_exact_request_and_receipt(graph, label, boundary):
    cloud, journal, inspect, execute = graph
    initial = inspect()
    original = initial.approval_record()
    with journal:
        journal.begin(original, approval="a" * 64, execution_id="b" * 32)
        cloud.fail = (label, boundary)
        with pytest.raises(managed.VMHAManagedCredentialError):
            execute(initial)
        assert not initial.source_path.exists()
        incomplete = journal.path.read_bytes()
        assert b"PRIVATE KEY" not in incomplete and b"PUBLIC KEY" not in incomplete
    # New inspection recognizes only observations. The owner must settle the
    # original request or operation before it accepts those resource identities.
    observed = inspect()
    with journal:
        journal.begin(original, approval="a" * 64, execution_id="b" * 32)
        result = execute(observed)
        receipt = journal.receipt()
    assert result.credentials.service_account_id == "service-account-created"
    assert result.credentials.authorized_key_id == "authorized-key-created"
    assert len(cloud.operations) == 5
    assert len(cloud.calls) == 5 + (boundary == "reply")
    assert all(row[1:] == cloud.requests[row[0]] for row in cloud.calls)
    assert len({row[2] for row in cloud.calls}) == 5
    assert initial.source_path.stat().st_mode & 0o777 == 0o600
    assert b"PRIVATE KEY" not in journal.path.read_bytes()
    assert b"PUBLIC KEY" not in journal.path.read_bytes()
    assert b"ephemeral-fixture-token" not in journal.path.read_bytes()
    before = journal.path.read_bytes(), initial.source_path.read_bytes(), list(cloud.calls)
    with journal:
        execute(inspect())
        assert journal.receipt() == receipt
    assert before == (journal.path.read_bytes(), initial.source_path.read_bytes(), cloud.calls)


def test_same_named_resource_without_original_operation_is_not_adopted(graph):
    cloud, journal, inspect, execute = graph
    initial = inspect()
    with journal:
        journal.begin(initial.approval_record(), approval="a" * 64, execution_id="b" * 32)
        service = cloud.services["service-account"]
        service.item = iam.ServiceAccount(
            metadata=common.ResourceMetadata(
                id="foreign",
                parent_id="project-a",
                name=initial.service_account_name,
                labels=dict(initial.iam_plan.ownership_labels),
            )
        )
        with pytest.raises(managed.VMHAManagedCredentialError, match="reconciliation failed"):
            execute(initial)
    assert cloud.calls == []


@pytest.mark.parametrize("label", REQUESTS)
@pytest.mark.parametrize("boundary", ["reply", "accepted"])
def test_settlement_reconciles_only_submitted_iam_prefix_without_new_key(graph, label, boundary):
    cloud, journal, inspect, execute = graph
    initial = inspect()
    with journal:
        journal.begin(initial.approval_record(), approval="a" * 64, execution_id="b" * 32)
        cloud.fail = (label, boundary)
        with pytest.raises(managed.VMHAManagedCredentialError):
            execute(initial)
    pending = initial.source_path.parent / managed._PENDING_KEY_NAME
    original_key = pending.read_bytes()
    original_labels = set(cloud.requests)
    original_ids = {name: op.id for name, op in cloud.operations.items()}
    observed = inspect()
    with journal:
        managed.settle_managed_vm_ha_enrollment(
            observed, enrollment=journal, tenant_id=None, region_id=None,
            client_factory=lambda *_: (cloud, True),
        )
    assert set(cloud.requests) == original_labels
    assert {name: op.id for name, op in cloud.operations.items()} == original_ids
    assert all(row[1:] == cloud.requests[row[0]] for row in cloud.calls)
    current = journal.snapshot()["transaction"]
    assert current["phase"] == "pending" and current["receipt"] is None
    assert all(record["status"] == "settled" for record in current["operations"].values())
    assert not initial.source_path.exists()
    assert pending.read_bytes() == original_key
    journal._admit_observation(inspect(), current)
    before = journal.path.read_bytes(), list(cloud.calls)
    with journal:
        managed.settle_managed_vm_ha_enrollment(
            inspect(), enrollment=journal, tenant_id=None, region_id=None,
            client_factory=lambda *_: (cloud, True),
        )
    assert before == (journal.path.read_bytes(), cloud.calls)
    assert b"PRIVATE KEY" not in journal.path.read_bytes()


def test_settlement_pairs_original_pending_key_without_submitting_iam(graph, monkeypatch):
    cloud, journal, inspect, execute = graph
    initial = inspect()
    with journal:
        journal.begin(initial.approval_record(), approval="a" * 64, execution_id="b" * 32)
        with monkeypatch.context() as patch:
            def stop_before_pair(*_a, **_kw):
                raise OSError("injected key-only boundary")

            patch.setattr(managed, "_write_journal", stop_before_pair)
            with pytest.raises(OSError, match="key-only"):
                execute(initial)
    pending = initial.source_path.parent / managed._PENDING_KEY_NAME
    original_key = pending.read_bytes()
    observed = inspect()
    assert observed.pending_present and not observed.journal_present
    with journal:
        managed.settle_managed_vm_ha_enrollment(
            observed, enrollment=journal, tenant_id=None, region_id=None,
            client_factory=lambda *_: pytest.fail("key pairing must not contact the provider"),
        )
    assert inspect().journal_present
    assert pending.read_bytes() == original_key
    assert not initial.source_path.exists()
    assert cloud.calls == []


@pytest.mark.parametrize(
    "label", ["service-account", "group", "membership", "permit", "authorized-key"]
)
def test_created_identity_drift_is_never_repaired_or_adopted(graph, label):
    cloud, journal, inspect, execute = graph
    initial = inspect()
    with journal:
        journal.begin(initial.approval_record(), approval="a" * 64, execution_id="b" * 32)
        execute(initial)
    cloud.services[label].item.metadata.id = "replacement-foreign"
    before = list(cloud.calls)
    with journal, pytest.raises((RuntimeError, managed.VMHAManagedCredentialError)):
        execute(inspect())
    assert cloud.calls == before


@pytest.mark.parametrize("boundary", ["key-only", "local-journal", "published", "receipt"])
def test_local_publication_boundaries_resume_selected_key(graph, monkeypatch, boundary):
    cloud, journal, inspect, execute = graph
    initial = inspect()
    if boundary in {"key-only", "local-journal"}:
        owner, attribute = managed, "_write_journal"
    elif boundary == "published":
        owner, attribute = managed, "_atomic_private_write"
    else:
        owner, attribute = managed, "_cleanup_enrollment_state"
    original = getattr(owner, attribute)
    fired = False

    def interrupted(*args, **kwargs):
        nonlocal fired
        if boundary == "published" and args[0] != initial.source_path or fired:
            return original(*args, **kwargs)
        fired = True
        if boundary in {"published", "local-journal"}:
            original(*args, **kwargs)
        raise OSError("publication interrupted at declared boundary")

    with journal:
        journal.begin(initial.approval_record(), approval="a" * 64, execution_id="b" * 32)
        with monkeypatch.context() as patch:
            patch.setattr(owner, attribute, interrupted)
            with pytest.raises((OSError, managed.VMHAManagedCredentialError)):
                execute(initial)
        pending = initial.source_path.parent / managed._PENDING_KEY_NAME
        selected_key = pending.read_bytes()
    observed = inspect()
    with journal:
        execute(observed)
    assert fired and len(cloud.operations) == len(cloud.calls) == 5
    # Compare derived public identities, never persist private test values in receipts.
    from cryptography.hazmat.primitives import serialization

    selected_public = (
        serialization.load_pem_private_key(selected_key, password=None)
        .public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    assert managed._public_key_from_credentials(initial.source_path) == selected_public
    assert not pending.exists()


def test_parent_receipt_survives_response_loss_and_repeats_without_mutations(
    graph, tmp_path, monkeypatch
):
    cloud, child, inspect, ensure = graph
    observed = inspect()
    intent = observed.approval_record()
    parent = CommandJournal("project-a", "gateway-a", root=tmp_path / "parent")
    plan = CommandPlan(
        "project-a",
        "gateway-a",
        "c" * 64,
        "d" * 64,
        (
            Effect(
                "credentials",
                "managed-credentials",
                "enroll",
                ("a", "b"),
                digest(intent),
                digest(intent),
                False,
            ),
        ),
    )

    def run(current, **changes):
        return credential_enrollment.execute(
            parent,
            child,
            current,
            **{
                "node_ids": ("a", "b"),
                "tenant_id": None,
                "region_id": None,
                "ensure": lambda plan, **_: ensure(plan),
                **changes,
            },
        )

    with parent:
        parent.begin(plan, intents={"credentials": intent})
        parent.started("credentials")
        with monkeypatch.context() as patch:
            patch.setattr(
                parent,
                "verified",
                lambda *args: (_ for _ in ()).throw(OSError("parent receipt write interrupted")),
            )
            with pytest.raises(OSError):
                run(observed)
    before = child.path.read_bytes(), list(cloud.calls)
    current = inspect()
    bound = child.bind_observation(
        current,
        intent=intent,
        approval=parent.pending()["approval"],
        execution_id=parent.pending()["execution_id"],
    )
    assert current.action == "reuse" and bound.command_action == "create"
    assert bound.approval_record() == intent and bound.observation_record() != intent
    assert (child.path.read_bytes(), cloud.calls) == before
    with parent:
        run(bound)
        completed = parent.path.read_bytes()
        run(inspect())
        assert parent.path.read_bytes() == completed
        with pytest.raises(RuntimeError, match="approved command"):
            run(inspect(), node_ids=("a", "foreign"))
        parent.complete()
    assert (child.path.read_bytes(), cloud.calls) == before


@pytest.mark.parametrize("change", ["approval", "execution", "intent"])
def test_observation_cannot_bind_another_command(graph, change):
    cloud, journal, inspect, _ = graph
    observed = inspect()
    intent = observed.approval_record()
    with journal:
        journal.begin(intent, approval="a" * 64, execution_id="b" * 32)
    args = {"intent": intent, "approval": "a" * 64, "execution_id": "b" * 32}
    if change == "intent":
        args["intent"] = {**intent, "destination": "foreign"}
    elif change == "execution":
        args["execution_id"] = "e" * 32
    else:
        args["approval"] = "f" * 64
    before = journal.path.read_bytes()
    with pytest.raises(RuntimeError, match="another command"):
        journal.bind_observation(observed, **args)
    assert journal.path.read_bytes() == before and cloud.calls == []


def test_journal_rejects_permission_expansion(graph):
    _cloud, journal, inspect, _ = graph
    intent = inspect().approval_record()
    intent["iam_resources"]["project_editor_permit"]["role"] = "owner"
    with journal, pytest.raises(ValueError, match="journal is invalid"):
        journal.begin(intent, approval="a" * 64, execution_id="b" * 32)


def test_uncertain_permit_never_replays_after_membership_drift(graph):
    cloud, journal, inspect, execute = graph
    initial = inspect()
    with journal:
        journal.begin(initial.approval_record(), approval="a" * 64, execution_id="b" * 32)
        cloud.fail = ("permit", "reply")
        with pytest.raises(managed.VMHAManagedCredentialError):
            execute(initial)
    observed = inspect()
    # A concurrent writer changes the group after read-only planning. Replaying
    # an uncertain grant must not precede fresh membership admission.
    cloud.services["membership"].item.spec.member_id = "foreign-account"
    before = list(cloud.calls)
    with journal, pytest.raises(managed.VMHAManagedCredentialError):
        execute(observed)
    assert cloud.calls == before


@pytest.fixture
def readback_clock(monkeypatch):
    now, sleeps = [0.0], []

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(managed, "time", NS(monotonic=lambda: now[0], sleep=sleep))
    monkeypatch.setattr(managed.random, "random", lambda: 1.0)
    return now, sleeps


@pytest.mark.parametrize("empty_reads", [1, 3, 8])
def test_settled_key_creation_waits_for_exact_readback_without_replaying(
    graph, monkeypatch, readback_clock, empty_reads
):
    cloud, journal, inspect, execute = graph
    initial = inspect()
    service = cloud.services["authorized-key"]
    original = service.list_by_account
    settled_reads = []

    def delayed_visibility(request, **options):
        if service.item is not None:
            settled_reads.append(options)
            records = journal.snapshot()["transaction"]["operations"].values()
            key = next(record for record in records if record["label"] == "authorized-key")
            assert key["status"] == "settled" and key["submission_count"] == 1
            if len(settled_reads) <= empty_reads:
                return NS(wait=lambda: NS(items=[], next_page_token=""))
        return original(request)

    monkeypatch.setattr(service, "list_by_account", delayed_visibility)
    with journal:
        journal.begin(initial.approval_record(), approval="a" * 64, execution_id="b" * 32)
        result = execute(initial)
        receipt = journal.receipt()
    assert result.credentials.authorized_key_id == "authorized-key-created"
    assert len(settled_reads) >= empty_reads + 1
    assert len(cloud.calls) == len(cloud.operations) == 5
    assert receipt


def test_unavailable_created_key_preserves_receipts_for_later_resume(
    graph, monkeypatch, readback_clock
):
    cloud, journal, inspect, execute = graph
    initial = inspect()
    service = cloud.services["authorized-key"]
    original = service.list_by_account
    reads = []

    def unavailable(request, **options):
        if service.item is not None:
            reads.append(options)
            return NS(wait=lambda: NS(items=[], next_page_token=""))
        return original(request)

    with journal:
        journal.begin(initial.approval_record(), approval="a" * 64, execution_id="b" * 32)
        with monkeypatch.context() as patch:
            patch.setattr(service, "list_by_account", unavailable)
            with pytest.raises(managed.VMHAManagedCredentialError, match="pending exact readback"):
                execute(initial)
        pending = journal.snapshot()["transaction"]
    assert len(reads) > 3 and len(cloud.calls) == 5
    assert 29.8 <= readback_clock[0][0] <= 30
    assert not initial.source_path.exists()
    assert all(record["status"] == "settled" for record in pending["operations"].values())
    with journal:
        execute(inspect())
        completed = journal.snapshot()["transaction"]
    for field in ("operations", "execution_id", "selected", "approval", "intent"):
        assert completed[field] == pending[field]
    assert completed["phase"] == "complete" and len(cloud.calls) == 5


def test_reuse_planning_and_execution_wait_for_exact_key_without_new_enrollment(
    graph, monkeypatch, readback_clock
):
    cloud, journal, inspect, execute = graph
    initial = inspect()
    with journal:
        journal.begin(initial.approval_record(), approval="a" * 64, execution_id="b" * 32)
        enrolled = execute(initial)
    service = cloud.services["authorized-key"]
    original = service.list_by_account
    reads = []

    def delayed(request, **options):
        reads.append(options)
        if len(reads) <= 8:
            return NS(wait=lambda: NS(items=[], next_page_token=""))
        return original(request, **options)

    monkeypatch.setattr(service, "list_by_account", delayed)
    reused = inspect()
    assert reused.action == "reuse" and len(reads) == 9
    reads.clear()
    with journal:
        result = execute(reused)
    assert len(reads) == 9
    assert result.credentials.authorized_key_id == enrolled.credentials.authorized_key_id
    assert len(cloud.calls) == len(cloud.operations) == 5


@pytest.mark.parametrize("failure", ["foreign", "extra-key", "permission", "malformed"])
def test_created_key_retry_does_not_hide_conflicting_or_unavailable_evidence(
    graph, monkeypatch, readback_clock, failure
):
    cloud, journal, inspect, execute = graph
    initial = inspect()
    service = cloud.services["authorized-key"]
    original = service.list_by_account
    reads = []

    def changed(request, **options):
        if service.item is None:
            return original(request)
        reads.append(options)
        if len(reads) == 1:
            return NS(wait=lambda: NS(items=[], next_page_token=""))
        if failure == "permission":
            raise PermissionError("private provider detail")
        if failure == "malformed":
            return NS(wait=lambda: NS(items=[service.item]))
        if failure == "foreign":
            service.item.spec.data = "foreign public key"
            return original(request)
        foreign = NS(metadata=NS(id="foreign-key"))
        return NS(wait=lambda: NS(items=[service.item, foreign], next_page_token=""))

    monkeypatch.setattr(service, "list_by_account", changed)
    with journal:
        journal.begin(initial.approval_record(), approval="a" * 64, execution_id="b" * 32)
        with pytest.raises(managed.VMHAManagedCredentialError) as raised:
            execute(initial)
    assert "private provider detail" not in str(raised.value)
    assert len(reads) == 2 and len(cloud.calls) == 5
    assert not initial.source_path.exists()


def test_created_key_readback_shares_parent_deadline_across_pages(readback_clock):
    now, _sleeps = readback_clock
    requests = []
    item = NS(metadata=NS(id="original-key"))

    def page(request, **options):
        requests.append((request.page_token, options))
        now[0] += 0.3
        return NS(
            wait=lambda: NS(
                items=[] if not request.page_token else [item],
                next_page_token="second" if not request.page_token else "",
            )
        )

    with managed.vm_ha_observation_budget(seconds=1.0, clock=lambda: now[0]):
        assert managed._reread_authorized_key(NS(list_by_account=page), "sa") == (item,)
    assert [row[0] for row in requests] == ["", "second"]
    assert requests[0][1]["timeout"] == pytest.approx(0.8)
    assert requests[1][1]["timeout"] == pytest.approx(0.5)
    for _token, options in requests:
        assert options["retries"] == 1
        assert options["auth_timeout"] == options["per_retry_timeout"] == options["timeout"]


def test_late_created_key_readback_cannot_publish_credentials(graph, monkeypatch, readback_clock):
    cloud, journal, inspect, execute = graph
    initial = inspect()
    service = cloud.services["authorized-key"]
    original = service.list_by_account
    now, _sleeps = readback_clock

    def late(request, **options):
        if service.item is not None:
            now[0] += 31.0
        return original(request)

    monkeypatch.setattr(service, "list_by_account", late)
    with journal:
        journal.begin(initial.approval_record(), approval="a" * 64, execution_id="b" * 32)
        with pytest.raises(managed.VMHAManagedCredentialError, match="pending exact readback"):
            execute(initial)
    assert len(cloud.calls) == 5 and not initial.source_path.exists()


def test_created_key_readback_does_not_start_request_without_remaining_auth_budget(
    readback_clock,
):
    now, sleeps = readback_clock
    requests = []

    def empty(request, **options):
        requests.append(options)
        now[0] += 0.3
        return NS(wait=lambda: NS(items=[], next_page_token=""))

    with (
        managed.vm_ha_observation_budget(seconds=0.65, clock=lambda: now[0]),
        pytest.raises(managed.VMHAManagedCredentialError, match="pending exact readback"),
    ):
        managed._reread_authorized_key(NS(list_by_account=empty), "sa")
    assert len(requests) == 1 and requests[0]["auth_timeout"] == pytest.approx(0.45)
    assert sleeps == [0.2] and now[0] == pytest.approx(0.5)


def test_cancelled_key_readback_keeps_accepted_creation_for_resume(
    graph, monkeypatch, readback_clock
):
    cloud, journal, inspect, execute = graph
    initial = inspect()
    service = cloud.services["authorized-key"]
    original = service.list_by_account

    def interrupted(request, **options):
        if service.item is not None:
            raise KeyboardInterrupt
        return original(request)

    with journal:
        journal.begin(initial.approval_record(), approval="a" * 64, execution_id="b" * 32)
        with monkeypatch.context() as patch:
            patch.setattr(service, "list_by_account", interrupted)
            with pytest.raises(KeyboardInterrupt):
                execute(initial)
        pending = journal.snapshot()["transaction"]
    assert len(cloud.calls) == 5 and not initial.source_path.exists()
    with journal:
        execute(inspect())
        assert journal.snapshot()["transaction"]["operations"] == pending["operations"]
    assert len(cloud.calls) == 5
