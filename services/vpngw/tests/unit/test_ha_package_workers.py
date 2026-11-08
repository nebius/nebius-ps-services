"""Fenced member preparation overlaps safely and drains uncertain work."""

import contextvars
import hashlib
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Empty
from types import SimpleNamespace

import pytest

from nebius_vpngw import cli
from nebius_vpngw.deploy import checkpoint_conversion, ssh_client_auth
from nebius_vpngw.deploy.ssh_policy import SSHTrustPolicy
from nebius_vpngw.deploy.ssh_push import SSHPush


def entries():
    return [
        (SimpleNamespace(hostname=name, vm_ha_node=SimpleNamespace(node_id=name)), name, object())
        for name in ("standby", "owner")
    ]


def prepare(staged, policy=None):
    cli._prepare_stopped_vm_ha_packages(
        staged=staged,
        ssh_policy=policy or SSHTrustPolicy(known_hosts_file=Path("unused"), known_hosts_sha256=""),
        local={},
        artifact=object(),
        operation_id="operation",
        cluster_id="cluster",
    )


def test_package_evidence_stays_on_command_owner_and_is_fresh(monkeypatch, tmp_path):
    """The real trust policy must not run its SDK/lifecycle callbacks in workers."""
    owner = threading.get_ident()
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("exact host pins\n")
    selected = object()
    token = ssh_client_auth.OPERATOR_IDENTITY.set(selected)
    checks = []
    arrived = threading.Barrier(2)

    def evidence():
        assert threading.get_ident() == owner, "cloud evidence escaped command owner"
        assert ssh_client_auth.operator_identity() is selected
        checks.append(len(checks))

    policy = SSHTrustPolicy(
        known_hosts_file=known_hosts,
        known_hosts_sha256=hashlib.sha256(known_hosts.read_bytes()).hexdigest(),
        _evidence_checks=(evidence,),
    )
    monkeypatch.setattr(checkpoint_conversion, "context", lambda *args, **kwargs: {})

    def package(worker, *_args, **_kwargs):
        assert threading.get_ident() != owner
        worker._ssh_policy.assert_current()
        arrived.wait(timeout=3)
        worker._ssh_policy.assert_current()

    monkeypatch.setattr(cli, "_prepare_vm_ha_agent_package", package)
    try:
        prepare(entries(), policy)
    finally:
        ssh_client_auth.OPERATOR_IDENTITY.reset(token)
    assert checks == [0, 1, 2, 3]


@pytest.mark.parametrize("failure", [RuntimeError("identity drift"), Empty(), KeyboardInterrupt()])
def test_evidence_failure_drains_both_package_workers(monkeypatch, tmp_path, failure):
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("exact host pins\n")
    arrived = threading.Barrier(2)
    settled, checked = [], []

    def evidence():
        checked.append(1)
        if len(checked) == 1:
            raise failure

    policy = SSHTrustPolicy(
        known_hosts_file=known_hosts,
        known_hosts_sha256=hashlib.sha256(known_hosts.read_bytes()).hexdigest(),
        _evidence_checks=(evidence,),
    )
    monkeypatch.setattr(checkpoint_conversion, "context", lambda *args, **kwargs: {})

    def package(worker, target, *_args, **_kwargs):
        arrived.wait(timeout=3)
        try:
            worker._ssh_policy.assert_current()
        finally:
            settled.append(target)

    monkeypatch.setattr(cli, "_prepare_vm_ha_agent_package", package)
    with pytest.raises(type(failure)) as caught:
        prepare(entries(), policy)
    assert caught.value is failure
    assert sorted(settled) == ["owner", "standby"]
    assert len(checked) == (2 if isinstance(failure, Exception) else 1)


def test_interrupted_submission_releases_worker_waiting_for_evidence(monkeypatch, tmp_path):
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("exact host pins\n")
    policy = SSHTrustPolicy(
        known_hosts_file=known_hosts,
        known_hosts_sha256=hashlib.sha256(known_hosts.read_bytes()).hexdigest(),
    )
    running, settled = threading.Event(), threading.Event()
    original_submit = ThreadPoolExecutor.submit
    submitted = []

    def submit(pool, *args, **kwargs):
        if submitted:
            assert running.wait(timeout=3)
            raise KeyboardInterrupt()
        submitted.append(1)
        return original_submit(pool, *args, **kwargs)

    def package(worker, *_args, **_kwargs):
        running.set()
        try:
            worker._ssh_policy.assert_current()
        finally:
            settled.set()

    monkeypatch.setattr(ThreadPoolExecutor, "submit", submit)
    monkeypatch.setattr(checkpoint_conversion, "context", lambda *args, **kwargs: {})
    monkeypatch.setattr(cli, "_prepare_vm_ha_agent_package", package)
    with pytest.raises(KeyboardInterrupt):
        prepare(entries(), policy)
    assert settled.is_set()


def test_interrupted_queue_handoff_does_not_strand_package_worker(tmp_path):
    """Interrupt after Queue removes a request, before its caller receives it."""
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("# Isolated cancellation test\n")
    script = r'''
import hashlib, inspect, queue, sys
from pathlib import Path
from types import SimpleNamespace
from nebius_vpngw import cli
from nebius_vpngw.deploy import checkpoint_conversion
from nebius_vpngw.deploy.ssh_policy import SSHTrustPolicy
path = Path(sys.argv[1])
policy = SSHTrustPolicy(path, hashlib.sha256(path.read_bytes()).hexdigest())
checkpoint_conversion.context = lambda *args, **kwargs: {}
cli._prepare_vm_ha_agent_package = lambda worker, *args, **kwargs: worker._ssh_policy.assert_current()
lines, start = inspect.getsourcelines(queue.Queue.get)
target = start + next(i for i, line in enumerate(lines) if 'self.not_full.notify()' in line)
injected = False
def trace(frame, event, argument):
    global injected
    if (event == 'line' and frame.f_code is queue.Queue.get.__code__
            and frame.f_lineno == target and not injected):
        injected = True
        raise KeyboardInterrupt()
    return trace
sys.settrace(trace)
try:
    cli._prepare_stopped_vm_ha_packages(
        staged=[(SimpleNamespace(hostname=n, vm_ha_node=SimpleNamespace(node_id=n)), n, object())
                for n in ('standby', 'owner')],
        ssh_policy=policy, local={}, artifact=object(), operation_id='operation', cluster_id='cluster')
except KeyboardInterrupt:
    assert injected
    print('interruption drained both workers')
else:
    raise AssertionError('interruption was not propagated')
finally:
    sys.settrace(None)
'''
    child = subprocess.Popen(
        [sys.executable, "-B", "-c", script, str(known_hosts)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        try:
            stdout, stderr = child.communicate(timeout=6)
        except subprocess.TimeoutExpired:
            pytest.fail("Interrupted command stranded a package worker")
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                child.kill()
                child.communicate(timeout=3)
    assert child.returncode == 0, stderr
    assert "interruption drained both workers" in stdout


@pytest.mark.parametrize("drift", ["pins", "cloud", "lifecycle"])
def test_package_workers_reject_evidence_drift_between_connections(monkeypatch, tmp_path, drift):
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("exact host pins\n")
    arrived = threading.Barrier(2)
    changed = threading.Event()
    state = {"cloud": "original", "lifecycle": "original"}
    admitted, rejected = [], []

    def evidence():
        if state != {"cloud": "original", "lifecycle": "original"}:
            raise RuntimeError("identity evidence changed")

    policy = SSHTrustPolicy(
        known_hosts_file=known_hosts,
        known_hosts_sha256=hashlib.sha256(known_hosts.read_bytes()).hexdigest(),
        _evidence_checks=(evidence,),
    )
    monkeypatch.setattr(checkpoint_conversion, "context", lambda *args, **kwargs: {})

    def package(worker, target, *_args, **_kwargs):
        worker._ssh_policy.assert_current()
        admitted.append(target)
        arrived.wait(timeout=3)
        if target == "owner":
            if drift == "pins":
                known_hosts.write_text("changed pins\n")
            else:
                state[drift] = "changed"
            changed.set()
        assert changed.wait(timeout=3)
        try:
            worker._ssh_policy.assert_current()
        except RuntimeError:
            rejected.append(target)
            raise
        pytest.fail("stale evidence admitted a new connection")

    monkeypatch.setattr(cli, "_prepare_vm_ha_agent_package", package)
    with pytest.raises(RuntimeError, match="changed"):
        prepare(entries(), policy)
    assert sorted(admitted) == sorted(rejected) == ["owner", "standby"]


def test_package_workers_overlap_with_exact_context_and_isolated_transports(monkeypatch):
    selected = object()
    token = ssh_client_auth.OPERATOR_IDENTITY.set(selected)
    arrived = threading.Barrier(2)
    observed = []
    monkeypatch.setattr(checkpoint_conversion, "context", lambda *args, **kwargs: {})

    def package(worker, target, instance, local, **kwargs):
        assert ssh_client_auth.operator_identity() is selected
        observed.append(worker)
        arrived.wait(timeout=3)

    monkeypatch.setattr(cli, "_prepare_vm_ha_agent_package", package)
    try:
        prepare(entries())
    finally:
        ssh_client_auth.OPERATOR_IDENTITY.reset(token)
    assert len(observed) == 2 and observed[0] is not observed[1]
    assert observed[0]._authentication_lock is observed[1]._authentication_lock


@pytest.mark.parametrize("failures", [{"standby"}, {"owner"}, {"standby", "owner"}])
def test_package_failure_waits_for_other_member_and_never_activates(monkeypatch, failures):
    running, release, finished = threading.Event(), threading.Event(), threading.Event()
    observed = []
    slow = "owner" if "standby" in failures else "standby"
    first_failure = "standby" if "standby" in failures else "owner"
    monkeypatch.setattr(checkpoint_conversion, "context", lambda *args, **kwargs: {})

    def package(_worker, target, *_args, **_kwargs):
        if target != slow:
            assert running.wait(timeout=3)
            raise RuntimeError(f"{target} failed")
        running.set()
        assert release.wait(timeout=3)
        observed.append(f"{target} settled")
        if target in failures:
            raise RuntimeError(f"{target} failed")

    monkeypatch.setattr(cli, "_prepare_vm_ha_agent_package", package)

    def run():
        try:
            prepare(entries())
            observed.append("activation")
        except RuntimeError as error:
            observed.append(str(error))
        finally:
            finished.set()

    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert running.wait(timeout=3)
        assert not finished.wait(timeout=0.05)
    finally:
        release.set()
        thread.join(timeout=3)
    assert not thread.is_alive()
    assert observed == [f"{slow} settled", f"{first_failure} failed"]


def test_shared_selected_agent_authentication_is_serialized(monkeypatch):
    selected = SimpleNamespace(paramiko_connect_kwargs=lambda: {})
    token = ssh_client_auth.OPERATOR_IDENTITY.set(selected)
    monkeypatch.setattr(
        "nebius_vpngw.deploy.ssh_push.resolve_ssh_client_auth",
        lambda *args, **kwargs: ssh_client_auth.operator_identity(),
    )
    authentication_lock = threading.Lock()
    first, second, release = threading.Event(), threading.Event(), threading.Event()
    callers = threading.Barrier(3)
    count = []

    class AgentTransport:
        def connect(self, **kwargs):
            count.append(1)
            if len(count) == 1:
                first.set()
                assert release.wait(timeout=3)
            else:
                second.set()

    def connect():
        callers.wait(timeout=3)
        SSHPush(authentication_lock=authentication_lock)._connect_client(
            AgentTransport(), hostname="host", username="operator", vm_spec={}
        )

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(contextvars.copy_context().run, connect) for _ in range(2)]
            try:
                callers.wait(timeout=3)
                assert first.wait(timeout=3)
                assert not second.wait(timeout=0.05)
            finally:
                release.set()
            for future in futures:
                future.result()
    finally:
        ssh_client_auth.OPERATOR_IDENTITY.reset(token)
    assert second.is_set()


@pytest.mark.parametrize("staged", [[], entries()[:1], [entries()[0], entries()[0]]])
def test_parallel_preparation_rejects_incomplete_member_set(staged):
    with pytest.raises(RuntimeError, match="two exact members"):
        prepare(staged)
