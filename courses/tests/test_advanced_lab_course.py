"""The advanced route owns runnable experiments without artificial lessons."""

from course_builder import config as cb_config, content as cb_content, metadata as cb_metadata, pages as cb_pages
import json
from pathlib import Path

import pytest
from test_course_content_contract import COURSES, ROOT

ADVANCED = "advanced-gpu-communication"


def test_labs_only_profile_is_explicit_and_last():
    assert cb_config.COURSES[-1] == ADVANCED
    metadata = cb_metadata.course_metadata(ROOT / ADVANCED)
    assert metadata["profile"] == "labs-only"
    assert metadata["lessons"] == []
    assert len(metadata["labs"]) == 34
    assert len({Path(item["path"]).stem for item in metadata["labs"]}) == 34


def test_lab_profile_has_complete_guides_and_no_conceptual_placeholders():
    document = cb_pages.render_course(ADVANCED)
    assert '<section class="lesson"' not in document
    assert document.count('data-lab-guide="complete"') == 34
    assert 'data-setup-guide="complete"' not in document
    assert 'href="../lab-guide.html"' in document
    assert 'href="#course-downloads"' in document
    assert 'data-asset="lab-results"' in document
    assert 'data-asset="lab-kit"' not in document
    assert document == (ROOT / ADVANCED / "index.html").read_text()


@pytest.mark.parametrize("defect", ["lessons", "profile", "path", "hours"])
def test_labs_metadata_rejects_invalid_contract(tmp_path, defect):
    course = tmp_path / ADVANCED
    (course / "reference").mkdir(parents=True)
    data = json.loads((ROOT / ADVANCED / "reference/course.json").read_text())
    (course / "labs").mkdir()
    for row in data["labs"]:
        (course / row["path"]).write_text("# valid inventory fixture\n")
    (course / "reference/course.json").write_text(json.dumps(data))
    assert cb_metadata.course_metadata(course)["profile"] == "labs-only"
    if defect == "lessons":
        data["lessons"] = [{"id": 1, "title": "Fake lesson"}]
    elif defect == "profile":
        data["profile"] = "text-only"
    elif defect == "path":
        data["labs"][0]["path"] = "../escape.py"
    else:
        data["estimated_guided_hours"] = True
    (course / "reference/course.json").write_text(json.dumps(data))
    with pytest.raises(ValueError):
        cb_metadata.course_metadata(course)


def test_source_vendor_wrapper_remains_executable():
    wrapper = ROOT / ADVANCED / "env/nixlbench"
    assert wrapper.is_file() and not wrapper.is_symlink()
    assert wrapper.stat().st_mode & 0o111 == 0o111


def test_all_migrated_practice_has_exactly_one_source_owner():
    seen = set()
    for name in COURSES:
        metadata = cb_metadata.course_metadata(ROOT / name)
        for external in metadata["external_labs"]:
            assert external["path"] not in seen
            seen.add(external["path"])
            assert (ROOT / ADVANCED / external["path"]).is_file()
            guide = (
                ROOT
                / ADVANCED
                / "reference/labs"
                / (Path(external["path"]).stem + ".md")
            )
            assert guide.read_text().splitlines()[0] == "# " + external["title"]
    migrated = {
        Path(row["path"]).stem
        for row in cb_metadata.course_metadata(ROOT / ADVANCED)["labs"]
        if int(Path(row["path"]).name[:2]) <= 25
    }
    assert migrated <= {Path(path).stem for path in seen}
    assert len(seen) == 28  # 25 moved activities plus the three Dynamo serving labs.


def test_serving_lesson_connects_to_its_distributed_experiments():
    metadata = cb_metadata.course_metadata(ROOT / "llm-inference")
    assert {
        Path(row["path"]).stem
        for row in metadata["external_labs"]
        if 15 in row["lessons"]
    } == {"32_dynamo_disaggregation", "33_dynamo_routing", "34_serving_goodput"}


def test_supporting_guide_resolves_advanced_link_from_its_markdown_directory():
    for name in (
        "gpu-fundamentals",
        "gpu-optimizations",
        "llm-training",
        "llm-inference",
    ):
        relative = "reference/cluster-smoke-test.md"
        source = (ROOT / name / relative).read_text()
        assert "(../../advanced-gpu-communication/index.html)" in source
        rendered = cb_content.guide_markup(
            ROOT / name,
            relative,
            cb_content.shared_guide_links(),
            (
                relative,
                "VERSIONS.md",
                "reference/benchmark-record.md",
                "reference/evidence-security.md",
                "reference/lab-mechanisms.md",
            ),
        )
        assert 'href="../advanced-gpu-communication/index.html"' in rendered
        assert 'href="../lab-guide.html#lab-preparation-scripts"' in rendered
