"""Learners have one setup route and a catalog-complete preparation lookup."""

import re

import pytest

from course_bootstrap import catalog, selection
from course_builder.config import COURSES, ROOT
from test_practice_integration import load_validator


GUIDES = sorted(ROOT.glob("*/reference/labs/*.md"))
REFERRAL = "[Lab Guide](../../../lab-guide.html#lab-preparation)"


def test_unified_preparation_covers_every_runtime_binding():
    source = (ROOT / "README.md").read_text()
    _, courses = catalog.discover(ROOT / "tools/lab-setup.py")
    definitions = catalog.load_catalog()
    plan = selection.resolve(courses, definitions)
    labs, launchers = selection.inventory(courses, definitions)
    assert set(plan["labs"]) == set(labs)
    assert set(launchers.values()) <= set(plan["runtimes"])
    assert len(labs) == len(GUIDES)
    assert 'python3.12 "$HOME/courses/tools/lab-setup.py"' in source
    assert "Reading courses have no executable labs" in source
    assert "Container exercises remain optional activities" in source
    assert "included in default preparation" in source
    assert not re.search(r"(?:regular|cuda|communication|serving|transformer-engine)-lab-setup", source)


@pytest.mark.parametrize("path", GUIDES, ids=lambda p: str(p.relative_to(ROOT)))
def test_preparation_is_shared_without_repeated_lab_referrals(path):
    validator = load_validator()
    validator.validate_preparation_referral(path.read_text())
    page = (path.parents[2] / "index.html").read_text()
    anchor = "lab-" + path.stem.replace("_", "-")
    article = re.search(rf'<article class="lab" id="{anchor}".*?</article>', page, re.S)
    assert article, path
    assert article[0].count('href="../lab-guide.html#lab-preparation"') == 0


@pytest.mark.parametrize(
    "extra",
    [
        REFERRAL,
        "[Another setup link](../../../lab-guide.html)",
        '`python3.12 "$HOME/courses/tools/lab-setup.py"`',
        "```bash\npython3 tools/course_setup.py\n```",
        "```bash\npip install torch\n```",
        "```bash\npython3.12 -m venv .venv\n```",
    ],
)
def test_validator_rejects_a_second_preparation_path(extra):
    guide = f"## Before you start\n\n{REFERRAL}\n\n## Practice\n\n{extra}\n"
    with pytest.raises(SystemExit):
        load_validator().validate_preparation_referral(guide)


def test_validator_requires_referral_in_prerequisites():
    with pytest.raises(SystemExit):
        load_validator().validate_preparation_referral(
            f"## Before you start\n\nOne GPU.\n\n## Practice\n\n{REFERRAL}\n"
        )


@pytest.mark.parametrize("course", COURSES)
def test_current_teaching_has_no_separate_installer_commands(course):
    for path in (ROOT / course).rglob("*.md"):
        if path.name == "PUBLICATION-REVIEW.md" or any(
            part in path.parts for part in (".venv", ".runtime", "results", "lab-results", "tools")
        ):
            continue
        source = path.read_text()
        assert not re.search(
            r"(?:[\w-]+-)?lab-setup\.py|course_setup\.py|install-vendor-candidates\.sh|vendor-environment\.sh",
            source,
        ), path
        preparation_links = re.findall(
            r"\[[^\]]+\]\([^)]*lab-guide\.html#lab-preparation\)", source
        )
        assert len(preparation_links) <= 1, path
