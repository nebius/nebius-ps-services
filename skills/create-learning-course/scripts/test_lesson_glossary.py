"""Single course glossary, no lesson copies, and complete ordered definitions."""

import re
import unittest

from test_course_assets import fixture, lesson_fixture
from test_lesson_contract import errors as lesson_errors, field
from test_presentation_contract import errors

ENTRIES = (
    "<dt>FIFO</dt><dd>First in, first out: serve in arrival order.</dd>"
    "<dt>WIP</dt><dd>Work in progress: all unfinished work.</dd>"
)
REFERENCES = (
    '  <div class="lesson-references"><h3>References</h3>'
    '<ol><li><a href="https://example.org/">Public source</a></li></ol>  </div>'
)


def glossary(entries=ENTRIES):
    return f'<section id="glossary"><h2>Glossary</h2><dl>{entries}</dl></section>'


def course(entries=ENTRIES):
    return re.sub(
        r'<section id="glossary">.*?</section>',
        glossary(entries),
        fixture(),
        flags=re.S,
    )


class CourseGlossary(unittest.TestCase):
    def test_course_glossary_is_required_exactly_once(self):
        page = course()
        self.assertEqual(errors(page), [])
        self.assertEqual(
            errors(
                page.replace(lesson_fixture(), lesson_fixture() + lesson_fixture(2))
            ),
            [],
        )
        for replacement in (
            "",
            glossary() + glossary().replace('id="glossary"', 'id="second"'),
        ):
            self.assertTrue(errors(page.replace(glossary(), replacement)))

    def test_local_glossaries_are_rejected_even_when_nested(self):
        for local in (
            '<div class="lesson-glossary"><h3>Glossary</h3><dl>'
            + ENTRIES
            + "</dl></div>",
            "<section><h4><em>Glossary</em></h4><dl>" + ENTRIES + "</dl></section>",
            '<div class="tools-glossary"><dl>' + ENTRIES + "</dl></div>",
        ):
            page = course().replace(
                "<h3>How it works</h3>", "<h3>How it works</h3>" + local
            )
            self.assertTrue(errors(page))

    def test_optional_lesson_references_are_last(self):
        base = lesson_fixture()
        valid = base.replace("</section>", REFERENCES + "</section>")
        self.assertEqual(lesson_errors(valid), [])
        for invalid in (
            base.replace(
                field("mental-model", base), REFERENCES + field("mental-model", base)
            ),
            valid.replace(REFERENCES, REFERENCES + REFERENCES),
            valid.replace("</section>", "<p>Extra teaching.</p></section>"),
        ):
            self.assertTrue(lesson_errors(invalid))

    def test_keys_sort_by_displayed_abbreviation_ignoring_case(self):
        valid = "<dt><code>api</code></dt><dd>Zeta interface.</dd><dt>CPU</dt><dd>Alpha processor.</dd>"
        self.assertEqual(errors(course(valid)), [])
        self.assertIn(
            "Glossary keys must be unique and sorted A-Z",
            errors(
                course(
                    "<dt>WIP</dt><dd>Work in progress.</dd><dt>FIFO</dt><dd>First in, first out.</dd>"
                )
            ),
        )
        self.assertEqual(
            errors(
                course(
                    "<dt>Evidence</dt><dd>Observations used to test a claim.</dd><dt>Inference</dt><dd>A conclusion drawn from evidence.</dd>"
                )
            ),
            [],
        )

    def test_entries_must_be_nonempty_pairs_with_unique_keys(self):
        for invalid in (
            "",
            "<dt>FIFO</dt>",
            "<dd>Orphan definition.</dd>",
            "<dt> </dt><dd>Meaning.</dd>",
            "<dt>FIFO</dt><dd><em> </em></dd>",
            "<dt>FIFO</dt><dt>WIP</dt><dd>Meaning.</dd>",
            "<dt>FIFO</dt><dd>Meaning.</dd><dd>Extra definition.</dd>",
            ENTRIES + "<dt> wip </dt><dd>Duplicate key.</dd>",
            "<dt><dl><dt>FIFO</dt><dd>Nested.</dd></dl></dt><dd>Meaning.</dd>",
        ):
            with self.subTest(entries=invalid):
                self.assertTrue(errors(course(invalid)))

    def test_one_direct_definition_list_and_final_references(self):
        base = course()
        for invalid in (
            base.replace(
                "<dl>" + ENTRIES + "</dl>", "<p>FIFO: first in, first out.</p>"
            ),
            base.replace("</dl>", "</dl><dl>" + ENTRIES + "</dl>"),
            base.replace("<dl>", "<div><dl>").replace("</dl>", "</dl></div>"),
            base.replace("<dl>", "<dl>Stray entry"),
            base.replace(glossary(), "").replace("</main>", glossary() + "</main>"),
        ):
            self.assertTrue(errors(invalid))


if __name__ == "__main__":
    unittest.main()
