"""Current-edition publication, accessible light palette and content integrity."""

import json
import re

import pytest

from test_course_content_contract import COURSES, ROOT, load_builder


@pytest.mark.parametrize("course", COURSES)
def test_publication_has_no_history_or_time_breakdown(course: str) -> None:
    root = ROOT / course
    document = (root / "index.html").read_text()
    for phrase in (
        "Guided learning plan",
        "core lab practice =",
        "rounded up to",
        "Lab time allocations",
        "Previous material map",
        "Topic coverage",
        "data-retained-source",
        "previous courses",
        "combined-course",
    ):
        assert phrase not in document
    assert not list((root / "learning-records").glob("*.md"))
    for obsolete in (
        "learning-plan.json",
        "retained-visuals.json",
        "source-coverage.md",
        "prior-content-retention.md",
    ):
        assert not (root / "reference" / obsolete).exists()
    assert document.index('id="official-references"') > document.index('id="labs"')


@pytest.mark.parametrize("course", COURSES)
def test_neutral_metadata_preserves_core_and_optional_lab_scope(course: str) -> None:
    root = ROOT / course
    metadata = json.loads((root / "reference/course.json").read_text())
    assert set(metadata) == {
        "slug",
        "title",
        "estimated_guided_hours",
        "labs",
        "extensions",
    }
    assert isinstance(metadata["estimated_guided_hours"], int)
    builder = load_builder()
    assert {item["path"] for item in metadata["labs"]} == {
        str(path.relative_to(root)) for path in builder.executable_sources(root)
    }
    assert all(
        set(item) == {"path", "optional", "lessons"} for item in metadata["labs"]
    )
    manifest = json.loads((root / "reference/visual-manifest.json").read_text())
    assert set(manifest) == {"diagrams"}
    assert all(
        set(item) == {"path", "title", "lessons", "after", "home"}
        for item in manifest["diagrams"]
    )


def test_light_palette_contrast_and_responsive_reading_rules() -> None:
    css = (ROOT / "tools/course.css").read_text()
    colors = dict(re.findall(r"--([a-z-]+): (#[0-9a-f]{6});", css))

    def luminance(color: str) -> float:
        channels = [int(color[n : n + 2], 16) / 255 for n in (1, 3, 5)]
        linear = [
            v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
            for v in channels
        ]
        return sum(a * b for a, b in zip(linear, (0.2126, 0.7152, 0.0722)))

    for foreground in ("ink", "muted", "blue", "teal", "amber"):
        for background in ("paper", "card", "blue-wash", "mint-wash", "amber-wash"):
            assert (luminance(colors[background]) + 0.05) / (
                luminance(colors[foreground]) + 0.05
            ) >= 4.5
    assert "--reading-width: 88ch" in css
    assert "position: sticky" in css
    assert "@media (max-width: 600px)" in css
    assert "prefers-reduced-motion" in css
    assert ":focus-visible" in css
    assert "@import" not in css
    # SVG groups deliberately carry white labels on darker semantic shapes.
    # A global text-fill rule would override their inherited foreground.
    assert not re.search(r"svg\s+text\s*\{[^}]*fill\s*:", css)


def test_all_course_lessons_labs_and_diagrams_remain_published() -> None:
    builder = load_builder()
    counts = {
        "gpu-fundamentals": (12, 13, 27),
        "gpu-optimizations": (13, 19, 28),
        "llm-training": (16, 24, 22),
        "llm-inference": (16, 25, 28),
        "custom-cuda-kernels": (16, 13, 17),
    }
    for course, expected in counts.items():
        root = ROOT / course
        page = (root / "index.html").read_text()
        assert (
            len(builder.parse_course(root / "COURSE.md")[2]),
            len(builder.executable_sources(root)),
            len(re.findall(r"<svg\b", page)),
        ) == expected


def test_tooling_preflight_has_only_current_general_gpu_tools() -> None:
    root = ROOT / "gpu-optimizations"
    for relative in (
        "reference/cluster-smoke-test.md",
        "reference/tooling-setup.md",
        "reference/diagrams/tool-scope-from-service-objective-to-selected-kernel.svg",
        "slurm/tooling_preflight.sbatch",
    ):
        assert "genai-perf" not in (root / relative).read_text().lower()
