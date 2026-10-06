"""Preflight the complete selected output set, then replace individual files."""

from __future__ import annotations

import argparse
import os
import sys
import zipfile
from pathlib import Path
from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit

from course_archives import MAX_FILE_BYTES, archive_bytes, result_entries, write_atomic
from publication import check_budget, format_mb

from .config import COURSES, ROOT
from .metadata import course_metadata
from .pages import render_catalog, render_course, render_shared_guide


class PageTargets(HTMLParser):
    """Validate generated IDs and local navigation against the planned bytes."""

    def __init__(self, content: bytes):
        super().__init__()
        self.ids = set()
        self.links = []
        self.feed(content.decode("utf-8"))

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            if attrs["id"] in self.ids:
                raise ValueError(f"duplicate generated ID: {attrs['id']}")
            self.ids.add(attrs["id"])
        if tag == "a" and "href" in attrs:
            self.links.append(attrs["href"])


def validate_output_links(outputs: dict[Path, bytes]) -> None:
    pages = {
        path: PageTargets(data)
        for path, data in outputs.items()
        if path.suffix == ".html"
    }
    for path, page in list(pages.items()):
        for href in page.links:
            link = urlsplit(href)
            if link.scheme or link.netloc:
                continue
            destination = (
                (path.parent / unquote(link.path)).resolve() if link.path else path
            )
            if destination not in outputs and not destination.is_file():
                raise ValueError(f"missing generated link target: {href}")
            if link.fragment:
                if destination not in pages:
                    data = outputs.get(destination)
                    pages[destination] = PageTargets(
                        data if data is not None else destination.read_bytes()
                    )
                if unquote(link.fragment) not in pages[destination].ids:
                    raise ValueError(f"missing generated fragment: {href}")


def plan_outputs(
    courses: list[str] | tuple[str, ...],
) -> dict[Path, bytes]:
    outputs = {
        ROOT / "lab-guide.html": render_shared_guide().encode("utf-8"),
        ROOT / "index.html": render_catalog().encode("utf-8"),
    }
    for name in courses:
        course = ROOT / name
        outputs[course / "index.html"] = render_course(name).encode("utf-8")
        if course_metadata(course).get("profile") in ("text-only", "reference-only"):
            continue
        resources = result_entries(course)
        outputs[course / "reference" / f"{name}-lab-results.zip"] = archive_bytes(
            resources
        )
    for path in outputs:
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError("generated output must not use symlinks")
    validate_output_links(outputs)
    return outputs


def publication_preflight(outputs: dict[Path, bytes]) -> dict:
    root = ROOT.parent
    return check_budget(
        root,
        {p.relative_to(root).as_posix(): data for p, data in outputs.items()},
        max_file_bytes=MAX_FILE_BYTES,
        max_site_bytes=1_000_000_000,
    )


def report_publication(report: dict, outputs: dict[Path, bytes]) -> None:
    listed_bytes = sum(len(content) for content in outputs.values())
    rows = (
        ("Listed HTML/ZIP outputs:", listed_bytes),
        ("Other publication files:", report["total_bytes"] - listed_bytes),
        ("Estimated site size:", report["total_bytes"]),
        None,
        ("Site limit:", report["max_site_bytes"]),
        ("Remaining capacity:", report["headroom_bytes"]),
        ("Per-file limit:", report["max_file_bytes"]),
    )
    lines = ["", "Publication summary"]
    for row in rows:
        lines.append(f"  {row[0]:<25} {format_mb(row[1]):>13}" if row else "")
    print("\n".join(lines), flush=True)


def report_check(message: str, *, current: bool) -> None:
    stream = sys.stdout if current else sys.stderr
    if (
        stream.isatty()
        and os.environ.get("TERM") != "dumb"
        and "NO_COLOR" not in os.environ
    ):
        color = "32" if current else "31"
        message = f"\033[{color}m{message}\033[0m"
    if not current:
        raise SystemExit(message)
    print(message, file=stream, flush=True)


def output_label(path: Path) -> str:
    label = path.relative_to(ROOT).as_posix()
    if label == "lab-guide.html":
        return label + " (shared guide)"
    if label == "index.html":
        return label + " (course catalog)"
    return label


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build static course pages and external downloads."
    )
    parser.add_argument("courses", nargs="*", choices=COURSES)
    parser.add_argument(
        "--check",
        action="store_true",
        help="check the shared guide, catalog, selected course pages and ZIPs against canonical sources without writing",
    )
    parser.add_argument(
        "--no-summary",
        action="store_true",
        help="omit the final publication summary; size limits and per-file MB output remain active",
    )
    args = parser.parse_args()
    try:
        outputs = plan_outputs(args.courses or COURSES)
        report = publication_preflight(outputs)
        if args.check:
            for path, expected in outputs.items():
                if not path.is_file() or path.read_bytes() != expected:
                    kind = "page" if path.suffix == ".html" else "archive"
                    report_check(
                        f"stale or missing generated {kind}: {output_label(path)}",
                        current=False,
                    )
                report_check(
                    f"current {format_mb(len(expected)):>11} {output_label(path)}",
                    current=True,
                )
        else:
            # Preflight completed. Atomicity is per file, not across the whole build.
            for path, content in outputs.items():
                write_atomic(path, content)
                print(
                    f"built   {format_mb(len(content)):>11} {output_label(path)}",
                    flush=True,
                )
        if not args.no_summary:
            report_publication(report, outputs)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        report_check(str(error), current=False)
