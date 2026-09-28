"""Verify an explicitly selected kubeconfig against provider cluster identity."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit

import yaml


def _named(payload: Mapping, key: str, name: str) -> Mapping:
    rows = [
        row for row in payload.get(key, []) if isinstance(row, Mapping) and row.get("name") == name
    ]
    if len(rows) != 1:
        raise RuntimeError("Selected kubeconfig context or cluster is missing or ambiguous")
    return rows[0]


def _endpoint(server: str) -> tuple:
    parsed = urlsplit(server)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("invalid endpoint")
    return (
        parsed.hostname.lower(),
        parsed.port or 443,
        parsed.path.rstrip("/"),
        parsed.query,
        parsed.fragment,
    )


def verify_context_cluster(kubeconfig: Path, *, context: str, server: str, ca_pem: str) -> None:
    """Names alone never prove that a local context reaches the intended cluster."""
    try:
        payload = yaml.safe_load(kubeconfig.read_text())
        if not isinstance(payload, Mapping):
            raise ValueError("invalid kubeconfig")
        selected = _named(payload, "contexts", context)["context"]
        if not isinstance(selected, Mapping):
            raise ValueError("invalid context")
        cluster = _named(payload, "clusters", selected["cluster"])["cluster"]
        if not isinstance(cluster, Mapping):
            raise ValueError("invalid cluster")
        if cluster.get("insecure-skip-tls-verify") or cluster.get("tls-server-name"):
            raise ValueError("overridden TLS identity")
        if _endpoint(str(cluster.get("server", ""))) != _endpoint(server):
            raise ValueError("different endpoint")
        data = cluster.get("certificate-authority-data")
        if data:
            authority = base64.b64decode(data, validate=True)
        else:
            path = Path(cluster["certificate-authority"]).expanduser()
            authority = (path if path.is_absolute() else kubeconfig.parent / path).read_bytes()
        if authority.strip() != ca_pem.encode().strip():
            raise ValueError("different certificate authority")
    except (OSError, ValueError, TypeError, KeyError, binascii.Error, yaml.YAMLError):
        raise RuntimeError(
            "Selected kubeconfig context does not match the requested cluster endpoint and CA"
        ) from None
