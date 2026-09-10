"""Lesson-local layout checks, independent of semantic teaching judgments."""

import json
import re
import unittest

from test_course_assets import CHECK, ROOT, fixture, lesson_fixture


def errors(lesson: str) -> list[str]:
    parser = CHECK.CoursePage()
    parser.feed(fixture().replace(lesson_fixture(), lesson))
    parser.close()
    return parser.finish()


def field(name: str, lesson: str | None = None) -> str:
    match = re.search(
        rf'  <div class="{name}">.*?  </div>',
        lesson or lesson_fixture(),
        re.S,
    )
    if not match:
        raise AssertionError("Missing test section")
    return match.group()


class LessonContract(unittest.TestCase):
    def test_all_four_sections_and_nested_explanation_are_valid(self):
        base = lesson_fixture()
        self.assertEqual(errors(base), [])
        self.assertEqual(errors(base + lesson_fixture(2)), [])
        for tag in ("div", "section"):
            nested = base.replace("<p>A process", f"<{tag}><div><p>A process").replace(
                "result criterion.</p>", f"result criterion.</p></div></{tag}>"
            )
            self.assertEqual(errors(nested), [])

    def test_missing_duplicate_and_reordered_sections_fail(self):
        base = lesson_fixture()
        for name in (
            "lesson-outcome",
            "how-it-works",
            "practice-links",
            "mental-model",
        ):
            part = field(name)
            for changed in (base.replace(part, ""), base.replace(part, part + part)):
                with self.subTest(section=name):
                    self.assertIn(
                        "Lesson needs the four canonical sections in order",
                        errors(changed),
                    )
        first, last = field("lesson-outcome"), field("mental-model")
        swapped = (
            base.replace(first, "@FIRST@").replace(last, first).replace("@FIRST@", last)
        )
        self.assertIn(
            "Lesson needs the four canonical sections in order", errors(swapped)
        )

    def test_visible_labels_and_heading_position_must_match(self):
        base = lesson_fixture()
        for changed in (
            base.replace("<h3>How it works</h3>", "<h3>Mechanism</h3>"),
            base.replace("<h3>Objective</h3>", "<h3>Objective</h3><h3>Recall</h3>"),
            base.replace("<h3>Objective</h3>", "<div><h3>Objective</h3></div>"),
            base.replace(
                "<h3>Objective</h3>", "<p>Extra preamble.</p><h3>Objective</h3>"
            ),
        ):
            self.assertTrue(errors(changed))

    def test_old_layout_and_trailing_content_fail(self):
        base = lesson_fixture()
        for content in (
            "<h3>Recall</h3><p>Explain the input.</p>",
            "<p>New teaching.</p>",
            "New teaching.",
        ):
            self.assertTrue(errors(base.replace("</section>", content + "</section>")))
        old = '<section class="lesson" id="lesson-01"><h2>1. Process</h2><div><h3>Start here</h3><p>Old format.</p></div></section>'
        self.assertIn("Lesson needs the four canonical sections in order", errors(old))

    def test_each_explanation_needs_its_own_svg_figure(self):
        base = lesson_fixture()
        figure = re.search(r"    <figure.*?</figure>", base, re.S).group()
        missing = base.replace(figure, "")
        for changed in (
            missing,
            base.replace((ROOT / "assets/diagram-example.svg").read_text(), ""),
            missing.replace("<h3>Mental model</h3>", "<h3>Mental model</h3>" + figure),
            missing.replace("<h3>Practice</h3>", "<h3>Practice</h3>" + figure),
        ):
            self.assertIn(
                "Each How it works needs its own inline SVG figure", errors(changed)
            )
        second = lesson_fixture(2)
        second = re.sub(r"    <figure.*?</figure>", "", second, flags=re.S)
        self.assertIn(
            "Each How it works needs its own inline SVG figure", errors(base + second)
        )

    def test_caption_svg_and_heading_text_do_not_count_as_explanation(self):
        base = lesson_fixture()
        no_prose = base.replace(
            "<p>A process transforms inputs into a result. A comparison needs "
            "a stated baseline and the same result criterion.</p>",
            "",
        )
        self.assertIn(
            "Lesson section needs content outside its heading and figures",
            errors(no_prose),
        )
        heading_only = base.replace("<p>An input is transformed, then checked.</p>", "")
        self.assertIn(
            "Lesson section needs content outside its heading and figures",
            errors(heading_only),
        )

    def test_missing_lesson_marker_cannot_skip_contract(self):
        self.assertIn(
            "Expected at least one lesson",
            errors(lesson_fixture().replace('class="lesson"', 'class="chapter"')),
        )

    def test_metadata_places_diagrams_inside_explanation(self):
        data = json.loads(
            (
                ROOT / "assets/course-workspace-template/reference/course.json"
            ).read_text()
        )
        self.assertTrue(data["diagrams"])
        for diagram in data["diagrams"]:
            self.assertEqual(diagram["section"], "How it works")
            self.assertNotIn("after", diagram)

    def test_markdown_starter_matches_field_order(self):
        text = (ROOT / "assets/course-workspace-template/COURSE.md").read_text()
        headings = re.findall(r"^### (.+)$", text, re.M)
        self.assertEqual(
            headings, ["Objective", "How it works", "Practice", "Mental model"]
        )


if __name__ == "__main__":
    unittest.main()
