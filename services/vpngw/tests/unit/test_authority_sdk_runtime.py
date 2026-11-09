"""Real pinned SDK/gRPC mechanics against a loopback-only synthetic provider."""

import asyncio
import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import grpc
import pytest
from nebius.aio.token.renewable import Bearer as RenewableBearer
from nebius.aio.token.token import Bearer, Receiver, Token
from nebius.api.nebius.compute.v1 import GetInstanceRequest, InstanceServiceClient
from nebius.api.nebius.compute.v1.instance_pb2 import Instance
from nebius.api.nebius.compute.v1.instance_service_pb2 import GetInstanceRequest as WireRequest
from nebius.base.resolver import Constant
from nebius.sdk import SDK

from nebius_vpngw.deploy.authority_observation import AuthorityUnavailable, read_compute
from nebius_vpngw.deploy.vm_ha_cloud import vm_ha_observation_budget


class SyntheticTokens(Bearer):
    def __init__(self):
        self.expiry = {}
        self.delay = 0

    def receiver(self):
        owner = self

        class Source(Receiver):
            async def _fetch(self, timeout=None, options=None):
                await asyncio.sleep(owner.delay)
                value = f"synthetic-loopback-{len(owner.expiry)}"
                expiration = datetime.now(timezone.utc) + timedelta(seconds=0.15)
                owner.expiry[value] = expiration
                return Token(value, expiration)

            def can_retry(self, err, options=None):
                return False

        return Source()


@pytest.fixture
def provider():
    source = SyntheticTokens()
    state = SimpleNamespace(reads=0, unavailable=0, denied=False, delayed=False)

    def get(request, context):
        state.reads += 1
        if state.denied:
            context.abort(grpc.StatusCode.PERMISSION_DENIED, "synthetic denial")
        token = dict(context.invocation_metadata()).get("authorization", "").removeprefix("Bearer ")
        if source.expiry.get(token, datetime.min.replace(tzinfo=timezone.utc)) <= datetime.now(
            timezone.utc
        ):
            context.abort(grpc.StatusCode.UNAUTHENTICATED, "synthetic expiry")
        if state.unavailable:
            state.unavailable -= 1
            context.abort(grpc.StatusCode.UNAVAILABLE, "synthetic transport loss")
        if state.delayed:
            while context.is_active():
                time.sleep(0.01)
        response = Instance()
        response.metadata.id = request.id
        return response

    executor = ThreadPoolExecutor(max_workers=2)
    server = grpc.server(executor)
    server.add_generic_rpc_handlers(
        (
            grpc.method_handlers_generic_handler(
                "nebius.compute.v1.InstanceService",
                {
                    "Get": grpc.unary_unary_rpc_method_handler(
                        get,
                        request_deserializer=WireRequest.FromString,
                        response_serializer=Instance.SerializeToString,
                    )
                },
            ),
        )
    )
    port = server.add_insecure_port("127.0.0.1:0")
    assert port
    server.start()
    loop = asyncio.new_event_loop()
    sdk = SDK(
        resolver=Constant(f"127.0.0.1:{port}"),
        options=[("nebius.insecure", True)],
        credentials=RenewableBearer(source),
        event_loop=loop,
    )
    client = InstanceServiceClient(sdk)

    def read():
        return read_compute(
            lambda options: client.get(GetInstanceRequest(id="exact-instance"), **options).wait()
        )

    try:
        yield state, source, read
    finally:
        sdk.sync_close(timeout=2)
        assert not [task for task in asyncio.all_tasks(loop) if not task.done()]
        loop.close()
        assert server.stop(0).wait(timeout=2)
        executor.shutdown(wait=True)


def test_real_sdk_recovers_transport_loss_and_renews_after_idle(provider):
    state, source, read = provider
    state.unavailable = 2
    assert read().metadata.id == "exact-instance"
    assert state.unavailable == 0
    minted = len(source.expiry)
    # The actual owned SDK loop is stopped while its short-lived token expires.
    time.sleep(0.25)
    assert read().metadata.id == "exact-instance"
    assert len(source.expiry) > minted


def test_real_sdk_deadlines_cover_auth_and_rpc_and_client_remains_usable(provider):
    state, source, read = provider
    source.delay = 1
    started = time.monotonic()
    with pytest.raises(AuthorityUnavailable), vm_ha_observation_budget(seconds=0.3):
        read()
    assert time.monotonic() - started < 0.8
    source.delay = 0
    assert read().metadata.id == "exact-instance"
    state.delayed = True
    started = time.monotonic()
    with pytest.raises(AuthorityUnavailable), vm_ha_observation_budget(seconds=0.3):
        read()
    assert time.monotonic() - started < 0.8
    state.delayed = False
    assert read().metadata.id == "exact-instance"
    state.denied = True
    before = state.reads
    with pytest.raises(AuthorityUnavailable) as caught:
        read()
    assert not caught.value.retryable and state.reads == before + 1


def test_real_sdk_concurrent_package_readers_remain_on_command_owner(
    provider, monkeypatch, tmp_path
):
    import threading
    from unittest.mock import Mock

    import paramiko

    from nebius_vpngw import cli
    from nebius_vpngw.deploy import checkpoint_conversion
    from nebius_vpngw.deploy.ssh_policy import SSHTrustPolicy

    _state, _source, read = provider
    owner = threading.get_ident()
    observed = []
    pins = tmp_path / "known_hosts"
    pins.write_text("# synthetic fixed pins\n")

    def evidence():
        assert threading.get_ident() == owner
        observed.append(read().metadata.id)

    policy = SSHTrustPolicy(
        pins, hashlib.sha256(pins.read_bytes()).hexdigest(), _evidence_checks=(evidence,)
    )
    arrived = threading.Barrier(2)
    key = paramiko.RSAKey.generate(1024)
    private = tmp_path / "operator"
    key.write_private_key_file(str(private))
    private.chmod(0o600)
    spec = {
        "ssh_public_key": f"{key.get_name()} {key.get_base64()}",
        "ssh_private_key_path": str(private),
    }

    def package(worker, *_args, **_kwargs):
        arrived.wait(timeout=2)
        worker._ssh_policy.assert_current()
        # Authentication overlaps the other member's real SDK read. Its key
        # discovery was completed before either worker started; no fork here.
        worker._connect_client_unlocked(
            Mock(), hostname="member", username="operator", vm_spec=spec
        )
        worker._ssh_policy.assert_current()

    monkeypatch.setattr(checkpoint_conversion, "context", lambda *a, **kw: {})
    monkeypatch.setattr(cli, "_prepare_vm_ha_agent_package", package)
    staged = [
        (
            SimpleNamespace(hostname=name, vm_ha_node=SimpleNamespace(node_id=name)),
            name,
            object(),
        )
        for name in ("owner", "standby")
    ]
    cli._prepare_stopped_vm_ha_packages(
        staged=staged,
        ssh_policy=policy,
        local={"gateway_group": {"vm_spec": spec}},
        artifact=SimpleNamespace(
            sha256="a" * 64,
            dependency_plans=tuple(
                (
                    instance.hostname,
                    SimpleNamespace(
                        instance=instance, target=target, manifest={"wheel_sha256": "a" * 64}
                    ),
                )
                for instance, target, _ in staged
            ),
        ),
        operation_id="operation",
        cluster_id="cluster",
    )
    assert observed == ["exact-instance"] * 4
