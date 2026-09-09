"""Deterministic template/checker coverage; no model or browser claims."""

from __future__ import annotations

import html
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "course_check", ROOT / "scripts/check_course.py"
)
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


def lesson_fixture(number: int = 1) -> str:
    lesson = (ROOT / "assets/lesson-fragment.html").read_text()
    values = {
        "LESSON_ID": f"lesson-{number:02}",
        "LESSON_NUMBER": str(number),
        "LESSON_TITLE": "Process inputs and results",
        "OBJECTIVE_HTML": "<p>Trace an input through a process to its result.</p>",
        "CONNECTED_EXPLANATION_HTML": (
            "<p>A process transforms inputs into a result. A comparison needs "
            "a stated baseline and the same result criterion.</p>"
        ),
        "FIGURE_ID": f"figure-workflow-{number}",
        "INLINE_SVG_WITH_UNIQUE_IDS": (ROOT / "assets/diagram-example.svg")
        .read_text()
        .replace("workflow-", "workflow-" if number == 1 else f"workflow-{number}-"),
        "CAPTION_WITH_ESSENTIAL_MEANING": (
            "Inputs become a checked result; arrows show processing order."
        ),
        "PRACTICE_HTML": '<p><a href="#lab-01">Lab 01: Compare two cases</a></p>',
        "MENTAL_MODEL_HTML": "<p>An input is transformed, then checked.</p>",
    }
    for key, value in values.items():
        lesson = lesson.replace("{{" + key + "}}", value)
    if "{{" in lesson:
        raise AssertionError("Unfilled lesson slot")
    return lesson


def fixture() -> str:
    shell = (ROOT / "assets/textbook-shell.html").read_text()
    values = {
        "COURSE_TITLE": "Reasoning from observations",
        "GUIDED_HOURS": "2",
        "INLINE_CSS": (ROOT / "assets/styles.css").read_text(),
        "LESSON_TOC": '<li><a href="#lesson-01">1. Process inputs and results</a></li>',
        "LAB_TOC": '<li><a href="#lab-01">Lab 01: Compare two cases</a></li>',
        "MISSION_HTML": "<p>Describe a process before choosing a change.</p>",
        "SYLLABUS_HTML": "<p>Observe, explain, then compare.</p>",
        "LESSONS_HTML": lesson_fixture(),
        "LABS_HTML": (
            '<article class="lab" id="lab-01"><h3>Lab 01: Compare two cases</h3>'
            "<p>Compare a baseline with one changed condition and explain the "
            "difference using the same acceptance rule.</p>"
            + "".join(
                f"<h4>{h}</h4><p>A concrete activity step.</p>"
                for h in CHECK.GUIDE_HEADINGS
            )
            + "</article>"
        ),
        "GLOSSARY_HTML": "<p>Baseline: the declared reference condition.</p>",
        "NEXT_STEPS_HTML": "<p>Study how additional observations test an explanation.</p>",
        "RESOURCES_HTML": '<p><a href="https://www.w3.org/TR/WCAG22/">Accessibility standard</a></p>',
    }
    for key, value in values.items():
        shell = shell.replace("{{" + key + "}}", value)
    if "{{" in shell:
        raise AssertionError("Unfilled fixture slot")
    return shell


class CourseAssets(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.page = self.root / "index.html"
        self.manifest = self.root / "sources.json"

    def run_check(self, page: str, sources: dict[str, str] | None = None) -> list[str]:
        sources = sources or {}
        self.page.write_text(page, encoding="utf-8", newline="")
        self.manifest.write_text(json.dumps(list(sources)), encoding="utf-8")
        for path, text in sources.items():
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8", newline="")
        return CHECK.check(self.page, self.root, self.manifest)

    def test_shell_and_inline_svg(self) -> None:
        self.assertEqual(self.run_check(fixture()), [])

    def test_bare_attributes_fail_without_crashing(self) -> None:
        for original, replacement in (
            ('class="skip-link"', "class"),
            ('content="width=device-width, initial-scale=1"', "content"),
            ('aria-labelledby="workflow-title workflow-desc"', "aria-labelledby"),
        ):
            with self.subTest(attribute=replacement):
                self.assertTrue(
                    self.run_check(fixture().replace(original, replacement))
                )

    def test_cli_reports_malformed_html_without_traceback(self) -> None:
        self.manifest.write_text("[]")
        for old, new, diagnostic in (
            ('class="skip-link"', "class", "Missing responsive viewport"),
            (
                "https://www.w3.org/TR/WCAG22/",
                "https://[",
                "Invalid reference URL",
            ),
            (
                'href="https://www.w3.org/TR/WCAG22/"',
                'href="https://www.w3.org/TR/WCAG22/" attributionsrc',
                "Active or external-loading attribute",
            ),
        ):
            with self.subTest(case=diagnostic):
                self.page.write_text(fixture().replace(old, new))
                result = subprocess.run(
                    [
                        sys.executable,
                        "-B",
                        str(ROOT / "scripts/check_course.py"),
                        str(self.page),
                        "--course-root",
                        str(self.root),
                        "--sources-manifest",
                        str(self.manifest),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                self.assertEqual(result.returncode, 1)
                self.assertIn("FAIL:", result.stdout)
                self.assertIn(diagnostic, result.stdout)
                self.assertNotIn("Traceback", result.stderr)

    def test_cli_help_needs_no_inputs_and_creates_no_files(self) -> None:
        result = subprocess.run(
            [sys.executable, "-B", str(ROOT / "scripts/check_course.py"), "--help"],
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("--sources-manifest", result.stdout)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_cli_success_and_incomplete_flags(self) -> None:
        self.run_check(fixture())
        command = [
            sys.executable,
            "-B",
            str(ROOT / "scripts/check_course.py"),
            str(self.page),
            "--course-root",
            str(self.root),
            "--sources-manifest",
            str(self.manifest),
        ]
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=10, check=False
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("STATIC_PASS:", result.stdout)
        command[4] = "--course-ro"
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=10, check=False
        )
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("Traceback", result.stderr)

    def test_guide_has_all_seven_roles_in_order(self) -> None:
        guide = (
            ROOT / "assets/course-workspace-template/reference/labs/lab-01.md"
        ).read_text()
        headings = [line[3:] for line in guide.splitlines() if line.startswith("## ")]
        self.assertEqual(headings, list(CHECK.GUIDE_HEADINGS))
        self.assertNotIn("H100", guide)

    def test_complete_source_round_trip(self) -> None:
        source = 'value = "<tag> & data"\r\nprint(value)\r\n'
        listing = (
            '<pre tabindex="0"><code data-source="labs/demo.py">'
            + html.escape(source)
            + "</code></pre>"
        )
        self.assertEqual(
            self.run_check(
                fixture().replace("</main>", listing + "</main>"),
                {"labs/demo.py": source},
            ),
            [],
        )

    def test_missing_or_changed_source_fails(self) -> None:
        self.assertTrue(self.run_check(fixture(), {"labs/demo.py": "print(1)\n"}))
        listing = (
            '<pre tabindex="0"><code data-source="labs/demo.py">print(2)\n</code></pre>'
        )
        self.assertIn(
            "Embedded source bytes differ from canonical source",
            self.run_check(
                fixture().replace("</main>", listing + "</main>"),
                {"labs/demo.py": "print(1)\n"},
            ),
        )

    def test_duplicate_source_rejected(self) -> None:
        listing = '<pre tabindex="0"><code data-source="demo.py">pass\n</code></pre>'
        self.assertTrue(
            self.run_check(
                fixture().replace("</main>", listing * 2 + "</main>"),
                {"demo.py": "pass\n"},
            )
        )

    def test_broken_toc_and_duplicate_ids(self) -> None:
        self.assertTrue(
            self.run_check(fixture().replace('href="#lesson-01"', 'href="#absent"'))
        )
        self.assertTrue(
            self.run_check(fixture().replace('id="lab-01"', 'id="lesson-01"'))
        )

    def test_active_html_and_external_loading_rejected(self) -> None:
        for active in (
            "<script>alert(1)</script>",
            '<img src="https://example.com/tracker">',
            '<p onclick="alert(1)">Text</p>',
            '<a href="javascript:alert(1)">Click</a>',
            '<iframe srcdoc="example"></iframe>',
        ):
            with self.subTest(active=active):
                self.assertTrue(
                    self.run_check(fixture().replace("</main>", active + "</main>"))
                )

    def test_attribution_reporting_rejected_without_blocking_reference_links(
        self,
    ) -> None:
        self.assertEqual(self.run_check(fixture()), [])
        link = 'href="https://www.w3.org/TR/WCAG22/"'
        for attribute in (
            "attributionsrc",
            'attributionsrc=""',
            'attributionsrc="https://example.com/register-source"',
            'ATTRIBUTIONSRC="https://example.com/register-source"',
        ):
            with self.subTest(attribute=attribute):
                self.assertIn(
                    "Active or external-loading attribute",
                    self.run_check(fixture().replace(link, f"{link} {attribute}")),
                )

    def test_external_css_and_svg_rejected(self) -> None:
        self.assertTrue(
            self.run_check(
                fixture().replace(
                    "</style>", '@import "https://example.com/a.css";</style>'
                )
            )
        )
        self.assertTrue(
            self.run_check(
                fixture().replace(
                    "url(#workflow-arrow)", "url(https://example.com/a.svg)"
                )
            )
        )

    def test_svg_name_description_and_context_required(self) -> None:
        for old, new in (
            ('role="img"', 'role="presentation"'),
            ('aria-labelledby="workflow-title workflow-desc"', ""),
            ('<desc id="workflow-desc">', "<desc>"),
            ("<figcaption>", "<p>"),
        ):
            with self.subTest(old=old):
                self.assertTrue(self.run_check(fixture().replace(old, new)))

    def test_source_paths_do_not_escape_course(self) -> None:
        self.page.write_text(fixture())
        self.manifest.write_text('["../outside.txt"]')
        with self.assertRaises(ValueError):
            CHECK.check(self.page, self.root, self.manifest)

    def test_linked_source_rejected(self) -> None:
        other = self.root / "other.txt"
        other.write_text("public fixture")
        (self.root / "linked.txt").symlink_to(other)
        self.page.write_text(fixture())
        self.manifest.write_text('["linked.txt"]')
        with self.assertRaises(ValueError):
            CHECK.check(self.page, self.root, self.manifest)

    def test_malformed_manifest(self) -> None:
        self.page.write_text(fixture())
        for manifest in ("{}", '["x", "x"]', "[7]"):
            self.manifest.write_text(manifest)
            with self.assertRaises(ValueError):
                CHECK.check(self.page, self.root, self.manifest)

    def test_banner_clutter_and_unbalanced_html_rejected(self) -> None:
        self.assertTrue(
            self.run_check(
                fixture().replace("</header>", "<p>Status badge</p></header>")
            )
        )
        self.assertTrue(self.run_check(fixture().replace("</main>", "")))

    def test_checker_is_read_only(self) -> None:
        self.run_check(fixture())
        before = {
            p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.root.iterdir()
        }
        self.assertEqual(CHECK.check(self.page, self.root, self.manifest), [])
        after = {
            p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.root.iterdir()
        }
        self.assertEqual(before, after)

    def test_reviewed_external_load_bypasses_rejected(self) -> None:
        variants = (
            fixture().replace(
                "</style>",
                'body {background: image-set("https://example.com/tracker.png" 1x)}</style>',
            ),
            fixture().replace(
                "url(#workflow-arrow)", r"u\72l(https://example.com/arrow.svg)"
            ),
            fixture().replace(
                'href="https://www.w3.org/TR/WCAG22/"',
                'href="https://www.w3.org/TR/WCAG22/" ping="https://example.com/tracker"',
            ),
        )
        for page in variants:
            with self.subTest(page_kind=variants.index(page)):
                self.assertTrue(self.run_check(page))

    def test_description_must_have_its_own_referenced_id(self) -> None:
        page = (
            fixture()
            .replace('id="workflow-desc"', "")
            .replace("workflow-title workflow-desc", "workflow-title")
        )
        self.assertTrue(self.run_check(page))

    def test_caption_is_local_and_nonempty(self) -> None:
        page = (
            fixture()
            .replace("<figcaption>", "</figure><figcaption>")
            .replace("</figcaption></figure>", "</figcaption>")
        )
        self.assertTrue(self.run_check(page))

    def test_source_listing_must_be_literal_text(self) -> None:
        listing = '<pre tabindex="0"><code data-source="demo.py"><strong>pass</strong>\n</code></pre>'
        self.assertTrue(
            self.run_check(
                fixture().replace("</main>", listing + "</main>"), {"demo.py": "pass\n"}
            )
        )

    def test_source_listing_rejects_non_element_markup(self) -> None:
        for markup in (
            "<!--hidden comment-->",
            "<!DOCTYPE html>",
            "<?course instruction?>",
            "<![CDATA[hidden data]]>",
        ):
            with self.subTest(markup=markup):
                listing = (
                    '<pre tabindex="0"><code data-source="demo.py">pa'
                    + markup
                    + "ss\n</code></pre>"
                )
                self.assertIn(
                    "Source listing must contain escaped literal text",
                    self.run_check(
                        fixture().replace("</main>", listing + "</main>"),
                        {"demo.py": "pass\n"},
                    ),
                )

    def test_escaped_markup_source_and_page_comments_are_valid(self) -> None:
        source = "<!--comment-->\n<!DOCTYPE html>\n<?data?>\n<![CDATA[text]]>\n"
        listing = (
            '<!--page comment--><pre tabindex="0"><code data-source="example.txt">'
            + html.escape(source)
            + "</code></pre>"
        )
        self.assertEqual(
            self.run_check(
                fixture().replace("</main>", listing + "</main>"),
                {"example.txt": source},
            ),
            [],
        )

    def test_banner_has_no_unwrapped_extra_text(self) -> None:
        self.assertTrue(
            self.run_check(
                fixture().replace("</header>", "Extra banner claim</header>")
            )
        )


if __name__ == "__main__":
    unittest.main()
