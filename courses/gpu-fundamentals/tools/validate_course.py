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
    "gpu-fundamentals",
    "gpu-optimizations",
    "llm-training",
    "llm-inference",
    "custom-cuda-kernels",
)
COURSE_NAVIGATION_LINKS = {"../index.html"} | {
    f"../{name}/index.html" for name in COURSE_NAMES
}
ALLOWED_HOSTS = {
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
    "prerequisite-bridge",
    "recall",
    "why-it-matters",
    "mental-model",
    "mechanism",
    "practice-links",
}
REQUIRED_LESSON_FIELDS = (
    "Objective",
    "Prerequisite bridge",
    "Recall",
    "Why it matters",
    "Mental model",
    "Mechanism",
    "Practice labs",
)
FIELD_MINIMUM_WORDS = {
    "Prerequisite bridge": 20,
    "Why it matters": 20,
    "Mental model": 15,
    "Mechanism": 60,
}


def fail(message: str) -> None:
    raise SystemExit(f"FAIL: {message}")


def validate_concept_opening(title: str, fields: dict[str, str]) -> None:
    """Require a real opening before application; depth still needs editorial review."""
    openings = set(fields) & {"Start here", "What it is"}
    if len(openings) != 1 or next(iter(fields), None) not in openings:
        fail(f"{title} needs one definition-led opening before its other fields")
    field = next(iter(openings))
    minimum = 350 if field == "Start here" else 60
    if len(words(fields[field])) < minimum:
        fail(f"{title} opening is too short to introduce its concept")


class Parser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.errors: list[str] = []
        self.ids: set[str] = set()
        self.fragments: list[str] = []
        self.external: list[str] = []
        self.reference_depth = 0
        self.lesson_depth = 0
        self.lesson_number = 0
        self.last_field_class = ""
        self.figures: dict[str, tuple[str, str]] = {}
        self.lab_id = ""
        self.last_lab_section = ""
        self.lab_heading: list[str] | None = None
        self.lesson_features: set[str] = set()
        self.lessons: list[set[str]] = []
        self.lesson_first_fields: list[str] = []
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
        self.current_courses: list[str | None] = []

    def check_css(self, css: str) -> None:
        # The course CSS subset needs only local SVG fragment URLs. Reject
        # escapes and resource-loading constructs instead of partially parsing
        # arbitrary CSS and missing an obfuscated external dependency.
        css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
        css = re.sub(r"url\(\s*(['\"]?)#[\w.-]+\1\s*\)", "", css, flags=re.I)
        if "\\" in css or re.search(
            r"@import\b|\burl\s*\(|\bimage-set\s*\(", css, re.I
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
            tag == "link"
            and values.get("rel") == "icon"
            and (href == "data:," or (href or "").startswith("data:image/"))
        ):
            pass
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
                    parsed.path.startswith(("/NVIDIA/", "/triton-inference-server/"))
                    or parsed.path.rstrip("/")
                    in {"/deepseek-ai/DeepEP", "/flashinfer-ai/flashinfer"}
                )
            )
            if parsed.scheme != "https" or not official_host:
                self.errors.append(f"non-official reference: {href}")
        if tag == "section" and element_id == "official-references":
            self.reference_depth += 1
        if tag == "section" and "lesson" in classes:
            self.lesson_depth = 1
            self.lesson_number += 1
            self.last_field_class = ""
            self.lesson_features = set()
            self.lesson_first_fields.append("")
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
            placement_classes = classes & (
                LESSON_CLASSES | {"start-here", "concept-introduction"}
            )
            if placement_classes:
                self.last_field_class = next(iter(placement_classes))
                if not self.lesson_first_fields[-1]:
                    self.lesson_first_fields[-1] = self.last_field_class
        if tag == "div":
            if self.opening_depth:
                self.opening_depth += 1
            elif self.lesson_depth and classes & {"start-here", "concept-introduction"}:
                self.opening_depth = 1
                self.opening_parts = []
        if tag == "figure":
            if not (self.lesson_depth or self.lab_depth) or not element_id:
                self.errors.append(
                    "every figure needs a lesson or lab home and unique ID"
                )
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
        if self.opening_depth:
            self.opening_parts.append(data)
        if self.style_parts is not None:
            self.style_parts.append(data)
        if self.capture_source:
            self.capture.append(data)
        if self.lab_depth:
            self.lab_text.append(data)

    def handle_endtag(self, tag: str) -> None:
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


def validate_rendered_openings(parser: Parser, lessons: list) -> None:
    """Bind complete introductory prose to its lesson, not a page-wide occurrence."""
    if len(parser.lesson_openings) != len(lessons):
        fail("lesson opening narrative inventory differs from canonical source")
    for (title, fields, _), openings in zip(
        lessons, parser.lesson_openings, strict=True
    ):
        field = "Start here" if "Start here" in fields else "What it is"
        if field not in fields or len(openings) != 1:
            fail(f"{title} opening narrative is missing or duplicated")
        plain = re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", fields[field])
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
    topics = re.split(r"(?m)^## ", markdown)[1:]
    if not topics or any(
        "**Investigate**" not in topic
        or "**Scope**" not in topic
        or not re.search(r"\[[^]]+\]\(https://[^)]+\)", topic)
        for topic in topics
    ):
        fail("next-steps topics need study questions, scope and official links")
    sections = list(
        re.finditer(r'<section id="next-steps">.*?</section>', document, re.S)
    )
    if len(sections) != 1 or document.count('data-source="NEXT-STEPS.md"') != 1:
        fail("next-steps section or guide is missing or duplicated")
    section = sections[0]
    guides = re.search(r'<section id="supporting-guides">.*?</section>', document, re.S)
    references = document.find('<section id="official-references">')
    nav = re.search(r'<nav id="course-contents".*?</nav>', document, re.S)
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
    heading = re.search(r"<h2>(.*?)</h2>", section[0], re.S)
    if (
        not heading
        or heading[1] != title
        or f'<a href="#next-steps">{title}</a>' not in nav[0]
    ):
        fail("next-steps heading and sidebar label must match its source title")
    article = re.search(
        r'<article class="supporting-guide" id="guide-next-steps" data-source="NEXT-STEPS.md">'
        r"(.*?)</article>",
        section[0],
        re.S,
    )
    if not article:
        fail("next-steps guide is outside its closing section or has invalid metadata")
    plain = re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", markdown)
    plain = re.sub(r"(?m)^#{1,6} ", "", plain).replace("**", "").replace("`", "")
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
    lesson_html = re.findall(r'<section class="lesson".*?</section>', document, re.S)
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
        if not assigned or fields.get("Practice labs") != "\n".join(links):
            fail(f"{title} Practice labs must match its assigned guides")
        expected_links = (
            '<div class="practice-links"><strong>Practice labs</strong><ul>'
            + "".join(html_links)
            + "</ul></div>"
        )
        if (
            not lesson_html[number - 1]
            .rstrip()
            .endswith(expected_links + "\n</section>")
        ):
            fail(f"{title} must end with its exact Practice labs links")
    for item in metadata["labs"]:
        source = ROOT / item["path"]
        membership = item["lessons"]
        if (
            not isinstance(membership, list)
            or not membership
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
        pieces = re.split(r"^## (.+)\n", narrative, flags=re.M)
        if (
            tuple(pieces[1::2]) != LAB_SECTIONS
            or len(pieces[0].split()) < 40
            or any(len(body.split()) < 20 for body in pieces[2::2])
        ):
            fail(f"lab {source.name} has incomplete authored sections")
        anchor = "lab-" + re.sub(r"[^a-z0-9]+", "-", source.stem.lower()).strip("-")
        match = re.search(
            rf'<article class="lab" id="{anchor}".*?</article>', document, re.S
        )
        if not match or f"<h3>{html.escape(title[2:])}</h3>" not in match[0]:
            fail(f"lab {source.name} title or article is missing")
        article = match[0]
        if tuple(re.findall(r"<h4>(.*?)</h4>", article)) != LAB_SECTIONS:
            fail(f"lab {source.name} rendered sections differ from its guide")
        visible = VisibleText()
        visible.feed(article.split('<details class="lab-source"', 1)[0])
        rendered_text = " ".join("".join(visible.parts).split())
        in_code = False
        for paragraph in re.split(r"\n\s*\n", markdown):
            lines = paragraph.splitlines()
            if (
                not in_code
                and len(lines) >= 2
                and lines[0].strip().startswith("|")
                and re.fullmatch(r"\|[\s:|\-]+\|", lines[1].strip())
            ):
                # Match the renderer's table subset without dropping cell text
                # or treating literal pipes in prose and code as delimiters.
                paragraph = "\n".join(
                    " ".join(
                        cell.strip() for cell in line.strip().strip("|").split("|")
                    )
                    if line.strip().startswith("|")
                    else line
                    for line in [lines[0], *lines[2:]]
                )
            if len(re.findall(r"(?m)^\s*```", paragraph)) % 2:
                in_code = not in_code
            plain = re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", paragraph)
            plain = re.sub(r"(?m)^#{1,6} |^```[^\n]*\n?|^```$", "", plain)
            plain = plain.replace("**", "").replace("`", "")
            if " ".join(plain.split()) not in rendered_text:
                fail(f"lab {source.name} narrative differs from its HTML")
        for number in membership:
            lesson_title = re.sub(r"^\d+\.\s*", "", lessons[number - 1][0])
            lesson_anchor = re.sub(r"[^a-z0-9]+", "-", lesson_title.lower()).strip("-")
            if (
                f'href="#{anchor}">{html.escape(title[2:])}</a>'
                not in lesson_html[number - 1]
                or f'href="#{lesson_anchor}"' not in article
            ):
                fail(f"lab {source.name} has inconsistent lesson links")
        sections = dict(zip(pieces[1::2], pieces[2::2], strict=True))
        commands = re.findall(r"```bash\n(.*?)\n```", sections["Practice"], re.S)
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


def validate_publication_artifacts(paths: list[Path]) -> None:
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


def validate_publication_text(paths: list[Path]) -> None:
    combined = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
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
        field: slug(field)
        for field in REQUIRED_LESSON_FIELDS
        if field != "Practice labs"
    }
    fields.update(
        {
            "Start here": "start-here",
            "What it is": "concept-introduction",
            "Objective": "lesson-outcome",
        }
    )
    labs = {Path(item["path"]).stem: item["lessons"] for item in metadata["labs"]}
    expected = {}

    def location(home: str, lesson: int, after: str) -> tuple[str, str]:
        if type(lesson) is not int or not 1 <= lesson <= len(lessons):
            fail("diagram points to a missing lesson")
        if home == "lesson":
            if after not in fields or after not in lessons[lesson - 1][1]:
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
    expected = {"../index.html"} | {
        f"../{name}/index.html" for name in COURSE_NAMES if name != course_name
    }
    if (
        len(parser.course_navigation_links) != len(expected)
        or set(parser.course_navigation_links) != expected
        or parser.current_courses != [course_name]
    ):
        fail(
            "course navigation needs the catalog, four siblings and the current course"
        )


def main() -> None:
    missing = sorted(path for path in REQUIRED_FILES if not (ROOT / path).is_file())
    if missing:
        fail(f"missing required artifacts: {missing}")
    document = (ROOT / "index.html").read_text(encoding="utf-8")
    if '<html lang="en">' not in document or 'name="viewport"' not in document:
        fail("HTML language or responsive viewport is missing")
    parser = Parser()
    parser.feed(document)
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
    validate_rendered_openings(parser, canonical_lessons)
    validate_next_steps(document)
    entry = canonical_lessons[0][1].get("Start here", "")
    if len(words(entry)) < 350:
        fail("first lesson requires a substantive beginner Start here explanation")
    entry_position = document.find('<div class="start-here">')
    objective_position = document.find('<div class="lesson-outcome">')
    if entry_position < 0 or entry_position > objective_position:
        fail("beginner explanation must precede advanced objectives")
    for lesson_index, (title, fields, lesson_words) in enumerate(canonical_lessons):
        validate_concept_opening(title, fields)
        opening_class = (
            "start-here" if "Start here" in fields else "concept-introduction"
        )
        if parser.lesson_first_fields[lesson_index] != opening_class:
            fail(f"{title} rendered opening must precede application fields")
        missing_fields = sorted(set(REQUIRED_LESSON_FIELDS) - fields.keys())
        if missing_fields:
            fail(f"{title} is missing teaching fields: {missing_fields}")
        unknown = set(fields) - {*REQUIRED_LESSON_FIELDS, "Start here", "What it is"}
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
    }:
        fail("course metadata must use the current publication schema")
    validate_course_navigation(parser, metadata["slug"])
    plan_paths = [item["path"] for item in metadata["labs"]]
    if len(plan_paths) != len(set(plan_paths)) or set(plan_paths) != set(expected):
        fail("course metadata must classify every numbered lab exactly once")
    if any(
        set(item) != {"path", "optional", "lessons"}
        or type(item["optional"]) is not bool
        for item in metadata["labs"]
    ):
        fail(
            "each lab needs a path, optional/core classification and lesson membership"
        )
    validate_lab_guides(document, metadata, canonical_lessons, sources)
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
