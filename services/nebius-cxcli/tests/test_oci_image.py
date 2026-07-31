from __future__ import annotations

import hashlib
import io
import json
import urllib.error
import urllib.request
from urllib.request import Request

import pytest

import nebius_cxcli.oci_image as oci_image
from nebius_cxcli.oci_image import (
    _default_requester,
    _redirect_request,
    is_immutable_oci_image_reference,
    resolve_oci_image,
)


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


_CONFIG_BODY = _json_bytes({"architecture": "amd64", "os": "linux"})
_CONFIG_DIGEST = _digest(_CONFIG_BODY)
_AMD64_BODY = _json_bytes(
    {
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": {
            "mediaType": "application/vnd.oci.image.config.v1+json",
            "digest": _CONFIG_DIGEST,
            "size": len(_CONFIG_BODY),
        },
        "layers": [],
    }
)
_AMD64_DIGEST = _digest(_AMD64_BODY)
_ARM64_DIGEST = "sha256:" + "c" * 64


@pytest.mark.parametrize(
    ("reference", "expected"),
    (
        ("registry.example/team/controller@sha256:" + "a" * 64, True),
        ("registry.example/controller:latest", False),
        ("registry.example/controller@sha256:" + "A" * 64, False),
        ("registry.example/controller@sha256:" + "a" * 63, False),
        ("registry.example/controller @sha256:" + "a" * 64, False),
        ("registry.example/controller@tag@sha256:" + "a" * 64, False),
        ("/registry.example/controller@sha256:" + "a" * 64, False),
        ("registry.example/controller/@sha256:" + "a" * 64, False),
        ("registry.example//controller@sha256:" + "a" * 64, False),
        ("@sha256:" + "a" * 64, False),
        (None, False),
    ),
)
def test_immutable_oci_image_reference_validation(reference: object, expected: bool) -> None:
    assert is_immutable_oci_image_reference(reference) is expected


def test_immutable_oci_image_reference_rejects_long_adversarial_input() -> None:
    reference = "!/" * 4096 + "!@sha256:" + "g" * 64

    assert is_immutable_oci_image_reference(reference) is False


class _TrackedBody(io.BytesIO):
    def __init__(self, body: bytes) -> None:
        super().__init__(body)
        self.read_sizes: list[int] = []

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        return super().read(size)


class _RegistryResponse(_TrackedBody):
    def __init__(
        self,
        body: bytes,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(body)
        self.status = status
        self.headers = headers or {}

    def __enter__(self) -> _RegistryResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class _ResponseOpener:
    def __init__(self, response: _RegistryResponse) -> None:
        self.response = response

    def open(self, _request: Request, *, timeout: int) -> _RegistryResponse:
        assert timeout == 30
        return self.response


class _ErrorOpener:
    def __init__(self, error: urllib.error.HTTPError) -> None:
        self.error = error

    def open(self, _request: Request, *, timeout: int) -> _RegistryResponse:
        assert timeout == 30
        raise self.error


def test_default_requester_bounds_success_response_without_content_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _RegistryResponse(b"12345")
    monkeypatch.setattr(oci_image, "_MAX_CONTROL_RESPONSE_BYTES", 4)
    monkeypatch.setattr(
        urllib.request,
        "build_opener",
        lambda *_handlers: _ResponseOpener(response),
    )

    with pytest.raises(RuntimeError, match="4-byte safety limit"):
        _default_requester(Request("https://registry.example/v2/image/manifests/tag"))

    assert response.read_sizes == [5]
    assert response.closed


def test_default_requester_rejects_oversized_http_error_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response_body = _TrackedBody(b"12345")
    error = urllib.error.HTTPError(
        "https://registry.example/v2/image/manifests/tag",
        500,
        "registry error",
        {},
        response_body,
    )
    monkeypatch.setattr(oci_image, "_MAX_CONTROL_RESPONSE_BYTES", 4)
    monkeypatch.setattr(
        urllib.request,
        "build_opener",
        lambda *_handlers: _ErrorOpener(error),
    )

    with pytest.raises(RuntimeError, match="4-byte safety limit"):
        _default_requester(Request(error.url))

    assert response_body.read_sizes == [5]
    assert response_body.closed


def test_default_requester_rejects_declared_oversized_body_before_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _RegistryResponse(b"ignored", headers={"Content-Length": "5"})
    monkeypatch.setattr(oci_image, "_MAX_CONTROL_RESPONSE_BYTES", 4)
    monkeypatch.setattr(
        urllib.request,
        "build_opener",
        lambda *_handlers: _ResponseOpener(response),
    )

    with pytest.raises(RuntimeError, match="4-byte safety limit"):
        _default_requester(Request("https://registry.example/v2/image/manifests/tag"))

    assert response.read_sizes == []
    assert response.closed


def test_resolves_anonymous_bearer_index_to_exact_platform_digest() -> None:
    requests: list[Request] = []
    index_body = _json_bytes(
        {
            "manifests": [
                {
                    "digest": _ARM64_DIGEST,
                    "platform": {"os": "linux", "architecture": "arm64"},
                },
                {
                    "digest": _AMD64_DIGEST,
                    "size": len(_AMD64_BODY),
                    "platform": {"os": "linux", "architecture": "amd64"},
                },
            ]
        }
    )
    index_digest = _digest(index_body)

    def requester(request: Request):  # type: ignore[no-untyped-def]
        requests.append(request)
        if "/v2/token" in request.full_url:
            return 200, {"Content-Type": "application/json"}, b'{"token":"transient"}'
        if f"/manifests/{_AMD64_DIGEST}" in request.full_url:
            return (
                200,
                {
                    "Docker-Content-Digest": _AMD64_DIGEST,
                    "Content-Type": "application/vnd.oci.image.manifest.v1+json",
                },
                _AMD64_BODY,
            )
        if f"/blobs/{_CONFIG_DIGEST}" in request.full_url:
            return 200, {"Content-Type": "application/vnd.oci.image.config.v1+json"}, _CONFIG_BODY
        if request.get_header("Authorization") is None:
            return (
                401,
                {
                    "WWW-Authenticate": (
                        'Bearer realm="https://registry.example/v2/token",'
                        'service="registry.example"'
                    )
                },
                b"",
            )
        return (
            200,
            {
                "Docker-Content-Digest": index_digest,
                "Content-Type": "application/vnd.oci.image.index.v1+json",
            },
            index_body,
        )

    resolved = resolve_oci_image(
        "registry.example/team/controller:25.11.3",
        requester=requester,
    )

    assert resolved.index_digest == index_digest
    assert resolved.platform_digest == _AMD64_DIGEST
    assert resolved.immutable_reference == f"registry.example/team/controller@{_AMD64_DIGEST}"
    assert all(
        request.get_header("Authorization") == "Bearer transient"
        for request in requests
        if "/manifests/sha256:" in request.full_url or "/blobs/sha256:" in request.full_url
    )


def test_cross_origin_blob_redirect_does_not_forward_bearer_credentials() -> None:
    request = Request(
        "https://registry.example/v2/team/controller/blobs/sha256:abc",
        headers={
            "Authorization": "Bearer transient",
            "Cookie": "private=1",
            "Accept": "application/octet-stream",
            "Host": "registry.example",
        },
        method="GET",
    )

    redirected = _redirect_request(
        request,
        location="https://storage.example/signed-object?signature=public",
    )

    assert redirected.full_url == "https://storage.example/signed-object?signature=public"
    assert redirected.get_header("Authorization") is None
    assert redirected.get_header("Cookie") is None
    assert redirected.get_header("Host") is None
    assert redirected.get_header("Accept") == "application/octet-stream"


def test_same_origin_registry_redirect_retains_bearer_credentials() -> None:
    request = Request(
        "https://registry.example/v2/team/controller/blobs/sha256:abc",
        headers={"Authorization": "Bearer transient"},
        method="GET",
    )

    redirected = _redirect_request(request, location="/internal/blob")

    assert redirected.full_url == "https://registry.example/internal/blob"
    assert redirected.get_header("Authorization") == "Bearer transient"


@pytest.mark.parametrize(
    "location",
    [
        "http://storage.example/object",
        "https://user:password@storage.example/object",
    ],
)
def test_registry_redirect_rejects_insecure_or_credentialed_target(location: str) -> None:
    request = Request("https://registry.example/v2/blob", method="GET")

    with pytest.raises(RuntimeError, match="credential-free HTTPS"):
        _redirect_request(request, location=location)


def test_rejects_non_https_token_realm() -> None:
    def requester(_request: Request):  # type: ignore[no-untyped-def]
        return (
            401,
            {"WWW-Authenticate": 'Bearer realm="http://registry.example/token"'},
            b"",
        )

    with pytest.raises(RuntimeError, match="absolute HTTPS"):
        resolve_oci_image("registry.example/team/controller:25.11.3", requester=requester)


def test_rejects_ambiguous_requested_platform() -> None:
    def requester(_request: Request):  # type: ignore[no-untyped-def]
        body = _json_bytes(
            {
                "manifests": [
                    {
                        "digest": _AMD64_DIGEST,
                        "platform": {"os": "linux", "architecture": "amd64"},
                    },
                    {
                        "digest": "sha256:" + "d" * 64,
                        "platform": {"os": "linux", "architecture": "amd64"},
                    },
                ]
            }
        )
        return (
            200,
            {
                "Docker-Content-Digest": _digest(body),
                "Content-Type": "application/vnd.oci.image.index.v1+json",
            },
            body,
        )

    with pytest.raises(RuntimeError, match="exactly one"):
        resolve_oci_image("registry.example/team/controller:25.11.3", requester=requester)


def test_rejects_registry_digest_header_that_does_not_match_manifest_body() -> None:
    def requester(_request: Request):  # type: ignore[no-untyped-def]
        return (
            200,
            {
                "Docker-Content-Digest": "sha256:" + "f" * 64,
                "Content-Type": "application/vnd.oci.image.manifest.v1+json",
            },
            _AMD64_BODY,
        )

    with pytest.raises(RuntimeError, match="body does not match"):
        resolve_oci_image("registry.example/team/controller:25.11.3", requester=requester)


def test_rejects_selected_manifest_whose_config_platform_is_wrong() -> None:
    wrong_config = _json_bytes({"architecture": "arm64", "os": "linux"})
    wrong_config_digest = _digest(wrong_config)
    manifest = _json_bytes(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {"digest": wrong_config_digest, "size": len(wrong_config)},
            "layers": [],
        }
    )
    manifest_digest = _digest(manifest)

    def requester(request: Request):  # type: ignore[no-untyped-def]
        if "/blobs/" in request.full_url:
            return 200, {}, wrong_config
        return (
            200,
            {
                "Docker-Content-Digest": manifest_digest,
                "Content-Type": "application/vnd.oci.image.manifest.v1+json",
            },
            manifest,
        )

    with pytest.raises(RuntimeError, match="platform does not match"):
        resolve_oci_image("registry.example/team/controller:25.11.3", requester=requester)


@pytest.mark.parametrize(
    "image",
    [
        "controller:25.11.3",
        "registry.example/team/controller",
        f"registry.example/team/controller@{_AMD64_DIGEST}",
    ],
)
def test_requires_explicit_registry_and_tag(image: str) -> None:
    with pytest.raises(ValueError):
        resolve_oci_image(image, requester=lambda _request: (500, {}, b""))
