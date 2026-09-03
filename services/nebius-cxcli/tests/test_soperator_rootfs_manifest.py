from __future__ import annotations

import pytest

from nebius_cxcli.soperator_rootfs_manifest import rootfs_manifest

_IMAGE = "registry.example.invalid/soperator/jail@sha256:" + ("a" * 64)
_DIGEST = "sha256:" + ("b" * 64)
_METADATA_DIGEST = "sha256:" + ("c" * 64)


def _entry(path: str, *, kind: str = "file") -> dict[str, str]:
    return {
        "path": path,
        "kind": kind,
        "digest": _DIGEST,
        "metadata_digest": _METADATA_DIGEST,
    }


def test_rootfs_manifest_normalizes_order_and_has_stable_digest() -> None:
    first = rootfs_manifest(
        image=_IMAGE,
        entries=[_entry("/usr/bin/tool"), _entry("/etc", kind="directory")],
    )
    second = rootfs_manifest(
        image=_IMAGE,
        entries=[_entry("/etc", kind="directory"), _entry("/usr/bin/tool")],
    )

    assert [entry.path for entry in first.entries] == ["/etc", "/usr/bin/tool"]
    assert first == second
    assert first.manifest_sha256 == second.manifest_sha256


def test_rootfs_manifest_rejects_duplicate_normalized_paths() -> None:
    with pytest.raises(ValueError, match="unique and path-sorted"):
        rootfs_manifest(
            image=_IMAGE,
            entries=[_entry("/usr/../etc"), _entry("/etc")],
        )


@pytest.mark.parametrize(
    ("image", "entry", "message"),
    [
        ("registry.example.invalid/soperator/jail:latest", _entry("/etc"), "digest-addressed"),
        (
            _IMAGE + "-suffix",
            _entry("/etc"),
            "digest-addressed",
        ),
        (_IMAGE, _entry("/"), "below the rootfs root"),
        (_IMAGE, _entry("/etc", kind="device"), "unsupported rootfs entry kind"),
    ],
)
def test_rootfs_manifest_rejects_unsafe_or_unsealed_identity(
    image: str,
    entry: dict[str, str],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        rootfs_manifest(image=image, entries=[entry])
