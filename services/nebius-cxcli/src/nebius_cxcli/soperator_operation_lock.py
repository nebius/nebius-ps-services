"""Process-local and cluster-visible locks for Soperator operations."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class SoperatorLeaseAuthority:
    """Exact cluster-visible writer authority for one operation."""

    lease_name: str
    lease_uid: str
    holder_identity_sha256: str
    fencing_epoch: int
    operation_fingerprint: str

    def as_payload(self) -> dict[str, object]:
        return {
            "leaseName": self.lease_name,
            "leaseUid": self.lease_uid,
            "holderIdentitySha256": self.holder_identity_sha256,
            "fencingEpoch": self.fencing_epoch,
            "operationFingerprint": self.operation_fingerprint,
        }


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _notify(emit: Callable[[str], None] | None, message: str) -> None:
    if emit is None:
        return
    try:
        emit(message)
    except Exception:
        # Presentation must never change lock acquisition or fencing semantics.
        return


class SoperatorOperationLocalLock:
    """Owner-only persistent lock whose authority is the held file lock."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fd: int | None = None

    def __enter__(self) -> SoperatorOperationLocalLock:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._acquire()
        os.ftruncate(self._fd, 0)
        os.write(
            self._fd,
            (
                json.dumps({"pid": os.getpid(), "created_at": _utc_now()}, sort_keys=True) + "\n"
            ).encode(),
        )
        return self

    def _acquire(self) -> None:
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        if not nofollow:
            raise RuntimeError("This platform cannot safely open the Soperator operation lock")
        try:
            self._fd = os.open(
                self.path,
                os.O_CREAT | os.O_RDWR | nofollow | getattr(os, "O_CLOEXEC", 0),
                0o600,
            )
        except OSError as exc:
            raise RuntimeError(
                f"Soperator operation lock is not a safe regular file: {self.path}"
            ) from exc
        metadata = os.fstat(self._fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != os.getuid()
        ):
            os.close(self._fd)
            self._fd = None
            raise RuntimeError(
                f"Soperator operation lock is not a single-link owner file: {self.path}"
            )
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(self._fd)
            self._fd = None
            raise RuntimeError(f"A Soperator operation is already running: {self.path}.") from exc
        os.fchmod(self._fd, 0o600)

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = None


class SoperatorOperationLease:
    """Renewable Kubernetes Lease fencing writers across workstations."""

    _NAMESPACE = "kube-system"
    _DURATION_SECONDS = 120
    _RENEW_INTERVAL_SECONDS = 30
    _PATCH_ATTEMPTS = 3
    _RELEASE_RENEW_TIME = "1970-01-01T00:00:00.000000Z"
    _TAKEOVER_QUIESCENCE_SECONDS = 190
    _TAKEOVER_POLL_SECONDS = 5

    def __init__(
        self,
        *,
        kube_context: str,
        cluster_id: str,
        operation_fingerprint: str,
        extra_env: Mapping[str, str] | None = None,
        emit: Callable[[str], None] | None = None,
    ) -> None:
        if not str(kube_context or "").strip():
            raise RuntimeError("Soperator operation Lease requires a Kubernetes context")
        if not str(cluster_id or "").strip():
            raise RuntimeError("Soperator operation Lease requires the immutable MK8s id")
        if not str(operation_fingerprint or "").strip():
            raise RuntimeError("Soperator operation Lease requires an operation fingerprint")
        digest = hashlib.sha256(str(cluster_id).encode()).hexdigest()[:20]
        self.name = f"nebius-cxcli-soperator-{digest}"
        self.fence_name = f"{self.name}-fence"
        self.kube_context = str(kube_context).strip()
        self.cluster_id = str(cluster_id).strip()
        self.operation_fingerprint = str(operation_fingerprint).strip()
        self.holder_identity = f"cxcli-{uuid4()}"
        self._extra_env = dict(extra_env or {})
        self._emit = emit
        self._kubeconfig_env = self._extra_env.get("KUBECONFIG", os.environ.get("KUBECONFIG"))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._renew_error = ""
        self._patch_lock = threading.Lock()
        self._authority: SoperatorLeaseAuthority | None = None
        self._takeover_quiescence_required = False

    def _kubectl(
        self,
        *args: str,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        command = (
            "kubectl",
            "--context",
            self.kube_context,
            "--namespace",
            self._NAMESPACE,
            *args,
        )
        env = dict(os.environ)
        env.update(self._extra_env)
        if self._kubeconfig_env is None:
            env.pop("KUBECONFIG", None)
        else:
            env["KUBECONFIG"] = self._kubeconfig_env
        try:
            return subprocess.run(
                command,
                input=input_text,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
                env=env,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"Unable to access the Soperator operation Lease: {exc}") from exc

    @staticmethod
    def _lease_time(value: object) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    def _manifest(self, *, resource_version: str = "") -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "name": self.name,
            "namespace": self._NAMESPACE,
            "annotations": {
                "nebius-cxcli/operation-fingerprint": self.operation_fingerprint,
                "nebius-cxcli/cluster-id": self.cluster_id,
            },
        }
        if resource_version:
            metadata["resourceVersion"] = resource_version
        return {
            "apiVersion": "coordination.k8s.io/v1",
            "kind": "Lease",
            "metadata": metadata,
            "spec": {
                "holderIdentity": self.holder_identity,
                "leaseDurationSeconds": self._DURATION_SECONDS,
                "acquireTime": _utc_now(),
                "renewTime": _utc_now(),
            },
        }

    @staticmethod
    def _holder_sha256(holder_identity: str) -> str:
        return "sha256:" + hashlib.sha256(holder_identity.encode("utf-8")).hexdigest()

    @staticmethod
    def _metadata(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        metadata = payload.get("metadata")
        return metadata if isinstance(metadata, Mapping) else {}

    @staticmethod
    def _data(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        data = payload.get("data")
        return data if isinstance(data, Mapping) else {}

    def _read_resource(self, kind: str, name: str) -> dict[str, Any] | None:
        result = self._kubectl("get", kind, name, "-o", "json")
        if result.returncode != 0:
            detail = f"{result.stdout}\n{result.stderr}".lower()
            if "notfound" in detail or "not found" in detail:
                return None
            raise RuntimeError(f"Unable to read Soperator {kind}/{name}")
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Soperator {kind}/{name} returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"Soperator {kind}/{name} must be a JSON object")
        return payload

    def _read(self) -> dict[str, Any] | None:
        result = self._kubectl("get", "lease", self.name, "-o", "json")
        if result.returncode != 0:
            detail = f"{result.stdout}\n{result.stderr}".lower()
            if "notfound" in detail or "not found" in detail:
                return None
            raise RuntimeError(
                "Unable to read the Soperator operation Lease: "
                + (result.stderr.strip() or result.stdout.strip() or "kubectl get failed")
            )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Soperator operation Lease returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("Soperator operation Lease must be a JSON object")
        return payload

    def _expired(self, lease: Mapping[str, Any]) -> bool:
        spec = lease.get("spec")
        if not isinstance(spec, Mapping):
            raise RuntimeError("Soperator operation Lease expiry metadata is malformed")
        renewed = self._lease_time(spec.get("renewTime") or spec.get("acquireTime"))
        try:
            duration = int(spec.get("leaseDurationSeconds", 0) or 0)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Soperator operation Lease expiry metadata is malformed") from exc
        if renewed is None or duration <= 0:
            raise RuntimeError("Soperator operation Lease expiry metadata is malformed")
        return (datetime.now(UTC) - renewed).total_seconds() >= duration

    def _acquire(self) -> None:
        for attempt in range(3):
            try:
                existing = self._read()
                if existing is None:
                    command = "create"
                    manifest = self._manifest()
                else:
                    spec = existing.get("spec")
                    holder = (
                        str(spec.get("holderIdentity", "") or "").strip()
                        if isinstance(spec, Mapping)
                        else ""
                    )
                    foreign_holder = bool(holder and holder != self.holder_identity)
                    expired_takeover = self._expired(existing) if foreign_holder else False
                    if foreign_holder and not expired_takeover:
                        raise RuntimeError(
                            "Soperator is locked by another operation through Kubernetes "
                            f"Lease {self._NAMESPACE}/{self.name}"
                        )
                    if expired_takeover:
                        self._takeover_quiescence_required = True
                    metadata = existing.get("metadata")
                    resource_version = (
                        str(metadata.get("resourceVersion", "") or "").strip()
                        if isinstance(metadata, Mapping)
                        else ""
                    )
                    if not resource_version:
                        raise RuntimeError(
                            "Existing Soperator operation Lease has no resourceVersion"
                        )
                    command = "replace"
                    manifest = self._manifest(resource_version=resource_version)
                result = self._kubectl(
                    command,
                    "-f",
                    "-",
                    "-o",
                    "json",
                    input_text=json.dumps(manifest, sort_keys=True),
                )
            except RuntimeError as exc:
                if "timed out" not in str(exc).lower() or attempt == 2:
                    raise
                time.sleep(float((attempt + 1) * 2))
                continue
            if result.returncode == 0:
                try:
                    acquired = json.loads(result.stdout or "{}")
                except json.JSONDecodeError as exc:
                    raise RuntimeError("acquired Soperator Lease returned invalid JSON") from exc
                metadata = acquired.get("metadata") if isinstance(acquired, Mapping) else None
                lease_uid = (
                    str(metadata.get("uid") or "").strip() if isinstance(metadata, Mapping) else ""
                )
                if not lease_uid:
                    reread = self._read()
                    metadata = reread.get("metadata") if isinstance(reread, Mapping) else None
                    lease_uid = (
                        str(metadata.get("uid") or "").strip()
                        if isinstance(metadata, Mapping)
                        else ""
                    )
                if not lease_uid:
                    raise RuntimeError("acquired Soperator Lease has no immutable UID")
                self._allocate_fencing_epoch(lease_uid=lease_uid)
                if self._takeover_quiescence_required:
                    self._wait_for_prior_writers_to_quiesce()
                    self._takeover_quiescence_required = False
                return
            detail = f"{result.stdout}\n{result.stderr}".lower()
            if any(
                marker in detail
                for marker in ("alreadyexists", "conflict", "object has been modified")
            ):
                continue
            raise RuntimeError(
                "Unable to acquire the Soperator operation Lease: "
                + (result.stderr.strip() or result.stdout.strip() or "kubectl failed")
            )
        raise RuntimeError(
            "Unable to acquire the Soperator operation Lease after concurrent updates"
        )

    def _fence_manifest(
        self,
        *,
        epoch: int,
        lease_uid: str,
        resource_version: str = "",
    ) -> dict[str, object]:
        metadata: dict[str, object] = {
            "name": self.fence_name,
            "namespace": self._NAMESPACE,
            "labels": {
                "app.kubernetes.io/managed-by": "nebius-cxcli",
                "app.kubernetes.io/part-of": "soperator",
            },
        }
        if resource_version:
            metadata["resourceVersion"] = resource_version
        return {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": metadata,
            "data": {
                "schema": "nebius-cxcli.soperator-fence.v1",
                "clusterIdSha256": self._holder_sha256(self.cluster_id),
                "epoch": str(epoch),
                "leaseUid": lease_uid,
                "holderIdentitySha256": self._holder_sha256(self.holder_identity),
                "operationFingerprint": self.operation_fingerprint,
            },
        }

    def _allocate_fencing_epoch(self, *, lease_uid: str) -> None:
        for _attempt in range(self._PATCH_ATTEMPTS):
            existing = self._read_resource("configmap", self.fence_name)
            if existing is None:
                epoch = 1
                command = "create"
                manifest = self._fence_manifest(epoch=epoch, lease_uid=lease_uid)
            else:
                metadata = self._metadata(existing)
                resource_version = str(metadata.get("resourceVersion") or "").strip()
                data = self._data(existing)
                try:
                    previous_epoch = int(data.get("epoch") or 0)
                except (TypeError, ValueError) as exc:
                    raise RuntimeError("Soperator fencing epoch is malformed") from exc
                if previous_epoch < 1 or not resource_version:
                    raise RuntimeError("Soperator fencing authority is incomplete")
                epoch = previous_epoch + 1
                command = "replace"
                manifest = self._fence_manifest(
                    epoch=epoch,
                    lease_uid=lease_uid,
                    resource_version=resource_version,
                )
            result = self._kubectl(
                command,
                "-f",
                "-",
                "-o",
                "json",
                input_text=json.dumps(manifest, sort_keys=True),
            )
            if result.returncode == 0:
                self._authority = SoperatorLeaseAuthority(
                    lease_name=self.name,
                    lease_uid=lease_uid,
                    holder_identity_sha256=self._holder_sha256(self.holder_identity),
                    fencing_epoch=epoch,
                    operation_fingerprint=self.operation_fingerprint,
                )
                return
            detail = f"{result.stdout}\n{result.stderr}".lower()
            if any(marker in detail for marker in ("alreadyexists", "conflict", "modified")):
                continue
            raise RuntimeError("Unable to allocate the Soperator fencing epoch")
        raise RuntimeError("Unable to allocate the Soperator fencing epoch after conflicts")

    @staticmethod
    def _job_terminal(job: Mapping[str, Any]) -> bool:
        status = job.get("status")
        status_map = status if isinstance(status, Mapping) else {}
        conditions = status_map.get("conditions")
        return any(
            isinstance(item, Mapping)
            and item.get("type") in {"Complete", "Failed"}
            and str(item.get("status") or "").lower() == "true"
            for item in (conditions if isinstance(conditions, list) else [])
        )

    def _prior_writers_active(self) -> bool:
        authority = self.authority
        selector = "app.kubernetes.io/managed-by=nebius-cxcli"
        for kind in ("jobs", "pods"):
            result = self._kubectl("get", kind, "-A", "-l", selector, "-o", "json")
            if result.returncode != 0:
                raise RuntimeError("Unable to prove prior Soperator writers are quiescent")
            try:
                payload = json.loads(result.stdout or "{}")
            except json.JSONDecodeError as exc:
                raise RuntimeError("Soperator writer inventory returned invalid JSON") from exc
            items = payload.get("items") if isinstance(payload, Mapping) else None
            if not isinstance(items, list):
                raise RuntimeError("Soperator writer inventory is malformed")
            for item in items:
                if not isinstance(item, Mapping):
                    continue
                metadata = self._metadata(item)
                labels = metadata.get("labels")
                label_map = labels if isinstance(labels, Mapping) else {}
                raw_epoch = str(label_map.get("nebius-cxcli/fence-epoch") or "").strip()
                if not raw_epoch or raw_epoch == str(authority.fencing_epoch):
                    continue
                if kind == "jobs" and not self._job_terminal(item):
                    return True
                if kind == "pods":
                    status = item.get("status")
                    phase = str(status.get("phase") or "") if isinstance(status, Mapping) else ""
                    if phase not in {"Succeeded", "Failed"}:
                        return True
        return False

    def _wait_for_prior_writers_to_quiesce(self) -> None:
        _notify(self._emit, "Verifying that prior Soperator writers have stopped")
        deadline = time.monotonic() + float(self._TAKEOVER_QUIESCENCE_SECONDS)
        while True:
            self._patch(self._renew_operations())
            if not self._prior_writers_active():
                _notify(self._emit, "Prior Soperator writers are quiescent")
                return
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    "recovery-required: expired Soperator Lease still has an active or "
                    "ambiguous prior writer"
                )
            _notify(
                self._emit,
                "Expired operation Lease was reclaimed; waiting up to "
                f"{self._TAKEOVER_QUIESCENCE_SECONDS}s for prior writers to stop",
            )
            time.sleep(float(self._TAKEOVER_POLL_SECONDS))

    def _patch(self, operations: Sequence[Mapping[str, Any]]) -> None:
        with self._patch_lock:
            last_detail = "kubectl patch failed"
            for attempt in range(1, self._PATCH_ATTEMPTS + 1):
                try:
                    result = self._kubectl(
                        "patch",
                        "lease",
                        self.name,
                        "--type=json",
                        "-p",
                        json.dumps(list(operations), separators=(",", ":")),
                    )
                except RuntimeError as exc:
                    last_detail = str(exc)
                    transient = True
                else:
                    if result.returncode == 0:
                        return
                    last_detail = result.stderr.strip() or result.stdout.strip() or last_detail
                    lowered = last_detail.lower()
                    holder_test_failed = any(
                        marker in lowered
                        for marker in (
                            "jsonpatch test operation does not apply",
                            "test failed",
                            "conflict",
                            "object has been modified",
                        )
                    )
                    transient = not holder_test_failed and any(
                        marker in lowered
                        for marker in (
                            "unable to create an mk8s exec credential",
                            "getting credentials",
                            "operation timed out",
                            "context deadline exceeded",
                            "connection reset",
                            "connection refused",
                            "tls handshake timeout",
                            "i/o timeout",
                            "etcdserver: leader changed",
                            "etcdserver: request timed out",
                            "rpc error: code = unavailable",
                            "temporarily unavailable",
                            "too many requests",
                            "you must be logged in to the server",
                            " eof",
                        )
                    )
                if not transient or attempt == self._PATCH_ATTEMPTS:
                    break
                time.sleep(float(attempt * 2))
        raise RuntimeError("Lost the Soperator operation Lease: " + last_detail)

    def _renew_loop(self) -> None:
        while not self._stop.wait(self._RENEW_INTERVAL_SECONDS):
            try:
                self._patch(self._renew_operations())
            except RuntimeError as exc:
                self._renew_error = str(exc)
                self._stop.set()
                return

    def _start_renew_thread(self) -> None:
        self._renew_error = ""
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._renew_loop,
            name=f"cxcli-lease-{self.name}",
            daemon=True,
        )
        self._thread.start()

    def _renew_operations(self) -> tuple[dict[str, object], ...]:
        authority = self.authority
        return (
            {"op": "test", "path": "/metadata/uid", "value": authority.lease_uid},
            {
                "op": "test",
                "path": "/spec/holderIdentity",
                "value": self.holder_identity,
            },
            {"op": "replace", "path": "/spec/renewTime", "value": _utc_now()},
        )

    @property
    def authority(self) -> SoperatorLeaseAuthority:
        if self._authority is None:
            raise RuntimeError("Soperator Lease has no allocated fencing authority")
        return self._authority

    def assert_held(self) -> SoperatorLeaseAuthority:
        renewal_failed = bool(self._renew_error or self._stop.is_set())
        self._patch(self._renew_operations())
        fence = self._read_resource("configmap", self.fence_name)
        data = self._data(fence or {})
        authority = self.authority
        expected = {
            "epoch": str(authority.fencing_epoch),
            "leaseUid": authority.lease_uid,
            "holderIdentitySha256": authority.holder_identity_sha256,
            "operationFingerprint": authority.operation_fingerprint,
        }
        if any(str(data.get(key) or "") != value for key, value in expected.items()):
            raise RuntimeError("Lost the Soperator fencing authority")
        if renewal_failed:
            previous_thread = self._thread
            if previous_thread is not None and previous_thread is not threading.current_thread():
                previous_thread.join(timeout=5)
            if previous_thread is not None and previous_thread.is_alive():
                raise RuntimeError("Lost the Soperator Lease renewal worker")
            self._start_renew_thread()
        return authority

    def __enter__(self) -> SoperatorOperationLease:
        self._acquire()
        self._start_renew_thread()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        with suppress(RuntimeError):
            self._patch(
                (
                    {
                        "op": "test",
                        "path": "/spec/holderIdentity",
                        "value": self.holder_identity,
                    },
                    {"op": "replace", "path": "/spec/leaseDurationSeconds", "value": 1},
                    {
                        "op": "replace",
                        "path": "/spec/renewTime",
                        "value": self._RELEASE_RENEW_TIME,
                    },
                )
            )


__all__ = [
    "SoperatorLeaseAuthority",
    "SoperatorOperationLease",
    "SoperatorOperationLocalLock",
]
