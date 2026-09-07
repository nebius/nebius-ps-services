#!/usr/bin/env python3
"""Build the self-contained GPU course catalog and individual course pages."""

from __future__ import annotations

import argparse
import html
import json
import os
import posixpath
import re
import tempfile
import textwrap
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]
COURSES = (
    "gpu-fundamentals",
    "gpu-optimizations",
    "llm-training",
    "llm-inference",
    "custom-cuda-kernels",
)
LICENSE_PATH = ROOT.parent / "LICENSE"
CATALOG_COPY = {
    "gpu-fundamentals": (
        "Understand the machine",
        "Build a working mental model of GPU execution before you begin optimizing. Connect what your code does to the hardware that runs it.",
        (
            "Explain threads, warps and scheduling",
            "Reason about memory and precision",
            "Read topology and health evidence",
        ),
        ("Execution", "Memory", "Precision"),
    ),
    "gpu-optimizations": (
        "Turn measurements into decisions",
        "Learn a repeatable approach to improving PyTorch workloads: establish a baseline, find the limiter, make one change and measure again.",
        (
            "Measure asynchronous GPU work correctly",
            "Use profiler evidence to find bottlenecks",
            "Evaluate changes with equivalent work",
        ),
        ("PyTorch", "Profiling", "Data movement"),
    ),
    "llm-training": (
        "Understand how models learn",
        "Connect the learning objective to the systems that make training possible. Explore correctness, memory, precision and distributed execution.",
        (
            "Preserve the intended learning objective",
            "Manage training memory and precision",
            "Investigate communication and recovery",
        ),
        ("Gradients", "Distributed training", "Recovery"),
    ),
    "llm-inference": (
        "Follow a request from prompt to tokens",
        "Understand how language models generate responses, then explore the trade-offs between serving capacity, latency, throughput and quality.",
        (
            "Explain prefill, decode and KV caches",
            "Measure latency and throughput",
            "Evaluate batching and serving behavior",
        ),
        ("Serving", "KV cache", "Latency"),
    ),
    "custom-cuda-kernels": (
        "Make the hardware your programming model",
        "Learn when a custom kernel is warranted, then build, validate and profile CUDA C++ kernels against a clear correctness and performance baseline.",
        (
            "Map computations to GPU execution",
            "Build and validate CUDA kernels",
            "Compare custom code with library baselines",
        ),
        ("CUDA C++", "Kernel design", "Validation"),
    ),
}
COMMON_GUIDES = (
    "README.md",
    "MISSION.md",
    "SYLLABUS.md",
    "GLOSSARY.md",
    "VERSIONS.md",
    "reference/benchmark-record.md",
    "reference/lab-mechanisms.md",
    "reference/cluster-smoke-test.md",
    "reference/evidence-security.md",
)
SUPPORTING_GUIDES = {"gpu-optimizations": ("reference/tooling-setup.md",)}
FIELD_CLASSES = {
    "Start here": "start-here",
    "What it is": "concept-introduction",
    "Objective": "lesson-outcome",
    "Prerequisite bridge": "prerequisite-bridge",
    "Recall": "recall",
    "Why it matters": "why-it-matters",
    "Mental model": "mental-model",
    "Mechanism": "mechanism",
    "Practice labs": "practice-links",
}
LESSON_FIELDS = (
    "Start here",
    "What it is",
    "Objective",
    "Prerequisite bridge",
    "Recall",
    "Why it matters",
    "Mental model",
    "Mechanism",
    "Practice labs",
)
LAB_SECTIONS = (
    "Before you start",
    "Concepts and code path",
    "Practice",
    "Check your results",
    "Investigate the behavior",
    "If something goes wrong",
    "Takeaways and next step",
)


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def inline(markdown: str, links: dict[str, str] | None = None) -> str:
    # Parse Markdown tokens before emitting HTML so formatting never rewrites
    # literal code or crosses an already-rendered element boundary.
    tokens = r"`([^`]+)`|\*\*([^*]+)\*\*|\[([^]]+)\]\(([^)]+)\)"
    rendered: list[str] = []
    position = 0
    for match in re.finditer(tokens, markdown):
        rendered.append(html.escape(markdown[position : match.start()], quote=True))
        code, bold, label, destination = match.groups()
        if code is not None:
            rendered.append(f"<code>{html.escape(code, quote=True)}</code>")
        elif bold is not None:
            rendered.append(f"<strong>{inline(bold, links)}</strong>")
        else:
            target = (links or {}).get(destination, destination)
            text = inline(label, links)
            if target.startswith(("#", "https://")):
                text = f'<a href="{html.escape(target, quote=True)}">{text}</a>'
            rendered.append(text)
        position = match.end()
    rendered.append(html.escape(markdown[position:], quote=True))
    return "".join(rendered)


def block(
    markdown: str, links: dict[str, str] | None = None, *, prefix: str = ""
) -> str:
    """Render the lesson/guide Markdown subset without dropping narrative blocks."""
    lines = markdown.strip().splitlines()
    rendered: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if not line:
            index += 1
            continue
        if line.startswith("```"):
            code: list[str] = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code.append(lines[index])
                index += 1
            if index == len(lines):
                raise ValueError("unterminated Markdown code block")
            rendered.append(
                f'<pre tabindex="0"><code>{html.escape(chr(10).join(code))}</code></pre>'
            )
            index += 1
            continue
        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading:
            level = min(6, len(heading.group(1)) + 2)
            title = heading.group(2)
            rendered.append(
                f'<h{level} id="{prefix}{slug(title)}">{inline(title, links)}</h{level}>'
            )
            index += 1
            continue
        if (
            line.startswith("|")
            and index + 1 < len(lines)
            and re.match(r"^\s*\|[\s:|\-]+\|\s*$", lines[index + 1])
        ):
            headers = [cell.strip() for cell in line.strip("|").split("|")]
            table = [
                '<div class="table-scroll" tabindex="0" role="region" aria-label="Scrollable table"><table><thead><tr>'
            ]
            table += [f'<th scope="col">{inline(cell, links)}</th>' for cell in headers]
            table.append("</tr></thead><tbody>")
            index += 2
            while index < len(lines) and lines[index].strip().startswith("|"):
                cells = [
                    cell.strip() for cell in lines[index].strip().strip("|").split("|")
                ]
                table.append(
                    "<tr>"
                    + "".join(f"<td>{inline(cell, links)}</td>" for cell in cells)
                    + "</tr>"
                )
                index += 1
            table.append("</tbody></table></div>")
            rendered.append("".join(table))
            continue
        item = re.match(r"^(?:[-*]|\d+\.)\s+(.+)$", line)
        if item:
            ordered = line[0].isdigit()
            tag = "ol" if ordered else "ul"
            items: list[str] = []
            while index < len(lines):
                match = re.match(r"^(?:[-*]|\d+\.)\s+(.+)$", lines[index].strip())
                if not match or lines[index].strip()[0].isdigit() != ordered:
                    break
                content = match.group(1)
                index += 1
                while (
                    index < len(lines)
                    and lines[index].startswith("  ")
                    and lines[index].strip()
                    and not re.match(r"^\s*(?:[-*]|\d+\.)\s", lines[index])
                ):
                    content += " " + lines[index].strip()
                    index += 1
                items.append(f"<li>{inline(content, links)}</li>")
            rendered.append(f"<{tag}>" + "".join(items) + f"</{tag}>")
            continue
        paragraph = [line]
        index += 1
        while (
            index < len(lines)
            and lines[index].strip()
            and not re.match(
                r"^(?:#{1,6}\s|```|\||[-*]\s|\d+\.\s)", lines[index].strip()
            )
        ):
            paragraph.append(lines[index].strip())
            index += 1
        rendered.append(f"<p>{inline(' '.join(paragraph), links)}</p>")
    return "".join(rendered)


def parse_course(path: Path) -> tuple[str, str, list[dict[str, str]]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    title_index = next(
        index for index, line in enumerate(lines) if line.startswith("# ")
    )
    title = lines[title_index][2:]
    first_lesson_index = next(
        index
        for index, line in enumerate(lines[title_index + 1 :], title_index + 1)
        if line.startswith("## ")
    )
    preamble = "\n".join(lines[title_index + 1 : first_lesson_index]).strip()
    lessons: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    active_field: str | None = None
    for line in lines:
        if line.startswith("## "):
            if current:
                lessons.append(current)
            current = {"title": re.sub(r"^\d+\.\s*", "", line[3:])}
            active_field = None
            continue
        if current is None:
            continue
        match = re.match(r"^\*\*([^*]+)\*\*\s*(.*)$", line)
        if match:
            active_field = match.group(1)
            current[active_field] = match.group(2)
        elif active_field:
            current[active_field] += "\n" + line
    if current:
        lessons.append(current)
    for lesson in lessons:
        unknown = set(lesson) - {"title", *LESSON_FIELDS}
        if unknown:
            raise ValueError(f"unsupported lesson fields: {sorted(unknown)}")
    return title, preamble, lessons


class OverviewDiagram(NamedTuple):
    title: str
    first: str
    second: str
    third: str
    explanation: str
    lesson: int
    after: str
    layout: str
    home: str


DIAGRAM_LAYOUTS = frozenset(
    {
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
)


def parse_visuals(path: Path) -> list[OverviewDiagram]:
    rows: list[OverviewDiagram] = []
    titles: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|") or line.startswith(("| ---", "| Title |")):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if (
            len(cells) != 9
            or not all(cells)
            or not cells[5].isdigit()
            or int(cells[5]) < 1
            or not valid_visual_section(cells[8], cells[6])
            or cells[7] not in DIAGRAM_LAYOUTS
            or cells[0] in titles
        ):
            raise ValueError(f"{path.name}: invalid or duplicate diagram placement")
        titles.add(cells[0])
        rows.append(
            OverviewDiagram(*cells[:5], int(cells[5]), cells[6], cells[7], cells[8])
        )
    if not rows:
        raise ValueError(f"{path.name}: missing diagram placements")
    return rows


def valid_visual_section(home: str, section: str) -> bool:
    if not isinstance(home, str) or not isinstance(section, str):
        return False
    """A figure declares a theory field or an existing practical-guide section."""
    if home == "lesson":
        return section in LESSON_FIELDS and section != "Practice labs"
    return bool(re.fullmatch(r"lab:\d+_[a-z0-9_]+", home)) and section in LAB_SECTIONS


def parse_references(path: Path) -> list[tuple[str, str]]:
    document = path.read_text(encoding="utf-8")
    return re.findall(r"\[([^]]+)\]\((https://[^)]+)\)", document)


def svg_label(
    value: str,
    x: int,
    y: int,
    *,
    anchor: str = "middle",
    width: int = 18,
    max_lines: int = 3,
) -> str:
    """Wrap an overview label around its visual center, not its first baseline.

    The shared overview type is 18px with 21.6px line spacing. Reject labels
    outside the allocated slot so new content cannot silently escape a shape.
    Character budgets complement, but do not replace, rendered font-fit review.
    """
    lines = textwrap.wrap(
        value, width=width, break_long_words=False, break_on_hyphens=False
    ) or [value]
    if len(lines) > max_lines or any(len(line) > width for line in lines):
        raise ValueError(
            "diagram label exceeds its layout budget; revise the label or slot"
        )
    baseline = y + 6 - (len(lines) - 1) * 10.8
    spans = "".join(
        f'<tspan x="{x}" dy="{0 if line_index == 0 else "1.2em"}">{html.escape(line)}</tspan>'
        for line_index, line in enumerate(lines)
    )
    return f'<text x="{x}" y="{baseline:g}" text-anchor="{anchor}">{spans}</text>'


def diagram(row: OverviewDiagram, index: int) -> str:
    title, first, second, third, explanation = row[:5]
    diagram_id = f"diagram-{index}-{slug(title)}"
    kind = row.layout
    if kind not in DIAGRAM_LAYOUTS:
        raise ValueError("invalid overview diagram layout")
    if kind in {"comparison", "flow", "cycle"}:
        label_y = 138 if kind == "comparison" else 130
        labels = (
            svg_label(first, 165, label_y),
            svg_label(second, 480, label_y),
            svg_label(third, 795, label_y),
        )
    if kind == "comparison":
        body = f"""<g text-anchor="middle"><rect x="45" y="80" width="240" height="115" rx="14"/><rect x="360" y="80" width="240" height="115" rx="14"/><rect x="675" y="80" width="240" height="115" rx="14"/>
{labels[0]}{labels[1]}{labels[2]}</g>
<text x="480" y="260" text-anchor="middle" class="diagram-note">Compare distinct concepts — no causal sequence implied</text>"""
    elif kind == "hierarchy":
        body = f"""<rect x="55" y="30" width="850" height="230" rx="18"/><rect x="180" y="75" width="600" height="155" rx="16"/><rect x="325" y="120" width="310" height="80" rx="14"/>
{svg_label(first, 90, 58, anchor="start")}{svg_label(second, 480, 105, width=40)}{svg_label(third, 480, 158)}"""
    elif kind == "matrix":
        body = f"""<text x="420" y="35" text-anchor="middle">Short OSL</text><text x="710" y="35" text-anchor="middle">Long OSL</text>
<rect x="275" y="55" width="285" height="95"/><rect x="560" y="55" width="310" height="95"/><rect x="275" y="150" width="285" height="95"/><rect x="560" y="150" width="310" height="95"/>
{svg_label(first, 145, 105)}{svg_label(second, 145, 195)}
{svg_label("Small prompt, short generation", 417, 92)}{svg_label("Small prompt, long generation", 715, 92)}
{svg_label("Large prompt, short generation", 417, 187)}{svg_label("Large prompt, long generation", 715, 187)}
<text x="480" y="282" text-anchor="middle" class="diagram-note">{html.escape(third)}</text>"""
    elif kind == "roofline":
        body = f"""<g class="axes"><line x1="120" y1="240" x2="870" y2="240"/><line x1="120" y1="240" x2="120" y2="35"/></g><polyline class="roofline" points="120,230 500,75 850,75"/>
{svg_label(first, 290, 205)}{svg_label(second, 480, 125)}{svg_label(third, 730, 28)}
<text x="490" y="280" text-anchor="middle" class="diagram-note">Arithmetic intensity (operations / byte) →</text><text x="45" y="140" transform="rotate(-90 45 140)" text-anchor="middle" class="diagram-note">Attainable operations / second</text>"""
    elif kind == "topology":
        body = f"""<rect x="45" y="75" width="250" height="130" rx="16"/><ellipse class="diagram-shape" cx="480" cy="140" rx="100" ry="65"/><rect x="665" y="75" width="250" height="130" rx="16"/>
{svg_label(first, 170, 135)}{svg_label(second, 480, 135)}{svg_label(third, 790, 135)}
<g class="arrows" marker-end="url(#arrow-{index})"><line x1="305" y1="140" x2="370" y2="140" marker-start="url(#arrow-{index})"/><line x1="590" y1="140" x2="655" y2="140" marker-start="url(#arrow-{index})"/></g>"""
    elif kind == "overlap":
        body = f"""<text x="100" y="85" text-anchor="middle">Compute</text><text x="100" y="170" text-anchor="middle">Collective</text>
<rect x="230" y="45" width="400" height="65" rx="8"/><rect x="390" y="135" width="460" height="65" rx="8"/>
<rect class="exposed" x="630" y="135" width="220" height="65" rx="8"/>
{svg_label(first, 430, 73)}{svg_label(second, 510, 162)}{svg_label(third, 740, 224)}
<line class="timeline" x1="630" y1="35" x2="630" y2="215" stroke-dasharray="5 5"/>
<text x="390" y="125" text-anchor="middle" class="diagram-note">Bucket ready</text>
<text x="480" y="283" text-anchor="middle" class="diagram-note">Schematic: an earlier ready bucket overlaps remaining backward; only its tail extends the step</text>"""
    elif kind == "pipeline":
        body = f"""<text x="100" y="85" text-anchor="middle">Compute</text><text x="100" y="170" text-anchor="middle">Async copy</text>
<rect x="230" y="45" width="300" height="65" rx="8"/><rect x="570" y="45" width="300" height="65" rx="8"/>
<rect x="300" y="135" width="220" height="65" rx="8"/>
{svg_label("Compute tile i", 380, 75)}{svg_label("Compute tile i+1", 720, 75)}{svg_label("Load tile i+1", 410, 163)}
<path d="M520 170 H550 V77 H570" fill="none" stroke="#206757" stroke-width="3" marker-end="url(#arrow-{index})"/>
<text x="480" y="250" text-anchor="middle" class="diagram-note">Wait for tile readiness before its consumer starts; wait for consumers before buffer reuse</text>
<text x="480" y="280" text-anchor="middle" class="diagram-note">Schematic steady state: prime tile i before this interval; drain after the final tile</text>"""
    elif kind == "timeline":
        body = f"""<line class="timeline" x1="135" y1="140" x2="825" y2="140"/><circle class="diagram-shape" cx="165" cy="140" r="38"/><circle class="diagram-shape" cx="480" cy="140" r="38"/><circle class="diagram-shape" cx="795" cy="140" r="38"/>
{svg_label(first, 165, 215)}{svg_label(second, 480, 215)}{svg_label(third, 795, 215)}"""
    elif kind == "decision":
        body = f"""<path class="diagram-shape" d="M80 55 H880 L790 115 H170 Z"/><path class="diagram-shape" d="M170 125 H790 L690 185 H270 Z"/><path class="diagram-shape" d="M270 195 H690 L585 255 H375 Z"/>
{svg_label(first, 480, 85, width=24, max_lines=2)}{svg_label(second, 480, 155, width=24, max_lines=2)}{svg_label(third, 480, 225, width=24, max_lines=2)}"""
    else:
        body = f"""<g text-anchor="middle"><rect x="45" y="80" width="240" height="100" rx="14"/><rect x="360" y="80" width="240" height="100" rx="14"/><rect x="675" y="80" width="240" height="100" rx="14"/>
{labels[0]}{labels[1]}{labels[2]}</g>
<g class="arrows" marker-end="url(#arrow-{index})"><line x1="285" y1="130" x2="350" y2="130"/><line x1="600" y1="130" x2="665" y2="130"/></g>"""
    if kind == "cycle":
        body += f'<path d="M795 185 V260 H165 V185" fill="none" stroke="#206757" stroke-width="3" marker-end="url(#arrow-{index})"/>'
    description = f"{explanation} Diagram concepts: {first}; {second}; {third}."
    return f"""
<figure class="overview-diagram" id="{diagram_id}" data-after="{html.escape(row.after)}" data-diagram-kind="{kind}"><svg viewBox="0 0 960 300" role="img" aria-labelledby="{diagram_id}-title {diagram_id}-desc">
<title id="{diagram_id}-title">{html.escape(title)}</title>
<desc id="{diagram_id}-desc">{html.escape(description)}</desc>
<defs><marker id="arrow-{index}" markerWidth="8" markerHeight="8" refX="7" refY="4" viewBox="0 0 8 8" orient="auto-start-reverse"><path d="M0,0 L0,8 L8,4 z" fill="#206757"/></marker></defs>
{body}
</svg><figcaption><strong>{html.escape(title)}</strong><p>{html.escape(explanation)}</p></figcaption></figure>"""


def lesson_markup(
    lesson: dict[str, str],
    number: int,
    links: dict[str, str] | None = None,
    related: list[tuple[str, str]] | None = None,
    figures: dict[str, list[str]] | None = None,
    practice_labs: list[dict] | None = None,
) -> str:
    title = lesson["title"]
    parts = [
        f'<section class="lesson" id="{slug(title)}">',
        f"<h2>{number}. {html.escape(title)}</h2>",
    ]
    for field in LESSON_FIELDS:
        if field == "Practice labs":
            continue
        content = lesson.get(field)
        if not content:
            continue
        class_name = FIELD_CLASSES[field]
        parts.append(
            f'<div class="{class_name}"><strong>{html.escape(field)}</strong> {block(content, links)}</div>'
        )
        parts.extend((figures or {}).get(field, []))
    if related:
        parts.append(
            '<div class="lesson-visuals"><strong>Related diagram</strong><ul>'
            + "".join(
                f'<li><a href="#{target}">{html.escape(label)}</a></li>'
                for label, target in related
            )
            + "</ul></div>"
        )
    if practice_labs is not None:
        expected = "\n".join(
            f"- [{guide['title']}](reference/labs/{guide['source'].stem}.md)"
            for guide in practice_labs
        )
        if not expected or lesson.get("Practice labs", "").strip() != expected:
            raise ValueError(f"{title}: Practice labs must match its assigned guides")
        parts.append(
            '<div class="practice-links"><strong>Practice labs</strong><ul>'
            + "".join(
                f'<li><a href="#lab-{slug(guide["source"].stem)}">{html.escape(guide["title"])}</a></li>'
                for guide in practice_labs
            )
            + "</ul></div>"
        )
    parts.append("</section>")
    return "\n".join(parts)


def executable_sources(course: Path) -> list[Path]:
    return sorted(
        path
        for path in (course / "labs").iterdir()
        if path.is_file()
        and re.match(r"^\d+_", path.name)
        and path.suffix in {".py", ".cu", ".cpp"}
    )


def lab_guides(course: Path, metadata: dict, lesson_count: int) -> list[dict]:
    """Load authored teaching content; never synthesize explanations from filenames."""
    entries = metadata["labs"]
    sources = {
        source.relative_to(course).as_posix(): source
        for source in executable_sources(course)
    }
    paths = [item["path"] for item in entries]
    if len(paths) != len(set(paths)) or set(paths) != set(sources):
        raise ValueError("lab guide metadata must assign each source exactly once")
    expected = {
        course / "reference/labs" / (source.stem + ".md") for source in sources.values()
    }
    if set((course / "reference/labs").glob("*.md")) != expected:
        raise ValueError("lab guide inventory has missing or orphaned guides")
    result = []
    for item in entries:
        source = sources[item["path"]]
        membership = item.get("lessons")
        if (
            not isinstance(membership, list)
            or not membership
            or any(
                type(number) is not int or not 1 <= number <= lesson_count
                for number in membership
            )
            or len(membership) != len(set(membership))
        ):
            raise ValueError(f"lab {source.name}: invalid lesson membership")
        path = course / "reference/labs" / (source.stem + ".md")
        document = path.read_text(encoding="utf-8").strip()
        number = source.name.split("_", 1)[0]
        title, _, narrative = document.partition("\n")
        if not re.fullmatch(rf"# Lab {number}: [^\n.]+", title):
            raise ValueError(f"lab guide {path.name}: title must identify Lab {number}")
        chunks = re.split(r"^## (.+)\n", narrative, flags=re.M)
        introduction = chunks[0].strip()
        names = chunks[1::2]
        sections = dict(
            zip(names, (part.strip() for part in chunks[2::2]), strict=True)
        )
        if (
            tuple(names) != LAB_SECTIONS
            or len(introduction.split()) < 40
            or any(len(body.split()) < 20 for body in sections.values())
        ):
            raise ValueError(
                f"lab guide {path.name}: incomplete introduction or ordered sections"
            )
        if "```bash" not in sections["Practice"]:
            raise ValueError(f"lab guide {path.name}: runnable commands are required")
        result.append(
            {
                "source": source,
                "number": number,
                "title": title[2:],
                "introduction": introduction,
                "sections": sections,
                "lessons": membership,
                "optional": item["optional"],
                "guide_path": path,
            }
        )
    return result


def lab_guide_links(guide: dict, references: dict[str, str]) -> dict[str, str]:
    links = dict(references)
    for target, anchor in references.items():
        if not target.startswith("https://"):
            links[posixpath.relpath(target, "reference/labs")] = anchor
    for destination in re.findall(
        r"\[[^]]+\]\(([^)]+)\)", guide["guide_path"].read_text(encoding="utf-8")
    ):
        base, separator, section = destination.partition("#")
        if (
            separator
            and base in links
            and links[base].startswith("#lab-")
            and section in {slug(name) for name in LAB_SECTIONS}
        ):
            links[destination] = links[base] + "-" + section
        if destination not in links and not destination.startswith("#"):
            raise ValueError(
                f"lab guide {guide['guide_path'].name}: unresolved link {destination}"
            )
    return links


def lab_markup(
    course: Path,
    guide: dict,
    lessons: list[dict],
    references: dict[str, str],
    figures: dict[str, list[str]] | None = None,
) -> str:
    source = guide["source"]
    relative = source.relative_to(course).as_posix()
    links = lab_guide_links(guide, references)
    narrative = "".join(
        f'<div class="lab-guide-section" id="lab-{slug(source.stem)}-{slug(name)}"><h4>{html.escape(name)}</h4>{block(body, links, prefix=f"lab-{slug(source.stem)}-{slug(name)}-")}</div>'
        + "".join((figures or {}).get(name, []))
        for name, body in guide["sections"].items()
    )
    lesson_links = "".join(
        f'<li><a href="#{slug(lessons[number - 1]["title"])}">{number}. {html.escape(lessons[number - 1]["title"])}</a></li>'
        for number in guide["lessons"]
    )
    return f"""
<article class="lab" id="lab-{slug(source.stem)}" data-lab-guide="complete"><h3>{html.escape(guide["title"])}</h3>
{block(guide["introduction"], links)}<p class="lab-scope">{"Optional extension" if guide["optional"] else "Core practice"}</p>
<div class="lab-lessons"><strong>Related lessons</strong><ul>{lesson_links}</ul></div>
<div class="lab-guide">{narrative}</div>
<details class="lab-source" data-source="{html.escape(relative)}"><summary>Complete source: {html.escape(relative)}</summary><pre tabindex="0"><code data-source="{html.escape(relative)}">{html.escape(source.read_text(encoding="utf-8"))}</code></pre></details></article>"""


def course_metadata(course: Path) -> dict:
    metadata = json.loads(
        (course / "reference/course.json").read_text(encoding="utf-8")
    )
    if set(metadata) != {
        "slug",
        "title",
        "estimated_guided_hours",
        "labs",
        "extensions",
    }:
        raise ValueError("course metadata must use the current publication schema")
    if metadata["slug"] != course.name or metadata["slug"] not in COURSES:
        raise ValueError("course slug must match its canonical catalog directory")
    hours = metadata["estimated_guided_hours"]
    if type(hours) is not int or hours <= 0:
        raise ValueError("estimated guided hours must be a positive whole number")
    if not isinstance(metadata["title"], str) or not metadata["title"].strip():
        raise ValueError("course title must be nonempty")
    paths = [item["path"] for item in metadata["labs"]]
    expected = {
        path.relative_to(course).as_posix() for path in executable_sources(course)
    }
    if len(paths) != len(set(paths)) or set(paths) != expected:
        raise ValueError("course metadata must classify every numbered lab once")
    if any(
        set(item) != {"path", "optional", "lessons"}
        or type(item["optional"]) is not bool
        for item in metadata["labs"]
    ):
        raise ValueError(
            "each lab needs its path, optional/core classification and lesson membership"
        )
    if not isinstance(metadata["extensions"], list) or any(
        not isinstance(item, str) or not item.strip() for item in metadata["extensions"]
    ):
        raise ValueError("optional extensions must be nonempty topic strings")
    return metadata


def guide_id(relative: str) -> str:
    return "guide-" + slug(str(Path(relative).with_suffix("")))


def guide_markup(
    course: Path, relative: str, references: dict[str, str], guides: tuple[str, ...]
) -> str:
    document = (course / relative).read_text(encoding="utf-8")
    lines = document.splitlines()
    title = next(
        (line[2:] for line in lines if line.startswith("# ")), Path(relative).stem
    )
    body = "\n".join(lines[1:]) if lines and lines[0].startswith("# ") else document
    links = dict(references)
    for target in guides:
        destination = posixpath.relpath(target, posixpath.dirname(relative) or ".")
        links[destination] = "#" + guide_id(target)
    links[posixpath.relpath("RESOURCES.md", posixpath.dirname(relative) or ".")] = (
        "#official-references"
    )
    links[posixpath.relpath("index.html", posixpath.dirname(relative) or ".")] = (
        "#course-contents"
    )
    for target, fragment in {
        "COURSE.md": "course-overview",
    }.items():
        links[posixpath.relpath(target, posixpath.dirname(relative) or ".")] = (
            "#" + fragment
        )
    for source in executable_sources(course):
        target = source.relative_to(course).as_posix()
        links[posixpath.relpath(target, posixpath.dirname(relative) or ".")] = (
            "#lab-" + slug(source.stem)
        )
    target_id = guide_id(relative)
    return f"""<article class="supporting-guide" id="{target_id}" data-source="{html.escape(relative)}">
<h3>{html.escape(title)}</h3>{block(body, links, prefix=target_id + "-")}
</article>"""


def detailed_visuals(course: Path, lesson_count: int) -> list[dict]:
    entries = json.loads(
        (course / "reference/visual-manifest.json").read_text(encoding="utf-8")
    )["diagrams"]
    seen: set[str] = set()
    for entry in entries:
        if (
            set(entry) != {"path", "title", "lessons", "after", "home"}
            or not isinstance(entry["title"], str)
            or not entry["title"].strip()
            or not isinstance(entry["lessons"], list)
            or not entry["lessons"]
            or any(
                type(number) is not int or not 1 <= number <= lesson_count
                for number in entry["lessons"]
            )
            or len(set(entry["lessons"])) != len(entry["lessons"])
            or not valid_visual_section(entry["home"], entry["after"])
            or entry["path"] in seen
        ):
            raise ValueError("invalid or duplicate detailed diagram placement")
        seen.add(entry["path"])
        source = course / entry["path"]
        if (
            not source.resolve().is_relative_to(course.resolve())
            or source.suffix != ".svg"
        ):
            raise ValueError("invalid detailed diagram path")
        entry["svg"] = source.read_text(encoding="utf-8").strip()
        entry["id"] = "detail-" + slug(source.stem)
        description = re.search(r"<desc[^>]*>(.*?)</desc>", entry["svg"], re.S)
        entry["description"] = (
            html.unescape(description.group(1)) if description else ""
        )
    return entries


def detailed_diagram_markup(entry: dict) -> str:
    return f"""<figure class="detail-diagram" id="{entry["id"]}" data-after="{html.escape(entry["after"])}" data-diagram-source="{html.escape(entry["path"])}">
{entry["svg"]}
<figcaption><strong>{html.escape(entry["title"])}</strong><p>{html.escape(entry["description"])}</p></figcaption></figure>"""


def catalog_metadata() -> dict[str, dict]:
    """Read the same canonical metadata used by each course page."""
    return {name: course_metadata(ROOT / name) for name in COURSES}


def license_footer() -> str:
    """Keep the complete license with each independently saved HTML document."""
    license_text = html.escape(LICENSE_PATH.read_text(encoding="utf-8"))
    return f"""<footer class="license-footer">
<small>© 2026 Nebius B.V. Provided free of charge for learning and education.
Licensed under <a href="#license">Apache License 2.0</a>.<br>
Third-party materials retain their respective licenses.</small>
<details id="license"><summary>License and notices</summary>
<pre class="license-text">{license_text}</pre></details>
</footer>"""


def course_switcher(course_name: str, metadata: dict[str, dict]) -> str:
    items = []
    for name in COURSES:
        title = html.escape(metadata[name]["title"])
        if name == course_name:
            item = f'<span aria-current="page" data-course="{name}">{title}<span class="current-label">Current course</span></span>'
        else:
            item = f'<a href="../{name}/index.html">{title}</a>'
        items.append(f"<li>{item}</li>")
    return f"""<div class="catalog-navigation">
<a class="catalog-home" href="../index.html">← All courses</a>
<details class="course-switcher"><summary>Switch course</summary>
<ul>{"".join(items)}</ul></details></div>"""


def render_catalog() -> str:
    metadata = catalog_metadata()
    cards = []
    for number, name in enumerate(COURSES, 1):
        title = html.escape(metadata[name]["title"])
        hours = metadata[name]["estimated_guided_hours"]
        eyebrow, introduction, outcomes, tags = CATALOG_COPY[name]
        prerequisite = (
            "Start here · No previous GPU course required"
            if number == 1
            else "Prerequisite: GPU Fundamentals"
            if number == 2
            else "Prerequisites: Fundamentals + Optimization"
        )
        outcome_items = "".join(f"<li>{html.escape(item)}</li>" for item in outcomes)
        tag_items = "".join(f"<span>{html.escape(tag)}</span>" for tag in tags)
        cards.append(f"""<article class="course-card course-{number}" id="{name}">
<div class="card-top"><span class="course-number">0{number}</span><span class="course-hours">{hours} guided hours</span></div>
<p class="card-eyebrow">{html.escape(eyebrow)}</p><h3>{title}</h3>
<p class="course-description">{html.escape(introduction)}</p>
<ul class="course-outcomes">{outcome_items}</ul>
<div class="course-tags" aria-label="Topics">{tag_items}</div>
<div class="card-bottom"><p class="prerequisite">{prerequisite}</p>
<a class="course-link" href="{name}/index.html" aria-label="Read {title}">Read course <span aria-hidden="true">↗</span></a></div>
</article>""")
    css = (ROOT / "tools/catalog.css").read_text(encoding="utf-8")
    lab_count = sum(len(item["labs"]) for item in metadata.values())
    return f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="Five free Nebius courses in GPU fundamentals, performance optimization, LLM training, inference and custom CUDA kernels, with practical NVIDIA H100 labs.">
<title>GPU Performance Engineering Courses | Nebius</title><link rel="icon" href="data:,">
<style>{css}</style></head>
<body><a class="skip-link" href="#main">Skip to courses</a>
<div class="site-header"><a class="wordmark" href="../index.html" aria-label="Nebius Platform Services home">nebius<span>courses</span></a>
<nav aria-label="Main navigation"><a href="#catalog">Explore courses</a><a href="#learning-path">Learning path</a><a href="https://github.com/nebius/nebius-ps-services">GitHub <span aria-hidden="true">↗</span></a></nav></div>
<main id="main"><header class="hero">
<div class="hero-copy"><p class="eyebrow"><span class="status-dot" aria-hidden="true"></span> Learn the system. Understand the result.</p>
<h1>GPU Performance<br><span>Engineering</span></h1>
<p class="hero-description">From your first GPU mental model to language models and custom kernels. Five practical courses that connect concepts, code and measured evidence.</p>
<div class="hero-actions"><a class="button primary" href="gpu-fundamentals/index.html">Start with GPU Fundamentals <span aria-hidden="true">↗</span></a><a class="button secondary" href="#catalog">Explore all courses <span aria-hidden="true">↓</span></a></div>
<p class="platform-note">Built around NVIDIA H100 · Linux · Slurm</p></div>
<aside class="learning-map" id="learning-path" aria-labelledby="path-title">
<div class="map-heading"><p class="eyebrow">Your learning path</p><span class="map-count">01 → 05</span></div>
<h2 id="path-title">A foundation.<br>Then your direction.</h2>
<ol class="path-foundations"><li><span class="path-step" aria-hidden="true">01</span><a href="#gpu-fundamentals">GPU Fundamentals<small>Understand the hardware</small></a></li><li><span class="path-step" aria-hidden="true">02</span><a href="#gpu-optimizations">GPU Performance Optimization<small>Learn how to measure and improve</small></a></li></ol>
<div class="path-branches"><p>Then choose a specialization</p><ul><li><a href="#llm-training">LLM Training <span aria-hidden="true">↗</span></a></li><li><a href="#llm-inference">LLM Inference <span aria-hidden="true">↗</span></a></li><li><a href="#custom-cuda-kernels">Custom CUDA Kernels <span aria-hidden="true">↗</span></a></li></ul></div>
<p class="path-note">The three specializations are independent. Start any of them after the two foundation courses.</p></aside>
</header>
<div class="catalog-facts" aria-label="Catalog overview"><p><strong>{len(COURSES)}</strong> focused courses</p><p><strong>{lab_count}</strong> practical labs</p><p><strong>One</strong> evidence-first approach</p><p class="fact-note">Free to read.<br>Built to put into practice.</p></div>
<section id="catalog" class="catalog-section" aria-labelledby="catalog-title"><div class="section-heading"><div><p class="eyebrow">The course collection</p><h2 id="catalog-title">Build understanding.<br>Put it to work.</h2></div><p>Begin with the foundations, then follow the questions that matter to your workload.</p></div>
<div class="collection-label"><span>01 / Foundations</span><p>Take these in order</p></div>
<div class="course-grid foundations">{"".join(cards[:2])}</div>
<div class="collection-label"><span>02 / Specializations</span><p>Choose your direction</p></div>
<div class="course-grid specializations">{"".join(cards[2:])}</div></section>
<section class="approach" aria-labelledby="approach-title"><div><p class="eyebrow">More than a reading list</p><h2 id="approach-title">Learn. Practice. Review.</h2><p>Every course connects explanations to practical experiments. Build an intuition, make a prediction, then investigate what actually happens.</p></div>
<ol><li><span>01</span><div><h3>Learn the mechanism</h3><p>Start with definitions, mental models and diagrams.</p></div></li><li><span>02</span><div><h3>Work through the experiment</h3><p>Read the prerequisites, explore the code and run the supplied lab.</p></div></li><li><span>03</span><div><h3>Explain the evidence</h3><p>Check correctness, interpret measurements and decide what to investigate next.</p></div></li></ol></section>
<p class="environment-note">Read the courses in your browser. Run labs in the documented Linux and Slurm environment; GPU exercises require the specified hardware and dependencies. Each course explains its setup and readiness checks.</p>
{license_footer()}</main></body></html>"""


def render_course(course_name: str) -> str:
    course = ROOT / course_name
    canonical_title, preamble, lessons = parse_course(course / "COURSE.md")
    for number, lesson in enumerate(lessons, 1):
        opening = "Start here" if number == 1 else "What it is"
        expected_fields = {"title", *LESSON_FIELDS} - {"Start here", "What it is"}
        if set(lesson) != expected_fields | {opening}:
            raise ValueError(
                "every lesson needs its definition, theory and Practice labs links"
            )
    metadata = course_metadata(course)
    title = metadata["title"]
    visuals = parse_visuals(course / "reference/visual-plan.md")
    detailed = detailed_visuals(course, len(lessons))
    if any(visual.lesson > len(lessons) for visual in visuals):
        raise ValueError("overview diagram placement points to a missing lesson")
    authored_labs = lab_guides(course, metadata, len(lessons))
    guides = COMMON_GUIDES + SUPPORTING_GUIDES.get(course_name, ())
    references = list(
        dict.fromkeys(
            parse_references(course / "RESOURCES.md")
            + parse_references(course / "NEXT-STEPS.md")
            + [item for guide in guides for item in parse_references(course / guide)]
            + [
                item
                for guide in authored_labs
                for item in parse_references(guide["guide_path"])
            ]
        )
    )
    reference_links = {
        url: f"#reference-{index}" for index, (_label, url) in enumerate(references, 1)
    }
    reference_links.update({path: "#" + guide_id(path) for path in guides})
    reference_links["NEXT-STEPS.md"] = "#next-steps"
    reference_links.update(
        {
            guide["guide_path"].relative_to(course).as_posix(): "#lab-"
            + slug(guide["source"].stem)
            for guide in authored_labs
        }
    )
    lesson_toc = "".join(
        f'<li><a href="#{slug(item["title"])}">{html.escape(item["title"])}</a></li>'
        for item in lessons
    )
    lab_toc = "".join(
        f'<li><a href="#lab-{slug(guide["source"].stem)}">{html.escape(guide["title"])}</a></li>'
        for guide in authored_labs
    )
    guide_labels = {
        "README.md": "Getting started",
        "MISSION.md": "Course mission",
        "SYLLABUS.md": "Syllabus",
        "GLOSSARY.md": "Glossary",
        "VERSIONS.md": "Versions and environment",
        "reference/benchmark-record.md": "Benchmark worksheet",
        "reference/lab-mechanisms.md": "Lab mechanisms and evidence",
        "reference/cluster-smoke-test.md": "Cluster smoke runbook",
        "reference/evidence-security.md": "Evidence and privacy",
        "reference/tooling-setup.md": "Diagnostic tooling setup",
    }
    guides_toc = "".join(
        f'<li><a href="#{guide_id(path)}">{guide_labels[path]}</a></li>'
        for path in guides
    )
    inline_figures: dict[int, dict[str, list[str]]] = {}
    lab_figures: dict[str, dict[str, list[str]]] = {}
    labs_by_stem = {guide["source"].stem: guide for guide in authored_labs}

    def place(home: str, lesson: int, after: str, markup: str) -> None:
        if home == "lesson":
            if after not in lessons[lesson - 1]:
                raise ValueError("diagram placement points to a missing lesson field")
            destination = inline_figures.setdefault(lesson, {})
        else:
            stem = home.removeprefix("lab:")
            if stem not in labs_by_stem or lesson not in labs_by_stem[stem]["lessons"]:
                raise ValueError("diagram lab home must include its primary lesson")
            destination = lab_figures.setdefault(stem, {})
        destination.setdefault(after, []).append(markup)

    for index, visual in enumerate(visuals, 1):
        place(visual.home, visual.lesson, visual.after, diagram(visual, index))
    for entry in detailed:
        place(
            entry["home"],
            entry["lessons"][0],
            entry["after"],
            detailed_diagram_markup(entry),
        )
    lesson_html = "\n".join(
        lesson_markup(
            item,
            index,
            reference_links,
            [
                (entry["title"], entry["id"])
                for entry in detailed
                if index
                in (
                    entry["lessons"]
                    if entry["home"] != "lesson"
                    else entry["lessons"][1:]
                )
            ]
            + [
                (visual.title, f"diagram-{visual_index}-{slug(visual.title)}")
                for visual_index, visual in enumerate(visuals, 1)
                if visual.home != "lesson" and visual.lesson == index
            ],
            inline_figures.get(index),
            [guide for guide in authored_labs if index in guide["lessons"]],
        )
        for index, item in enumerate(lessons, 1)
    )
    labs_html = "\n".join(
        lab_markup(
            course,
            guide,
            lessons,
            reference_links,
            lab_figures.get(guide["source"].stem),
        )
        for guide in authored_labs
    )
    extensions_html = (
        '<div class="optional-study"><h3>Optional further study</h3><ul>'
        + "".join(f"<li>{html.escape(topic)}</li>" for topic in metadata["extensions"])
        + "</ul></div>"
        if metadata["extensions"]
        else ""
    )
    guides_html = "\n".join(
        guide_markup(course, path, reference_links, guides + ("NEXT-STEPS.md",))
        for path in guides
    )
    next_steps_html = guide_markup(course, "NEXT-STEPS.md", reference_links, guides)
    refs_html = "\n".join(
        f'<li id="reference-{index}"><a href="{html.escape(url, quote=True)}">{html.escape(label)}</a></li>'
        for index, (label, url) in enumerate(references, 1)
    )
    css = (ROOT / "tools/course.css").read_text(encoding="utf-8")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title><link rel="icon" href="data:,"><style>{css}</style></head>
<body><a class="skip-link" href="#main">Skip to course</a>
<header><h1>{html.escape(title)}</h1><p class="guided-hours">Estimated guided hours: {metadata["estimated_guided_hours"]}</p></header>
<div class="course-layout"><aside>
<nav id="course-contents" aria-label="Course contents">{course_switcher(course_name, catalog_metadata())}<details open><summary>Table of contents</summary>
<ul class="section-links"><li><a href="#course-overview">Course overview</a></li><li><a href="#course-contract">How to use this course</a></li></ul>
<h2>Lessons</h2><ol>{lesson_toc}</ol>
<h2>Practice and reference</h2><ul class="section-links"><li><a href="#labs">Practical labs</a><details><summary>Browse labs</summary><ul>{lab_toc}</ul></details></li>
<li><a href="#supporting-guides">Course guides</a><ul>{guides_toc}</ul></li><li><a href="#next-steps">Where to Go Next</a></li><li><a href="#official-references">Official references</a></li></ul>
</details></nav></aside><main id="main">
<section id="course-overview"><h2>Course overview</h2><p class="course-subtitle">{html.escape(canonical_title)}</p>{block(preamble, reference_links)}</section>
<section id="course-contract"><h2>How to use this course</h2><div class="course-contract"><div><h3>Learn</h3><p>Read the definition, mental model and mechanism. Explain what the technique is, why it is useful and how it works before following the Practice labs links.</p></div><div><h3>Practice</h3><p>Read the lab prerequisites, work through its Practice examples and run the supplied experiment. Follow the cluster runbook and record evidence in the benchmark worksheet.</p></div><div><h3>Review</h3><p>Compare correctness, repeated measurements, and profiler observations. Use the glossary and detailed diagrams when a term or relationship needs a second look.</p></div></div>{extensions_html}</section>
{lesson_html}
<section id="labs"><h2>Practical labs and complete source</h2>{labs_html}</section>
<section id="supporting-guides"><h2>Course guides</h2>{guides_html}</section>
<section id="next-steps"><h2>Where to Go Next</h2>{next_steps_html}</section>
<section id="official-references"><h2>Official references</h2><ol>{refs_html}</ol></section>
<p>Use the benchmark worksheet to record what your environment demonstrates and what remains to be tested.</p>
{license_footer()}
</main></div></body></html>"""


def build(course_name: str) -> None:
    course = ROOT / course_name
    document = render_course(course_name)
    write_page(course / "index.html", document)


def write_page(destination: Path, document: str) -> None:
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=destination.parent,
        prefix=".index.",
        delete=False,
    ) as stream:
        stream.write(document)
        temporary = Path(stream.name)
    try:
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("courses", nargs="*", choices=COURSES)
    parser.add_argument(
        "--check",
        action="store_true",
        help="check selected course pages and the catalog against their canonical sources",
    )
    args = parser.parse_args()
    catalog = render_catalog()
    catalog_path = ROOT / "index.html"
    if args.check:
        if (
            not catalog_path.is_file()
            or catalog_path.read_text(encoding="utf-8") != catalog
        ):
            raise SystemExit(
                "stale or missing generated page: index.html (course catalog)"
            )
        print("current index.html (course catalog)")
    else:
        write_page(catalog_path, catalog)
        print("built index.html (course catalog)")
    for course_name in args.courses or COURSES:
        if args.check:
            expected = render_course(course_name)
            actual = (ROOT / course_name / "index.html").read_text(encoding="utf-8")
            if actual != expected:
                raise SystemExit(f"stale generated page: {course_name}/index.html")
            print(f"current {course_name}/index.html")
        else:
            build(course_name)
            print(f"built {course_name}/index.html")


if __name__ == "__main__":
    main()
