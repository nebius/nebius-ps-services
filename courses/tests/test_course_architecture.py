"""Publication boundaries for modular authoring and external course archives."""

import hashlib
import io
import json
import sys
import zipfile

import pytest

import course_archives as archives
from course_builder import build, markdown, visuals


@pytest.mark.parametrize("name", [name for name in build.COURSES if name not in ("soperator", "gpu-performance-tools")])
def test_practical_downloads_offer_results_and_setup_without_kit(name):
    import re

    from validate_course_template import VisibleText

    course = build.ROOT / name
    document = (course / "index.html").read_text()
    introduction = re.search(r'<div id="course-downloads">(.*?)</div>', document, re.DOTALL)[1]
    assert '<section id="labs"><h2>Practical labs</h2>' in document
    lab_intro = document.split('<section id="labs">', 1)[1].split('<article', 1)[0]
    assert 'class="shared-setup"' not in lab_intro
    assert "Find this dashboard" not in lab_intro
    assert "For a separately saved course" not in lab_intro
    assert '<h3>Download results</h3>' in introduction
    assert build.PageTargets(introduction.encode()).links == [
        f"reference/{name}-lab-results.zip", "../lab-guide.html"
    ]
    anchors = re.findall(r"<a\b([^>]+)>(.*?)</a>", introduction)
    assert [text for _, text in anchors] == [
        "Grafana dashboards, Small and Large results", "Lab setup guide"
    ]
    assert "download=" in anchors[0][0] and "download=" not in anchors[1][0]
    visible = VisibleText()
    visible.feed(introduction)
    assert " ".join("".join(visible.parts).split()) == (
        "Download results Download all lab results: Grafana dashboards, Small and Large results "
        "Setup the lab environment: Lab setup guide"
    )
    assert 'data-asset="lab-kit"' not in document
    assert not (course / "reference" / f"{name}-lab-kit.zip").exists()


@pytest.mark.parametrize(
    "target,allowed",
    [("../lab-guide.html", True), ("../lab-guide.html#unknown", False),
     ("../../lab-guide.html", False), ("../private.html", False)],
)
def test_validator_allows_only_the_declared_setup_route(target, allowed):
    from validate_course_template import Parser

    parser = Parser()
    parser.feed(f'<a href="{target}">Lab setup guide</a>')
    assert (not parser.errors) == allowed


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


@pytest.fixture
def course(tmp_path):
    course = tmp_path / "example"
    write_json(
        course / "reference/course.json",
        {
            "slug": "example",
            "labs": [
                {
                    "path": "labs/01_example.py",
                    "dashboard": "reference/grafana/01_example.json",
                }
            ],
        },
    )
    write_json(course / "reference/observability.json", {})
    for name in ("environment_readiness", "01_example"):
        write_json(course / "reference/grafana" / (name + ".json"), {"uid": name})
    source = course / "labs/01_example.py"
    source.parent.mkdir()
    source.write_text('#!/usr/bin/env python3\nprint("example")\n')
    source.chmod(0o755)
    for profile in ("small", "large"):
        base = course / "reference/lab-results/01_example" / profile
        base.mkdir(parents=True)
        content = b"metric,value\nduration,1\n"
        (base / "summary.csv").write_bytes(content)
        write_json(
            base / "manifest.json",
            {
                "schema": "course-lab-results/v1",
                "course": "example",
                "lab": "01_example",
                "profile": profile,
                "summary_sha256": hashlib.sha256(content).hexdigest(),
                "artifacts": [],
            },
        )
    return course


def test_resources_preserve_original_bytes_and_directory_contract(course):
    entries = archives.result_entries(course)
    data = archives.archive_bytes(entries)
    assert data == archives.archive_bytes(archives.result_entries(course))
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert set(archive.namelist()) == {
            "grafana-dashboards/01_example.json",
            "grafana-dashboards/environment_readiness.json",
            "small/01_example/manifest.json",
            "small/01_example/summary.csv",
            "large/01_example/manifest.json",
            "large/01_example/summary.csv",
        }
        for profile in ("small", "large"):
            base = course / "reference/lab-results/01_example" / profile
            for path in base.iterdir():
                assert (
                    archive.read(f"{profile}/01_example/{path.name}")
                    == path.read_bytes()
                )


@pytest.mark.parametrize(
    "defect", ["checksum", "identity", "duplicate", "traversal", "symlink", "dashboard"]
)
def test_invalid_resources_preserve_previous_archive(course, defect):
    output = archives.publish_results(course)
    before = output.read_bytes()
    base = course / "reference/lab-results/01_example/small"
    manifest = base / "manifest.json"
    data = json.loads(manifest.read_text())
    if defect == "checksum":
        (base / "summary.csv").write_text("changed")
    elif defect == "identity":
        data["course"] = "another-course"
    elif defect == "duplicate":
        data["artifacts"] = [{"file": "summary.csv", "sha256": data["summary_sha256"]}]
    elif defect == "traversal":
        data["artifacts"] = [{"file": "../secret.json", "sha256": "a" * 64}]
    elif defect == "symlink":
        (base / "summary.csv").unlink()
        (base / "summary.csv").symlink_to(base.parent / "large/summary.csv")
    else:
        (course / "reference/grafana/environment_readiness.json").unlink()
    write_json(manifest, data)
    with pytest.raises(ValueError):
        archives.publish_results(course)
    assert output.read_bytes() == before
    assert not list(output.parent.glob(".course-*"))


def test_canonical_results_archive_is_not_retired_on_repeated_builds(course, monkeypatch):
    configure_build(monkeypatch, course.parent, course)
    build.main()
    output = course / "reference/example-lab-results.zip"
    before = (output.read_bytes(), output.stat().st_mtime_ns)
    build.main()
    assert before == (output.read_bytes(), output.stat().st_mtime_ns)
    assert not (course / "reference/example-lab-resources.zip").exists()


def configure_build(monkeypatch, root, course):
    monkeypatch.setattr(build, "ROOT", root)
    monkeypatch.setattr(build, "publication_preflight", lambda outputs: {})
    monkeypatch.setattr(build, "report_publication", lambda report, outputs: None)
    monkeypatch.setattr(build, "COURSES", ("example",))
    monkeypatch.setattr(build, "render_catalog", lambda: "catalog")
    monkeypatch.setattr(build, "render_shared_guide", lambda: "guide")
    monkeypatch.setattr(build, "render_course", lambda name: "course")
    monkeypatch.setattr(
        build,
        "course_metadata",
        lambda path: json.loads((course / "reference/course.json").read_text()),
    )
    monkeypatch.setattr(sys, "argv", ["build_course_html.py"])


def test_build_check_is_read_only_and_detects_archive_drift(course, monkeypatch):
    configure_build(monkeypatch, course.parent, course)
    build.main()
    assert not (course / "reference/example-lab-kit.zip").exists()
    paths = [p for p in course.parent.rglob("*") if p.is_file()]
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in paths}
    monkeypatch.setattr(sys, "argv", ["build_course_html.py", "--check"])
    build.main()
    assert before == {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in paths}
    (course / "reference/example-lab-results.zip").write_bytes(b"stale")
    with pytest.raises(SystemExit, match="stale or missing generated archive"):
        build.main()
    assert (course / "reference/example-lab-results.zip").read_bytes() == b"stale"


def test_late_preflight_error_leaves_all_outputs_unchanged(course, monkeypatch):
    configure_build(monkeypatch, course.parent, course)
    build.main()
    paths = [
        course.parent / "index.html",
        course.parent / "lab-guide.html",
        course / "index.html",
        *course.glob("reference/*.zip"),
    ]
    before = {p: p.read_bytes() for p in paths}
    monkeypatch.setattr(build, "render_catalog", lambda: "changed catalog")
    (course / "reference/lab-results/01_example/large/summary.csv").write_text(
        "corrupt final input"
    )
    with pytest.raises(SystemExit, match="differs from its manifest"):
        build.main()
    assert before == {p: p.read_bytes() for p in paths}


def test_failed_atomic_replace_keeps_destination_and_cleans_temporary(
    course, monkeypatch
):
    path = course / "index.html"
    path.write_bytes(b"previous")

    def fail(*args):
        raise OSError("replace failed")

    monkeypatch.setattr(archives.os, "replace", fail)
    with pytest.raises(OSError, match="replace failed"):
        archives.write_atomic(path, b"new")
    assert path.read_bytes() == b"previous"
    assert not list(course.glob(".course-*"))


@pytest.mark.parametrize(
    "source",
    ["- Parent\n  - Child", "1. Parent\n   1. Child", "*emphasis*", "_emphasis_"],
)
def test_unsupported_markdown_fails_instead_of_losing_structure(source):
    with pytest.raises(ValueError):
        markdown.block(source)


def test_markdown_code_and_source_scoped_plain_text():
    assert "<code>*literal*</code>" in markdown.block("`*literal*`")
    assert "  - literal" in markdown.block("```text\n  - literal\n```")
    assert (
        markdown.inline(
            "[route](SYLLABUS.md)", source="advanced-gpu-communication/README.md"
        )
        == "route"
    )
    with pytest.raises(ValueError, match="unresolved"):
        markdown.inline("[route](SYLLABUS.md)", source="another/README.md")


@pytest.mark.parametrize(
    "mutation", ["script", "event", "external", "style", "dtd", "symlink"]
)
def test_passive_svg_rejects_active_content_for_every_owner(tmp_path, mutation):
    path = tmp_path / "figure.svg"
    svg = '<svg role="img" viewBox="0 0 10 10" aria-labelledby="fig-title fig-desc"><title id="fig-title">Title</title><desc id="fig-desc">Description</desc><rect width="10" height="10"/></svg>'
    replacements = {
        "script": ("</svg>", "<script>alert(1)</script></svg>"),
        "event": ("<rect ", '<rect onload="alert(1)" '),
        "external": ("<rect ", '<rect fill="url(https://example.invalid/image)" '),
        "style": (
            "<rect ",
            '<rect style="background:url(https://example.invalid/image)" ',
        ),
        "dtd": ("<svg ", "<!DOCTYPE svg><svg "),
    }
    if mutation in replacements:
        svg = svg.replace(*replacements[mutation])
    path.write_text(svg)
    if mutation == "symlink":
        real = tmp_path / "real.svg"
        path.rename(real)
        path.symlink_to(real)
    with pytest.raises(ValueError):
        visuals.passive_svg(tmp_path, path)


@pytest.mark.parametrize(
    "document",
    [b'<p id="same"></p><p id="same"></p>', b'<a href="#missing">Broken</a>'],
)
def test_generated_targets_fail_preflight(course, monkeypatch, document):
    configure_build(monkeypatch, course.parent, course)
    build.main()
    before = (course.parent / "index.html").read_bytes()
    monkeypatch.setattr(build, "render_catalog", lambda: document.decode())
    with pytest.raises(
        SystemExit, match="duplicate generated ID|missing generated fragment"
    ):
        build.main()
    assert (course.parent / "index.html").read_bytes() == before


def test_standalone_validator_checks_resources_even_before_results_exist(
    course, monkeypatch
):
    import validate_course_template as validator

    (course / "reference/lab-results").rename(course.parent / "saved-results")
    output = archives.publish_results(course)
    monkeypatch.setattr(validator, "ROOT", course)
    validator.validate_student_results()
    with zipfile.ZipFile(output, "a") as archive:
        archive.writestr("unowned.json", "{}")
    with pytest.raises(SystemExit, match="unowned"):
        validator.validate_student_results()
