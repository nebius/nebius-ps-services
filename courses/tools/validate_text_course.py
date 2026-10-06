#!/usr/bin/env python3
"""Validate the catalog's reading profiles without executing examples."""

from __future__ import annotations

from course_builder import config as cb_config, metadata as cb_metadata, pages as cb_pages
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit

from validate_course_template import Parser, validate_course_glossary


class TextPage(HTMLParser):
    def __init__(self, document: str, *, allow_diagrams=False):
        super().__init__()
        self.ids: list[str] = []
        self.links: list[tuple[str, bool]] = []
        self.current: list[str] = []
        self.errors: list[str] = []
        self.stack: list[str] = []
        self.in_navigation = False
        self.menu_depth = 0
        self.menu_order: list[tuple[str, str | None]] = []
        self.allow_diagrams = allow_diagrams
        self.resources = Parser()
        self.style_parts: list[str] | None = None
        self.feed(document)
        self.close()
        self.errors.extend(self.resources.errors)
        if self.stack:
            self.errors.append("unclosed HTML element")

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "style":
            self.style_parts = []
        for name in (
            "style", "fill", "stroke", "filter", "clip-path", "mask",
            "marker-start", "marker-mid", "marker-end", "cursor",
        ):
            if values.get(name):
                self.resources.check_css(values[name])
        if any(name in values for name in ("ping", "attributionsrc")):
            self.errors.append("automatic link reporting is forbidden")
        if tag == "meta" and (values.get("http-equiv") or "").lower() == "refresh":
            self.errors.append("automatic page refresh is forbidden")
        for name in ("href", "xlink:href"):
            if name in values and tag not in {"a", "link"}:
                if not (values[name] or "").startswith("#"):
                    self.errors.append(f"nonlocal resource on {tag}: {name}")
        if len(values) != len(attrs):
            self.errors.append("duplicate HTML attribute")
        if tag not in {"meta", "link", "br", "hr", "img", "input", "source", "wbr"}:
            self.stack.append(tag)
        if tag == "nav":
            self.in_navigation = True
        if tag == "div":
            if self.menu_depth:
                self.menu_depth += 1
            elif self.in_navigation and "catalog-navigation" in (values.get("class") or "").split():
                self.menu_depth = 1
        if "id" in values:
            self.ids.append(values["id"])
        if values.get("aria-current") == "page":
            self.current.append(values.get("data-course"))
            if self.menu_depth:
                self.menu_order.append(("current", values.get("data-course")))
        if tag == "a":
            self.links.append((values.get("href", ""), bool(self.menu_depth)))
            if self.menu_depth:
                self.menu_order.append(("link", values.get("href")))
        if (
            tag
            in ({"script", "iframe", "object", "embed", "img", "video", "audio", "form", "foreignobject", "animate", "animatetransform", "set"} | (set() if self.allow_diagrams else {"svg"}))
            or any(name in values for name in ("src", "srcset", "poster", "background"))
            or (tag == "link" and values.get("href") != "data:,")
            or any(name.startswith("on") for name in values)
        ):
            self.errors.append(f"unsupported active or non-text resource: {tag}")

    def handle_endtag(self, tag):
        if tag == "style" and self.style_parts is not None:
            self.resources.check_css("".join(self.style_parts))
            self.style_parts = None
        if tag == "div" and self.menu_depth:
            self.menu_depth -= 1
        if not self.stack or self.stack.pop() != tag:
            self.errors.append(f"unbalanced HTML structure: {tag}")
        if tag == "nav":
            self.in_navigation = False
            self.menu_depth = 0

    def handle_data(self, data):
        if self.style_parts is not None:
            self.style_parts.append(data)


def validate_document(document: str, course_name="soperator") -> None:
    reference = course_name == "gpu-performance-tools"
    page = TextPage(document, allow_diagrams=reference)
    if page.errors or len(page.ids) != len(set(page.ids)):
        raise ValueError(f"invalid text page resources or duplicate IDs: {page.errors}")
    expected = {"../index.html", "../lab-guide.html"} | {
        f"../{name}/index.html" for name in cb_config.COURSES if name != course_name
    }
    found = []
    for target, in_navigation in page.links:
        if target.startswith("#"):
            if target[1:] not in page.ids:
                raise ValueError(f"missing local anchor: {target}")
        elif target in expected and in_navigation:
            found.append(target)
        elif target in {
            "../lab-guide.html#how-to-set-up-the-lab",
            "../lab-guide.html#lab-preparation-scripts",
            "../lab-guide.html#how-to-run-the-labs",
        }:
            continue
        else:
            parsed = urlsplit(target)
            official = (
                (reference and parsed.hostname in {"docs.nvidia.com", "nvidia.github.io", "docs.pytorch.org", "grafana.com", "docs.victoriametrics.com"})
                or
                parsed.hostname == "slurm.schedmd.com"
                or (
                    parsed.hostname == "kubernetes.io"
                    and parsed.path.startswith("/docs/")
                )
                or (
                    parsed.hostname == "github.com"
                    and any(
                        parsed.path == prefix or parsed.path.startswith(prefix + "/")
                        for prefix in (
                            "/nebius/soperator",
                            "/nebius/nebius-solutions-library",
                            "/pytorch/pytorch",
                        )
                    )
                )
            )
            if (
                parsed.scheme != "https"
                or not official
                or parsed.username
                or parsed.password
            ):
                raise ValueError(f"unrecognized reference or navigation: {target}")
    expected_order = [("link", "../index.html")] + [
        ("current", name) if name == course_name else
        ("link", "../" + cb_config.catalog_destination(name))
        for name in cb_config.CATALOG_ENTRIES
    ]
    if (
        set(found) != expected
        or len(found) != len(expected)
        or page.current != [course_name]
        or page.menu_order != expected_order
    ):
        raise ValueError(
            "text course navigation must include catalog and all ordered resources with one current identity"
        )


def validate(course_name="soperator") -> None:
    course = cb_config.ROOT / course_name
    reference = course_name == "gpu-performance-tools"
    metadata = cb_metadata.course_metadata(course)
    required = {
        "COURSE.md",
        "MISSION.md",
        "RESOURCES.md",
        "SYLLABUS.md",
        "README.md",
        "GLOSSARY.md",
        "NEXT-STEPS.md",
        "PUBLICATION-REVIEW.md",
        "reference/course.json",
        "index.html",
    }
    if any(not (course / path).is_file() for path in required):
        raise ValueError("text course is missing canonical publication files")
    for path in (
        "labs",
        "slurm",
        "env",
        "reference/setup.md",
        "reference/grafana",
    ):
        if (course / path).exists():
            raise ValueError(f"text-only course must not contain {path}")
    if not reference and (course / "reference/diagrams").exists():
        raise ValueError("text-only course must not contain reference/diagrams")
    document = (course / "index.html").read_text(encoding="utf-8")
    if document != cb_pages.render_course(course_name):
        raise ValueError("stale text course: rebuild the canonical sources")
    validate_document(document, course_name)
    validate_course_glossary(document, (course / "GLOSSARY.md").read_text())
    syllabus = (course / "SYLLABUS.md").read_text(encoding="utf-8")
    _, _, lessons = cb_metadata.parse_text_course(course / "COURSE.md", metadata["profile"])
    for number, lesson in enumerate(lessons, 1):
        if f"{number}. {lesson['title']}" not in syllabus:
            raise ValueError("syllabus lesson identity differs from canonical prose")
        if not reference and "Answer:" not in lesson["Practice"]:
            raise ValueError("text comprehension checks require feedback")
    for path in course.rglob("*.md"):
        text = path.read_text(encoding="utf-8")
        if re.search(
            r"atlassian\.net|/Users/|/home/[^ /]+|BEGIN (?:RSA |OPENSSH )?PRIVATE KEY",
            text,
        ):
            raise ValueError(f"private material in {path.name}")
    print(
        f"PASS: {course_name} reading course, complete prose, {len(lessons)} lessons and navigation"
    )


if __name__ == "__main__":
    try:
        for name in ("soperator", "gpu-performance-tools"):
            validate(name)
    except ValueError as error:
        raise SystemExit(f"FAIL: {error}") from error
