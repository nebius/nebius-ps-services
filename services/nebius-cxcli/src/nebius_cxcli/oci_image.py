"""Minimal fail-closed OCI image resolution for upgrade runtime locks."""

from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

_ACCEPT = ", ".join(
    (
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    )
)
_INDEX_MEDIA_TYPES = frozenset(
    {
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
    }
)
_DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
_MANIFEST_MEDIA_TYPES = frozenset(
    {
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    }
)
_MAX_CONTROL_RESPONSE_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class OCIImageResolution:
    source: str
    repository: str
    tag: str
    index_digest: str
    platform_digest: str
    os: str
    architecture: str
    variant: str
    media_type: str

    @property
    def immutable_reference(self) -> str:
        return f"{self.repository}@{self.platform_digest}"

    def as_payload(self) -> dict[str, str]:
        return {
            "source": self.source,
            "repository": self.repository,
            "tag": self.tag,
            "index_digest": self.index_digest,
            "platform_digest": self.platform_digest,
            "os": self.os,
            "architecture": self.architecture,
            "variant": self.variant,
            "media_type": self.media_type,
            "immutable_reference": self.immutable_reference,
        }


Response = tuple[int, Mapping[str, str], bytes]
Requester = Callable[[urllib.request.Request], Response]

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_SENSITIVE_REDIRECT_HEADERS = frozenset(
    {"authorization", "cookie", "proxy-authorization"}
)
_REQUEST_TARGET_HEADERS = frozenset(
    {"connection", "content-length", "host", "transfer-encoding"}
)


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(  # type: ignore[override]
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Mapping[str, str],
        newurl: str,
    ) -> None:
        del req, fp, code, msg, headers, newurl
        return None


def _read_control_response_body(
    response: Any,
    *,
    headers: Mapping[str, str],
) -> bytes:
    content_length = _header(headers, "Content-Length").strip()
    if content_length:
        try:
            declared_size = int(content_length)
        except ValueError:
            declared_size = -1
        if declared_size > _MAX_CONTROL_RESPONSE_BYTES:
            raise RuntimeError(
                "OCI registry control response exceeded the "
                f"{_MAX_CONTROL_RESPONSE_BYTES}-byte safety limit."
            )
    body = response.read(_MAX_CONTROL_RESPONSE_BYTES + 1)
    if len(body) > _MAX_CONTROL_RESPONSE_BYTES:
        raise RuntimeError(
            "OCI registry control response exceeded the "
            f"{_MAX_CONTROL_RESPONSE_BYTES}-byte safety limit."
        )
    return body


def _https_origin(url: str) -> tuple[str, int]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise RuntimeError("OCI registry redirects must use an absolute credential-free HTTPS URL.")
    try:
        port = parsed.port or 443
    except ValueError as exc:
        raise RuntimeError("OCI registry redirect URL has an invalid port.") from exc
    return parsed.hostname.lower(), port


def _redirect_request(
    request: urllib.request.Request,
    *,
    location: str,
) -> urllib.request.Request:
    target = urllib.parse.urljoin(request.full_url, location)
    source_origin = _https_origin(request.full_url)
    target_origin = _https_origin(target)
    headers = {
        key: value
        for key, value in request.header_items()
        if key.lower() not in _REQUEST_TARGET_HEADERS
        and (
            source_origin == target_origin
            or key.lower() not in _SENSITIVE_REDIRECT_HEADERS
        )
    }
    return urllib.request.Request(target, headers=headers, method="GET")


def _default_requester(request: urllib.request.Request) -> Response:
    opener = urllib.request.build_opener(_NoRedirectHandler())
    current = request
    for _redirect_count in range(6):
        try:
            with opener.open(current, timeout=30) as response:  # noqa: S310
                status = response.status
                headers = dict(response.headers.items())
                body = (
                    b""
                    if status in _REDIRECT_STATUSES
                    else _read_control_response_body(response, headers=headers)
                )
        except urllib.error.HTTPError as exc:
            try:
                status = exc.code
                headers = dict(exc.headers.items())
                body = (
                    b""
                    if status in _REDIRECT_STATUSES
                    else _read_control_response_body(exc, headers=headers)
                )
            finally:
                exc.close()
        if status not in _REDIRECT_STATUSES:
            return status, headers, body
        location = _header(headers, "Location")
        if not location:
            raise RuntimeError("OCI registry redirect omitted its Location header.")
        current = _redirect_request(current, location=location)
    raise RuntimeError("OCI registry exceeded the maximum redirect count.")


def _split_image_reference(image: str) -> tuple[str, str, str]:
    value = str(image or "").strip()
    if not value or "@" in value or any(character.isspace() for character in value):
        raise ValueError("OCI image must be a mutable repository:tag reference to resolve.")
    first, slash, remainder = value.partition("/")
    if not slash or not ("." in first or ":" in first or first == "localhost"):
        raise ValueError("OCI image must include an explicit registry hostname.")
    final_slash = remainder.rfind("/")
    final_colon = remainder.rfind(":")
    if final_colon <= final_slash:
        raise ValueError("OCI image must include an explicit tag.")
    repository_path = remainder[:final_colon]
    tag = remainder[final_colon + 1 :]
    if not repository_path or not tag:
        raise ValueError("OCI image repository and tag must be non-empty.")
    return first, repository_path, tag


def _header(headers: Mapping[str, str], name: str) -> str:
    lowered = name.lower()
    return next((str(value) for key, value in headers.items() if key.lower() == lowered), "")


def _bearer_parameters(challenge: str) -> dict[str, str]:
    scheme, separator, raw_parameters = challenge.partition(" ")
    if not separator or scheme.lower() != "bearer":
        raise RuntimeError("OCI registry did not offer anonymous Bearer authentication.")
    parameters: dict[str, str] = {}
    for match in re.finditer(r'(\w+)="([^"\\]*(?:\\.[^"\\]*)*)"', raw_parameters):
        parameters[match.group(1).lower()] = bytes(
            match.group(2), "utf-8"
        ).decode("unicode_escape")
    realm = parameters.get("realm", "")
    parsed = urllib.parse.urlparse(realm)
    if parsed.scheme != "https" or not parsed.netloc:
        raise RuntimeError("OCI Bearer token realm must be an absolute HTTPS URL.")
    return parameters


def _json_object(body: bytes, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{label} returned invalid JSON.") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} returned a non-object payload.")
    return payload


def _manifest_request(
    *,
    url: str,
    requester: Requester,
    bearer_token: str = "",
) -> Response:
    headers = {"Accept": _ACCEPT}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    return requester(urllib.request.Request(url, headers=headers, method="GET"))


def _content_digest(body: bytes) -> str:
    return "sha256:" + hashlib.sha256(body).hexdigest()


def _require_digest_body(body: bytes, expected: str, *, label: str) -> None:
    if not _DIGEST_PATTERN.fullmatch(expected) or _content_digest(body) != expected:
        raise RuntimeError(f"{label} body does not match its locked SHA-256 digest.")


def _authenticated_get(
    *,
    url: str,
    requester: Requester,
    bearer_token: str,
    accept: str = "",
) -> Response:
    headers: dict[str, str] = {}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    if accept:
        headers["Accept"] = accept
    return requester(urllib.request.Request(url, headers=headers, method="GET"))


def resolve_oci_image(
    image: str,
    *,
    os_name: str = "linux",
    architecture: str = "amd64",
    variant: str = "",
    requester: Requester | None = None,
) -> OCIImageResolution:
    """Resolve a tagged OCI image to one exact platform manifest digest."""

    active_requester = requester or _default_requester
    registry, repository_path, tag = _split_image_reference(image)
    encoded_repository = "/".join(
        urllib.parse.quote(part, safe="") for part in repository_path.split("/")
    )
    manifest_url = (
        f"https://{registry}/v2/{encoded_repository}/manifests/"
        f"{urllib.parse.quote(tag, safe='')}"
    )
    bearer_token = ""
    status, headers, body = _manifest_request(url=manifest_url, requester=active_requester)
    if status == 401:
        parameters = _bearer_parameters(_header(headers, "WWW-Authenticate"))
        query = {
            key: value
            for key, value in (
                ("service", parameters.get("service", "")),
                ("scope", parameters.get("scope", f"repository:{repository_path}:pull")),
            )
            if value
        }
        token_url = parameters["realm"]
        token_url += ("&" if "?" in token_url else "?") + urllib.parse.urlencode(query)
        token_status, _token_headers, token_body = active_requester(
            urllib.request.Request(token_url, method="GET")
        )
        if token_status != 200:
            raise RuntimeError(f"OCI anonymous token request failed with HTTP {token_status}.")
        token_payload = _json_object(token_body, label="OCI anonymous token request")
        bearer_token = str(token_payload.get("token") or token_payload.get("access_token") or "")
        if not bearer_token:
            raise RuntimeError("OCI anonymous token response did not contain a token.")
        status, headers, body = _manifest_request(
            url=manifest_url,
            requester=active_requester,
            bearer_token=bearer_token,
        )
    if status != 200:
        raise RuntimeError(f"OCI manifest resolution failed with HTTP {status}.")

    digest = _header(headers, "Docker-Content-Digest")
    if not _DIGEST_PATTERN.fullmatch(digest):
        raise RuntimeError("OCI registry response did not include a valid content digest.")
    _require_digest_body(body, digest, label="OCI manifest")
    media_type = _header(headers, "Content-Type").split(";", 1)[0].strip()
    platform_digest = digest
    platform_body = body
    platform_media_type = media_type
    if media_type in _INDEX_MEDIA_TYPES:
        index = _json_object(body, label="OCI image index")
        candidates: list[Mapping[str, Any]] = []
        for manifest in index.get("manifests", []):
            if not isinstance(manifest, Mapping):
                continue
            platform = manifest.get("platform")
            if not isinstance(platform, Mapping):
                continue
            if (
                str(platform.get("os", "")) == os_name
                and str(platform.get("architecture", "")) == architecture
                and str(platform.get("variant", "")) == variant
            ):
                candidates.append(manifest)
        if len(candidates) != 1 or not _DIGEST_PATTERN.fullmatch(
            str(candidates[0].get("digest", ""))
        ):
            raise RuntimeError(
                "OCI image index must contain exactly one requested platform manifest."
            )
        descriptor = candidates[0]
        platform_digest = str(descriptor.get("digest", ""))
        child_url = (
            f"https://{registry}/v2/{encoded_repository}/manifests/"
            f"{urllib.parse.quote(platform_digest, safe=':')}"
        )
        child_status, child_headers, platform_body = _authenticated_get(
            url=child_url,
            requester=active_requester,
            bearer_token=bearer_token,
            accept=_ACCEPT,
        )
        if child_status != 200:
            raise RuntimeError(
                f"OCI selected platform manifest fetch failed with HTTP {child_status}."
            )
        child_digest = _header(child_headers, "Docker-Content-Digest")
        if child_digest != platform_digest:
            raise RuntimeError(
                "OCI selected platform manifest response changed its locked digest."
            )
        _require_digest_body(
            platform_body,
            platform_digest,
            label="OCI selected platform manifest",
        )
        descriptor_size = descriptor.get("size")
        if type(descriptor_size) is int and descriptor_size != len(platform_body):
            raise RuntimeError("OCI selected platform manifest size does not match its index.")
        platform_media_type = (
            _header(child_headers, "Content-Type").split(";", 1)[0].strip()
        )

    if platform_media_type not in _MANIFEST_MEDIA_TYPES:
        raise RuntimeError("OCI selected platform payload is not an image manifest.")
    platform_manifest = _json_object(platform_body, label="OCI selected platform manifest")
    config = platform_manifest.get("config")
    if not isinstance(config, Mapping):
        raise RuntimeError("OCI selected platform manifest has no image config descriptor.")
    config_digest = str(config.get("digest", ""))
    if not _DIGEST_PATTERN.fullmatch(config_digest):
        raise RuntimeError("OCI selected platform manifest has an invalid config digest.")
    config_url = (
        f"https://{registry}/v2/{encoded_repository}/blobs/"
        f"{urllib.parse.quote(config_digest, safe=':')}"
    )
    config_status, _config_headers, config_body = _authenticated_get(
        url=config_url,
        requester=active_requester,
        bearer_token=bearer_token,
    )
    if config_status != 200:
        raise RuntimeError(f"OCI image config fetch failed with HTTP {config_status}.")
    _require_digest_body(config_body, config_digest, label="OCI image config")
    config_size = config.get("size")
    if type(config_size) is int and config_size != len(config_body):
        raise RuntimeError("OCI image config size does not match its manifest.")
    image_config = _json_object(config_body, label="OCI image config")
    observed_platform = (
        str(image_config.get("os", "")),
        str(image_config.get("architecture", "")),
        str(image_config.get("variant", "")),
    )
    if observed_platform != (os_name, architecture, variant):
        raise RuntimeError(
            "OCI image config platform does not match the requested OS, architecture, and variant."
        )

    return OCIImageResolution(
        source=image,
        repository=f"{registry}/{repository_path}",
        tag=tag,
        index_digest=digest,
        platform_digest=platform_digest,
        os=os_name,
        architecture=architecture,
        variant=variant,
        media_type=media_type,
    )
