"""Cross-workstation lease for a fresh Soperator installation."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
import threading
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .terraform_backend import TerraformBackendSettings

SOPERATOR_INSTALL_LEASE_SCHEMA = "nebius-cxcli.soperator-install-lease.v1"
SOPERATOR_INSTALL_LEASE_PREFIX = "nebius-cxcli/soperator-install-leases"
DEFAULT_SOPERATOR_INSTALL_LEASE_TTL_SECONDS = 3600
DEFAULT_SOPERATOR_INSTALL_LEASE_RENEW_INTERVAL_SECONDS = 60


class _LeaseConflict(RuntimeError):
    """A conditional S3 write lost its compare-and-swap race."""


class SoperatorInstallLocalLock:
    """Fail-fast local worktree lock; the Object Storage lease is authoritative."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fd: int | None = None

    def __enter__(self) -> SoperatorInstallLocalLock:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        if not nofollow:
            raise RuntimeError("This platform cannot safely open the Soperator install lock")
        try:
            self._fd = os.open(
                self.path,
                os.O_CREAT | os.O_RDWR | nofollow | getattr(os, "O_CLOEXEC", 0),
                0o600,
            )
        except OSError as exc:
            raise RuntimeError(
                f"Soperator install lock is not a safe regular file: {self.path}"
            ) from exc
        opened = os.fstat(self._fd)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            os.close(self._fd)
            self._fd = None
            raise RuntimeError(
                f"Soperator install lock is not a single-link regular file: {self.path}"
            )
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(self._fd)
            self._fd = None
            raise RuntimeError(
                f"Another Soperator install is using this project: {self.path}"
            ) from exc
        os.fchmod(self._fd, 0o600)
        os.ftruncate(self._fd, 0)
        os.write(
            self._fd,
            (json.dumps({"pid": os.getpid(), "createdAt": _utc_now()}) + "\n").encode("utf-8"),
        )
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._fd is None:
            return
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
            self._fd = None


def soperator_install_lease_key(settings: TerraformBackendSettings, target_ref: str) -> str:
    material = f"{settings.project_id}|{settings.client_name}|{target_ref}".encode()
    digest = hashlib.sha256(material).hexdigest()
    return f"{SOPERATOR_INSTALL_LEASE_PREFIX}/{digest}.json"


def _etag(value: object) -> str:
    return str(value or "").strip().strip('"')


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class SoperatorInstallRemoteLease:
    """Hold a conditional-write S3 lease for one install target.

    The Terraform backend bucket exists before the MK8s cluster, so this lease
    serializes planning and infrastructure creation across workstations. A
    Kubernetes lease can take over as an additional cluster-visible fence after
    cluster handoff.
    """

    def __init__(
        self,
        *,
        settings: TerraformBackendSettings,
        target_ref: str,
        operation_id: str,
        ttl_seconds: int = DEFAULT_SOPERATOR_INSTALL_LEASE_TTL_SECONDS,
        renew_interval_seconds: int = DEFAULT_SOPERATOR_INSTALL_LEASE_RENEW_INTERVAL_SECONDS,
    ) -> None:
        if not target_ref.strip() or not operation_id.strip():
            raise ValueError("Soperator install remote lease requires target_ref and operation_id")
        self.settings = settings
        self.target_ref = target_ref.strip()
        self.operation_id = operation_id.strip()
        self.object_key = soperator_install_lease_key(settings, self.target_ref)
        self.holder_identity = f"cxcli-{uuid4()}"
        if ttl_seconds < 2:
            raise ValueError("Soperator install lease TTL must be at least 2 seconds")
        if renew_interval_seconds < 1 or renew_interval_seconds >= ttl_seconds:
            raise ValueError(
                "Soperator install lease renewal interval must be positive and shorter than its TTL"
            )
        self.ttl_seconds = ttl_seconds
        self.renew_interval_seconds = renew_interval_seconds
        self._etag = ""
        self._lock = threading.Lock()
        self._held = False
        self._renewal_error: Exception | None = None
        self._stop_renewal = threading.Event()
        self._renewal_thread: threading.Thread | None = None
        self._cluster_id = ""
        self._kubernetes_uid = ""

    def _aws(self, *args: str) -> subprocess.CompletedProcess[str]:
        if not shutil.which("aws"):
            raise RuntimeError(
                "aws CLI is required for the cross-workstation Soperator install lease"
            )
        env = os.environ.copy()
        return subprocess.run(
            [
                "aws",
                "--cli-connect-timeout",
                "5",
                "--cli-read-timeout",
                "30",
                "--endpoint-url",
                self.settings.endpoint,
                "s3api",
                *args,
            ],
            env=env,
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )

    def _payload(self, *, expires_at: int) -> dict[str, Any]:
        payload = {
            "schema": SOPERATOR_INSTALL_LEASE_SCHEMA,
            "operationId": self.operation_id,
            "holderIdentity": self.holder_identity,
            "targetRef": self.target_ref,
            "renewedAt": _utc_now(),
            "expiresAtEpoch": expires_at,
        }
        if self._cluster_id and self._kubernetes_uid:
            payload["clusterId"] = self._cluster_id
            payload["kubernetesUid"] = self._kubernetes_uid
        return payload

    def _put(self, *, if_none_match: bool = False, if_match: str = "") -> str:
        expires_at = int(time.time()) + self.ttl_seconds
        payload = self._payload(expires_at=expires_at)
        with tempfile.TemporaryDirectory(prefix="nebius-cxcli-soperator-lease-") as temp_dir:
            body = Path(temp_dir) / "lease.json"
            body.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
            metadata = (
                f"operationid={self.operation_id},holder={self.holder_identity},"
                f"expiresat={expires_at}"
            )
            if self._cluster_id and self._kubernetes_uid:
                metadata += (
                    ",clusteridhash="
                    + hashlib.sha256(self._cluster_id.encode()).hexdigest()
                    + ",kubernetesuidhash="
                    + hashlib.sha256(self._kubernetes_uid.encode()).hexdigest()
                )
            command = [
                "put-object",
                "--bucket",
                self.settings.bucket,
                "--key",
                self.object_key,
                "--body",
                str(body),
                "--content-type",
                "application/json",
                "--metadata",
                metadata,
            ]
            if if_none_match:
                command.extend(["--if-none-match", "*"])
            if if_match:
                command.extend(["--if-match", if_match])
            result = self._aws(*command)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            if any(
                token in detail.lower()
                for token in (
                    "preconditionfailed",
                    "conditionalrequestconflict",
                    "412",
                    "409",
                    "conditional",
                )
            ):
                raise _LeaseConflict(detail or "conditional S3 write conflict")
            raise RuntimeError(
                "Could not acquire the cross-workstation Soperator install lease: "
                + (detail or "unknown S3 error")
            )
        try:
            response = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise RuntimeError("Soperator install lease PUT returned invalid JSON") from exc
        etag = _etag(response.get("ETag") if isinstance(response, Mapping) else "")
        if not etag:
            raise RuntimeError("Soperator install lease PUT returned no ETag")
        return etag

    def _head(self) -> tuple[str, Mapping[str, Any]] | None:
        result = self._aws(
            "head-object",
            "--bucket",
            self.settings.bucket,
            "--key",
            self.object_key,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            if any(
                token in detail.lower() for token in ("notfound", "not found", "404", "nosuchkey")
            ):
                return None
            raise RuntimeError(
                "Could not inspect the cross-workstation Soperator install lease: "
                + (detail or "unknown S3 error")
            )
        try:
            response = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise RuntimeError("Soperator install lease HEAD returned invalid JSON") from exc
        if not isinstance(response, Mapping):
            raise RuntimeError("Soperator install lease HEAD returned an invalid response")
        etag = _etag(response.get("ETag"))
        metadata = response.get("Metadata")
        if not etag or not isinstance(metadata, Mapping):
            raise RuntimeError("Soperator install lease HEAD omitted its ETag or metadata")
        return etag, metadata

    def _acquire(self) -> str:
        for _attempt in range(4):
            try:
                return self._put(if_none_match=True)
            except _LeaseConflict:
                observed = self._head()
                if observed is None:
                    continue
                observed_etag, metadata = observed
                try:
                    expires_at = int(str(metadata.get("expiresat", "")))
                except ValueError as exc:
                    raise RuntimeError(
                        "Cross-workstation Soperator install lease has invalid expiry metadata; "
                        "refusing ambiguous takeover"
                    ) from exc
                if expires_at > int(time.time()):
                    operation = str(metadata.get("operationid", "unknown"))
                    raise RuntimeError(
                        "Another workstation owns the Soperator install lease for target "
                        f"{self.target_ref} (operation {operation}). Wait for it to finish or "
                        f"investigate s3://{self.settings.bucket}/{self.object_key}."
                    ) from None
                try:
                    return self._put(if_match=observed_etag)
                except _LeaseConflict:
                    continue
        raise RuntimeError(
            "Could not acquire the Soperator install lease because its ownership kept changing"
        )

    def _renewal_loop(self) -> None:
        while not self._stop_renewal.wait(self.renew_interval_seconds):
            try:
                self.renew()
            except Exception as exc:  # pragma: no cover - timing-dependent safety path
                with self._lock:
                    self._renewal_error = exc
                return

    def __enter__(self) -> SoperatorInstallRemoteLease:
        with self._lock:
            self._etag = self._acquire()
            self._held = True
            self._renewal_error = None
            self._stop_renewal.clear()
        self._renewal_thread = threading.Thread(
            target=self._renewal_loop,
            name="soperator-install-lease-renewal",
            daemon=True,
        )
        self._renewal_thread.start()
        return self

    def assert_held(self) -> None:
        with self._lock:
            if not self._held or not self._etag:
                raise RuntimeError("Soperator install remote lease is not held")
            if self._renewal_error is not None:
                raise RuntimeError(
                    "Soperator install remote lease renewal failed; refusing to continue"
                ) from self._renewal_error
            observed = self._head()
            if observed is None:
                raise RuntimeError("Soperator install remote lease disappeared")
            observed_etag, metadata = observed
            if (
                observed_etag != self._etag
                or str(metadata.get("operationid", "")) != self.operation_id
                or str(metadata.get("holder", "")) != self.holder_identity
            ):
                raise RuntimeError("Soperator install remote lease ownership changed")
            try:
                expires_at = int(str(metadata.get("expiresat", "")))
            except ValueError as exc:
                self._held = False
                raise RuntimeError(
                    "Soperator install remote lease has invalid expiry metadata"
                ) from exc
            if expires_at <= int(time.time()):
                self._held = False
                raise RuntimeError("Soperator install remote lease expired; refusing to continue")
            if self._cluster_id and self._kubernetes_uid:
                expected_cluster_hash = hashlib.sha256(self._cluster_id.encode()).hexdigest()
                expected_kubernetes_hash = hashlib.sha256(self._kubernetes_uid.encode()).hexdigest()
                if (
                    str(metadata.get("clusteridhash", "")) != expected_cluster_hash
                    or str(metadata.get("kubernetesuidhash", "")) != expected_kubernetes_hash
                ):
                    raise RuntimeError("Soperator install remote lease cluster binding changed")

    def renew(self) -> None:
        with self._lock:
            if not self._held or not self._etag:
                raise RuntimeError("Soperator install remote lease is not held")
            try:
                self._etag = self._put(if_match=self._etag)
            except _LeaseConflict as exc:
                self._held = False
                raise RuntimeError("Soperator install remote lease ownership changed") from exc

    def bind_cluster_identity(self, *, cluster_id: str, kubernetes_uid: str) -> None:
        normalized_cluster_id = str(cluster_id or "").strip()
        normalized_kubernetes_uid = str(kubernetes_uid or "").strip()
        if not normalized_cluster_id or not normalized_kubernetes_uid:
            raise ValueError("Soperator install cluster binding requires both immutable identities")
        with self._lock:
            if not self._held or not self._etag:
                raise RuntimeError("Soperator install remote lease is not held")
            if self._cluster_id and (
                self._cluster_id != normalized_cluster_id
                or self._kubernetes_uid != normalized_kubernetes_uid
            ):
                raise RuntimeError("Soperator install remote lease is bound to another cluster")
            self._cluster_id = normalized_cluster_id
            self._kubernetes_uid = normalized_kubernetes_uid
            try:
                self._etag = self._put(if_match=self._etag)
            except _LeaseConflict as exc:
                self._held = False
                raise RuntimeError("Soperator install remote lease ownership changed") from exc

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self._stop_renewal.set()
        renewal_thread = self._renewal_thread
        if renewal_thread is not None:
            renewal_thread.join(timeout=max(1, self.renew_interval_seconds + 1))
        with self._lock:
            if not self._held or not self._etag:
                return
            result = self._aws(
                "delete-object",
                "--bucket",
                self.settings.bucket,
                "--key",
                self.object_key,
                "--if-match",
                self._etag,
            )
            self._held = False
            if result.returncode != 0 and exc is None:
                detail = (result.stderr or result.stdout or "").strip()
                raise RuntimeError(
                    "Soperator install completed but its remote lease could not be released: "
                    + (detail or "unknown S3 error")
                )


__all__ = [
    "DEFAULT_SOPERATOR_INSTALL_LEASE_RENEW_INTERVAL_SECONDS",
    "DEFAULT_SOPERATOR_INSTALL_LEASE_TTL_SECONDS",
    "SOPERATOR_INSTALL_LEASE_SCHEMA",
    "SoperatorInstallLocalLock",
    "SoperatorInstallRemoteLease",
    "soperator_install_lease_key",
]
