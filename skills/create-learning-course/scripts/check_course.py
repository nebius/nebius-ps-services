#!/usr/bin/env python3
"""Read-only structural checks; not a renderer or a publication approval."""

from __future__ import annotations

import argparse
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

ALLOWED_TAGS = set(
    "html head meta title style body a header h1 h2 h3 h4 h5 h6 p div aside "
    "nav details summary ul ol li main section article strong em code pre "
    "figure figcaption footer table thead tbody tr th td caption br hr span "
    "blockquote dl dt dd svg title desc defs marker path rect circle ellipse "
    "line polyline polygon g text tspan".split()
)
VOID_TAGS = {"meta", "br", "hr"}
GUIDE_HEADINGS = (
    "Before you start",
    "Concepts and code path",
    "Run the experiment",
    "Check your results",
    "Investigate the behavior",
    "If something goes wrong",
    "Takeaways and next step",
)
LESSON_FIELDS = {
    "lesson-outcome": "Objective",
    "how-it-works": "How it works",
    "practice-links": "Practice",
    "mental-model": "Mental model",
}
OPTIONAL_LESSON_FIELDS = {"lesson-references": "References"}
LESSON_LABELS = LESSON_FIELDS | OPTIONAL_LESSON_FIELDS


def safe_file(root: Path, relative: str) -> Path:
    """Accept only an existing regular file under a non-linked relative path."""
    path = Path(relative)
    if not relative or path.is_absolute() or ".." in path.parts:
        raise ValueError("Expected a course-relative file path")
    candidate = root
    for part in path.parts:
        candidate /= part
        if candidate.is_symlink():
            raise ValueError("Symlinked course input is not allowed")
    if not candidate.is_file() or not candidate.resolve().is_relative_to(root):
        raise ValueError("Missing or out-of-course file")
    return candidate


class CoursePage(HTMLParser):
    def __init__(self, local_links: set[str] | None = None) -> None:
        super().__init__(convert_charrefs=True)
        self.local_links = local_links or set()
        self.errors: list[str] = []
        self.ids: set[str] = set()
        self.fragments: list[str] = []
        self.references: list[str] = []
        self.stack: list[str] = []
        self.tags: dict[str, int] = {}
        self.listings: dict[str, str] = {}
        self.source: str | None = None
        self.style: list[str] = []
        self.svg: list[dict[str, object]] = []
        self.current_svg: dict[str, object] | None = None
        self.svg_label: str | None = None
        self.header_text: list[str] = []
        self.figure: dict[str, object] | None = None
        self.has_viewport = False
        self.has_skip = False
        self.lesson_count = 0
        self.lesson_depth: int | None = None
        self.lesson_fields: list[str] = []
        self.lesson_titles = 0
        self.lesson_diagrams = 0
        self.field: str | None = None
        self.field_depth: int | None = None
        self.field_headings: list[str] = []
        self.field_has_text = False
        self.field_children = 0
        self.heading_depth: int | None = None
        self.heading_text: list[str] = []
        self.glossary_section_depth: int | None = None
        self.glossary_lists = 0
        self.glossary_depth: int | None = None
        self.glossary_entry_depth: int | None = None
        self.glossary_entries: list[tuple[str, str]] = []
        self.glossary_text: list[str] = []
        self.presentation_headings: list[dict] = []
        self.presentation_lists: list[dict] = []
        self.course_section: dict | None = None
        self.course_sections: list[tuple[str, str]] = []
        self.nav_fragments: set[str] = set()

    def start_presentation_list(self, expected: str, depth: int) -> None:
        self.presentation_lists.append(
            {
                "depth": depth,
                "tag": expected,
                "items": 0,
                "list_depth": None,
                "item_depth": None,
                "item_text": [],
            }
        )

    def start_presentation_element(self, tag: str, attributes: dict[str, str]) -> None:
        classes = attributes.get("class", "").split()
        identity = attributes.get("id", "")
        is_teaching = bool({"lesson", "lab"}.intersection(classes))
        if is_teaching:
            if self.course_sections:
                self.errors.append(
                    "Lessons and practical guides must precede course appendices"
                )
            if self.course_section:
                self.course_section["teaching"] = True
                if self.course_section.get("appendix"):
                    self.errors.append(
                        "Course appendices cannot contain lessons or practical guides"
                    )
        if tag == "section" and self.stack[-1:] == ["main"]:
            self.course_section = {
                "depth": len(self.stack) + 1,
                "id": identity,
                "teaching": is_teaching,
            }
        if tag == "a" and "nav" in self.stack:
            target = attributes.get("href", "")
            if target.startswith("#"):
                self.nav_fragments.add(unquote(target[1:]))
        if "lesson-references" in classes:
            self.start_presentation_list("ol", len(self.stack) + 1)
        for scope in self.presentation_lists:
            if tag in {"ul", "ol"} and scope["list_depth"] is None:
                scope["list_depth"] = len(self.stack) + 1
                if tag != scope["tag"]:
                    self.errors.append(
                        "Next steps need bullets; references need numbers"
                    )
            if tag == "li" and len(self.stack) == scope["list_depth"]:
                scope["item_depth"] = len(self.stack) + 1
                scope["item_text"] = []
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"} or (
            tag == "a" and "nav" in self.stack
        ):
            self.presentation_headings.append(
                {
                    "depth": len(self.stack) + 1,
                    "tag": tag,
                    "text": [],
                    "course_section": self.course_section
                    if self.course_section
                    and len(self.stack) == self.course_section["depth"]
                    and not self.course_section["teaching"]
                    else None,
                }
            )

    def end_presentation_element(self) -> None:
        for scope in self.presentation_lists:
            if scope["item_depth"] == len(self.stack):
                if "".join(scope["item_text"]).strip():
                    scope["items"] += 1
                else:
                    self.errors.append(
                        "Next-step and reference list items must not be empty"
                    )
                scope["item_depth"] = None
            if scope["list_depth"] == len(self.stack):
                scope["list_depth"] = None
        if self.presentation_headings and self.presentation_headings[-1][
            "depth"
        ] == len(self.stack):
            heading = self.presentation_headings.pop()
            label = " ".join("".join(heading["text"]).split()).casefold()
            if label in {"course mission", "syllabus"}:
                self.errors.append(
                    "Keep mission and syllabus out of course headings and navigation"
                )
            section = heading["course_section"]
            if (
                heading["tag"] == "h2"
                and section
                and label in {"glossary", "where to go next", "official references"}
            ):
                self.course_sections.append((label, section["id"]))
                section["appendix"] = True
                if label == "glossary":
                    self.glossary_section_depth = section["depth"]
                    self.glossary_lists = 0
                    self.glossary_entries = []
                else:
                    self.start_presentation_list(
                        "ul" if label == "where to go next" else "ol", section["depth"]
                    )
            if (
                label in {"glossary", "where to go next"}
                and heading["tag"] != "a"
                and not (heading["tag"] == "h2" and section)
            ):
                self.errors.append(
                    "Glossary and Where to Go Next must each be one independent course section"
                )
        if self.course_section and self.course_section["depth"] == len(self.stack):
            self.course_section = None
        if self.presentation_lists and self.presentation_lists[-1]["depth"] == len(
            self.stack
        ):
            scope = self.presentation_lists.pop()
            if not scope["items"]:
                self.errors.append(
                    "Next steps and references need nonempty semantic lists"
                )

    def start_lesson_element(self, tag: str, attributes: dict[str, str]) -> None:
        """Track ownership by depth, so nested containers cannot end a field."""
        classes = attributes.get("class", "").split()
        if "lesson" in classes:
            if self.lesson_depth is not None:
                self.errors.append("Nested lessons are not supported")
            else:
                self.lesson_count += 1
                self.lesson_depth = len(self.stack) + 1
                self.lesson_fields = []
                self.lesson_titles = self.lesson_diagrams = 0
                if tag != "section" or not attributes.get("id"):
                    self.errors.append("Lesson needs a section and unique ID")
            return
        if self.lesson_depth is None:
            return
        if len(self.stack) == self.lesson_depth:
            fields = [name for name in classes if name in LESSON_LABELS]
            if tag == "h2" and not self.lesson_fields:
                self.lesson_titles += 1
            elif tag == "div" and len(fields) == 1:
                self.field = fields[0]
                self.lesson_fields.append(self.field)
                self.field_depth = len(self.stack) + 1
                self.field_headings = []
                self.field_has_text = False
                self.field_children = 0
            else:
                self.errors.append("Lesson content must use canonical sections")
        elif self.field_depth is not None and len(self.stack) == self.field_depth:
            self.field_children += 1
            if self.field_children == 1 and tag != "h3":
                self.errors.append("Lesson section must start with its visible heading")
        if tag == "h3":
            if self.field_depth is None or len(self.stack) != self.field_depth:
                self.errors.append("Lesson section heading must be a direct h3")
            else:
                self.heading_depth = len(self.stack) + 1
                self.heading_text = []
        if tag == "figure" and self.field != "how-it-works":
            self.errors.append("Lesson figures must be inside How it works")

    def start_glossary_element(self, tag: str) -> None:
        if tag == "dl":
            self.glossary_lists += 1
            if len(self.stack) != self.glossary_section_depth:
                self.errors.append("Glossary needs one direct definition list")
            else:
                self.glossary_depth = len(self.stack) + 1
        elif tag in {"dt", "dd"}:
            if len(self.stack) != self.glossary_depth:
                self.errors.append("Glossary entries must be direct dt/dd pairs")
            else:
                self.glossary_entry_depth = len(self.stack) + 1
                self.glossary_text = []
        elif len(self.stack) == self.glossary_depth:
            self.errors.append("Glossary entries must be direct dt/dd pairs")
        elif len(self.stack) == self.glossary_section_depth:
            self.errors.append("Glossary needs one direct definition list")

    def check_glossary(self) -> None:
        if self.glossary_lists != 1:
            self.errors.append("Glossary needs one direct definition list")
        entries = self.glossary_entries
        if (
            not entries
            or len(entries) % 2
            or any(
                tag != ("dt" if index % 2 == 0 else "dd") or not value
                for index, (tag, value) in enumerate(entries)
            )
        ):
            self.errors.append("Glossary needs nonempty term/definition pairs")
        keys = [value.casefold() for tag, value in entries if tag == "dt"]
        if keys != sorted(set(keys)):
            self.errors.append("Glossary keys must be unique and sorted A-Z")

    def end_glossary_element(self) -> None:
        if self.glossary_entry_depth == len(self.stack):
            self.glossary_entries.append(
                (self.stack[-1], " ".join("".join(self.glossary_text).split()))
            )
            self.glossary_entry_depth = None
        if self.glossary_depth == len(self.stack):
            self.glossary_depth = None
        if self.glossary_section_depth == len(self.stack):
            self.check_glossary()
            self.glossary_section_depth = None

    def end_lesson_element(self) -> None:
        if self.heading_depth == len(self.stack):
            self.field_headings.append(" ".join("".join(self.heading_text).split()))
            self.heading_depth = None
        if self.field_depth == len(self.stack):
            if self.field_headings != [LESSON_LABELS[self.field]]:
                self.errors.append("Lesson section needs its matching visible heading")
            if not self.field_has_text:
                self.errors.append(
                    "Lesson section needs content outside its heading and figures"
                )
            self.field = self.field_depth = None
        if self.lesson_depth == len(self.stack):
            required = list(LESSON_FIELDS)
            if self.lesson_fields not in (required, required + ["lesson-references"]):
                self.errors.append(
                    "Lesson needs canonical sections in order, with References last if present"
                )
            if self.lesson_titles != 1:
                self.errors.append("Lesson needs exactly one opening h2 title")
            if not self.lesson_diagrams:
                self.errors.append("Each How it works needs its own inline SVG figure")
            self.lesson_depth = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # HTMLParser uses None for attributes without a value. Normalize at
        # the input boundary; required-value checks still reject empty values.
        attributes = {key: value or "" for key, value in attrs}
        self.start_presentation_element(tag, attributes)
        self.start_lesson_element(tag, attributes)
        if {"lesson-glossary", "tools-glossary", "lesson-next-steps"} & set(
            attributes.get("class", "").split()
        ):
            self.errors.append(
                "Local glossary and next-step containers are not allowed"
            )
        if self.glossary_section_depth is not None:
            self.start_glossary_element(tag)
        self.reject_source_markup()
        self.tags[tag] = self.tags.get(tag, 0) + 1
        if len(attributes) != len(attrs):
            self.errors.append("Duplicate HTML attribute")
        if tag not in ALLOWED_TAGS:
            self.errors.append(f"Unsupported or active element: {tag}")
        for key, value in attrs:
            value = value or ""
            if key.startswith("on") or key in {
                "src",
                "srcset",
                "srcdoc",
                "action",
                "ping",
                "attributionsrc",
                "background",
                "poster",
                "manifest",
                "profile",
                "codebase",
                "archive",
            }:
                self.errors.append("Active or external-loading attribute")
            if key in {"href", "xlink:href"}:
                if value.startswith("#") and len(value) > 1:
                    self.fragments.append(unquote(value[1:]))
                else:
                    try:
                        reference = urlsplit(value)
                    except ValueError:
                        self.errors.append("Invalid reference URL")
                    else:
                        if tag == "a" and key == "href" and value in self.local_links:
                            pass
                        elif tag == "a" and reference.scheme == "https":
                            if not reference.hostname or reference.username:
                                self.errors.append("Invalid reference URL")
                        else:
                            self.errors.append("Non-fragment or non-HTTPS link")
            if key in {"aria-labelledby", "aria-describedby"}:
                self.references.extend(value.split())
            if key == "style" or "url(" in value.lower() or "\\" in value:
                self.check_css(value)
        identity = attributes.get("id")
        if identity:
            if identity in self.ids:
                self.errors.append("Duplicate element ID")
            self.ids.add(identity)
        if tag == "meta":
            if attributes.get("http-equiv"):
                self.errors.append("HTTP-equivalent metadata is not allowed")
            if attributes.get("name") == "viewport":
                self.has_viewport = "width=device-width" in attributes.get(
                    "content", ""
                )
        if tag == "html" and not attributes.get("lang"):
            self.errors.append("Missing document language")
        if tag == "nav" and not attributes.get("aria-label"):
            self.errors.append("Navigation lacks an accessible label")
        if tag == "a" and "skip-link" in attributes.get("class", "").split():
            self.has_skip = attributes.get("href") == "#main"
        if tag == "pre" and attributes.get("tabindex") != "0":
            self.errors.append("Code scroller is not keyboard-focusable")
        if "header" in self.stack and tag not in {"h1", "p"}:
            self.errors.append("Banner must contain only title and guided hours")
        if tag == "p" and "header" in self.stack:
            if attributes.get("class") != "guided-hours":
                self.errors.append("Unexpected banner paragraph")
        if tag == "code" and "data-source" in attributes:
            if not self.stack or self.stack[-1] != "pre":
                self.errors.append("Source listing must be inside a code scroller")
            self.source = attributes["data-source"]
            if not self.source or self.source in self.listings:
                self.errors.append("Missing or duplicate source identity")
            self.listings[self.source or ""] = ""
        if tag == "figure":
            if self.figure is not None:
                self.errors.append("Nested figures are not supported")
            self.figure = {"captions": 0, "text": "", "depth": len(self.stack) + 1}
        if tag == "figcaption":
            if self.figure is None:
                self.errors.append("Caption must be inside its figure")
            else:
                self.figure["captions"] += 1
        if tag == "svg":
            if (
                self.field == "how-it-works"
                and self.figure is not None
                and self.figure["depth"] > self.field_depth
            ):
                self.lesson_diagrams += 1
            if self.current_svg is not None:
                self.errors.append("Nested SVG is not supported")
            self.current_svg = {
                "title": "",
                "desc": "",
                "title_id": None,
                "desc_id": None,
                "refs": attributes.get("aria-labelledby", "").split(),
            }
            if not attributes.get("viewbox") or attributes.get("role") != "img":
                self.errors.append("SVG needs a viewBox and image role")
            if "figure" not in self.stack:
                self.errors.append("SVG must be inside a contextual figure")
        if self.current_svg is not None and tag in {"title", "desc"}:
            self.svg_label = tag
            self.current_svg[tag + "_id"] = identity
        if tag not in VOID_TAGS:
            self.stack.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def reject_source_markup(self) -> None:
        if self.source is not None:
            self.errors.append("Source listing must contain escaped literal text")

    # HTMLParser routes these constructs separately from elements and text.
    # Ignoring them would silently accept nonliteral markup in source listings.
    def handle_comment(self, data: str) -> None:
        self.reject_source_markup()

    def handle_decl(self, decl: str) -> None:
        self.reject_source_markup()

    def handle_pi(self, data: str) -> None:
        self.reject_source_markup()

    def unknown_decl(self, data: str) -> None:
        self.reject_source_markup()

    def handle_endtag(self, tag: str) -> None:
        if not self.stack or self.stack[-1] != tag:
            self.errors.append("Unbalanced HTML structure")
            return
        self.end_presentation_element()
        self.end_glossary_element()
        self.end_lesson_element()
        if tag == "code":
            self.source = None
        if tag in {"title", "desc"}:
            self.svg_label = None
        if tag == "svg" and self.current_svg is not None:
            self.svg.append(self.current_svg)
            self.current_svg = None
        if tag == "figure" and self.figure is not None:
            if self.figure["captions"] != 1 or not self.figure["text"].strip():
                self.errors.append("Figure needs exactly one nonempty local caption")
            self.figure = None
        self.stack.pop()

    def handle_data(self, data: str) -> None:
        for heading in self.presentation_headings:
            heading["text"].append(data)
        for scope in self.presentation_lists:
            if scope["item_depth"] is not None:
                scope["item_text"].append(data)
        if self.glossary_entry_depth is not None:
            self.glossary_text.append(data)
        elif self.glossary_section_depth is not None and data.strip():
            self.errors.append("Glossary text must be inside a term/definition pair")
        if self.heading_depth is not None:
            self.heading_text.append(data)
        elif self.field is not None and data.strip():
            if not any(
                tag in self.stack for tag in ("figure", "svg", "h3", "h4", "h5", "h6")
            ):
                self.field_has_text = True
                if not self.field_headings:
                    self.errors.append(
                        "Lesson section must start with its visible heading"
                    )
        elif self.lesson_depth == len(self.stack) and data.strip():
            self.errors.append("Lesson text must be inside a canonical section")
        if self.source is not None:
            self.listings[self.source] += data
        if "style" in self.stack:
            self.style.append(data)
        if "header" in self.stack:
            self.header_text.append(data)
            if self.stack[-1] == "header" and data.strip():
                self.errors.append("Unexpected banner text")
        if self.figure is not None and "figcaption" in self.stack:
            self.figure["text"] += data
        if self.current_svg is not None and self.svg_label:
            self.current_svg[self.svg_label] += data

    def check_css(self, css: str) -> None:
        # The format has no imported assets. Local SVG marker references are OK.
        if re.search(
            r"@import|@font-face|@namespace|expression\s*\(|-moz-binding|\\|/\*"
            r"|(?:image-set|image|src)\s*\(",
            css,
            re.I,
        ):
            self.errors.append("Unsupported CSS directive or escape")
        for value in re.findall(r"url\s*\((.*?)\)", css, re.I | re.S):
            if not re.fullmatch(r"#[A-Za-z][A-Za-z0-9_.:-]*", value.strip()):
                self.errors.append("External CSS resource")
            else:
                self.fragments.append(value.strip()[1:])

    def finish(self) -> list[str]:
        labels = [label for label, _ in self.course_sections]
        targets = {target for _, target in self.course_sections}
        if (
            labels != ["where to go next", "glossary", "official references"]
            or len(targets) != 3
        ):
            self.errors.append(
                "Course needs independent next steps, Glossary and Official references sections"
            )
        if any(
            not target or target not in self.nav_fragments
            for _, target in self.course_sections
        ):
            self.errors.append(
                "Course navigation must link to each actual supporting section"
            )
        if not self.lesson_count:
            self.errors.append("Expected at least one lesson")
        if self.stack:
            self.errors.append("Unclosed HTML element")
        for tag in ("html", "head", "body", "header", "h1", "nav", "main", "style"):
            if self.tags.get(tag) != 1:
                self.errors.append(f"Expected exactly one {tag}")
        if not self.has_viewport or not self.has_skip or "main" not in self.ids:
            self.errors.append("Missing responsive viewport or skip destination")
        if not set(self.fragments + self.references).issubset(self.ids):
            self.errors.append("Broken fragment or accessible-name reference")
        if self.tags.get("figure", 0) != self.tags.get("figcaption", 0):
            self.errors.append("Every figure needs a visible caption")
        for svg in self.svg:
            if not str(svg["title"]).strip() or not str(svg["desc"]).strip():
                self.errors.append("SVG needs a nonempty title and description")
            label_ids = {svg["title_id"], svg["desc_id"]}
            if (
                None in label_ids
                or len(label_ids) != 2
                or not set(svg["refs"]).issuperset(label_ids)
            ):
                self.errors.append("SVG must reference its title and description")
        self.check_css("".join(self.style))
        if not set(self.fragments).issubset(self.ids):
            self.errors.append("Broken CSS/SVG reference")
        if "estimated guided hours" not in "".join(self.header_text).lower():
            self.errors.append("Missing concise guided-hours estimate")
        return self.errors


def approved_links(
    page: Path, root: Path, manifest: Path | None, publication_root: Path | None
) -> set[str]:
    publication_root = (publication_root or root).absolute()
    if (
        not publication_root.is_dir()
        or any(p.is_symlink() for p in (publication_root, *publication_root.parents))
        or not root.is_relative_to(publication_root.resolve())
    ):
        raise ValueError("Invalid publication root")
    if manifest is None:
        return set()
    manifest = safe_file(root, str(manifest.absolute().relative_to(root)))
    values = json.loads(manifest.read_text(encoding="utf-8"))
    if (
        not isinstance(values, list)
        or any(not isinstance(v, str) for v in values)
        or len(values) != len(set(values))
    ):
        raise ValueError("Links manifest must contain unique local href strings")
    for value in values:
        link = urlsplit(value)
        path = unquote(link.path)
        if (
            not path
            or link.scheme
            or link.netloc
            or link.query
            or path.startswith("/")
            or "\\" in path
            or ":" in path
            or any(ord(c) < 32 for c in value + path)
        ):
            raise ValueError("Links manifest accepts only ordinary relative file links")
        target = page.parent / path
        if (
            any(p.is_symlink() for p in (target, *target.parents))
            or not target.resolve().is_relative_to(publication_root.resolve())
            or not target.is_file()
        ):
            raise ValueError("Missing, linked or out-of-publication companion")
        if link.fragment:
            if target.suffix.lower() not in {".html", ".htm"}:
                raise ValueError("Fragment destination must be HTML")

            class Targets(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.ids = set()

                def handle_starttag(self, tag, attrs):
                    self.ids.add(dict(attrs).get("id"))

            targets = Targets()
            targets.feed(target.read_text(encoding="utf-8"))
            if unquote(link.fragment) not in targets.ids:
                raise ValueError("Missing companion HTML fragment")
    return set(values)


def check(
    page: Path,
    root: Path,
    manifest: Path,
    links_manifest: Path | None = None,
    publication_root: Path | None = None,
) -> list[str]:
    if any(p.is_symlink() for p in (root, *root.parents)) or not root.is_dir():
        raise ValueError("Course root must be a real directory")
    root = root.resolve()
    page = safe_file(root, str(page.absolute().relative_to(root)))
    manifest = safe_file(root, str(manifest.absolute().relative_to(root)))
    expected = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(expected, list) or any(not isinstance(p, str) for p in expected):
        raise ValueError("Sources manifest must be an array of relative paths")
    if len(expected) != len(set(expected)):
        raise ValueError("Duplicate source allowlist entry")
    contents = {p: safe_file(root, p).read_bytes().decode("utf-8") for p in expected}
    parser = CoursePage(approved_links(page, root, links_manifest, publication_root))
    parser.feed(page.read_bytes().decode("utf-8"))
    parser.close()
    errors = parser.finish()
    if set(parser.listings) != set(contents):
        errors.append("Embedded listings do not match source allowlist")
    for path, source in contents.items():
        if parser.listings.get(path) != source:
            errors.append("Embedded source bytes differ from canonical source")
    return sorted(set(errors))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("page", type=Path, help="Rendered self-contained HTML file")
    parser.add_argument("--course-root", type=Path, required=True)
    parser.add_argument("--sources-manifest", type=Path, required=True)
    parser.add_argument(
        "--links-manifest",
        type=Path,
        help="JSON array of explicitly allowed local hrefs",
    )
    parser.add_argument(
        "--publication-root",
        type=Path,
        help="Contained companion-file root; defaults to course root",
    )
    args = parser.parse_args()
    try:
        errors = check(
            args.page,
            args.course_root,
            args.sources_manifest,
            args.links_manifest,
            args.publication_root,
        )
    except (OSError, ValueError, UnicodeError):
        parser.exit(2, "Invalid or unreadable course input; inspect paths and JSON.\n")
    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1
    print(
        "STATIC_PASS: bounded HTML/source checks; other publication gates remain separate."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
