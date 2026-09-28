"""Presentation regressions for complete course output, without browser claims."""

import re
import unittest

from test_course_assets import CHECK, fixture


def errors(page):
    parser = CHECK.CoursePage()
    parser.feed(page)
    parser.close()
    return parser.finish()


class PresentationContract(unittest.TestCase):
    def test_supporting_headings_cannot_share_one_section(self):
        page = fixture().replace('href="#glossary"', 'href="#next-steps"')
        page = page.replace('    </section>\n    <section id="glossary">', "")
        self.assertTrue(errors(page))

    def test_course_owned_anchors_are_preserved(self):
        page = fixture()
        for old, new in (
            ("glossary", "guide-glossary"),
            ("next-steps", "onward"),
            ("references", "official-references"),
        ):
            page = page.replace(f'id="{old}"', f'id="{new}"').replace(
                f'href="#{old}"', f'href="#{new}"'
            )
        self.assertEqual(errors(page), [])

    def test_glossary_navigation_targets_the_actual_section(self):
        page = fixture().replace('<section id="glossary">', '<section id="appendix">')
        page = page.replace(
            '<section id="next-steps">',
            '<section id="next-steps"><span id="glossary"></span>',
        )
        self.assertTrue(errors(page))

    def test_bulleted_option_can_preserve_a_numbered_procedure(self):
        page = fixture().replace(
            "explanation.</li>",
            "explanation.<ol><li>Choose a case.</li><li>Compare observations.</li></ol></li>",
        )
        self.assertEqual(errors(page), [])

    def test_empty_option_does_not_count_as_a_list(self):
        page = fixture().replace(
            "<li>Study how additional observations test an explanation.</li>",
            "<li> </li>",
        )
        self.assertTrue(errors(page))

    def test_semantic_lists_and_independent_glossary_pass(self):
        self.assertEqual(errors(fixture()), [])

    def test_course_next_steps_require_bullets(self):
        text = "Study how additional observations test an explanation."
        original = f"<ul><li>{text}</li></ul>"
        self.assertIn(original, fixture())
        for replacement in (f"<p>{text}</p>", f"<ol><li>{text}</li></ol>"):
            with self.subTest(replacement=replacement):
                self.assertTrue(errors(fixture().replace(original, replacement)))

    def test_course_next_steps_are_required_exactly_once(self):
        page = fixture()
        match = re.search(r'<section id="next-steps">.*?</section>', page, re.S)
        self.assertIsNotNone(match)
        section = match.group()
        for replacement in (
            "",
            section + section.replace('id="next-steps"', 'id="more-study"'),
            section.replace("Where to Go Next", "Further study"),
        ):
            with self.subTest(replacement=replacement):
                self.assertIn(
                    "Course needs independent next steps, Glossary and Official references sections",
                    errors(page.replace(section, replacement)),
                )

    def test_appendices_cannot_be_local_to_guides_or_nested(self):
        for title in ("Glossary", "Where to Go Next"):
            block = f"<section><h4>{title}</h4><p>Local appendix.</p></section>"
            self.assertTrue(
                errors(fixture().replace("</article>", block + "</article>"))
            )
        page = fixture()
        section = re.search(
            r'<section id="next-steps">.*?</section>', page, re.S
        ).group()
        self.assertTrue(
            errors(
                page.replace(
                    section, "<section><h2>Extras</h2>" + section + "</section>"
                )
            )
        )

    def test_lesson_title_cannot_substitute_for_course_next_steps(self):
        page = fixture()
        section = re.search(
            r'<section id="next-steps">.*?</section>', page, re.S
        ).group()
        page = page.replace(section, "").replace(
            'href="#next-steps"', 'href="#lesson-01"'
        )
        page = page.replace(
            "<h2>1. Process inputs and results</h2>", "<h2>Where to Go Next</h2>"
        )
        page = page.replace(
            "<p>An input is transformed, then checked.</p>",
            "<ul><li>Explore another process.</li></ul>",
        )
        self.assertIn(
            "Course needs independent next steps, Glossary and Official references sections",
            errors(page),
        )

    def test_course_appendix_cannot_wrap_a_lesson_or_guide(self):
        from test_course_assets import lesson_fixture

        page = fixture()
        lesson = lesson_fixture()
        for content in (lesson, '<article class="lab"><p>Activity.</p></article>'):
            changed = page.replace(lesson, "") if content == lesson else page
            changed = changed.replace(
                "<h2>Where to Go Next</h2>", "<h2>Where to Go Next</h2>" + content
            )
            self.assertIn(
                "Course appendices cannot contain lessons or practical guides",
                errors(changed),
            )

    def test_course_appendices_follow_all_teaching_and_practical_work(self):
        page = fixture()
        section = re.search(
            r'<section id="next-steps">.*?</section>', page, re.S
        ).group()
        without = page.replace(section, "")
        for marker in (
            '<main id="main" tabindex="-1">',
            '<section id="practical-work"><h2>Practical work</h2>',
        ):
            self.assertIn(marker, without)
            with self.subTest(marker=marker):
                changed = without.replace(
                    marker,
                    marker + section
                    if marker.startswith("<main")
                    else section + marker,
                )
                self.assertIn(
                    "Lessons and practical guides must precede course appendices",
                    errors(changed),
                )

    def test_appendix_cannot_hide_teaching_before_its_heading(self):
        from test_course_assets import lesson_fixture

        page = fixture()
        for content in (
            lesson_fixture(),
            re.search(r'<article class="lab".*?</article>', page, re.S).group(),
        ):
            with self.subTest(content=content):
                changed = page.replace(content, "").replace(
                    '<section id="next-steps">', '<section id="next-steps">' + content
                )
                self.assertIn(
                    "Course needs independent next steps, Glossary and Official references sections",
                    errors(changed),
                )

    def test_official_references_require_numbers(self):
        page = fixture()
        start = page.index('<section id="references">')
        for tag in ("ul", "div"):
            changed = page[:start] + page[start:].replace("<ol>", f"<{tag}>").replace(
                "</ol>", f"</{tag}>"
            )
            self.assertTrue(errors(changed))

    def test_glossary_cannot_be_nested_under_another_topic(self):
        page = fixture().replace(
            '<section id="glossary">',
            '<section><h2>Other material</h2><section id="glossary">',
        )
        page = page.replace(
            '<section id="references">', '</section><section id="references">'
        )
        self.assertTrue(errors(page))

    def test_planning_labels_are_not_course_headings_or_navigation(self):
        for label in ("Syllabus", "Course mission", "Course <em>mission</em>"):
            for markup in (
                f"<h2>{label}</h2>",
                f'<nav aria-label="Extra"><a href="#main">{label}</a></nav>',
            ):
                self.assertIn(
                    "Keep mission and syllabus out of course headings and navigation",
                    errors(fixture().replace("</main>", markup + "</main>")),
                )


if __name__ == "__main__":
    unittest.main()
