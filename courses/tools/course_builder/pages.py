"""Compose the catalog, shared guide and three course profiles."""

from __future__ import annotations
from pathlib import Path
import html
import json
import re
from .markdown import block, markdown_heading_id, slug
from .metadata import (
    catalog_metadata,
    course_metadata,
    executable_sources,
    external_lab_guides,
    lab_guides,
    parse_course,
    parse_references,
    parse_text_course,
    parse_visuals,
    shared_guide_source,
    valid_lesson_fields,
)
from .visuals import detailed_diagram_markup, detailed_visuals, diagram, passive_svg
from .content import (
    course_glossary_markup,
    guide_id,
    guide_markup,
    lab_markup,
    lesson_markup,
    next_steps_markup,
    performance_tools_markup,
    shared_guide_links,
)
from .downloads import course_downloads
from .shell import course_switcher, license_footer, page_head
from .config import (
    CATALOG_COPY,
    CATALOG_ENTRIES,
    CATALOG_GROUPS,
    COMMON_GUIDES,
    COURSES,
    FIELD_CLASSES,
    GUIDE_TITLE,
    LAB_SECTIONS,
    ROOT,
    SUPPORTING_GUIDES,
    catalog_destination,
)


def render_lab_course(course: Path, metadata: dict) -> str:
    # Reuse the same rendering functions; this profile deliberately has no lessons.
    if (course / "COURSE.md").read_text().splitlines()[0] != "# " + metadata["title"]:
        raise ValueError("course title must match canonical metadata")
    guides = lab_guides(course, metadata, 0)
    supporting = (
        "README.md",
        "GLOSSARY.md",
        "VERSIONS.md",
        "reference/evidence-security.md",
        "reference/benchmark-record.md",
        "reference/cluster-smoke-test.md",
        "reference/lab-mechanisms.md",
    )
    references = list(
        dict.fromkeys(
            parse_references(course / "RESOURCES.md")
            + [
                item
                for guide in guides
                for item in parse_references(guide["guide_path"])
            ]
            + [
                item
                for name in supporting + ("NEXT-STEPS.md",)
                for item in parse_references(course / name)
            ]
        )
    )
    links = {url: f"#reference-{n}" for n, (_, url) in enumerate(references, 1)}
    links.update({name: "#" + guide_id(name) for name in supporting})
    links.update(shared_guide_links())
    links.update(
        {
            "reference/labs/" + g["source"].stem + ".md": "#lab-"
            + slug(g["source"].stem)
            for g in guides
        }
    )
    figures = {}
    for entry in json.loads((course / "reference/visual-manifest.json").read_text())[
        "diagrams"
    ]:
        if (
            set(entry) != {"path", "title", "lab", "after"}
            or entry["lab"] not in {g["source"].stem for g in guides}
            or entry["after"] not in LAB_SECTIONS
        ):
            raise ValueError("invalid laboratory diagram ownership")
        path = course / entry["path"]
        if (
            path.is_symlink()
            or not path.resolve().is_relative_to(
                (course / "reference/diagrams").resolve()
            )
            or path.suffix != ".svg"
        ):
            raise ValueError("unsafe laboratory diagram source")
        _, description = passive_svg(course, path)
        entry = dict(
            entry,
            svg=path.read_text(),
            id="detail-" + slug(path.stem),
            description=description,
        )
        figures.setdefault(entry["lab"], {}).setdefault(entry["after"], []).append(
            dict(
                path="../diagrams/" + path.name,
                title=entry["title"],
                markup=detailed_diagram_markup(entry),
            )
        )
    toc = "".join(
        f'<li><a href="#lab-{slug(g["source"].stem)}">{html.escape(g["title"])}</a></li>'
        for g in guides
    )
    body = "".join(
        lab_markup(course, g, [], links, figures.get(g["source"].stem)) for g in guides
    )
    helpers = "".join(
        f'<details><summary>{html.escape(str(p.relative_to(course)))}</summary><pre tabindex="0"><code data-source="{html.escape(str(p.relative_to(course)))}">{html.escape(p.read_text())}</code></pre></details>'
        for p in sorted((course / "labs").glob("*.py"))
        if not re.match(r"^[0-9]+_", p.name)
    )
    supporting_html = "".join(
        guide_markup(course, name, links, supporting)
        for name in supporting
        if name != "GLOSSARY.md"
    )
    glossary_html = course_glossary_markup(course)
    refs = "".join(
        f'<li id="reference-{n}"><a href="{html.escape(url)}">{html.escape(label)}</a></li>'
        for n, (label, url) in enumerate(references, 1)
    )
    title = metadata["title"]
    intro = (course / "COURSE.md").read_text().split("\n", 1)[1]
    css = (ROOT / "tools/course.css").read_text()
    return f"""{page_head(title, css)}
<body class="lab-course"><a class="skip-link" href="#main">Skip to course</a><header><h1>{html.escape(title)}</h1><p class="guided-hours">Estimated guided hours: {metadata["estimated_guided_hours"]}</p></header>
<div class="course-layout"><aside><nav id="course-contents" aria-label="Course contents">{course_switcher(course.name, catalog_metadata())}<details open><summary>Table of contents</summary><ul class="section-links"><li><a href="#course-overview">Course overview</a></li></ul><h2>Labs</h2><ul>{toc}</ul><ul class="section-links"><li><a href="#supporting-guides">Course guides</a></li><li><a href="#next-steps">Where to Go Next</a></li><li><a href="#guide-glossary">Glossary</a></li><li><a href="#official-references">Official references</a></li></ul></details></nav></aside>
<main id="main"><section id="course-overview"><h2>Course overview</h2>{block(intro, links)}</section><section id="labs"><h2>Practical labs</h2>{course_downloads(course)}{body}</section><section id="runtime-sources"><h2>Complete runtime support</h2>{helpers}</section><section id="supporting-guides"><h2>Course guides</h2>{supporting_html}</section>{next_steps_markup(course, links)}{glossary_html}<section id="official-references"><h2>Official references</h2><ol>{refs}</ol></section>{license_footer()}</main></div></body></html>"""


def render_text_course(course: Path, metadata: dict) -> str:
    title, preamble, lessons = parse_text_course(course / "COURSE.md")
    identities = [
        {"id": n, "title": lesson["title"]} for n, lesson in enumerate(lessons, 1)
    ]
    if title != metadata["title"] or identities != metadata["lessons"]:
        raise ValueError(
            "text title and lesson identities must match canonical metadata"
        )
    links = {"NEXT-STEPS.md": "#next-steps"}
    links.update(
        {
            "#"
            + markdown_heading_id(
                f"{n}. {lesson['title']}"
            ): f"#lesson-{n}-{slug(lesson['title'])}"
            for n, lesson in enumerate(lessons, 1)
        }
    )
    lesson_toc, lesson_html = [], []
    for number, lesson in enumerate(lessons, 1):
        anchor = f"lesson-{number}-{slug(lesson['title'])}"
        heading = html.escape(lesson["title"])
        lesson_toc.append(f'<li><a href="#{anchor}">{heading}</a></li>')
        sections = "".join(
            f'<div class="{FIELD_CLASSES[field]}"><h3>{field}</h3>'
            + block(lesson[field], links, heading_offset=0)
            + "</div>"
            for field in lesson
            if field != "title"
        )
        lesson_html.append(
            f'<section class="lesson" id="{anchor}"><h2>{number}. {heading}</h2>{sections}</section>'
        )
    references = parse_references(course / "RESOURCES.md")
    refs = "".join(
        f'<li id="reference-{n}"><a href="{html.escape(url, quote=True)}">{html.escape(label)}</a></li>'
        for n, (label, url) in enumerate(references, 1)
    )
    guide_toc = (
        '<li><a href="#next-steps">Where to Go Next</a></li>'
        '<li><a href="#guide-glossary">Glossary</a></li>'
        '<li><a href="#official-references">Official references</a></li>'
    )
    guide_html = (
        next_steps_markup(course, shared_guide_links())
        + course_glossary_markup(course)
        + '<section id="official-references"><h2>Official references</h2><ol>'
        + refs
        + "</ol></section>"
    )
    css = (ROOT / "tools/course.css").read_text(encoding="utf-8")
    return f"""{page_head(title, css)}
<body class="text-course"><a class="skip-link" href="#main">Skip to course</a>
<header><h1>{html.escape(title)}</h1><p class="guided-hours">Estimated guided hours: {metadata["estimated_guided_hours"]}</p></header>
<div class="course-layout"><aside><nav id="course-contents" aria-label="Course contents">{course_switcher(course.name, catalog_metadata())}<details open><summary>Table of contents</summary>
<ul class="section-links"><li><a href="#course-overview">Course overview</a></li></ul><h2>Lessons</h2><ol>{"".join(lesson_toc)}</ol>
<h2>Reference and further reading</h2><ul class="section-links">{guide_toc}</ul></details></nav></aside>
<main id="main"><section id="course-overview"><h2>Course overview</h2>{block(preamble)}</section>
{"".join(lesson_html)}{guide_html}{license_footer()}</main></div></body></html>"""


def render_shared_guide() -> str:
    text = shared_guide_source()
    # README-only navigation and attribution have their own HTML presentation.
    # Match only these exact boundaries so instructional prose is preserved.
    setup_heading = "## How to set up the lab\n\n"
    online_link = (
        "[Read this guide online]"
        "(https://nebius.github.io/nebius-ps-services/courses/lab-guide.html).\n\n"
    )
    text = text.replace(setup_heading + online_link, setup_heading, 1)
    text = text.removesuffix(
        "\n\n© 2026 Nebius B.V. Free educational material under "
        "[Apache License 2.0](../LICENSE)."
    )
    _, _, rest = text.partition("\n")
    chunks = re.split(r"^## (.+)\n", rest, flags=re.MULTILINE)
    links = {name + "/README.md": name + "/index.html" for name in COURSES}
    links["lab-guide.html"] = "#main"
    for name in COURSES:
        course = ROOT / name
        for heading in re.findall(
            r"^#{2,3} (.+)$", (course / "README.md").read_text(), re.M
        ):
            anchor = slug(heading)
            links[f"{name}/README.md#{anchor}"] = (
                f"{name}/index.html#guide-readme-{anchor}"
            )
        for source in executable_sources(course) if (course / "labs").is_dir() else ():
            links[f"{name}/reference/labs/{source.stem}.md"] = (
                f"{name}/index.html#lab-{slug(source.stem)}"
            )
    links["skills/run-labs/SKILL.md"] = (
        "https://github.com/nebius/nebius-ps-services/blob/main/courses/skills/run-labs/SKILL.md"
    )
    links["../LICENSE"] = "#license"
    body = block(chunks[0], links, source="README.md", images=True)
    toc = ""
    for heading, content in zip(chunks[1::2], chunks[2::2], strict=True):
        anchor = slug(heading)
        toc += f'<li><a href="#{anchor}">{html.escape(heading)}</a></li>'
        rendered = block(content, links, source="README.md", images=True)
        # Standalone guide headings retain their authored levels.
        rendered = re.sub(
            r"<(/?)h([5-6])(?=[ >])", lambda m: f"<{m[1]}h{int(m[2]) - 2}", rendered
        )
        body += f'<section id="{anchor}"><h2>{html.escape(heading)}</h2>{rendered}</section>'
    css = (ROOT / "tools/course.css").read_text(encoding="utf-8")
    return f"""{page_head(GUIDE_TITLE + ' | Performance Engineering Courses', css)}
<body class="text-course"><a class="skip-link" href="#main">Skip to guide</a><header><h1>{GUIDE_TITLE}</h1></header>
<div class="course-layout"><aside><nav aria-label="Lab guide contents">{course_switcher('lab-guide', catalog_metadata())}<details open><summary>Table of contents</summary><ul>{toc}</ul></details></nav></aside><main id="main">{body}{license_footer()}</main></div></body></html>"""


def render_catalog() -> str:
    metadata = catalog_metadata()
    cards = {}
    path_groups = []
    collection_groups = []
    for number, name in enumerate(CATALOG_ENTRIES, 1):
        guide = name == "lab-guide"
        title = html.escape(GUIDE_TITLE if guide else metadata[name]["title"])
        hours = "Shared guide" if guide else f'{metadata[name]["estimated_guided_hours"]} guided hours'
        eyebrow, introduction, outcomes, tags = CATALOG_COPY[name]
        prerequisite = (
            "Start here · Basic Linux knowledge; no cluster required"
            if name == "soperator"
            else "Use before running labs; no setup needed to read the courses"
            if guide
            else "Prerequisites: GPU profiling + the relevant training or inference course; 16 H100 GPUs"
            if name == "advanced-gpu-communication"
            else "No previous GPU course required"
            if name == "gpu-fundamentals"
            else "Prerequisite: GPU Fundamentals"
            if name == "gpu-optimizations"
            else "Prerequisites: Fundamentals + Optimization"
        )
        outcome_items = "".join(f"<li>{html.escape(item)}</li>" for item in outcomes)
        tag_items = "".join(f"<span>{html.escape(tag)}</span>" for tag in tags)
        cards[name] = f"""<article class="course-card resource-{name}" id="{name}">
<div class="card-top"><span class="course-number">{number:02d}</span><span class="course-hours">{hours}</span></div>
<p class="card-eyebrow">{html.escape(eyebrow)}</p><h3>{title}</h3>
<p class="course-description">{html.escape(introduction)}</p>
<ul class="course-outcomes">{outcome_items}</ul>
<div class="course-tags" aria-label="Topics">{tag_items}</div>
<div class="card-bottom"><p class="prerequisite">{prerequisite}</p>
<a class="course-link" href="{catalog_destination(name)}" aria-label="Read {title}">Read {'guide' if guide else 'course'} <span aria-hidden="true">↗</span></a></div>
</article>"""
    path_labels = {"soperator": "Soperator", "lab-guide": GUIDE_TITLE, "advanced-gpu-communication": "Advanced Labs"}
    for group_number, (label, description, grid_class, names) in enumerate(CATALOG_GROUPS, 1):
        path_items = []
        for name in names:
            number = CATALOG_ENTRIES.index(name) + 1
            title = html.escape(path_labels[name] if name in path_labels else metadata[name]["title"])
            purpose = html.escape(CATALOG_COPY[name][0])
            path_items.append(f'<li value="{number}"><span class="path-step" aria-hidden="true">{number:02d}</span><a href="#{name}">{title}<small>{purpose}</small></a></li>')
        path_groups.append(f'<p class="path-group-label">{html.escape(label)} · {html.escape(description)}</p><ol class="path-foundations path-group-{grid_class or "advanced"}" start="{CATALOG_ENTRIES.index(names[0]) + 1}">{"".join(path_items)}</ol>')
        collection_groups.append(f'<div class="collection-label"><span>{group_number:02d} / {html.escape(label)}</span><p>{html.escape(description)}</p></div><div class="course-grid {grid_class}">{"".join(cards[name] for name in names)}</div>')
    css = (ROOT / "tools/catalog.css").read_text(encoding="utf-8")
    lab_count = sum(len(item.get("labs", [])) for item in metadata.values())
    return f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="Six free Nebius courses, an Advanced Labs collection and a shared Lab Guide: explore Slurm, GPUs, language models and custom kernels.">
<title>GPU Performance Engineering Courses | Nebius</title><link rel="icon" href="data:,">
<style>{css}</style></head>
<body><a class="skip-link" href="#main">Skip to courses</a>
<div class="site-header"><a class="wordmark" href="../index.html" aria-label="Nebius Platform Services home">nebius<span>courses</span></a>
<nav aria-label="Main navigation"><a href="#catalog">Explore courses</a><a href="#learning-path">Learning path</a><a href="https://github.com/nebius/nebius-ps-services">GitHub <span aria-hidden="true">↗</span></a></nav></div>
<main id="main"><header class="hero">
<div class="hero-copy"><p class="eyebrow"><span class="status-dot" aria-hidden="true"></span> Learn the system. Understand the result.</p>
<h1>GPU Performance<br><span>Engineering</span></h1>
<p class="hero-description">Start with Slurm and Soperator, use the Lab Guide to prepare for practice, then build your GPU mental model and explore language models and custom kernels. Finish with advanced communication labs for sixteen H100 GPUs.</p>
<div class="hero-actions"><a class="button primary" href="soperator/index.html">Start with Soperator <span aria-hidden="true">↗</span></a><a class="button secondary" href="lab-guide.html">Set up and run the labs <span aria-hidden="true">→</span></a></div>
<p class="platform-note">GPU performance · Linux · Slurm</p></div>
<aside class="learning-map" id="learning-path" aria-labelledby="path-title">
<div class="map-heading"><p class="eyebrow">Your learning path</p><span class="map-count">8 resources</span></div>
<h2 id="path-title">A foundation.<br>Then your direction.</h2>
{"".join(path_groups)}
<p class="path-note">Soperator introduces the Slurm concepts used throughout the courses. The three specializations are independent. Start any of them after the two GPU foundation courses. Then use the <a href="#advanced-gpu-communication">advanced communication labs</a> when you have a sixteen-GPU cluster.</p></aside>
</header>
<div class="catalog-facts" aria-label="Catalog overview"><p><strong>{len(COURSES) - 1}</strong> courses</p><p><strong>1</strong> Advanced Labs collection</p><p><strong>1</strong> Lab Guide</p><p class="fact-note">{lab_count} practical labs.<br>Free to read.</p></div>
<section id="catalog" class="catalog-section" aria-labelledby="catalog-title"><div class="section-heading"><div><p class="eyebrow">The course collection</p><h2 id="catalog-title">Build understanding.<br>Put it to work.</h2></div><p>Begin with the foundations, then follow the questions that matter to your workload.</p></div>
{"".join(collection_groups)}</section>
<section class="approach" aria-labelledby="approach-title"><div><p class="eyebrow">From concepts to evidence</p><h2 id="approach-title">Learn. Practice. Review.</h2><p>The five GPU courses connect explanations to experiments. The Soperator introduction explains the cluster and commands through text examples. The advanced course develops communication skills through complete labs.</p></div>
<ol><li><span>01</span><div><h3>Learn the mechanism</h3><p>Start with definitions, mental models and diagrams.</p></div></li><li><span>02</span><div><h3>Work through the experiment</h3><p>Read the prerequisites, explore the code and run the supplied lab.</p></div></li><li><span>03</span><div><h3>Explain the evidence</h3><p>Check correctness, interpret measurements and decide what to investigate next.</p></div></li></ol></section>
<p class="environment-note">Read every course in your browser. The Soperator introduction requires no cluster. Run GPU-course labs in their documented Linux and Slurm environment with the specified hardware, dependencies and readiness checks.</p>
{license_footer()}</main></body></html>"""


def render_course(course_name: str) -> str:
    course = ROOT / course_name
    if course_name == "soperator":
        return render_text_course(course, course_metadata(course))
    if course_name == "advanced-gpu-communication":
        return render_lab_course(course, course_metadata(course))
    canonical_title, preamble, lessons = parse_course(course / "COURSE.md")
    for lesson in lessons:
        if not valid_lesson_fields(list(lesson)[1:]) or any(
            not value.strip() for value in lesson.values()
        ):
            raise ValueError(
                "every lesson needs Objective, How it works, Practice, Mental model and optional References in order"
            )
    metadata = course_metadata(course)
    advanced = set(metadata["advanced_lessons"])
    if any(n > len(lessons) for n in advanced):
        raise ValueError("advanced route refers to a missing lesson")
    lesson_sequence = [
        (n, item) for n, item in enumerate(lessons, 1) if n not in advanced
    ] + [(n, lessons[n - 1]) for n in metadata["advanced_lessons"]]
    title = metadata["title"]
    if canonical_title != title:
        raise ValueError("course title must match canonical metadata")
    visuals = parse_visuals(course / "reference/visual-plan.md")
    detailed = detailed_visuals(course, len(lessons))
    if any(visual.lesson > len(lessons) for visual in visuals):
        raise ValueError("overview diagram placement points to a missing lesson")
    authored_labs = lab_guides(course, metadata, len(lessons))
    external_labs = external_lab_guides(metadata, len(lessons))
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
    for number, lesson in enumerate(lessons, 1):
        source_anchor = "#" + markdown_heading_id(f"{number}. {lesson['title']}")
        target = "#" + slug(lesson["title"])
        reference_links[source_anchor] = target
        reference_links["../COURSE.md" + source_anchor] = target
    primer_html = performance_tools_markup(course, metadata, reference_links)
    reference_links[metadata["performance_tools"]] = "#using-gpu-performance-tools"
    reference_links.update(shared_guide_links())
    reference_links.update(
        {
            guide["guide_path"].relative_to(course).as_posix(): "#lab-"
            + slug(guide["source"].stem)
            for guide in authored_labs
        }
    )

    reference_links.update({item["reference"]: item["href"] for item in external_labs})

    def lesson_links(rows):
        return "".join(
            f'<li value="{number}"><a href="#{slug(item["title"])}">{html.escape(item["title"])}</a></li>'
            for number, item in rows
        )

    lesson_toc = lesson_links(
        [(n, item) for n, item in lesson_sequence if n not in advanced]
    )
    if advanced:
        lesson_toc += (
            '<li><a href="#advanced-fabric-track">Advanced: 16-H100 fabric</a><ul>'
            + lesson_links([(n, item) for n, item in lesson_sequence if n in advanced])
            + "</ul></li>"
        )
    advanced_labs = {
        guide["source"].stem
        for guide in authored_labs
        if all(n in advanced for n in guide["lessons"])
    }
    route_positions = {
        number: position for position, number in enumerate(metadata["advanced_lessons"])
    }
    authored_labs.sort(
        key=lambda guide: (
            guide["source"].stem in advanced_labs,
            min(route_positions.get(n, -1) for n in guide["lessons"])
            if guide["source"].stem in advanced_labs
            else -1,
        )
    )
    lab_toc = ""
    for route in (False, True):
        entries = [
            guide
            for guide in authored_labs
            if (guide["source"].stem in advanced_labs) == route
        ]
        if entries:
            lab_toc += "".join(
                f'<li><a href="#lab-{slug(guide["source"].stem)}">{html.escape(guide["title"])}</a></li>'
                for guide in entries
            )
    guide_labels = {
        "README.md": "Getting started",
        "GLOSSARY.md": "Glossary",
        "VERSIONS.md": "Versions and environment",
        "reference/benchmark-record.md": "Benchmark worksheet",
        "reference/lab-mechanisms.md": "Lab mechanisms and evidence",
        "reference/cluster-smoke-test.md": "Cluster smoke runbook",
        "reference/evidence-security.md": "Evidence and privacy",
        "reference/tooling-setup.md": "Diagnostic tooling reference",
    }
    guides_toc = "".join(
        f'<li><a href="#{guide_id(path)}">{guide_labels[path]}</a></li>'
        for path in guides
        if path != "GLOSSARY.md"
    )
    inline_figures: dict[int, dict[str, list[dict]]] = {}
    lab_figures: dict[str, dict[str, list[dict]]] = {}
    labs_by_stem = {guide["source"].stem: guide for guide in authored_labs}

    def place(
        home: str, lesson: int, after: str, path: str, title: str, markup: str
    ) -> None:
        if home == "lesson":
            if after not in lessons[lesson - 1]:
                raise ValueError("diagram placement points to a missing lesson field")
            destination = inline_figures.setdefault(lesson, {})
        else:
            stem = home.removeprefix("lab:")
            if stem not in labs_by_stem or lesson not in labs_by_stem[stem]["lessons"]:
                raise ValueError("diagram lab home must include its primary lesson")
            destination = lab_figures.setdefault(stem, {})
            if not path.startswith("#"):
                path = "../diagrams/" + Path(path).name
        destination.setdefault(after, []).append(
            dict(path=path, title=title, markup=markup)
        )

    for index, visual in enumerate(visuals, 1):
        place(
            visual.home,
            visual.lesson,
            visual.after,
            f"#diagram-{index}-{slug(visual.title)}",
            visual.title,
            diagram(visual, index),
        )
    for entry in detailed:
        place(
            entry["home"],
            entry["lessons"][0],
            entry["after"],
            entry["path"],
            entry["title"],
            detailed_diagram_markup(entry),
        )
    for number in range(1, len(lessons) + 1):
        if not inline_figures.get(number, {}).get("How it works"):
            raise ValueError(
                f"Lesson {number} needs a core diagram inside How it works"
            )
    advanced_heading = '<section id="advanced-fabric-track" class="route-heading"><h2>Advanced multi-GPU and multi-node performance</h2><p>Provision a separate two-node cluster with eight H100 GPUs per node (16 total). Require verified NVLink/NVSwitch inside each node and InfiniBand between nodes. The base two one-GPU workers use TCP/IP; their communication timings do not qualify fabric tuning. Some mechanics deliberately use two ranks on this advanced allocation.</p></section>'
    lesson_html = "\n".join(
        (
            advanced_heading
            if advanced and index == metadata["advanced_lessons"][0]
            else ""
        )
        + lesson_markup(
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
            [
                guide
                for guide in authored_labs + external_labs
                if index in guide["lessons"]
            ],
        )
        for index, item in lesson_sequence
    )
    labs_html = (
        "\n".join(
            lab_markup(
                course,
                guide,
                lessons,
                reference_links,
                lab_figures.get(guide["source"].stem),
            )
            for guide in authored_labs
        )
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
        if path != "GLOSSARY.md"
    )
    glossary_html = course_glossary_markup(course)
    next_steps_html = next_steps_markup(course, reference_links)
    refs_html = "\n".join(
        f'<li id="reference-{index}"><a href="{html.escape(url, quote=True)}">{html.escape(label)}</a></li>'
        for index, (label, url) in enumerate(references, 1)
    )
    css = (ROOT / "tools/course.css").read_text(encoding="utf-8")
    return f"""{page_head(title, css)}
<body><a class="skip-link" href="#main">Skip to course</a>
<header><h1>{html.escape(title)}</h1><p class="guided-hours">Estimated guided hours: {metadata["estimated_guided_hours"]}</p></header>
<div class="course-layout"><aside>
<nav id="course-contents" aria-label="Course contents">{course_switcher(course_name, catalog_metadata())}<details open><summary>Table of contents</summary>
<ul class="section-links"><li><a href="#course-overview">Course overview</a></li><li><a href="#course-contract">How to use this course</a></li></ul>
<h2>Lessons</h2><ul class="section-links"><li><a href="#using-gpu-performance-tools">Using GPU performance tools</a></li></ul><ol>{lesson_toc}</ol>
<h2>Practice and reference</h2><ul class="section-links"><li><a href="#labs">Practical labs</a><details><summary>Browse labs</summary><ul>{lab_toc}</ul></details></li>
<li><a href="#supporting-guides">Course guides</a><ul>{guides_toc}</ul></li><li><a href="#next-steps">Where to Go Next</a></li><li><a href="#guide-glossary">Glossary</a></li><li><a href="#official-references">Official references</a></li></ul>
</details></nav></aside><main id="main">
<section id="course-overview"><h2>Course overview</h2>{block(preamble, reference_links)}</section>
<section id="course-contract"><h2>How to use this course</h2><div class="course-contract"><div><h3>Learn</h3><p>Read the objective and explanation, then use the mental model to review the mechanism. Explain what the technique is, why it is useful and how it works before following the Practice links.</p></div><div><h3>Practice</h3><p>Complete the shared README setup once. Each practical lab explains its purpose and concepts, then gives the commands and comparison to make. Check the results before interpreting performance.</p></div><div><h3>Review</h3><p>Compare correctness, repeated measurements, and profiler observations. Use the glossary and detailed diagrams when a term or relationship needs a second look.</p><p>Use the benchmark worksheet to record what your environment demonstrates and what remains to be tested.</p></div></div>{extensions_html}</section>
{primer_html}
{lesson_html}
<section id="labs"><h2>Practical labs</h2>{course_downloads(course)}{labs_html}</section>
<section id="supporting-guides"><h2>Course guides</h2>{guides_html}</section>
{next_steps_html}
{glossary_html}
<section id="official-references"><h2>Official references</h2><ol>{refs_html}</ol></section>
{license_footer()}
</main></div></body></html>"""
