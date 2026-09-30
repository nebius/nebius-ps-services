"""Reusable lesson, lab and guide markup."""

from __future__ import annotations
from pathlib import Path
import html
import posixpath
import re
from .markdown import block, inline, slug
from .metadata import executable_sources, valid_lesson_fields
from .visuals import detailed_diagram_markup, tool_figure
from .downloads import dashboard_pointer, lab_results_pointer
from .config import FIELD_CLASSES, LAB_SECTIONS, SHARED_GUIDE_SECTIONS


def lesson_markup(
    lesson: dict[str, str],
    number: int,
    links: dict[str, str] | None = None,
    related: list[tuple[str, str]] | None = None,
    figures: dict[str, list[str]] | None = None,
    practice_labs: list[dict] | None = None,
    authored_figures: list[dict] | None = None,
) -> str:
    title = lesson["title"]
    parts = [
        f'<section class="lesson" id="{slug(title)}" data-lesson-number="{number}">',
        f"<h2>{number}. {html.escape(title)}</h2>",
    ]
    for field in FIELD_CLASSES:
        content = lesson.get(field)
        remaining_figures = list((figures or {}).get(field, []))

        def figure(path: str, caption: str) -> str:
            entry = next(
                (
                    item
                    for item in (authored_figures or [])
                    if item["path"] == path and item["after"] == field
                ),
                None,
            )
            if entry is None or caption != entry["title"]:
                raise ValueError(
                    f"{title}: figure must match its declared lesson home and title"
                )
            markup = detailed_diagram_markup(entry)
            if markup not in remaining_figures:
                raise ValueError(f"{title}: duplicate or misplaced lesson figure")
            remaining_figures.remove(markup)
            return markup

        if field == "Practice" and practice_labs is not None:
            assigned = {
                f"- [{guide['title']}]({guide.get('reference', 'reference/labs/' + guide['source'].stem + '.md')})": guide
                for guide in practice_labs
            }
            context, separator, listing = (content or "").partition("\n\n- [")
            entries = ("- [" + listing).splitlines() if separator else []
            if (
                not context.strip()
                or not assigned
                or len(entries) != len(assigned)
                or set(entries) != set(assigned)
            ):
                raise ValueError(f"{title}: Practice must match its assigned guides")
            body = (
                block(context, links)
                + "<ul>"
                + "".join(
                    f'<li><a href="{guide.get("href", "#lab-" + slug(guide["source"].stem))}">{html.escape(guide["title"])}</a></li>'
                    for guide in (assigned[entry] for entry in entries)
                )
                + "</ul>"
            )
        elif content:
            body = block(content, links, heading_offset=1, figure=figure)
        else:
            continue
        class_name = FIELD_CLASSES[field]
        parts.append(f'<div class="{class_name}"><h3>{html.escape(field)}</h3>{body}')
        parts.extend(remaining_figures)
        if field == "How it works" and related:
            parts.append(
                '<aside class="lesson-visuals"><strong>Related diagram</strong><ul>'
                + "".join(
                    f'<li><a href="#{target}">{html.escape(label)}</a></li>'
                    for label, target in related
                )
                + "</ul></aside>"
            )
        parts.append("</div>")
    parts.append("</section>")
    return "\n".join(parts)


def glossary_markup(content: str, links: dict[str, str] | None = None) -> str:
    """Render the canonical course glossary without accepting unstructured entries."""
    entries = []
    for line in content.strip().splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(r"- \*\*(.+?)\*\* — (.+)", line)
        if not match or not all(value.strip() for value in match.groups()):
            raise ValueError("Glossary entries must use '- **Term** — Definition'")
        entries.append(match.groups())
    keys = [re.sub(r"[`*_]", "", term).strip().casefold() for term, _ in entries]
    if not keys or keys != sorted(set(keys)):
        raise ValueError("Glossary must contain unique terms in A–Z order")
    return (
        "<dl>"
        + "".join(
            f"<dt>{inline(term, links)}</dt><dd>{inline(definition, links)}</dd>"
            for term, definition in entries
        )
        + "</dl>"
    )


def lab_guide_links(guide: dict, references: dict[str, str]) -> dict[str, str]:
    links = dict(references)
    links.update(shared_guide_links())
    links["../grafana/" + guide["source"].stem + ".json"] = "#dashboard-" + slug(
        guide["source"].stem
    )
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
    return f"""
<article class="lab" id="lab-{slug(source.stem)}" data-lab-guide="complete"><h3>{html.escape(guide["title"])}</h3>
{block(guide["introduction"], links)}<p class="lab-scope">{"Optional extension" if guide["optional"] else "Core practice"}</p>
<div class="lab-guide">{narrative}</div>
<div id="dashboard-{slug(source.stem)}">{dashboard_pointer()}</div>
{lab_results_pointer(course, source.stem)}
<details class="lab-source" data-source="{html.escape(relative)}"><summary>Complete source: {html.escape(relative)}</summary><pre tabindex="0"><code data-source="{html.escape(relative)}">{html.escape(source.read_text(encoding="utf-8"))}</code></pre></details></article>"""


def performance_tools_markup(
    course: Path, metadata: dict, links: dict[str, str] | None = None
) -> str:
    if (
        metadata.get("performance_tools") != "reference/performance-tools.md"
        or metadata.get("observability") != "reference/observability.json"
    ):
        raise ValueError(
            "performance lesson and observability inventory must use canonical paths"
        )
    text = (course / metadata["performance_tools"]).read_text()
    chunks = re.split(r"^## (.+)\n", text, flags=re.MULTILINE)
    if chunks[0].strip() != "# Using GPU performance tools" or not valid_lesson_fields(
        chunks[1::2]
    ):
        raise ValueError(
            "performance lesson requires ordered teaching fields and optional References"
        )
    diagram = '<figure class="performance-workflow" id="tools-measurement-loop"><svg viewBox="0 0 320 210" style="max-width: 380px; margin-inline: auto" role="img" aria-labelledby="tools-title tools-desc"><title id="tools-title">A measured optimization loop</title><desc id="tools-desc">Measure an unprofiled baseline; inspect with Systems, Compute and Grafana; change one variable; measure again and check correctness before keeping it.</desc><rect x="5" y="5" width="310" height="200" rx="10" fill="#edf3f7"/><g fill="#172b3a" font-size="15"><text x="20" y="35">Measure → inspect → predict</text><text x="20" y="70">↓ Change one variable</text><text x="20" y="105">↓ Measure again → check correctness</text><text x="20" y="140">↓ Explain the result</text></g><text x="20" y="170" fill="#172b3a" font-size="13">Profiler runs explain.</text><text x="20" y="190" fill="#172b3a" font-size="13">Unprofiled runs establish the outcome.</text></svg><figcaption>Use each observation to test a specific explanation.</figcaption></figure>'
    seen: set[str] = set()

    def figure(relative: str, title: str) -> str:
        if relative in seen:
            raise ValueError("duplicate tool figure")
        seen.add(relative)
        return tool_figure(course, relative, title)

    body = "".join(
        f'<div class="tools-field tools-{slug(name)}"><h3>{html.escape(name)}</h3>'
        + block(
            content,
            links,
            prefix="tools-",
            figure=figure if name == "How it works" else None,
            heading_offset=1,
        )
        + (diagram if name == "How it works" else "")
        + "</div>"
        for name, content in zip(chunks[1::2], chunks[2::2], strict=True)
    )
    return (
        '<section id="using-gpu-performance-tools" class="performance-tools"><h2>Using GPU performance tools</h2>'
        + body
        + "</section>"
    )


def shared_guide_links() -> dict[str, str]:
    return {
        parent + "README.md#" + slug(section): "../lab-guide.html#" + slug(section)
        for parent in ("../", "../../", "../../../")
        for section in SHARED_GUIDE_SECTIONS
    }


def guide_id(relative: str) -> str:
    return "guide-" + slug(str(Path(relative).with_suffix("")))


def course_glossary_markup(course: Path) -> str:
    body = (course / "GLOSSARY.md").read_text(encoding="utf-8").split("\n", 1)[1]
    return (
        '<section id="guide-glossary" data-source="GLOSSARY.md"><h2>Glossary</h2>'
        + glossary_markup(body)
        + "</section>"
    )


def next_steps_markup(course: Path, links: dict[str, str]) -> str:
    """Keep each complete authored study direction in one semantic bullet."""
    text = (course / "NEXT-STEPS.md").read_text(encoding="utf-8")
    if not text.startswith("# Where to Go Next\n"):
        raise ValueError("next-steps requires its canonical title")
    parts = re.split(r"^- \*\*(.+)\*\*\s*$", text.split("\n", 1)[1], flags=re.M)
    if len(parts) < 3 or len(parts) % 2 != 1:
        raise ValueError("next-steps requires complete bulleted topics")
    topics = []
    for title, body in zip(parts[1::2], parts[2::2], strict=True):
        lines = body.strip("\n").splitlines()
        if not lines or any(line and not line.startswith("  ") for line in lines):
            raise ValueError(
                "next-steps topic paragraphs must be indented under their bullet"
            )
        content = "\n".join(line[2:] if line else "" for line in lines)
        if not title.strip() or not content.strip():
            raise ValueError("next-steps topics need a title and explanation")
        topics.append(
            f"<li><h3>{inline(title, links)}</h3>{block(content, links)}</li>"
        )
    return (
        '<section id="next-steps"><h2>Where to Go Next</h2>'
        '<div data-source="NEXT-STEPS.md">'
        + block(parts[0], links)
        + '<ul class="next-steps-list">'
        + "".join(topics)
        + "</ul></div></section>"
    )


def guide_markup(
    course: Path,
    relative: str,
    references: dict[str, str],
    guides: tuple[str, ...],
    *,
    heading_level: int = 3,
) -> str:
    document = (course / relative).read_text(encoding="utf-8")
    lines = document.splitlines()
    title = next(
        (line[2:] for line in lines if line.startswith("# ")), Path(relative).stem
    )
    body = "\n".join(lines[1:]) if lines and lines[0].startswith("# ") else document
    links = dict(references)
    advanced_page = "../advanced-gpu-communication/index.html"
    links[posixpath.relpath(advanced_page, posixpath.dirname(relative) or ".")] = (
        advanced_page
    )
    for target in guides:
        destination = posixpath.relpath(target, posixpath.dirname(relative) or ".")
        links[destination] = (
            "#next-steps" if target == "NEXT-STEPS.md" else "#" + guide_id(target)
        )
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
<h{heading_level}>{html.escape(title)}</h{heading_level}>{block(body, links, source=f"{course.name}/{relative}", prefix=target_id + "-")}
</article>"""
