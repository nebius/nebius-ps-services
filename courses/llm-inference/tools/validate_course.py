#!/usr/bin/env python3
"""Validate one standalone GPU course without importing its runtime stack."""

from __future__ import annotations

import html.parser
import json
import os
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COURSE_NAMES = (
    "soperator",
    "gpu-fundamentals",
    "gpu-optimizations",
    "llm-training",
    "llm-inference",
    "custom-cuda-kernels",
    "advanced-gpu-communication",
)
CATALOG_ENTRIES = ("soperator", "lab-guide", *COURSE_NAMES[1:])
COURSE_NAVIGATION_LINKS = {"../index.html", "../lab-guide.html"} | {
    f"../{name}/index.html" for name in COURSE_NAMES
}
ALLOWED_HOSTS = {
    "docs.nebius.com",
    "arxiv.org",
    "docs.nvidia.com",
    "developer.nvidia.com",
    "www.nvidia.com",
    "docs.pytorch.org",
    "pytorch.org",
    "docs.vllm.ai",
    "nvidia.github.io",
    "huggingface.co",
    "slurm.schedmd.com",
    "cmake.org",
    "www.lmsys.org",
    "flashinfer.ai",
    "docs.dynamo.nvidia.com",
    "grafana.com",
}
LAB_SECTIONS = (
    "Before you start",
    "Concepts and code path",
    "Practice",
    "Check your results",
    "Investigate the behavior",
    "If something goes wrong",
    "Takeaways and next step",
)
REQUIRED_FILES = {
    "reference/performance-tools.md",
    "reference/observability.json",
    "MISSION.md",
    "COURSE.md",
    "SYLLABUS.md",
    "README.md",
    "GLOSSARY.md",
    "RESOURCES.md",
    "NEXT-STEPS.md",
    "VERSIONS.md",
    "PUBLICATION-REVIEW.md",
    "reference/visual-plan.md",
    "reference/benchmark-record.md",
    "reference/lab-mechanisms.md",
    "reference/cluster-smoke-test.md",
    "reference/evidence-security.md",
    "reference/course.json",
    "reference/visual-manifest.json",
}
LESSON_CLASSES = {
    "lesson-outcome",
    "how-it-works",
    "practice-links",
    "mental-model",
}
REQUIRED_LESSON_FIELDS = (
    "Objective",
    "How it works",
    "Practice",
    "Mental model",
)
FIELD_MINIMUM_WORDS = {"How it works": 180, "Mental model": 15}


def fail(message: str) -> None:
    raise SystemExit(f"FAIL: {message}")


def validate_concept_opening(title: str, fields: dict[str, str]) -> None:
    """Require an objective then substantive teaching; semantic review checks meaning."""
    if tuple(fields) not in (
        REQUIRED_LESSON_FIELDS,
        (*REQUIRED_LESSON_FIELDS, "References"),
    ):
        fail(
            f"{title} opening and fields must be Objective, How it works, Practice, Mental model and optional References in order"
        )
    if "References" in fields and not fields["References"].strip():
        fail(f"{title} References must not be empty")
    if len(words(fields["How it works"])) < 180:
        fail(f"{title} opening is too short to explain its concept")


def validate_lesson_structure(parser: Parser) -> None:
    expected = [
        "lesson-outcome",
        "how-it-works",
        "practice-links",
        "mental-model",
    ]
    for number, (order, diagrams) in enumerate(
        zip(
            parser.lesson_field_orders, parser.lesson_core_diagrams, strict=True
        ),
        1,
    ):
        if order not in (expected, [*expected, "lesson-references"]):
            fail(
                f"Lesson {number} must render ordered teaching fields before any final References"
            )
        if diagrams < 1:
            fail(f"Lesson {number} needs a core diagram inside How it works")


def glossary_entries(content: str) -> list[tuple[str, str]]:
    entries = []
    for line in content.strip().splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(r"- \*\*(.+?)\*\* — (.+)", line)
        if not match or not all(value.strip() for value in match.groups()):
            fail("Glossary entries must use '- **Term** — Definition'")
        entries.append(match.groups())
    keys = [
        re.sub(r"[`*_]", "", term).strip().casefold() for term, _ in entries
    ]
    if not keys or keys != sorted(set(keys)):
        fail("Glossary must contain unique terms in A–Z order")
    return entries


class AppendixLayout(html.parser.HTMLParser):
    """Track actual section ancestry, including unexpected nested appendices."""

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[tuple[str, str]] = []
        self.sections: list[str] = []
        self.headings: list[tuple[str, str, str]] = []
        self.heading: tuple[str, str] | None = None
        self.text: list[str] = []
        self.navigation: list[str] = []
        self.invalid = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        identity = attributes.get("id") or ""
        classes = set((attributes.get("class") or "").split())
        if classes & {
            "lesson-glossary",
            "tools-glossary",
            "lesson-next-steps",
            "tools-where-to-go-next",
        }:
            self.invalid = True
        if tag == "section" and self.stack[-1:] == [("main", "main")]:
            self.sections.append(identity)
        if re.fullmatch(r"h[1-6]", tag):
            parent = ""
            if (
                len(self.stack) >= 2
                and self.stack[-2] == ("main", "main")
                and self.stack[-1][0] == "section"
            ):
                parent = self.stack[-1][1]
            self.heading = (tag, parent)
            self.text = []
        if tag == "a" and any(name == "nav" for name, _ in self.stack):
            self.navigation.append(attributes.get("href") or "")
        if tag not in {
            "meta",
            "link",
            "br",
            "hr",
            "img",
            "input",
            "source",
            "wbr",
        }:
            self.stack.append((tag, identity))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if self.stack and self.stack[-1][0] == tag:
            self.handle_endtag(tag)

    def handle_data(self, data):
        if self.heading is not None:
            self.text.append(data)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1][0] != tag:
            self.invalid = True
            return
        if self.heading is not None and tag == self.heading[0]:
            self.headings.append(
                (*self.heading, " ".join("".join(self.text).split()).casefold())
            )
            self.heading = None
        self.stack.pop()


def validate_course_appendices(document: str) -> None:
    layout = AppendixLayout()
    layout.feed(document)
    layout.close()
    expected = {
        "where to go next": "next-steps",
        "glossary": "guide-glossary",
        "official references": "official-references",
    }
    if (
        layout.invalid
        or layout.stack
        or layout.sections[-3:] != list(expected.values())
    ):
        fail(
            "Where to Go Next, Glossary and Official references must be the final independent course sections; no local copies"
        )
    for label, identity in expected.items():
        headings = [item for item in layout.headings if item[2] == label]
        if (
            headings != [("h2", identity, label)]
            or layout.navigation.count("#" + identity) != 1
        ):
            fail(
                "Where to Go Next, Glossary and Official references each need one top-level heading and one navigation entry"
            )


def validate_course_glossary(document: str, source: str) -> None:
    """Require one course glossary and preserve every canonical term/definition."""
    validate_course_appendices(document)
    rendered = re.findall(
        r'<section id="guide-glossary" data-source="GLOSSARY.md"><h2>Glossary</h2><dl>(.*?)</dl></section>',
        document,
        re.S,
    )
    if len(rendered) != 1:
        fail("Each course must render exactly one Glossary from GLOSSARY.md")
    body = rendered[0]
    pairs = re.findall(r"<dt>(.*?)</dt><dd>(.*?)</dd>", body, re.S)
    if (
        "".join(
            f"<dt>{term}</dt><dd>{definition}</dd>"
            for term, definition in pairs
        )
        != body
    ):
        fail("Course Glossary has invalid definition markup")
    if not source.startswith("# Glossary\n"):
        fail("Course Glossary source must start with its title")
    expected = glossary_entries(source.split("\n", 1)[1])
    if len(pairs) != len(expected):
        fail("Course Glossary entries do not match source")
    for actual_pair, source_pair in zip(pairs, expected, strict=True):
        for actual, canonical in zip(actual_pair, source_pair, strict=True):
            visible = VisibleText()
            visible.feed(actual)
            plain = re.sub(r"[`*]", "", canonical)
            if " ".join("".join(visible.parts).split()) != " ".join(
                plain.split()
            ):
                fail("Course Glossary content does not match source")


STUDENT_RESULT_LINK = re.compile(
    r"reference/(?:lab-results/[0-9]{2}_[a-z0-9_]+/(?:small|large)/[a-zA-Z0-9_.-]+\.(?:json|csv|png)|[a-z0-9-]+-lab-results\.zip)"
)


def validate_result_links(parser) -> None:
    if any(not student_result_link(href) for href in parser.result_links):
        fail("curated results link is missing or not manifest-owned")


def student_result_link(href: str) -> bool:
    """Allow only local downloads named by a curated evidence manifest."""
    if not href or not STUDENT_RESULT_LINK.fullmatch(href):
        return False
    path = ROOT / href
    if not path.is_file() or any(p.is_symlink() for p in (path, *path.parents)):
        return False
    try:
        if path.suffix == ".zip":
            course = json.loads((ROOT / "reference/course.json").read_text())["slug"]
            return path.name == f"{course}-lab-results.zip"
        manifest = json.loads((path.parent / "manifest.json").read_text())
        return path.name in {
            "manifest.json",
            "summary.csv",
            *[r["file"] for r in manifest["artifacts"]],
        }
    except (OSError, ValueError, KeyError, TypeError):
        return False


class Parser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.errors: list[str] = []
        self.ids: set[str] = set()
        self.fragments: list[str] = []
        self.external: list[str] = []
        self.result_links: list[str] = []
        self.reference_depth = 0
        self.lesson_depth = 0
        self.tools_depth = 0
        self.tools_mechanism_depth = 0
        self.lesson_number = 0
        self.last_field_class = ""
        self.figures: dict[str, tuple[str, str]] = {}
        self.lab_id = ""
        self.last_lab_section = ""
        self.lab_heading: list[str] | None = None
        self.lesson_features: set[str] = set()
        self.lessons: list[set[str]] = []
        self.lesson_first_fields: list[str] = []
        self.lesson_field_orders: list[list[str]] = []
        self.lesson_core_diagrams: list[int] = []
        self.figure_depth = 0
        self.related_depth = 0
        self.lesson_openings: list[list[str]] = []
        self.opening_depth = 0
        self.opening_parts: list[str] = []
        self.lab_depth = 0
        self.lab_text: list[str] = []
        self.labs: list[str] = []
        self.details_source: str | None = None
        self.capture_source: str | None = None
        self.capture: list[str] = []
        self.embedded: dict[str, str] = {}
        self.svg_depth = 0
        self.svg_has_title = False
        self.svg_has_desc = False
        self.svg_results: list[tuple[bool, bool]] = []
        self.style_parts: list[str] | None = None
        self.course_nav_depth = 0
        self.catalog_navigation_depth = 0
        self.course_navigation_links: list[str] = []
        self.course_navigation_order: list[tuple[str, str | None]] = []
        self.current_courses: list[str | None] = []

    def check_css(self, css: str) -> None:
        # The course CSS subset needs only local SVG fragment URLs. Reject
        # escapes and resource-loading constructs instead of partially parsing
        # arbitrary CSS and missing an obfuscated external dependency.
        css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
        css = re.sub(r"url\(\s*(['\"]?)#[\w.-]+\1\s*\)", "", css, flags=re.IGNORECASE)
        if "\\" in css or re.search(
            r"@import\b|\burl\s*\(|\bimage-set\s*\(", css, re.IGNORECASE
        ):
            self.errors.append("unsupported CSS resource or escape")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if len({name for name, _ in attrs}) != len(attrs):
            self.errors.append(f"duplicate attribute on {tag}")
        values = dict(attrs)
        classes = set((values.get("class") or "").split())
        if tag in {"script", "iframe", "object", "embed", "form"}:
            self.errors.append(f"forbidden element: {tag}")
        if any(name.lower().startswith("on") for name, _ in attrs):
            self.errors.append(f"inline event handler on {tag}")
        if tag == "meta" and (values.get("http-equiv") or "").lower() == "refresh":
            self.errors.append("automatic page refresh is forbidden")
        if tag == "style":
            self.style_parts = []
        for name in (
            "style",
            "fill",
            "stroke",
            "filter",
            "clip-path",
            "mask",
            "marker-start",
            "marker-mid",
            "marker-end",
            "cursor",
        ):
            if values.get(name):
                self.check_css(values[name])
        for name in ("src", "srcset", "poster", "data", "xlink:href"):
            resource = values.get(name)
            if resource and not (
                (name == "xlink:href" and resource.startswith("#"))
                or (
                    name == "src"
                    and tag == "img"
                    and resource.startswith("data:image/")
                )
            ):
                self.errors.append(f"non-embedded resource on {tag}: {name}")
        element_id = values.get("id")
        if tag == "nav" and (self.course_nav_depth or element_id == "course-contents"):
            self.course_nav_depth += 1
        if tag == "div":
            if self.catalog_navigation_depth:
                self.catalog_navigation_depth += 1
            elif self.course_nav_depth and "catalog-navigation" in classes:
                self.catalog_navigation_depth = 1
        if self.catalog_navigation_depth and values.get("aria-current") == "page":
            self.current_courses.append(values.get("data-course"))
            self.course_navigation_order.append(("current", values.get("data-course")))
        if self.catalog_navigation_depth and tag == "a":
            self.course_navigation_order.append(("link", values.get("href")))
        if element_id:
            if element_id in self.ids:
                self.errors.append(f"duplicate id: {element_id}")
            self.ids.add(element_id)
        href = values.get("href")
        if href and href.startswith("#"):
            self.fragments.append(href[1:])
        elif (
            tag == "a"
            and self.course_nav_depth
            and self.catalog_navigation_depth
            and href in COURSE_NAVIGATION_LINKS
        ):
            self.course_navigation_links.append(href)
        elif (
            (
                (
                    tag == "link"
                    and values.get("rel") == "icon"
                    and (href == "data:," or (href or "").startswith("data:image/"))
                )

            )
            or tag == "a"
            and re.fullmatch(
                r"\.\./advanced-gpu-communication/index\.html(?:#lab-[a-z0-9-]+)?",
                href or "",
            )
        ):
            pass
        elif tag == "a" and href in {
            "../lab-guide.html",
            "../lab-guide.html#how-to-set-up-the-lab",
            "../lab-guide.html#how-to-run-the-labs",
            "https://nebius.github.io/nebius-ps-services/courses/lab-guide.html",
        }:
            pass
        elif tag == "a" and STUDENT_RESULT_LINK.fullmatch(href or ""):
            self.result_links.append(href)
        elif href:
            if tag != "a" or not href.startswith("https://"):
                self.errors.append(f"unsupported link or external resource on {tag}")
            self.external.append(href)
            if self.reference_depth == 0:
                self.errors.append("external link outside official references")
            parsed = urllib.parse.urlparse(href)
            official_host = parsed.hostname in ALLOWED_HOSTS or (
                parsed.hostname == "github.com"
                and (
                    parsed.path.startswith(
                        (
                            "/NVIDIA/",
                            "/NVIDIA-NeMo/Megatron-Bridge/",
                            "/ai-dynamo/nixl/",
                            "/ai-dynamo/dynamo/",
                            "/ai-dynamo/aiperf/",
                            "/triton-inference-server/",
                            "/linux-rdma/perftest/",
                            "/nebius/soperator/",
                        )
                    )
                    or parsed.path.rstrip("/")
                    in {
                        "/deepseek-ai/DeepEP",
                        "/linux-rdma/perftest",
                        "/flashinfer-ai/flashinfer",
                        "/nebius/soperator",
                        "/nebius/nebius-ps-services/tree/main/services/nebius-cxcli",
                    }
                )
            )
            if parsed.scheme != "https" or not official_host:
                self.errors.append(f"non-official reference: {href}")
        if tag == "section" and element_id == "official-references":
            self.reference_depth += 1
        if tag == "section" and element_id == "using-gpu-performance-tools":
            self.tools_depth = 1
        elif tag == "section" and self.tools_depth:
            self.tools_depth += 1
        if tag == "section" and "lesson" in classes:
            self.lesson_depth = 1
            self.lesson_number = int(values.get("data-lesson-number", "0"))
            if self.lesson_number < 1:
                fail("lesson must retain its canonical numeric identity")
            self.last_field_class = ""
            self.lesson_features = set()
            self.lesson_first_fields.append("")
            self.lesson_field_orders.append([])
            self.lesson_core_diagrams.append(0)
            self.lesson_openings.append([])
        elif tag == "section" and self.lesson_depth:
            self.lesson_depth += 1
        if self.lesson_depth:
            if classes & {
                "h100-focus",
                "worked-example",
                "trade-offs",
                "exercise",
                "evidence",
                "interpretation",
                "warning",
                "answer-key",
                "review-cue",
            }:
                self.errors.append("applied teaching sections belong in labs")
            self.lesson_features.update(classes & LESSON_CLASSES)
            placement_classes = classes & (LESSON_CLASSES | {"lesson-references"})
            if placement_classes:
                self.last_field_class = next(iter(placement_classes))
                self.lesson_field_orders[-1].append(self.last_field_class)
                if not self.lesson_first_fields[-1]:
                    self.lesson_first_fields[-1] = self.last_field_class
        if tag == "div":
            if self.tools_mechanism_depth:
                self.tools_mechanism_depth += 1
            elif self.tools_depth and "tools-how-it-works" in classes:
                self.tools_mechanism_depth = 1
            if self.opening_depth:
                self.opening_depth += 1
            elif self.lesson_depth and classes & {"how-it-works"}:
                self.opening_depth = 1
                self.opening_parts = []
        if tag == "aside" and self.opening_depth:
            self.related_depth += 1
        if tag == "figure":
            self.figure_depth += 1
            if self.lesson_depth and not self.opening_depth:
                self.errors.append("lesson diagram must be inside How it works")
            if self.tools_depth and not self.tools_mechanism_depth:
                self.errors.append("tools diagram must be inside How it works")
            if (
                not (self.lesson_depth or self.lab_depth or self.tools_depth)
                or not element_id
            ):
                self.errors.append(
                    "every figure needs a lesson or lab home and unique ID"
                )
            elif self.tools_depth:
                self.figures[element_id] = ("tools", "how-it-works")
            else:
                self.figures[element_id] = (
                    (f"lesson:{self.lesson_number}", self.last_field_class)
                    if self.lesson_depth
                    else (self.lab_id, self.last_lab_section)
                )
        if tag == "article" and "lab" in classes:
            self.lab_depth = 1
            self.lab_text = []
            self.lab_id = "lab:" + (element_id or "").removeprefix("lab-")
            self.last_lab_section = ""
            if values.get("data-lab-guide") != "complete":
                self.errors.append("lab guide is not marked complete")
        elif tag == "article" and self.lab_depth:
            self.lab_depth += 1
        if tag == "h4" and self.lab_depth:
            self.lab_heading = []
        if tag == "details" and "lab-source" in classes:
            self.details_source = values.get("data-source")
        if tag == "code" and self.details_source:
            self.capture_source = self.details_source
            self.capture = []
        if tag == "svg":
            if self.lesson_depth and self.opening_depth and self.figure_depth:
                self.lesson_core_diagrams[-1] += 1
            self.svg_depth = 1
            self.svg_has_title = False
            self.svg_has_desc = False
            if values.get("role") != "img" or not values.get("aria-labelledby"):
                self.errors.append("SVG lacks accessible role/label")
        elif self.svg_depth:
            self.svg_depth += 1
            if tag == "title":
                self.svg_has_title = True
            elif tag == "desc":
                self.svg_has_desc = True

    def handle_data(self, data: str) -> None:
        if self.lab_heading is not None:
            self.lab_heading.append(data)
        if self.opening_depth and not self.figure_depth and not self.related_depth:
            self.opening_parts.append(data)
        if self.style_parts is not None:
            self.style_parts.append(data)
        if self.capture_source:
            self.capture.append(data)
        if self.lab_depth:
            self.lab_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "div" and self.tools_mechanism_depth:
            self.tools_mechanism_depth -= 1
        if tag == "section" and self.tools_depth:
            self.tools_depth -= 1
        if tag == "figure" and self.figure_depth:
            self.figure_depth -= 1
        if tag == "aside" and self.related_depth:
            self.related_depth -= 1
        if tag == "div" and self.catalog_navigation_depth:
            self.catalog_navigation_depth -= 1
        if tag == "nav" and self.course_nav_depth:
            self.course_nav_depth -= 1
            if not self.course_nav_depth:
                self.catalog_navigation_depth = 0
        if tag == "h4" and self.lab_heading is not None:
            self.last_lab_section = slug("".join(self.lab_heading))
            self.lab_heading = None
        if self.opening_depth:
            if tag in {"p", "pre", "h3", "h4", "h5", "h6", "li", "td", "th", "tr"}:
                self.opening_parts.append("\n")
            if tag == "div":
                self.opening_depth -= 1
                if not self.opening_depth:
                    self.lesson_openings[-1].append("".join(self.opening_parts))
                    self.opening_parts = []
        if tag == "style" and self.style_parts is not None:
            self.check_css("".join(self.style_parts))
            self.style_parts = None
        if tag == "code" and self.capture_source:
            self.embedded[self.capture_source] = "".join(self.capture)
            self.capture_source = None
            self.capture = []
        if tag == "details":
            self.details_source = None
        if tag == "article" and self.lab_depth:
            self.lab_depth -= 1
            if not self.lab_depth:
                self.labs.append(" ".join(self.lab_text))
        if tag == "section" and self.lesson_depth:
            self.lesson_depth -= 1
            if not self.lesson_depth:
                self.lessons.append(set(self.lesson_features))
        if tag == "section" and self.reference_depth:
            self.reference_depth -= 1
        if self.svg_depth:
            self.svg_depth -= 1
            if tag == "svg":
                self.svg_results.append((self.svg_has_title, self.svg_has_desc))


def course_paths() -> list[Path]:
    found: list[Path] = []
    for directory, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [
            name
            for name in sorted(dirnames)
            if not name.startswith(".venv")
            and not name.startswith("build")
            and name
            not in {".ruff_cache", ".pytest_cache", "__pycache__", "results", "logs"}
        ]
        found.extend(Path(directory) / name for name in filenames)
    return found


def words(value: str) -> list[str]:
    return re.findall(r"\b[\w'-]+\b", value)


def source_lessons() -> list[tuple[str, dict[str, str], int]]:
    document = (ROOT / "COURSE.md").read_text(encoding="utf-8")
    lessons: list[tuple[str, dict[str, str], int]] = []
    for section in re.split(r"(?m)^## ", document)[1:]:
        title = section.splitlines()[0].strip()
        fields = {
            match.group(1): match.group(2).strip()
            for match in re.finditer(
                r"(?ms)^\*\*([^*]+)\*\*\s*(.*?)(?=^\*\*[^*]+\*\*|\Z)",
                section,
            )
        }
        lessons.append((title, fields, len(words(section))))
    return lessons


class VisibleText(html.parser.HTMLParser):
    """Keep inline punctuation intact while separating block elements."""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"p", "pre", "h3", "h4", "h5", "h6", "li", "th", "td", "tr"}:
            self.parts.append("\n")


def prose_paragraphs(markdown: str):
    """Normalize contiguous table rows, preserving prose and fenced literals."""
    lines = markdown.splitlines()
    normalized = []
    in_code = False
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.strip().startswith("```"):
            in_code = not in_code
        if (
            not in_code
            and line.strip().startswith("|")
            and index + 1 < len(lines)
            and re.fullmatch(r"\|[\s:|\-]+\|", lines[index + 1].strip())
        ):
            rows = [line]
            index += 2
            while index < len(lines) and lines[index].strip().startswith("|"):
                rows.append(lines[index])
                index += 1
            normalized.extend(
                " ".join(cell.strip() for cell in row.strip().strip("|").split("|"))
                for row in rows
            )
            continue
        if not in_code:
            line = re.sub(r"^\s*(?:[-*] |\d+\. )", "", line)
        normalized.append(line)
        index += 1
    yield from re.split(r"\n\s*\n", "\n".join(normalized))


def validate_rendered_openings(parser: Parser, lessons: list) -> None:
    """Bind complete introductory prose to its lesson, not a page-wide occurrence."""
    if len(parser.lesson_openings) != len(lessons):
        fail("lesson opening narrative inventory differs from canonical source")
    for (title, fields, _), openings in zip(
        lessons, parser.lesson_openings, strict=True
    ):
        field = "How it works"
        if field not in fields or len(openings) != 1:
            fail(f"{title} opening narrative is missing or duplicated")
        plain = "\n\n".join(prose_paragraphs(fields[field]))
        plain = re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", plain)
        plain = re.sub(r"(?m)^#{1,6} |^```[^\n]*\n?|^```$", "", plain)
        plain = plain.replace("**", "").replace("`", "")
        expected = " ".join(f"{field} {plain}".split())
        if " ".join(openings[0].split()) != expected:
            fail(f"{title} opening narrative differs from its own lesson HTML")


def validate_next_steps(document: str) -> None:
    """Check the complete optional reading guide at its own closing location."""
    source = ROOT / "NEXT-STEPS.md"
    if not source.is_file():
        fail("next-steps source is missing")
    markdown = source.read_text(encoding="utf-8")
    if not markdown.startswith("# Where to Go Next\n") or "optional" not in markdown:
        fail("next-steps needs its title and optional scope")
    topics = re.split(r"(?m)^- \*\*", markdown)[1:]
    if not topics or any(
        "**Investigate**" not in topic
        or "**Scope**" not in topic
        or not re.search(r"\[[^]]+\]\(https://[^)]+\)", topic)
        for topic in topics
    ):
        fail("next-steps topics need study questions, scope and official links")
    sections = list(
        re.finditer(r'<section id="next-steps">.*?</section>', document, re.DOTALL)
    )
    if len(sections) != 1 or document.count('data-source="NEXT-STEPS.md"') != 1:
        fail("next-steps section or guide is missing or duplicated")
    section = sections[0]
    guides = re.search(
        r'<section id="supporting-guides">.*?</section>', document, re.DOTALL
    )
    references = document.find('<section id="official-references">')
    nav = re.search(r'<nav id="course-contents".*?</nav>', document, re.DOTALL)
    if (
        not guides
        or not nav
        or not guides.end() <= section.start() < section.end() <= references
        or nav[0].count('href="#next-steps"') != 1
    ):
        fail(
            "next-steps must follow guides, precede references and have a sidebar link"
        )
    title = html.escape(markdown.splitlines()[0][2:])
    heading = re.search(r"<h2>(.*?)</h2>", section[0], re.DOTALL)
    if (
        not heading
        or heading[1] != title
        or f'<a href="#next-steps">{title}</a>' not in nav[0]
    ):
        fail("next-steps heading and sidebar label must match its source title")
    article = re.search(
        r'<div data-source="NEXT-STEPS.md">'
        r"(.*?)</div>",
        section[0],
        re.DOTALL,
    )
    if not article:
        fail("next-steps guide is outside its closing section or has invalid metadata")
    if article[1].count('<ul class="next-steps-list">') != 1 or article[1].count(
        "<li>"
    ) != len(topics):
        fail("next-steps must render one bullet per complete topic")
    plain = re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", markdown.split("\n", 1)[1])
    plain = re.sub(r"(?m)^- ", "", plain).replace("**", "").replace("`", "")
    visible = VisibleText()
    visible.feed(article[1])
    if " ".join(plain.split()) != " ".join("".join(visible.parts).split()):
        fail("next-steps narrative differs from its own canonical source")
    bibliography = dict(
        re.findall(
            r'<li id="(reference-\d+)"><a href="([^"]+)">',
            document[references:],
        )
    )
    expected_links = re.findall(r"\[[^]]+\]\((https://[^)]+)\)", markdown)
    actual_links = [
        html.unescape(bibliography.get(target.removeprefix("#"), ""))
        for target in re.findall(r'<a href="([^"]+)">', article[1])
    ]
    if actual_links != expected_links:
        fail("next-steps references differ from their canonical destinations")


def validate_lab_guides(
    document: str, metadata: dict, lessons: list, sources: list[Path]
) -> None:
    expected = {ROOT / "reference/labs" / f"{source.stem}.md" for source in sources}
    if set((ROOT / "reference/labs").glob("*.md")) != expected:
        fail("lab guide inventory contains missing or orphaned files")
    lesson_html = {
        int(re.search(r"<h2>([0-9]+)\.", block)[1]): block
        for block in re.findall(
            r'<section class="lesson".*?</section>', document, re.DOTALL
        )
    }
    for number, (title, fields, _) in enumerate(lessons, 1):
        assigned = [item for item in metadata["labs"] if number in item["lessons"]]
        links = []
        html_links = []
        for item in assigned:
            stem = Path(item["path"]).stem
            guide_title = (
                (ROOT / "reference/labs" / f"{stem}.md").read_text().splitlines()[0][2:]
            )
            links.append(f"- [{guide_title}](reference/labs/{stem}.md)")
            html_links.append(
                f'<li><a href="#lab-{slug(stem)}">{html.escape(guide_title)}</a></li>'
            )
        for item in metadata.get("external_labs", []):
            if number not in item["lessons"]:
                continue
            if (
                set(item) != {"course", "path", "lessons", "title"}
                or item["course"] != "advanced-gpu-communication"
                or not re.fullmatch(r"labs/[0-9]{2}_[a-z0-9_]+\.py", item["path"])
            ):
                fail("invalid external lab ownership")
            stem = Path(item["path"]).stem
            label = item["title"]
            links.append(
                f"- [{label}](../advanced-gpu-communication/reference/labs/{stem}.md)"
            )
            html_links.append(
                f'<li><a href="../advanced-gpu-communication/index.html#lab-{slug(stem)}">{html.escape(label)}</a></li>'
            )
        context, separator, listing = fields.get("Practice", "").partition("\n\n- [")
        entries = ("- [" + listing).splitlines() if separator else []
        if (
            not context.strip()
            or not links
            or len(entries) != len(links)
            or set(entries) != set(links)
        ):
            fail(f"{title} Practice must match its assigned guides")
        practice = re.search(
            r'<div class="practice-links"><h3>Practice</h3>(.*?)<ul>(.*?)</ul>\n</div>',
            lesson_html[number],
            re.DOTALL,
        )
        expected_links = dict(zip(links, html_links, strict=True))
        if not practice or practice[2] != "".join(
            expected_links[entry] for entry in entries
        ):
            fail(f"{title} must contain its exact Practice links")
        visible = VisibleText()
        visible.feed(practice[1])
        plain = re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", context)
        plain = plain.replace("**", "").replace("`", "")
        if " ".join(plain.split()) != " ".join("".join(visible.parts).split()):
            fail(f"{title} Practice context differs from its canonical source")
    for item in metadata["labs"]:
        source = ROOT / item["path"]
        membership = item["lessons"]
        if (
            not isinstance(membership, list)
            or (not membership and metadata.get("profile") != "labs-only")
            or any(
                type(number) is not int or not 1 <= number <= len(lessons)
                for number in membership
            )
            or len(membership) != len(set(membership))
        ):
            fail(f"lab {source.name} has invalid lesson membership")
        markdown = (
            (ROOT / "reference/labs" / f"{source.stem}.md")
            .read_text(encoding="utf-8")
            .strip()
        )
        title, _, narrative = markdown.partition("\n")
        number = source.stem.split("_", 1)[0]
        if not re.fullmatch(rf"# Lab {number}: [^\n.]+", title):
            fail(f"lab {source.name} has a mismatched guide title")
        pieces = re.split(r"^## (.+)\n", narrative, flags=re.MULTILINE)
        if (
            tuple(pieces[1::2]) != LAB_SECTIONS
            or len(pieces[0].split()) < 40
            or any(len(body.split()) < 20 for body in pieces[2::2])
        ):
            fail(f"lab {source.name} has incomplete authored sections")
        anchor = "lab-" + re.sub(r"[^a-z0-9]+", "-", source.stem.lower()).strip("-")
        match = re.search(
            rf'<article class="lab" id="{anchor}".*?</article>', document, re.DOTALL
        )
        if not match or f"<h3>{html.escape(title[2:])}</h3>" not in match[0]:
            fail(f"lab {source.name} title or article is missing")
        article = match[0]
        if tuple(re.findall(r"<h4>(.*?)</h4>", article)) != LAB_SECTIONS:
            fail(f"lab {source.name} rendered sections differ from its guide")
        visible = VisibleText()
        visible.feed(article.split('<details class="lab-source"', 1)[0])
        rendered_text = " ".join("".join(visible.parts).split())
        for paragraph in prose_paragraphs(markdown):
            plain = re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", paragraph)
            plain = re.sub(r"(?m)^#{1,6} |^```[^\n]*\n?|^```$", "", plain)
            plain = plain.replace("**", "").replace("`", "")
            if " ".join(plain.split()) not in rendered_text:
                fail(f"lab {source.name} narrative differs from its HTML")
        for number in membership:
            if (
                f'href="#{anchor}">{html.escape(title[2:])}</a>'
                not in lesson_html[number]
            ):
                fail(f"lab {source.name} has inconsistent lesson links")
        sections = dict(zip(pieces[1::2], pieces[2::2], strict=True))
        commands = re.findall(r"```bash\n(.*?)\n```", sections["Practice"], re.DOTALL)
        if not commands:
            fail(f"lab {source.name} has no run command")
        for command in commands:
            result = subprocess.run(
                ["bash", "-n"],
                input=command,
                text=True,
                capture_output=True,
                check=False,
            )
            if result.returncode:
                fail(f"lab {source.name} has invalid shell command syntax")


def validate_lab_evidence(
    recipe: dict, guide: str, dashboard: dict, course: str
) -> None:
    """Reject missing applicability, wrong dashboards and stale capture instructions."""
    import hashlib

    systems = recipe.get("systems", {})
    if (
        set(systems) != {"applicable", "target", "reason", "view"}
        or type(systems.get("applicable")) is not bool
        or systems.get("target")
        not in {"none", "worker", "rank", "server", "vendor", "optional-cuda"}
        or not systems.get("reason")
        or not systems.get("view")
        or type(recipe.get("gpu_telemetry")) is not bool
    ):
        fail("lab requires explicit Systems applicability and telemetry scope")
    command = recipe.get("systems_command")
    if (
        bool(command) != systems["applicable"]
        or (systems["target"] != "none") != systems["applicable"]
    ):
        fail("Systems command disagrees with applicability")
    if command and (
        command not in guide
        or (
            "COURSE_PROFILE_TOOL=nsys" not in command
            and "--capture systems" not in command
        )
    ):
        fail("guide must include the exact worker/server Systems command")
    if systems["reason"] not in guide or systems["view"] not in guide:
        fail("guide must explain Systems applicability and inspection")
    if "compute_companion" in recipe:
        companion = recipe["compute_companion"]
        if (
            not isinstance(companion, dict)
            or set(companion) != {"path", "nvtx_range"}
            or recipe.get("kind") != "distributed"
            or not isinstance(companion.get("path"), str)
            or not re.fullmatch(r"labs/[a-z][a-z0-9_]*\.py", companion["path"])
            or not isinstance(companion.get("nvtx_range"), str)
            or not re.fullmatch(r"[A-Za-z0-9_:.-]{1,80}", companion["nvtx_range"])
        ):
            fail("invalid local Compute companion metadata")
        source = ROOT / companion["path"]
        if (
            not source.is_file()
            or source.is_symlink()
            or source.resolve().parent != (ROOT / "labs").resolve()
            or companion["nvtx_range"] not in source.read_text()
        ):
            fail("Compute companion source or NVTX range is missing")
        compute_command = recipe.get("compute_command")
        if (
            not isinstance(compute_command, str)
            or not compute_command
            or compute_command not in guide
        ):
            fail("guide must include the exact local Compute companion command")
    lab = recipe["lab"]
    expected_uid = (
        "course-" + hashlib.sha256((course + "/" + lab).encode()).hexdigest()[:24]
    )
    if (
        dashboard.get("uid") != expected_uid
        or dashboard.get("title") != recipe["title"]
        or lab not in dashboard.get("tags", [])
    ):
        fail("dashboard identity differs from assigned lab")
    serialized = json.dumps(dashboard)
    has_gpu = "DCGM_FI_DEV_" in serialized
    if has_gpu != recipe["gpu_telemetry"]:
        fail("dashboard GPU telemetry disagrees with lab applicability")
    if (
        "../../../README.md#how-to-set-up-the-lab" not in guide
        or ("../grafana/" + recipe["lab"] + ".json") not in guide
    ):
        fail("guide must identify shared setup and its assigned dashboard")
    if (
        "nebius-cxcli grafana import" in guide
        or "nebius-cxcli grafana validate" in guide
    ):
        fail("dashboard installation belongs to shared setup")
    if any(
        obsolete in guide
        for obsolete in (
            "--dashboard-json",
            "--dashboard-folder",
            "validate-dashboards",
        )
    ):
        fail("guide contains a removed Grafana CLI command")
    for metric in recipe["metrics"]:
        matches = [
            panel for panel in dashboard["panels"] if panel["title"] == metric["title"]
        ]
        if len(matches) != 1:
            fail("dashboard measurement panel or unit differs from recipe")
        fields = matches[0]["fieldConfig"]
        units = {
            override["matcher"]["options"]: prop["value"]
            for override in fields["overrides"]
            if override["matcher"]["id"] == "byName"
            for prop in override["properties"]
            if prop["id"] == "unit"
        }
        if (
            fields["defaults"]["unit"] != "none"
            or units.get("case") != "string"
            or any(units.get("Value #" + ref) != metric["unit"] for ref in ("A", "B"))
        ):
            fail("dashboard measurement panel or unit differs from recipe")
        for target in matches[0]["targets"]:
            expression = target["expr"]
            if (
                f"course_lab_{metric['name']}" not in expression
                or f'lab="{lab}"' not in expression
                or f'course="{course}"' not in expression
            ):
                fail("dashboard query uses the wrong metric or lab")


def validate_observability_assets(document: str, metadata: dict) -> None:
    for name in ("COURSE.md", "README.md"):
        if (ROOT / name).read_text().splitlines()[0] != "# " + metadata["title"]:
            fail(f"{name} title must match canonical metadata")
    if (
        metadata.get("profile") != "labs-only"
        and metadata.get("performance_tools") != "reference/performance-tools.md"
    ) or metadata.get("observability") != "reference/observability.json":
        fail("invalid performance tools metadata")
    recipes = json.loads((ROOT / metadata["observability"]).read_text())
    if set(recipes["labs"]) != {Path(row["path"]).stem for row in metadata["labs"]}:
        fail("observability recipes must cover every executable lab")
    dashboards = ["reference/grafana/environment_readiness.json"]
    companions = []
    for row in metadata["labs"]:
        expected = "reference/grafana/" + Path(row["path"]).stem + ".json"
        if (
            row["dashboard"] != expected
            or recipes["labs"][Path(row["path"]).stem]["dashboard"] != expected
        ):
            fail("lab dashboard wiring differs")
        recipe = recipes["labs"][Path(row["path"]).stem]
        validate_lab_evidence(
            recipe,
            (ROOT / "reference/labs" / (recipe["lab"] + ".md")).read_text(),
            json.loads((ROOT / expected).read_text()),
            metadata["slug"],
        )
        dashboards.append(expected)
        if "compute_companion" in recipe:
            companions.append(recipe["compute_companion"]["path"])
    assets = re.findall(
        r'<a class="course-download" download="([^\"]+)" data-asset="([^\"]+)" href="([^\"]+)">',
        document,
    )
    expected_links = {
        (f"{metadata['slug']}-lab-results.zip", "lab-results",
         f"reference/{metadata['slug']}-lab-results.zip")
    }
    if len(assets) != 1 or set(assets) != expected_links or "data:application/" in document:
        fail("downloadable lab assets must be exactly one external course results ZIP")
    if (ROOT / "reference" / f"{metadata['slug']}-lab-kit.zip").exists():
        fail("lab-kit archives must not be published; sync original sources instead")
    introduction = re.search(r'<div id="course-downloads">(.*?)</div>', document, re.DOTALL)
    if not introduction or '<h2>Practical labs</h2>' not in document:
        fail("Practical labs must include the course download introduction")
    visible = VisibleText()
    visible.feed(introduction[1])
    expected_intro = (
        "Download results Download all lab results: Grafana dashboards, Small and Large results "
        "Setup the lab environment: Lab setup guide"
    )
    if (
        " ".join("".join(visible.parts).split()) != expected_intro
        or '<h3>Download results</h3>' not in introduction[1]
        or '<a href="../lab-guide.html">Lab setup guide</a>' not in introduction[1]
    ):
        fail("course download introduction must separate results and setup labels from their links")
    if metadata.get("profile") == "labs-only":
        return
    primer = re.search(
        r'<section id="using-gpu-performance-tools".*?</section>', document, re.DOTALL
    )
    if not primer or document.index(primer[0]) > document.index('class="lesson"'):
        fail("performance tools lesson must precede numbered lessons")
    navigation = re.search(r"<nav\b.*?</nav>", document, re.DOTALL)
    if (
        not navigation
        or navigation[0].count('href="#using-gpu-performance-tools"') != 1
    ):
        fail("performance tools lesson must be in navigation")
    visible = VisibleText()
    visible.feed(primer[0])
    rendered = " ".join("".join(visible.parts).split())
    source = (ROOT / metadata["performance_tools"]).read_text()
    sections = re.split(r"^## (.+)\n", source, flags=re.M)
    fields = dict(zip(sections[1::2], sections[2::2], strict=True))
    if tuple(fields) not in (REQUIRED_LESSON_FIELDS, (*REQUIRED_LESSON_FIELDS, "References")):
        fail("Performance tools must use the lesson fields without local Where to Go Next or Glossary sections")
    code_blocks = re.findall(r"```[^\n]*\n(.*?)\n```", source, re.DOTALL)
    rendered_code = re.findall(
        r"<pre[^>]*><code>(.*?)</code></pre>", primer[0], re.DOTALL
    )
    if code_blocks != [html.unescape(code) for code in rendered_code]:
        fail("performance tools code differs from source")
    prose = re.sub(r"```[^\n]*\n.*?\n```", "", source, flags=re.DOTALL)
    for paragraph in prose_paragraphs(prose):
        plain = re.sub(r"!?\[([^]]+)\]\([^)]+\)", r"\1", paragraph)
        plain = re.sub(r"(?m)^#{1,6} ", "", plain).replace("**", "").replace("`", "")
        if " ".join(plain.split()) not in rendered:
            fail("performance tools lesson differs from source")


def validate_shared_setup_link(document: str, metadata: dict) -> None:
    if "setup_guide" in metadata or 'class="setup-guide"' in document:
        fail("numbered setup guides are not part of the publication contract")
    introduction = re.search(r'<div id="course-downloads">(.*?)</div>', document, re.DOTALL)
    if not introduction or '<a href="../lab-guide.html">Lab setup guide</a>' not in introduction[1]:
        fail("course must link to the shared setup guide in its download introduction")
    if 'class="shared-setup"' in document:
        fail("repeated setup instructions must not follow the download introduction")


def validate_publication_artifacts(paths: list[Path]) -> None:
    validate_student_results()
    runtime_suffixes = {
        ".out",
        ".log",
        ".qdrep",
        ".nsys-rep",
        ".ncu-rep",
        ".sqlite",
        ".pt",
        ".pth",
        ".safetensors",
        ".pem",
        ".key",
    }
    if any(
        path.suffix.lower() in runtime_suffixes or path.name == ".env" for path in paths
    ):
        fail(
            "raw runtime or credential artifacts are present outside excluded private directories"
        )


def validate_student_results() -> None:
    import hashlib
    import zipfile

    root = ROOT / "reference/lab-results"
    if root.is_symlink():
        fail("student evidence directory must not be a symlink")
    catalog = json.loads((ROOT / "reference/course.json").read_text())
    course_slug = catalog["slug"]
    archive = ROOT / "reference" / f"{course_slug}-lab-results.zip"
    labs = {Path(row["path"]).stem for row in catalog["labs"]}
    expected = {}
    for manifest in root.glob("*/*/manifest.json"):
        if any(p.is_symlink() for p in (manifest, *manifest.parents)):
            fail("student evidence manifest must not use symlinks")
        data = json.loads(manifest.read_text())
        lab, profile = manifest.parent.parent.name, manifest.parent.name
        if (
            lab not in labs
            or profile not in ("small", "large")
            or data.get("schema") != "course-lab-results/v1"
            or data.get("lab") != lab
            or data.get("profile") != profile
            or data.get("course") != course_slug
        ):
            fail("student evidence identity or workload differs")
        rows = [
            {"file": "summary.csv", "sha256": data["summary_sha256"]},
            *data["artifacts"],
        ]
        if len({r["file"] for r in rows}) != len(rows):
            fail("duplicate public evidence file")
        for row in rows:
            name = row["file"]
            path = manifest.parent / name
            if (
                Path(name).name != name
                or path.is_symlink()
                or path.suffix not in (".csv", ".json", ".png")
                or not path.is_file()
            ):
                fail("invalid public evidence file")
            content = path.read_bytes()
            if hashlib.sha256(content).hexdigest() != row["sha256"]:
                fail("public evidence checksum differs")
            if path.suffix == ".png" and not content.startswith(b"\x89PNG\r\n\x1a\n"):
                fail("invalid evidence screenshot")
            expected[f"{profile}/{lab}/{name}"] = content
        expected[f"{profile}/{lab}/manifest.json"] = (
            manifest.read_bytes()
        )
    if (root.exists() and not expected) or not archive.is_file():
        fail("student evidence needs complete manifests and its separate ZIP")
    dashboards = {"reference/grafana/environment_readiness.json"} | {row["dashboard"] for row in catalog["labs"]}
    for relative in dashboards:
        path = ROOT / relative
        if not path.is_file() or any(p.is_symlink() for p in (path, *path.parents)):
            fail("invalid resource dashboard")
        expected["grafana-dashboards/" + path.name] = path.read_bytes()
    with zipfile.ZipFile(archive) as bundle:
        if len(bundle.namelist()) != len(expected) or set(bundle.namelist()) != set(
            expected
        ):
            fail("student results ZIP has missing, duplicate or unowned files")
        if any(bundle.read(name) != content for name, content in expected.items()):
            fail("student results ZIP differs from current evidence")


def validate_publication_text(paths: list[Path]) -> None:
    def source_text(path: Path) -> str:
        return path.read_text(encoding="utf-8", errors="ignore")

    combined = "\n".join(
        source_text(path)
        for path in paths
        if path.suffix.lower()
        in {
            ".md",
            ".py",
            ".html",
            ".sbatch",
            ".sh",
            ".txt",
            ".cu",
            ".cpp",
            ".cuh",
            ".hpp",
            ".h",
            ".svg",
            ".json",
            ".css",
        }
    )
    for pattern in (
        r"/" + r"Users/[^/\s]+",
        r"AKIA[0-9A-Z]{16}",
        r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----",
        r"https?://[^\s]*(?:\.internal|localhost)",
    ):
        if re.search(pattern, combined, re.IGNORECASE):
            fail(f"publication-safety pattern matched: {pattern}")


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def validate_figures(
    document: str, parser: Parser, lessons: list, metadata: dict
) -> None:
    """Compare declared placement with the actual enclosing article and section."""
    fields = {
        field: slug(field) for field in REQUIRED_LESSON_FIELDS if field != "Practice"
    }
    fields.update(
        {
            "Objective": "lesson-outcome",
        }
    )
    labs = {Path(item["path"]).stem: item["lessons"] for item in metadata["labs"]}
    expected = (
        {"tools-measurement-loop": ("tools", "how-it-works")}
        if metadata.get("performance_tools")
        else {}
    )

    if metadata.get("performance_tools"):
        source = (ROOT / metadata["performance_tools"]).read_text()
        source = re.sub(r"```[^\n]*\n.*?\n```", "", source, flags=re.DOTALL)
        section = ""
        for line in source.splitlines():
            if line.startswith("## "):
                section = line[3:]
            match = re.fullmatch(r"!\[([^]]+)\]\(([^)]+)\)", line.strip())
            if not match:
                continue
            relative = match[2]
            if section != "How it works" or not re.fullmatch(
                r"diagrams/tools-[a-z0-9-]+\.svg", relative
            ):
                fail("tool figure has an invalid path or section")
            path = ROOT / "reference" / relative
            if (
                not path.is_file()
                or not path.resolve().is_relative_to(ROOT.resolve())
                or any(part.is_symlink() for part in (path, *path.parents))
            ):
                fail("tool figure path is missing or unsafe")
            target = path.stem
            if target in expected:
                fail("duplicate tool figure")
            figure = re.search(
                rf'<figure[^>]*id="{target}"[^>]*>.*?</figure>', document, re.DOTALL
            )
            if not figure or path.read_text().strip() not in figure[0]:
                fail("tool figure differs from source")
            expected[target] = ("tools", "how-it-works")

    def location(home: str, lesson: int, after: str) -> tuple[str, str]:
        if type(lesson) is not int or not 1 <= lesson <= len(lessons):
            fail("diagram points to a missing lesson")
        if home == "lesson":
            if after != "How it works" or after not in lessons[lesson - 1][1]:
                fail("diagram points to an invalid lesson field")
            return f"lesson:{lesson}", fields[after]
        if not isinstance(home, str) or not home.startswith("lab:"):
            fail("diagram needs an explicit lesson or lab home")
        stem = home.removeprefix("lab:")
        if stem not in labs or lesson not in labs[stem] or after not in LAB_SECTIONS:
            fail("diagram lab home must include its primary lesson and a valid section")
        return "lab:" + slug(stem), slug(after)

    rows = [
        line
        for line in (ROOT / "reference/visual-plan.md").read_text().splitlines()
        if line.startswith("|") and not line.startswith(("| ---", "| Title |"))
    ]
    titles = set()
    for index, line in enumerate(rows, 1):
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if (
            len(cells) != 9
            or not all(cells)
            or not cells[5].isdigit()
            or cells[0] in titles
            or cells[7]
            not in {
                "flow",
                "comparison",
                "hierarchy",
                "matrix",
                "roofline",
                "topology",
                "timeline",
                "decision",
                "overlap",
                "pipeline",
                "cycle",
            }
        ):
            fail("invalid or duplicate overview diagram placement")
        titles.add(cells[0])
        expected[f"diagram-{index}-{slug(cells[0])}"] = location(
            cells[8], int(cells[5]), cells[6]
        )
    detailed = json.loads((ROOT / "reference/visual-manifest.json").read_text())[
        "diagrams"
    ]
    for entry in detailed:
        if (
            set(entry) != {"path", "title", "lessons", "after", "home"}
            or not isinstance(entry["path"], str)
            or not isinstance(entry["title"], str)
            or not entry["title"].strip()
            or not isinstance(entry["lessons"], list)
            or not entry["lessons"]
            or any(
                type(n) is not int or not 1 <= n <= len(lessons)
                for n in entry["lessons"]
            )
            or len(set(entry["lessons"])) != len(entry["lessons"])
        ):
            fail("invalid detailed diagram placement")
        source = ROOT / entry["path"]
        if (
            not source.resolve().is_relative_to(ROOT.resolve())
            or source.suffix != ".svg"
        ):
            fail("detailed diagram path is outside the course")
        if source.read_text().strip() not in document:
            fail("a detailed diagram is absent from HTML")
        target = "detail-" + slug(source.stem)
        if target in expected:
            fail("duplicate detailed diagram placement")
        expected[target] = location(entry["home"], entry["lessons"][0], entry["after"])
    if parser.figures != expected:
        fail(
            "figures must appear once after their declared section in the owning lesson or lab"
        )


def validate_course_navigation(parser: Parser, course_name: str) -> None:
    if course_name not in COURSE_NAMES:
        fail("course metadata needs a known catalog slug")
    expected = {"../index.html", "../lab-guide.html"} | {
        f"../{name}/index.html" for name in COURSE_NAMES if name != course_name
    }
    expected_order = [("link", "../index.html")] + [
        ("current", name) if name == course_name else
        ("link", "../lab-guide.html" if name == "lab-guide" else f"../{name}/index.html")
        for name in CATALOG_ENTRIES
    ]
    if (
        len(parser.course_navigation_links) != len(expected)
        or set(parser.course_navigation_links) != expected
        or parser.current_courses != [course_name]
        or parser.course_navigation_order != expected_order
    ):
        fail("course navigation needs the catalog and eight ordered resources with one current course")


def validate_labs_only() -> None:
    metadata = json.loads((ROOT / "reference/course.json").read_text())
    if (
        set(metadata)
        != {
            "slug",
            "title",
            "profile",
            "estimated_guided_hours",
            "lessons",
            "labs",
            "extensions",
            "observability",
        }
        or metadata["slug"] != "advanced-gpu-communication"
        or metadata["profile"] != "labs-only"
        or metadata["lessons"] != []
    ):
        fail("invalid labs-only publication contract")
    missing = sorted(
        p
        for p in REQUIRED_FILES - {"reference/performance-tools.md"}
        if not (ROOT / p).is_file()
    )
    if missing:
        fail(f"missing required lab-course artifacts: {missing}")
    document = (ROOT / "index.html").read_text()
    validate_course_glossary(document, (ROOT / "GLOSSARY.md").read_text())
    parser = Parser()
    parser.feed(document)
    validate_result_links(parser)
    if parser.errors or set(parser.fragments) - parser.ids or parser.lessons:
        fail(
            "invalid labs-only HTML structure: "
            + str(parser.errors or set(parser.fragments) - parser.ids)
        )
    validate_course_navigation(parser, metadata["slug"])
    sources = sorted((ROOT / "labs").glob("[0-9][0-9]_*.py"))
    inventory = [x["path"] for x in metadata["labs"]]
    if (
        len(inventory) != 34
        or len(set(inventory)) != 34
        or set(inventory) != {str(p.relative_to(ROOT)) for p in sources}
        or any(x["lessons"] != [] for x in metadata["labs"])
    ):
        fail("lab inventory or lesson-free contract differs")
    expected = {str(p.relative_to(ROOT)): p.read_text() for p in sources}
    if parser.embedded != expected:
        fail("embedded executable source differs")
    validate_lab_guides(document, metadata, [], sources)
    validate_shared_setup_link(document, metadata)
    validate_observability_assets(document, metadata)
    if any(not all(item) for item in parser.svg_results):
        fail("lab diagrams require accessible titles and descriptions")
    for path in sorted((ROOT / "labs").glob("*.py")):
        compile(path.read_bytes(), str(path), "exec")
    for path in sources:
        result = subprocess.run(
            [sys.executable, str(path), "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=20,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            check=False,
        )
        if result.returncode or "usage:" not in result.stdout.lower():
            fail(f"dependency-free help failed: {path.name}: {result.stderr}")
    for path in (ROOT / "slurm").glob("*.sbatch"):
        subprocess.run(["bash", "-n", str(path)], check=True)
    validate_publication_artifacts(course_paths())
    validate_publication_text(course_paths())
    print(
        f"PASS: {ROOT.name}: 34 complete labs, shared setup link and no conceptual lessons"
    )


def main() -> None:
    if (
        json.loads((ROOT / "reference/course.json").read_text()).get("profile")
        == "labs-only"
    ):
        validate_labs_only()
        return
    missing = sorted(path for path in REQUIRED_FILES if not (ROOT / path).is_file())
    if missing:
        fail(f"missing required artifacts: {missing}")
    document = (ROOT / "index.html").read_text(encoding="utf-8")
    if '<html lang="en">' not in document or 'name="viewport"' not in document:
        fail("HTML language or responsive viewport is missing")
    validate_course_glossary(document, (ROOT / "GLOSSARY.md").read_text())
    parser = Parser()
    parser.feed(document)
    validate_result_links(parser)
    if parser.errors:
        fail("; ".join(sorted(set(parser.errors))))
    missing_fragments = sorted(set(parser.fragments) - parser.ids)
    if missing_fragments:
        fail(f"broken HTML fragments: {missing_fragments}")
    if len(parser.lessons) < 8 or any(
        features != LESSON_CLASSES for features in parser.lessons
    ):
        fail("every course needs at least eight complete long-form lesson templates")
    canonical_lessons = source_lessons()
    if len(canonical_lessons) != len(parser.lessons):
        fail("canonical lesson count does not match generated HTML")
    metadata = json.loads((ROOT / "reference/course.json").read_text())
    advanced = metadata.get("advanced_lessons", [])
    if (
        not isinstance(advanced, list)
        or len(set(advanced)) != len(advanced)
        or any(
            type(n) is not int or not 1 <= n <= len(canonical_lessons) for n in advanced
        )
    ):
        fail("invalid advanced lesson route")
    displayed = [
        lesson for n, lesson in enumerate(canonical_lessons, 1) if n not in advanced
    ] + [canonical_lessons[n - 1] for n in advanced]
    validate_rendered_openings(parser, displayed)
    validate_next_steps(document)
    entry = canonical_lessons[0][1].get("How it works", "")
    if len(words(entry)) < 350:
        fail("first lesson requires a substantive beginner How it works explanation")
    validate_lesson_structure(parser)
    for lesson_index, (title, fields, lesson_words) in enumerate(canonical_lessons):
        validate_concept_opening(title, fields)
        if parser.lesson_first_fields[lesson_index] != "lesson-outcome":
            fail(f"{title} rendered Objective must precede its explanation")
        missing_fields = sorted(set(REQUIRED_LESSON_FIELDS) - fields.keys())
        if missing_fields:
            fail(f"{title} is missing teaching fields: {missing_fields}")
        unknown = set(fields) - {*REQUIRED_LESSON_FIELDS, "References"}
        if unknown:
            fail(f"{title} has obsolete or unknown fields: {sorted(unknown)}")
        if lesson_words < 300:
            fail(
                f"{title} is too compressed: {lesson_words} words; expected at least 300"
            )
        for field, minimum in FIELD_MINIMUM_WORDS.items():
            count = len(words(fields[field]))
            if count < minimum:
                fail(f"{title} field {field} has {count} words; expected {minimum}")
    if len(parser.svg_results) < 6 or any(
        not all(result) for result in parser.svg_results
    ):
        fail("at least six accessible diagrams with title and description are required")
    for text in parser.labs:
        for cue in LAB_SECTIONS:
            if cue not in text:
                fail(f"lab guide missing {cue}")
    sources = sorted(
        path
        for path in (ROOT / "labs").iterdir()
        if path.is_file()
        and re.match(r"^\d+_", path.name)
        and path.suffix in {".py", ".cu", ".cpp"}
    )
    expected = {
        path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in sources
    }
    if parser.embedded != expected:
        fail("embedded lab source does not exactly match canonical source")
    metadata = json.loads((ROOT / "reference/course.json").read_text(encoding="utf-8"))
    if set(metadata) != {
        "slug",
        "title",
        "estimated_guided_hours",
        "labs",
        "extensions",
        "performance_tools",
        "observability",
        "advanced_lessons",
        "external_labs",
    }:
        fail("course metadata must use the current publication schema")
    validate_course_navigation(parser, metadata["slug"])
    plan_paths = [item["path"] for item in metadata["labs"]]
    if len(plan_paths) != len(set(plan_paths)) or set(plan_paths) != set(expected):
        fail("course metadata must classify every numbered lab exactly once")
    if any(
        set(item) != {"path", "optional", "lessons", "dashboard"}
        or type(item["optional"]) is not bool
        for item in metadata["labs"]
    ):
        fail(
            "each lab needs a path, optional/core classification and lesson membership"
        )
    validate_lab_guides(document, metadata, canonical_lessons, sources)
    validate_shared_setup_link(document, metadata)
    validate_observability_assets(document, metadata)
    hours = metadata["estimated_guided_hours"]
    if type(hours) is not int or hours <= 0:
        fail("estimated guided hours must be a positive whole number")
    expected_header = f'<header><h1>{html.escape(metadata["title"])}</h1><p class="guided-hours">Estimated guided hours: {hours}</p></header>'
    if expected_header not in document:
        fail(
            "banner must contain only the course title and short guided-hours estimate"
        )
    for phrase in (
        'id="learning-plan"',
        'id="visual-models"',
        "core lab practice =",
        "Lab time allocations",
        "Previous material map",
        "Topic coverage",
        "data-retained-source",
    ):
        if phrase in document:
            fail("page contains an obsolete publication surface")
    validate_figures(document, parser, canonical_lessons, metadata)
    for field in REQUIRED_LESSON_FIELDS:
        if f"<strong>{field}.</strong>" in document:
            fail("teaching labels must not end with a period")
    for path in sources:
        if path.suffix == ".py":
            compile(path.read_bytes(), str(path), "exec")
            result = subprocess.run(
                [sys.executable, str(path), "--help"],
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=20,
                check=False,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
            if result.returncode != 0 or "usage:" not in result.stdout.lower():
                fail(
                    f"dependency-free help failed for {path.name}: {result.stderr.strip()}"
                )
    for launcher in sorted((ROOT / "slurm").glob("*.sbatch")):
        result = subprocess.run(
            ["bash", "-n", str(launcher)], text=True, capture_output=True, check=False
        )
        if result.returncode:
            fail(f"Bash syntax failed for {launcher.name}: {result.stderr.strip()}")
    publishable = course_paths()
    validate_publication_artifacts(publishable)
    forbidden_names = [
        path
        for path in publishable
        if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}
    ]
    if forbidden_names:
        fail(f"generated cache artifacts are present: {forbidden_names}")
    validate_publication_text(publishable)
    print(
        f"PASS: {ROOT.name}: {len(parser.lessons)} lessons, {len(sources)} labs, {len(parser.svg_results)} diagrams"
    )


if __name__ == "__main__":
    main()
