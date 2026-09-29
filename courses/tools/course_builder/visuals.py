"""Accessible, passive course diagrams."""

from __future__ import annotations
from pathlib import Path
import html
import json
import re
import textwrap
import xml.etree.ElementTree as ET
from .markdown import slug
from .metadata import DIAGRAM_LAYOUTS, OverviewDiagram, valid_visual_section


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
    width, height = 360, 430
    marker = f"url(#arrow-{index})"

    def card(value: str, y: int) -> str:
        return f'<rect x="35" y="{y}" width="290" height="90" rx="12"/>' + svg_label(
            value, 180, y + 45, width=26
        )

    def arrow(path: str, *, both: bool = False) -> str:
        start = f' marker-start="{marker}"' if both else ""
        return (
            f'<path class="arrows" d="{path}" fill="none" '
            f'marker-end="{marker}"{start}/>'
        )

    if kind in {"flow", "cycle", "comparison", "timeline", "decision", "topology"}:
        body = card(first, 20) + card(second, 165) + card(third, 310)
        if kind != "comparison":
            body += arrow("M180 114 V157", both=kind == "topology")
            body += arrow("M180 259 V302", both=kind == "topology")
        if kind == "cycle":
            body += arrow("M328 355 H345 V65 H328")
        # Stage order is not a measured duration or an invented decision branch.
    elif kind == "hierarchy":
        height = 380
        body = (
            '<rect x="10" y="15" width="340" height="345" rx="16"/>'
            '<rect x="30" y="110" width="300" height="230" rx="14"/>'
            '<rect x="50" y="215" width="260" height="105" rx="12"/>'
            + svg_label(first, 180, 60, width=28)
            + svg_label(second, 180, 155, width=26)
            + svg_label(third, 180, 267, width=24)
        )
    elif kind == "matrix":
        width, height = 460, 400
        body = (
            svg_label("Short OSL", 197, 30)
            + svg_label("Long OSL", 362, 30)
            + svg_label(first, 55, 120, width=9)
            + svg_label(second, 55, 250, width=9)
        )
        for x, y, label in (
            (115, 60, "Small prompt, short generation"),
            (280, 60, "Small prompt, long generation"),
            (115, 190, "Large prompt, short generation"),
            (280, 190, "Large prompt, long generation"),
        ):
            body += f'<rect x="{x}" y="{y}" width="165" height="130"/>'
            body += svg_label(label, x + 82, y + 65, width=15)
        body += svg_label(third, 230, 365, width=38)
    elif kind == "roofline":
        height = 380
        body = (
            '<g class="axes"><line x1="65" y1="295" x2="345" y2="295"/>'
            '<line x1="65" y1="295" x2="65" y2="40"/></g>'
            '<polyline class="roofline" points="65,280 205,105 340,105"/>'
            + svg_label(first, 200, 250, width=17)
            + svg_label(second, 270, 190, width=14)
            + svg_label(third, 268, 55, width=17)
            + arrow("M240 165 L209 112")
            + '<text x="200" y="345" text-anchor="middle">Operations / byte →</text>'
            + '<text x="22" y="175" transform="rotate(-90 22 175)" '
            'text-anchor="middle">Operations / second</text>'
        )
    elif kind == "overlap":
        height = 490
        body = (
            svg_label("Compute", 100, 30)
            + svg_label("Collective", 280, 30)
            + '<rect x="35" y="70" width="130" height="260" rx="8"/>'
            '<rect x="215" y="195" width="130" height="260" rx="8"/>'
            '<rect class="exposed" x="215" y="330" width="130" height="125" rx="8"/>'
            + svg_label(first, 100, 190, width=13)
            + svg_label(second, 280, 262, width=13)
            + svg_label(third, 280, 392, width=13)
            + svg_label("Bucket ready", 280, 154, width=13)
            + '<line class="timeline" x1="25" y1="330" x2="350" y2="330" stroke-dasharray="5 5"/>'
            + svg_label("Compute ends", 100, 357, width=13)
            + arrow("M190 80 V450")
            + svg_label("Time ↓", 100, 450)
        )
    elif kind == "pipeline":
        height = 465
        body = (
            svg_label("Compute", 95, 30)
            + svg_label("Async copy", 280, 30)
            + '<rect x="30" y="70" width="130" height="120" rx="8"/>'
            '<rect x="30" y="270" width="130" height="120" rx="8"/>'
            '<rect x="215" y="110" width="130" height="110" rx="8"/>'
            + svg_label("Compute tile i", 95, 130, width=13)
            + svg_label("Compute tile i+1", 95, 330, width=13)
            + svg_label("Load tile i+1", 280, 165, width=13)
            + arrow("M280 224 V242 H95 V262")
            + svg_label("Copy complete", 280, 257, width=13)
            + svg_label("Time runs downward", 180, 430, width=28)
        )
    description = f"{explanation} Diagram concepts: {first}; {second}; {third}."
    return f"""
<figure class="overview-diagram" id="{diagram_id}" data-after="{html.escape(row.after)}" data-diagram-kind="{kind}"><svg viewBox="0 0 {width} {height}" style="max-width: {width + 60}px; margin-inline: auto" role="img" aria-labelledby="{diagram_id}-title {diagram_id}-desc">
<title id="{diagram_id}-title">{html.escape(title)}</title>
<desc id="{diagram_id}-desc">{html.escape(description)}</desc>
<defs><marker id="arrow-{index}" markerWidth="8" markerHeight="8" refX="7" refY="4" viewBox="0 0 8 8" orient="auto-start-reverse"><path d="M0,0 L0,8 L8,4 z" fill="#206757"/></marker></defs>
{body}
</svg><figcaption><strong>{html.escape(title)}</strong><p>{html.escape(explanation)}</p></figcaption></figure>"""


def passive_svg(
    course: Path, path: Path, *, id_prefix: str | None = None
) -> tuple[str, str]:
    """Validate all authored SVGs with one passive policy, without rewriting them."""
    if (
        not path.is_file()
        or not path.resolve().is_relative_to(course.resolve())
        or any(p.is_symlink() for p in (path, *path.parents))
    ):
        raise ValueError("SVG path is missing or unsafe")
    svg = path.read_text(encoding="utf-8").strip()
    if "<!" in svg or "<?" in svg:
        raise ValueError("SVG declarations are unsupported")
    try:
        root = ET.fromstring(svg)
    except ET.ParseError as error:
        raise ValueError("invalid SVG") from error
    for node in root.iter():
        if node.tag.startswith("{http://www.w3.org/2000/svg}"):
            node.tag = node.tag.split("}", 1)[1]
    tags = {
        "svg",
        "title",
        "desc",
        "g",
        "rect",
        "line",
        "path",
        "polyline",
        "polygon",
        "circle",
        "text",
        "tspan",
        "defs",
        "marker",
    }
    attributes = {
        "class",
        "style",
        "marker-start",
        "markerUnits",
        "viewBox",
        "role",
        "aria-labelledby",
        "id",
        "x",
        "y",
        "x1",
        "y1",
        "x2",
        "y2",
        "width",
        "height",
        "rx",
        "ry",
        "cx",
        "cy",
        "r",
        "d",
        "points",
        "fill",
        "stroke",
        "stroke-width",
        "stroke-dasharray",
        "stroke-linecap",
        "font-family",
        "font-size",
        "font-weight",
        "text-anchor",
        "dominant-baseline",
        "dx",
        "dy",
        "transform",
        "marker-end",
        "markerWidth",
        "markerHeight",
        "refX",
        "refY",
        "orient",
    }
    ids = [node.get("id") for node in root.iter() if node.get("id")]
    if (
        root.tag != "svg"
        or not root.get("viewBox")
        or root.get("role") != "img"
        or len(ids) != len(set(ids))
        or (
            id_prefix is not None
            and any(not value.startswith(id_prefix) for value in ids)
        )
        or root.get("aria-labelledby", "").split()
        != [
            node.get("id")
            for node in (root.find("title"), root.find("desc"))
            if node is not None
        ]
        or any(
            root.find(tag) is None
            or not "".join(root.find(tag).itertext()).strip()
            or not root.find(tag).get("id")
            for tag in ("title", "desc")
        )
    ):
        raise ValueError("SVG needs unique scoped IDs, title and description")
    for node in root.iter():
        if node.tag not in tags or set(node.attrib) - attributes:
            raise ValueError("SVG contains unsupported active content")
        if "style" in node.attrib and not re.fullmatch(
            r"max-width: [0-9]+px; margin-inline: auto", node.attrib["style"]
        ):
            raise ValueError("SVG contains unsupported style")
        for value in node.attrib.values():
            if (
                "\\" in value
                or "/*" in value
                or (
                    re.search(r"url\s*\(", value, re.IGNORECASE)
                    and not re.fullmatch(r"url\(#[a-z0-9_-]+\)", value)
                )
            ):
                raise ValueError("SVG cannot load external resources")
            for target in re.findall(r"url\(#([a-z0-9_-]+)\)", value):
                if target not in ids:
                    raise ValueError("SVG references an unknown local ID")
    return svg, "".join(root.find("desc").itertext())


def tool_figure(course: Path, relative: str, title: str) -> str:
    """Embed only passive, accessible course-owned tool diagrams."""
    if not title.strip() or not re.fullmatch(
        r"diagrams/tools-[a-z0-9_-]+\.svg", relative
    ):
        raise ValueError("tool figure requires a title and local tools SVG path")
    path = course / "reference" / relative
    if (
        not path.is_file()
        or not path.resolve().is_relative_to(course.resolve())
        or any(part.is_symlink() for part in (path, *path.parents))
    ):
        raise ValueError("tool figure path is missing or unsafe")
    svg = path.read_text(encoding="utf-8").strip()
    svg, description = passive_svg(course, path, id_prefix=path.stem + "-")
    return (
        f'<figure class="detail-diagram tools-diagram" id="{path.stem}" '
        f'data-diagram-source="reference/{relative}">{svg}'
        f"<figcaption><strong>{html.escape(title)}</strong>"
        f"<p>{html.escape(description)}</p></figcaption></figure>"
    )


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
        entry["svg"], entry["description"] = passive_svg(course, source)
        entry["id"] = "detail-" + slug(source.stem)
    return entries


def detailed_diagram_markup(entry: dict) -> str:
    return f"""<figure class="detail-diagram" id="{entry["id"]}" data-after="{html.escape(entry["after"])}" data-diagram-source="{html.escape(entry["path"])}">
{entry["svg"]}
<figcaption><strong>{html.escape(entry["title"])}</strong><p>{html.escape(entry["description"])}</p></figcaption></figure>"""
