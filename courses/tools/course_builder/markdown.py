"""Strict course Markdown and inline formatting."""

from __future__ import annotations
from typing import Callable
import base64
import html
import re
from .config import COURSES, ROOT, SHARED_GUIDE_SECTIONS
from .links import link_attributes
from .code import render_code


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def markdown_heading_id(value: str) -> str:
    """Markdown source headings remove punctuation instead of replacing it."""
    return re.sub(r"[^\w -]", "", value.lower()).replace(" ", "-")


# These source links intentionally render as labels in the standalone reading
# edition. Every other destination must resolve; this is not a global allowlist.
PLAIN_TEXT_LINKS = {
    "README.md": {
        "skills/run-labs/references/environment.md",
        "skills/run-labs/references/tool-recovery.md",
    },
    "advanced-gpu-communication/README.md": {"SYLLABUS.md", "PUBLICATION-REVIEW.md"},
    "custom-cuda-kernels/reference/lab-mechanisms.md": {"labs/08_async_pipeline.md"},
    "custom-cuda-kernels/reference/cluster-smoke-test.md": {
        "../SYLLABUS.md",
        "labs/10_hopper_cluster.md",
    },
}
for _course in COURSES:
    if _course != "soperator":
        PLAIN_TEXT_LINKS[_course + "/VERSIONS.md"] = {
            "reference/lab-results/",
            "../docs/profiling-validation.md",
        }
    if _course not in (
        "soperator",
        "advanced-gpu-communication",
        "custom-cuda-kernels",
    ):
        PLAIN_TEXT_LINKS[_course + "/reference/cluster-smoke-test.md"] = {
            "../SYLLABUS.md"
        }


def literal_text(value: str) -> str:
    if re.search(
        r"(?<![\w*])\*(?!\*)(?=\S)[^*\n]*?\*(?!\*)|(?<!\w)_(?=\S)[^_\n]+_(?!\w)", value
    ):
        raise ValueError("single emphasis is unsupported; use **bold** or plain text")
    return html.escape(value, quote=True)


def inline(
    markdown: str, links: dict[str, str] | None = None, *, source: str = ""
) -> str:
    # Parse Markdown tokens before emitting HTML so formatting never rewrites
    # literal code or crosses an already-rendered element boundary.
    tokens = r"`([^`]+)`|\*\*([^*]+)\*\*|\[([^]]+)\]\(([^)]+)\)"
    rendered: list[str] = []
    position = 0
    for match in re.finditer(tokens, markdown):
        rendered.append(literal_text(markdown[position : match.start()]))
        code, bold, label, destination = match.groups()
        if code is not None:
            rendered.append(f"<code>{html.escape(code, quote=True)}</code>")
        elif bold is not None:
            rendered.append(f"<strong>{inline(bold, links, source=source)}</strong>")
        else:
            target = (links or {}).get(destination, destination)
            text = inline(label, links, source=source)
            local_pages = (
                {"index.html", "../gpu-performance-tools/index.html"}
                | {f"{name}/index.html" for name in COURSES}
                | {f"../{name}/index.html" for name in COURSES}
                | {
                    "../lab-guide.html#" + slug(section)
                    for section in SHARED_GUIDE_SECTIONS
                }
            )
            if (
                target in local_pages
                or target.startswith(("#", "https://"))
                or any(
                    re.fullmatch(
                        re.escape(f"{name}/index.html") + r"#[a-z0-9-]+", target
                    )
                    for name in COURSES
                )
                or re.fullmatch(
                    r"\.\./advanced-gpu-communication/index\.html(?:#lab-[a-z0-9-]+)?",
                    target,
                )
            ):
                text = f'<a {link_attributes(target)}>{text}</a>'
            elif destination not in PLAIN_TEXT_LINKS.get(source, set()):
                raise ValueError(
                    f"{source or 'Markdown'}: unresolved destination {destination}"
                )
            rendered.append(text)
        position = match.end()
    rendered.append(literal_text(markdown[position:]))
    return "".join(rendered)


def guide_image(relative: str, alt: str) -> str:
    """Embed an authored local PNG without permitting arbitrary resource loads."""
    if not alt.strip() or not re.fullmatch(r"docs/[a-z0-9][a-z0-9/_-]*\.png", relative):
        raise ValueError("guide images require alternative text and a local docs PNG")
    path = ROOT / relative
    if (
        not path.is_file()
        or not path.resolve().is_relative_to((ROOT / "docs").resolve())
        or any(part.is_symlink() for part in (path, *path.parents))
    ):
        raise ValueError("guide image is missing or has an unsafe path")
    content = path.read_bytes()
    if not content.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("guide image must contain PNG bytes")
    encoded = base64.b64encode(content).decode("ascii")
    return (
        '<figure class="guide-image"><img '
        f'alt="{html.escape(alt, quote=True)}" data-source="{relative}" '
        f'src="data:image/png;base64,{encoded}"></figure>'
    )


def comparison_panels(lines: list[str], start: int) -> tuple[list[str], int]:
    """Consume exactly two nonempty panels, preserving fenced marker literals."""
    panels: list[list[str]] = [[]]
    fenced = False
    for index in range(start + 1, len(lines)):
        line = lines[index].strip()
        if line.startswith("```"):
            fenced = not fenced
        elif not fenced and line.startswith("<!-- comparison:"):
            if line == "<!-- comparison:next -->" and len(panels) == 1:
                panels.append([])
                continue
            if line == "<!-- comparison:end -->" and len(panels) == 2:
                contents = ["\n".join(panel).strip() for panel in panels]
                if not all(contents):
                    raise ValueError("comparison requires two nonempty panels")
                return contents, index + 1
            raise ValueError("invalid or nested comparison marker")
        panels[-1].append(lines[index])
    raise ValueError("unterminated comparison; expected next and end markers")


def block(
    markdown: str,
    links: dict[str, str] | None = None,
    *,
    source: str = "",
    context: str = "",
    prefix: str = "",
    images: bool = False,
    figure: Callable[[str, str], str] | None = None,
    heading_offset: int = 2,
) -> str:
    """Render the lesson/guide Markdown subset without dropping narrative blocks."""
    fenced = False
    for authored_line in markdown.splitlines():
        if authored_line.strip().startswith("```"):
            fenced = not fenced
        elif not fenced and re.match(r"^\s+(?:[-*]|\d+\.)\s+", authored_line):
            raise ValueError(f"{source or 'Markdown'}: nested lists are unsupported")
    lines = markdown.strip().splitlines()
    rendered: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if not line:
            index += 1
            continue
        if line.startswith("<!-- comparison:"):
            if line != "<!-- comparison:start -->":
                raise ValueError("unexpected comparison marker")
            panels, index = comparison_panels(lines, index)
            rendered.append(
                '<div class="comparison">'
                + "".join(
                    '<div class="comparison-panel">'
                    + block(
                        panel, links, source=source, context=context, prefix=prefix,
                        images=images, figure=figure, heading_offset=heading_offset,
                    )
                    + "</div>"
                    for panel in panels
                )
                + "</div>"
            )
            continue
        if line.startswith("```"):
            language = line[3:].strip()
            location = source or "Markdown"
            if context:
                location += f" [{context}]"
            code_source = f"{location}: fragment line {index + 1}"
            code: list[str] = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code.append(lines[index])
                index += 1
            if index == len(lines):
                raise ValueError(f"{code_source}: unterminated Markdown code block")
            rendered.append(
                f'<pre tabindex="0"><code>{render_code(chr(10).join(code), language, source=code_source)}</code></pre>'
            )
            index += 1
            continue
        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading:
            level = min(6, len(heading.group(1)) + heading_offset)
            title = heading.group(2)
            rendered.append(
                f'<h{level} id="{prefix}{slug(title)}">{inline(title, links, source=source)}</h{level}>'
            )
            index += 1
            continue
        picture = re.fullmatch(r"!\[([^\]]*)\]\(([^)]+)\)", line)
        if picture:
            if figure is not None:
                rendered.append(figure(picture[2], picture[1]))
                index += 1
                continue
            if not images:
                raise ValueError(
                    "local PNG images are supported in the shared guide only"
                )
            rendered.append(guide_image(picture[2], picture[1]))
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
            table += [
                f'<th scope="col">{inline(cell, links, source=source)}</th>'
                for cell in headers
            ]
            table.append("</tr></thead><tbody>")
            index += 2
            while index < len(lines) and lines[index].strip().startswith("|"):
                cells = [
                    cell.strip() for cell in lines[index].strip().strip("|").split("|")
                ]
                table.append(
                    "<tr>"
                    + "".join(
                        f"<td>{inline(cell, links, source=source)}</td>"
                        for cell in cells
                    )
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
                items.append(f"<li>{inline(content, links, source=source)}</li>")
            rendered.append(f"<{tag}>" + "".join(items) + f"</{tag}>")
            continue
        paragraph = [line]
        index += 1
        while (
            index < len(lines)
            and lines[index].strip()
            and not re.match(
                r"^(?:#{1,6}\s|```|\||[-*]\s|\d+\.\s|<!-- comparison:)",
                lines[index].strip(),
            )
        ):
            paragraph.append(lines[index].strip())
            index += 1
        rendered.append(f"<p>{inline(' '.join(paragraph), links, source=source)}</p>")
    return "".join(rendered)
