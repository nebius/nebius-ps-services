"""Bounded streaming helpers for untrusted gzip-compressed tar archives."""

from __future__ import annotations

import gzip
import tarfile
from collections.abc import Iterator
from contextlib import contextmanager
from typing import BinaryIO


class _BoundedDecompressedReader:
    def __init__(self, source: BinaryIO, *, limit: int, label: str) -> None:
        if limit < 1:
            raise ValueError("archive decompression limit must be positive")
        self._source = source
        self._limit = limit
        self._label = label
        self._read = 0

    def read(self, size: int = -1) -> bytes:
        remaining = self._limit - self._read
        requested = remaining + 1 if size < 0 else min(size, remaining + 1)
        chunk = self._source.read(requested)
        self._read += len(chunk)
        if self._read > self._limit:
            raise ValueError(f"{self._label} exceeds the decompressed tar-stream limit")
        return chunk


@contextmanager
def open_bounded_tar_gz(
    source: BinaryIO,
    *,
    max_uncompressed_bytes: int,
    label: str,
) -> Iterator[tarfile.TarFile]:
    """Open one gzip tar stream while counting all physical decompressed bytes."""

    with gzip.GzipFile(fileobj=source, mode="rb") as expanded:
        bounded = _BoundedDecompressedReader(
            expanded,
            limit=max_uncompressed_bytes,
            label=label,
        )
        with tarfile.open(fileobj=bounded, mode="r|") as bundle:
            yield bundle


__all__ = ["open_bounded_tar_gz"]
