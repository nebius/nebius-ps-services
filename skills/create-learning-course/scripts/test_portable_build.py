"""Exercise copied build scaffolds; no renderer, model or installation claims."""

import html
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_course_assets import ROOT, fixture

TEMPLATE = ROOT / "assets/course-workspace-template"
ADAPTER = """from pathlib import Path
from publication import results_zip
PROJECT_ROOT = Path(__file__).resolve().parents[1]
PUBLICATION_ROOT = PROJECT_ROOT
INVENTORY = 'directory'
MAX_FILE_BYTES = None
MAX_SITE_BYTES = None
COURSES = {courses!r}
DOWNLOADS = {downloads!r}
def plan_outputs(project_root):
    outputs = {{}}
    for name in COURSES:
        outputs[name + '/index.html'] = (project_root / name / 'authored.html').read_bytes()
        if DOWNLOADS:
            outputs[name + '/reference/' + name + '-lab-results.zip'] = results_zip(project_root, {{'results.csv': name + '/results.csv'}})
    if len(COURSES) > 1:
        outputs['lab-guide.html'] = b'<html><body><h1 id="setup">Setup</h1></body></html>'
    return outputs
"""


class PortableBuild(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "project with spaces"
        shutil.copytree(TEMPLATE, self.root)

    def run_build(self, *args, wrapper=False, env=None):
        command = (
            ["/bin/bash", str(self.root / "build-courses.sh")]
            if wrapper
            else [sys.executable, "-B", str(self.root / "tools/build_course_html.py")]
        )
        return subprocess.run(
            command + list(args),
            cwd=self.root.parent,
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )

    def configure(self, courses=("reading",), downloads=False):
        (self.root / "tools/course_adapter.py").write_text(
            ADAPTER.format(courses=courses, downloads=downloads)
        )
        for name in courses:
            base = self.root / name
            base.mkdir()
            source = 'print("<complete> & source")\n'
            text = fixture()
            if downloads:
                (base / "example.py").write_text(source)
                text = text.replace(
                    "</main>",
                    '<pre tabindex="0"><code data-source="example.py">'
                    + html.escape(source)
                    + '</code></pre><a href="reference/'
                    + name
                    + '-lab-results.zip">Results</a></main>',
                )
                (base / "results.csv").write_text("metric,value\ncount,3\n")
            if len(courses) > 1:
                text = text.replace(
                    "</main>", '<a href="../lab-guide.html#setup">Setup</a></main>'
                )
            (base / "authored.html").write_text(text)

    def test_unconfigured_adapter_fails_without_outputs(self):
        result = self.run_build()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Configure course_adapter", result.stderr)
        self.assertFalse((self.root / "index.html").exists())

    def test_three_course_shapes_build_check_and_repeat(self):
        for courses, downloads in [
            (("reading",), False),
            (("technical",), True),
            (("first", "second"), True),
        ]:
            with self.subTest(courses=courses):
                self.configure(courses, downloads)
                for _ in range(3):
                    result = self.run_build(wrapper=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    for name in courses:
                        base = self.root / name
                        self.assertEqual(
                            (base / "index.html").read_bytes(),
                            (base / "authored.html").read_bytes(),
                        )
                before = {
                    p: (p.read_bytes(), p.stat().st_mtime_ns)
                    for p in self.root.rglob("*")
                    if p.is_file()
                }
                result = self.run_build("--check")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in before}
                )
                (self.root / courses[0] / "index.html").write_text("stale")
                self.assertNotEqual(self.run_build("--check").returncode, 0)

    def test_size_error_preserves_outputs(self):
        self.configure()
        self.assertEqual(self.run_build().returncode, 0)
        output = self.root / "reading/index.html"
        before = (output.read_bytes(), output.stat().st_mtime_ns)
        adapter = self.root / "tools/course_adapter.py"
        adapter.write_text(
            adapter.read_text().replace("MAX_SITE_BYTES = None", "MAX_SITE_BYTES = 1")
        )
        for args in [(), ("--check",)]:
            self.assertNotEqual(self.run_build(*args).returncode, 0)
            self.assertEqual(before, (output.read_bytes(), output.stat().st_mtime_ns))

    def test_wrapper_help_without_python_and_invalid_arguments(self):
        binaries = self.root / "bin"
        binaries.mkdir()
        (binaries / "cat").symlink_to("/bin/cat")
        env = {**os.environ, "PATH": str(binaries)}
        self.assertEqual(self.run_build("--help", wrapper=True, env=env).returncode, 0)
        self.assertEqual(self.run_build("invalid", wrapper=True, env=env).returncode, 2)
        self.assertEqual(self.run_build(wrapper=True, env=env).returncode, 1)

    def test_wrapper_preserves_builder_exit_codes(self):
        for status, condition in [(42, "True"), (43, '"--check" in sys.argv')]:
            (self.root / "tools/build_course_html.py").write_text(
                f"import sys\nsys.exit({status} if {condition} else 0)\n"
            )
            result = self.run_build(wrapper=True)
            self.assertEqual(result.returncode, status)
            self.assertNotIn("rebuilt and verified", result.stdout)

    def test_copied_presentation_assets_match_shared_authority(self):
        for asset in (self.root / "tools/assets").iterdir():
            self.assertEqual(
                asset.read_bytes(), (ROOT / "assets" / asset.name).read_bytes()
            )


if __name__ == "__main__":
    unittest.main()
