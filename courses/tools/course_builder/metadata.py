"""Canonical course parsing and inventory."""

from __future__ import annotations
from pathlib import Path
from typing import NamedTuple
import json
import re
from .markdown import slug
from .config import (
    COURSES,
    FIELD_CLASSES,
    LAB_SECTIONS,
    LESSON_FIELDS,
    ROOT,
    SHARED_GUIDE_SECTIONS,
    TEXT_TITLE,
)


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
    fenced = False
    for line in lines:
        if current is not None and active_field and line.strip().startswith("```"):
            current[active_field] += "\n" + line
            fenced = not fenced
            continue
        if fenced:
            current[active_field] += "\n" + line
            continue
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
            if active_field in current:
                raise ValueError(
                    f"{path.name}: duplicate lesson field {active_field!r} "
                    f"in {current['title']!r}"
                )
            current[active_field] = match.group(2)
            fenced = match.group(2).strip().startswith("```")
        elif active_field:
            current[active_field] += "\n" + line
        elif line.strip():
            raise ValueError(
                f"{path.name}: content outside a lesson field in {current['title']!r}"
            )
    if fenced:
        raise ValueError(f"{path.name}: unterminated Markdown code block")
    if current:
        lessons.append(current)
    for lesson in lessons:
        unknown = set(lesson) - {"title", *FIELD_CLASSES}
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
        return section == "How it works"
    return bool(re.fullmatch(r"lab:\d+_[a-z0-9_]+", home)) and section in LAB_SECTIONS


def parse_references(path: Path) -> list[tuple[str, str]]:
    document = path.read_text(encoding="utf-8")
    return re.findall(r"\[([^]]+)\]\((https://[^)]+)\)", document)


def valid_lesson_fields(fields: list[str] | tuple[str, ...]) -> bool:
    return tuple(fields) in (LESSON_FIELDS, (*LESSON_FIELDS, "References"))


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
        lab_only = metadata.get("profile") == "labs-only"
        if (
            not isinstance(membership, list)
            or (not membership and not lab_only)
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
        chunks = re.split(r"^## (.+)\n", narrative, flags=re.MULTILINE)
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


def course_metadata(course: Path) -> dict:
    metadata = json.loads(
        (course / "reference/course.json").read_text(encoding="utf-8")
    )
    if metadata.get("profile") == "reference-only":
        return reference_course_metadata(metadata)
    if course.name == "soperator":
        return text_course_metadata(metadata)
    if course.name == "advanced-gpu-communication":
        return lab_course_metadata(course, metadata)
    if set(metadata) != {
        "slug",
        "title",
        "estimated_guided_hours",
        "labs",
        "extensions",
        "observability",
        "advanced_lessons",
        "external_labs",
    }:
        raise ValueError("course metadata must use the current publication schema")
    if metadata["slug"] != course.name or metadata["slug"] not in COURSES:
        raise ValueError("course slug must match its canonical catalog directory")
    advanced = metadata["advanced_lessons"]
    if (
        not isinstance(advanced, list)
        or any(type(n) is not int or n < 1 for n in advanced)
        or len(advanced) != len(set(advanced))
    ):
        raise ValueError("advanced_lessons must contain unique positive lesson IDs")
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
        set(item) != {"path", "optional", "lessons", "dashboard"}
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


def lab_course_metadata(course: Path, metadata: dict) -> dict:
    expected = {
        "slug",
        "title",
        "profile",
        "estimated_guided_hours",
        "lessons",
        "labs",
        "extensions",
        "observability",
    }
    if (
        set(metadata) != expected
        or metadata.get("profile") != "labs-only"
        or metadata.get("lessons") != []
    ):
        raise ValueError(
            "labs-only metadata requires the exact profile and no conceptual lessons"
        )
    if (
        metadata["slug"] != "advanced-gpu-communication"
        or metadata["title"]
        != "Advanced Labs: Multi-GPUs Multi-Nodes communication optimization"
    ):
        raise ValueError("invalid advanced laboratory identity")
    if (
        type(metadata["estimated_guided_hours"]) is not int
        or metadata["estimated_guided_hours"] < 1
    ):
        raise ValueError("guided hours must be a positive integer")
    if metadata["observability"] != "reference/observability.json":
        raise ValueError("invalid observability path")
    labs = metadata["labs"]
    if not isinstance(labs, list) or not labs:
        raise ValueError("labs-only course requires executable labs")
    for row in labs:
        if (
            set(row) != {"path", "optional", "lessons", "dashboard"}
            or row["lessons"] != []
            or type(row["optional"]) is not bool
        ):
            raise ValueError("invalid lab entry")
        if (
            not re.fullmatch(r"labs/[0-9]{2}_[a-z0-9_]+\.py", row["path"])
            or row["dashboard"]
            != "reference/grafana/" + Path(row["path"]).stem + ".json"
        ):
            raise ValueError("invalid lab source or dashboard path")
        path = course / row["path"]
        if path.is_symlink() or not path.is_file():
            raise ValueError("missing or unsafe lab source")
    if len({row["path"] for row in labs}) != len(labs) or {
        row["path"] for row in labs
    } != {str(p.relative_to(course)) for p in executable_sources(course)}:
        raise ValueError("lab inventory must be complete and unique")
    return metadata


def external_lab_guides(metadata: dict, lesson_count: int) -> list[dict]:
    guides = []
    seen = set()
    for row in metadata["external_labs"]:
        if (
            set(row) != {"course", "path", "lessons", "title"}
            or row["course"] != "advanced-gpu-communication"
            or not re.fullmatch(r"labs/[0-9]{2}_[a-z0-9_]+\.py", row["path"])
        ):
            raise ValueError("invalid external practical ownership")
        if (
            row["path"] in seen
            or not row["lessons"]
            or any(
                type(n) is not int or not 1 <= n <= lesson_count for n in row["lessons"]
            )
        ):
            raise ValueError("invalid external lab membership")
        seen.add(row["path"])
        source = ROOT / row["course"] / row["path"]
        path = source.parent.parent / "reference/labs" / (source.stem + ".md")
        title = path.read_text().splitlines()[0].removeprefix("# ")
        if title != row["title"]:
            raise ValueError("external title differs from owning course")
        guides.append(
            {
                "source": source,
                "title": title,
                "lessons": row["lessons"],
                "reference": "../"
                + row["course"]
                + "/reference/labs/"
                + source.stem
                + ".md",
                "href": "../" + row["course"] + "/index.html#lab-" + slug(source.stem),
            }
        )
    return guides


def text_course_metadata(metadata: dict) -> dict:
    """Keep the text-only exception narrow; GPU metadata retains its own schema."""
    if set(metadata) != {
        "slug",
        "title",
        "profile",
        "estimated_guided_hours",
        "lessons",
    }:
        raise ValueError("text-only metadata must use the exact publication schema")
    if (
        metadata["slug"] != "soperator"
        or metadata["profile"] != "text-only"
        or metadata["title"] != TEXT_TITLE
    ):
        raise ValueError("text-only metadata must declare the Soperator identity")
    if (
        type(metadata["estimated_guided_hours"]) is not int
        or metadata["estimated_guided_hours"] <= 0
    ):
        raise ValueError("estimated guided hours must be a positive whole number")
    lessons = metadata["lessons"]
    if not isinstance(lessons, list) or len(lessons) != 6:
        raise ValueError("the Soperator introduction must declare six lessons")
    for number, lesson in enumerate(lessons, 1):
        if (
            not isinstance(lesson, dict)
            or set(lesson) != {"id", "title"}
            or type(lesson["id"]) is not int
            or lesson["id"] != number
            or not isinstance(lesson["title"], str)
            or not lesson["title"].strip()
        ):
            raise ValueError(
                "text lesson identities must be ordered, numbered and titled"
            )
    return metadata


def reference_course_metadata(metadata: dict) -> dict:
    expected = {"slug", "title", "profile", "estimated_guided_hours", "lessons", "visual_manifest"}
    if (set(metadata) != expected or metadata["slug"] != "gpu-performance-tools"
        or metadata["title"] != "GPU Performance Tools" or metadata["profile"] != "reference-only"
        or metadata["estimated_guided_hours"] != 1 or metadata["visual_manifest"] != "reference/visual-manifest.json"):
        raise ValueError("invalid performance tools reference metadata")
    lessons = metadata["lessons"]
    if (not isinstance(lessons, list) or len(lessons) != 5 or any(
        not isinstance(row, dict) or set(row) != {"id", "title"} or row["id"] != n
        or not isinstance(row["title"], str) or not row["title"].strip()
        for n, row in enumerate(lessons, 1))):
        raise ValueError("reference course requires five ordered lessons")
    return metadata


def parse_text_course(path: Path, profile: str = "text-only") -> tuple[str, str, list[dict[str, str]]]:
    """Parse the standard heading-based lesson format without lab assumptions."""
    text = path.read_text(encoding="utf-8")
    parts = re.split(r"^## (\d+)\. (.+)$", text, flags=re.MULTILINE)
    count = 5 if profile == "reference-only" else 6
    if len(parts) != 1 + 3 * count or not parts[0].startswith("# "):
        raise ValueError(f"reading course must have a title and {count} numbered lessons")
    title, preamble = parts[0][2:].split("\n", 1)
    lessons = []
    for number in range(1, count + 1):
        identity, heading, body = parts[3 * number - 2 : 3 * number + 1]
        fields = re.split(r"^### (.+)$", body, flags=re.MULTILINE)
        if (
            int(identity) != number
            or fields[0].strip()
            or not (tuple(fields[1::2]) in (("Objective", "How it works", "Mental model"), ("Objective", "How it works", "Mental model", "References")) if profile == "reference-only" else valid_lesson_fields(fields[1::2]))
            or any(not value.strip() for value in fields[2::2])
        ):
            raise ValueError(
                "text lessons require ordered, nonempty Objective, How it works, Practice, Mental model and optional References sections"
            )
        lessons.append({"title": heading, **dict(zip(fields[1::2], fields[2::2]))})
    return title, preamble.strip(), lessons


def shared_guide_source() -> str:
    path = ROOT / "README.md"
    if path.is_symlink() or not path.is_file():
        raise ValueError("shared guide requires a regular README.md")
    text = path.read_text(encoding="utf-8").strip()
    if (
        not text.startswith("# ")
        or tuple(re.findall(r"^## (.+)$", text, re.MULTILINE)) != SHARED_GUIDE_SECTIONS
    ):
        raise ValueError(
            "shared guide requires the ordered setup, run and browsing sections"
        )
    return text


def catalog_metadata() -> dict[str, dict]:
    """Read the same canonical metadata used by each course page."""
    return {name: course_metadata(ROOT / name) for name in COURSES}
