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
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
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
            fields = [name for name in classes if name in LESSON_FIELDS]
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
                self.errors.append(
                    "Lesson content must use the four canonical sections"
                )
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

    def end_lesson_element(self) -> None:
        if self.heading_depth == len(self.stack):
            self.field_headings.append(" ".join("".join(self.heading_text).split()))
            self.heading_depth = None
        if self.field_depth == len(self.stack):
            if self.field_headings != [LESSON_FIELDS[self.field]]:
                self.errors.append("Lesson section needs its matching visible heading")
            if not self.field_has_text:
                self.errors.append(
                    "Lesson section needs content outside its heading and figures"
                )
            self.field = self.field_depth = None
        if self.lesson_depth == len(self.stack):
            if self.lesson_fields != list(LESSON_FIELDS):
                self.errors.append("Lesson needs the four canonical sections in order")
            if self.lesson_titles != 1:
                self.errors.append("Lesson needs exactly one opening h2 title")
            if not self.lesson_diagrams:
                self.errors.append("Each How it works needs its own inline SVG figure")
            self.lesson_depth = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # HTMLParser uses None for attributes without a value. Normalize at
        # the input boundary; required-value checks still reject empty values.
        attributes = {key: value or "" for key, value in attrs}
        self.start_lesson_element(tag, attributes)
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
                        if tag == "a" and reference.scheme == "https":
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


def check(page: Path, root: Path, manifest: Path) -> list[str]:
    if root.is_symlink() or not root.is_dir():
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
    parser = CoursePage()
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
    args = parser.parse_args()
    try:
        errors = check(args.page, args.course_root, args.sources_manifest)
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
