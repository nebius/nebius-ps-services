"""Text-only publication is explicit and does not weaken the GPU contract."""

from course_builder import build as cb_build, config as cb_config, markdown as cb_markdown, metadata as cb_metadata, pages as cb_pages
import json
import re
import shutil
import subprocess

import pytest
from test_course_content_contract import ROOT


def test_text_course_has_complete_canonical_prose_and_no_runtime_assets():
    course = ROOT / "soperator"
    document = cb_pages.render_course("soperator")
    assert document == (course / "index.html").read_text()
    title, preamble, lessons = cb_metadata.parse_text_course(course / "COURSE.md")
    assert title == cb_config.TEXT_TITLE
    assert cb_markdown.block(preamble) in document
    links = {
        "NEXT-STEPS.md": "#next-steps",
        **{
            f"#{n}-{cb_markdown.slug(item['title'])}": f"#lesson-{n}-{cb_markdown.slug(item['title'])}"
            for n, item in enumerate(lessons, 1)
        },
    }
    for number, lesson in enumerate(lessons, 1):
        assert (
            f'id="lesson-{number}-{cb_markdown.slug(lesson["title"])}"' in document
        )
        for field in cb_config.TEXT_FIELDS:
            rendered = cb_markdown.block(lesson[field], links, heading_offset=0)
            assert rendered in document
    assert len(lessons) == 6
    assert document.count('<section class="lesson"') == 6
    for forbidden in (
        "<svg",
        'id="labs"',
        'data-asset="lab-kit"',
        'id="using-gpu-performance-tools"',
    ):
        assert forbidden not in document
    for forbidden in (
        "labs",
        "slurm",
        "env",
        "reference/grafana",
        "reference/setup.md",
    ):
        assert not (course / forbidden).exists()


@pytest.mark.parametrize(
    "mutation", ["extra", "profile", "id", "title", "hours", "labs"]
)
def test_text_metadata_rejects_malformed_contract(tmp_path, mutation):
    course = tmp_path / "soperator"
    (course / "reference").mkdir(parents=True)
    data = json.loads((ROOT / "soperator/reference/course.json").read_text())
    if mutation == "extra":
        data["unexpected"] = True
    elif mutation == "profile":
        data["profile"] = "gpu"
    elif mutation == "id":
        data["lessons"][0]["id"] = 2
    elif mutation == "title":
        data["title"] = "Wrong title"
    elif mutation == "hours":
        data["estimated_guided_hours"] = True
    else:
        data["labs"] = []
    (course / "reference/course.json").write_text(json.dumps(data))
    with pytest.raises(ValueError):
        cb_metadata.course_metadata(course)


def test_text_profile_cannot_bypass_gpu_metadata(tmp_path):
    course = tmp_path / "gpu-fundamentals"
    (course / "reference").mkdir(parents=True)
    data = json.loads((ROOT / "soperator/reference/course.json").read_text())
    data["slug"] = "gpu-fundamentals"
    (course / "reference/course.json").write_text(json.dumps(data))
    with pytest.raises(ValueError, match="publication schema"):
        cb_metadata.course_metadata(course)


@pytest.mark.parametrize(
    "change", ["missing", "reordered", "empty", "number", "duplicate"]
)
def test_text_parser_rejects_invalid_lesson_structure(tmp_path, change):
    text = (ROOT / "soperator/COURSE.md").read_text()
    if change == "missing":
        text = text.replace("### Practice", "### Unspecified", 1)
    elif change == "reordered":
        text = text.replace("### Objective", "### Mental model", 1)
    elif change == "empty":
        text = re.sub(
            r"### Objective\n.*?\n### How",
            "### Objective\n\n### How",
            text,
            count=1,
            flags=re.DOTALL,
        )
    elif change == "number":
        text = text.replace("## 2.", "## 3.", 1)
    else:
        text = text.replace(
            "### How it works",
            "### Objective\n\nDuplicate\n\n### How it works",
            1,
        )
    path = tmp_path / "COURSE.md"
    path.write_text(text)
    with pytest.raises(ValueError):
        cb_metadata.parse_text_course(path)


def test_text_renderer_rejects_mismatched_lesson_identity(
    tmp_path, monkeypatch
):
    shutil.copytree(ROOT / "soperator", tmp_path / "soperator")
    path = tmp_path / "soperator/reference/course.json"
    data = json.loads(path.read_text())
    data["lessons"][0]["title"] = "Different lesson"
    path.write_text(json.dumps(data))
    monkeypatch.setattr(cb_build, "ROOT", tmp_path)
    monkeypatch.setattr(cb_pages, "ROOT", tmp_path)
    monkeypatch.setattr(cb_markdown, "ROOT", tmp_path)
    monkeypatch.setattr(cb_metadata, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="lesson identities"):
        cb_pages.render_course("soperator")


def test_every_command_example_has_valid_shell_syntax():
    text = (ROOT / "soperator/COURSE.md").read_text()
    for command in re.findall(r"```bash\n(.*?)\n```", text, re.DOTALL):
        result = subprocess.run(
            ["bash", "-n"],
            input=command,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr


def test_shared_runtime_distribution_stays_gpu_only():
    import importlib.util

    path = ROOT / "tools/sync_course_tools.py"
    spec = importlib.util.spec_from_file_location("text_test_sync_tools", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert "soperator" not in module.COURSES


@pytest.fixture
def text_validator(monkeypatch):
    import importlib.util

    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    spec = importlib.util.spec_from_file_location(
        "text_course_validator", ROOT / "tools/validate_text_course.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "extra",
    [
        "<script>alert(1)</script>",
        '<a href="#missing-anchor">Missing</a>',
        '<a href="https://github.com/nebius/soperator-elsewhere">Unrelated</a>',
        '<a href="https://github.com/nebius/nebius-solutions-library-elsewhere">Unrelated</a>',
        '<a href="https://kubernetes.io.example.org/docs/">Unrelated</a>',
        '<a href="https://kubernetes.io/blog/">Outside documentation</a>',
        '<a href="../soperator/index.html">Outside navigation</a>',
        '<a href="../lab-guide.html#unknown">Unknown guide section</a>',
        '<a href="../../lab-guide.html#how-to-set-up-the-lab">Wrong guide path</a>',
        '<div id="main">Duplicate</div>',
        "<div><p>Unbalanced</div>",
    ],
)
def test_text_validator_rejects_unsafe_or_broken_publication(
    text_validator, extra
):
    document = (ROOT / "soperator/index.html").read_text()
    with pytest.raises(ValueError):
        text_validator.validate_document(
            document.replace("</main>", extra + "</main>")
        )


def test_text_validator_checks_current_identity(text_validator):
    document = (ROOT / "soperator/index.html").read_text()
    text_validator.validate_document(document)
    with pytest.raises(ValueError, match="navigation"):
        text_validator.validate_document(
            document.replace(
                'data-course="soperator"', 'data-course="gpu-fundamentals"'
            )
        )


@pytest.mark.parametrize("defect", ["reorder", "missing-guide", "duplicate-guide", "outside-menu", "extra-fragment"])
def test_text_navigation_rejects_broken_resource_menus(text_validator, defect):
    document = (ROOT / "soperator/index.html").read_text()
    match = re.search(r'<details class="course-switcher">.*?<ul>(.*?)</ul>', document, re.S)
    items = re.findall(r'<li>.*?</li>', match.group(1), re.S)
    extra = ""
    if defect == "reorder":
        items[0], items[1] = items[1], items[0]
    elif defect == "missing-guide":
        items.pop(1)
    elif defect == "duplicate-guide":
        items.insert(1, items[1])
    elif defect == "outside-menu":
        extra = items.pop(1)
    else:
        items.append('<li><a href="#main">Extra menu item</a></li>')
    document = document[:match.start(1)] + ''.join(items) + document[match.end(1):]
    if extra:
        document = document.replace('</nav>', extra + '</nav>', 1)
    with pytest.raises(ValueError, match="navigation"):
        text_validator.validate_document(document)


def test_text_next_steps_link_to_shared_setup(text_validator):
    document = cb_pages.render_course("soperator")
    assert "Lab 00" not in document
    assert (
        'href="../lab-guide.html#lab-preparation-scripts">shared environment setup</a>'
        in document
    )
    text_validator.validate_document(document)


def test_text_validator_rejects_stale_source(
    tmp_path, monkeypatch, text_validator
):
    shutil.copytree(ROOT / "soperator", tmp_path / "soperator")
    course = tmp_path / "soperator"
    source = course / "COURSE.md"
    source.write_text(
        source.read_text().replace(
            "### Objective\n", "### Objective\n\nNew canonical teaching.\n", 1
        )
    )
    monkeypatch.setattr(text_validator.cb_config, "ROOT", tmp_path)
    metadata = cb_metadata.catalog_metadata()
    monkeypatch.setattr(cb_build, "ROOT", tmp_path)
    monkeypatch.setattr(cb_pages, "ROOT", tmp_path)
    monkeypatch.setattr(cb_markdown, "ROOT", tmp_path)
    monkeypatch.setattr(cb_metadata, "ROOT", tmp_path)
    monkeypatch.setattr(cb_pages, "catalog_metadata", lambda: metadata)
    (tmp_path / "tools").mkdir()
    shutil.copyfile(ROOT / "tools/course.css", tmp_path / "tools/course.css")
    with pytest.raises(ValueError, match="stale text course"):
        text_validator.validate()
