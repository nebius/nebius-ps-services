#!/usr/bin/env python3
"""Focused unit tests for the read-only code-info reporter."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tarfile
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock


SCRIPT_PATH = Path(__file__).with_name("code_info.py")
SPEC = importlib.util.spec_from_file_location("code_info_under_test", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:  # pragma: no cover - import invariant
    raise RuntimeError(f"cannot import {SCRIPT_PATH}")
code_info = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = code_info
SPEC.loader.exec_module(code_info)


def write(root: Path, relative: str, content: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")
    return path


class CodeInfoTests(unittest.TestCase):
    def test_description_prefers_manifest_and_features_use_primary_readme(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write(
                root,
                "pyproject.toml",
                """
                [project]
                name = "sample"
                description = "A concise project description."
                """,
            )
            write(
                root,
                "README.md",
                """
                # Sample

                This README description must not override package metadata.

                ## Features

                - First capability
                  - Nested detail must not count
                - Second [capability](https://example.com)

                ```text
                - Example output is not a feature
                ```

                ## Installation

                - Not a feature
                """,
            )

            self.assertEqual(
                code_info.project_description(root),
                ("A concise project description.", "pyproject.toml"),
            )
            features, source = code_info.documented_features(root)
            self.assertEqual(features, ["First capability", "Second capability"])
            self.assertEqual(source, "README.md")

    def test_readme_description_is_bounded_and_omits_bare_urls(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write(
                root,
                "README.md",
                """
                # Sample

                This project talks to https://private.example.invalid/path and
                provides a deliberately long but otherwise useful description.
                """,
            )
            description, source = code_info.project_description(root)
            self.assertNotIn("private.example.invalid", description)
            self.assertIn("[link omitted]", description)
            self.assertLessEqual(len(description), code_info.MAX_DESCRIPTION_CHARS)
            self.assertEqual(source, "README.md")

    def test_root_metadata_symlinks_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "project"
            root.mkdir()
            outside = write(
                Path(temp_dir),
                "outside.md",
                "External content must not be reported.\n",
            )
            (root / "README.md").symlink_to(outside)
            self.assertIsNone(code_info.project_description(root))
            self.assertEqual(code_info.documented_features(root), ([], None))

    def test_loc_categories_exclusions_and_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write(root, "src/main.py", "# comment\nprint('ok')\n\n")
            write(root, "tests/test_main.py", "def test_ok():\n    assert True\n")
            write(root, "README.md", "# Title\n\nDocumentation.\n")
            write(root, "config.yaml", "enabled: true\n")
            write(root, "build/generated.py", "print('excluded')\n")
            outside = write(
                root.parent, f"{root.name}-outside.py", "print('outside')\n"
            )
            try:
                (root / "outside.py").symlink_to(outside)
                stats = code_info.collect_file_stats(root)
            finally:
                outside.unlink(missing_ok=True)

            by_language: dict[str, int] = {}
            for stat in stats:
                by_language[stat.language] = (
                    by_language.get(stat.language, 0) + stat.loc
                )
            self.assertEqual(by_language["Python"], 3)
            self.assertEqual(by_language["Markdown"], 2)
            self.assertEqual(by_language["YAML"], 1)
            self.assertFalse(any(stat.path.name == "outside.py" for stat in stats))

    def test_binary_detection_reads_only_a_bounded_prefix(self) -> None:
        path = mock.MagicMock(spec=Path)
        handle = path.open.return_value.__enter__.return_value
        handle.read.return_value = b"print('ok')\n"

        self.assertFalse(code_info.is_probably_binary(path))
        path.open.assert_called_once_with("rb")
        handle.read.assert_called_once_with(8192)

    def test_symlinked_artifact_directory_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "project"
            outside = Path(temp_dir) / "outside"
            root.mkdir()
            outside.mkdir()
            write(outside, "artifact.whl", "not really an artifact\n")
            (root / "build").symlink_to(outside, target_is_directory=True)
            self.assertEqual(code_info.artifact_dirs(root), [])

    def test_python_command_hierarchy_is_capped_at_three_levels(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = write(
                root,
                "cli.py",
                """
                import argparse
                import typer

                app = typer.Typer()
                cluster_app = typer.Typer()
                node_app = typer.Typer()
                app.add_typer(cluster_app, name="cluster")
                cluster_app.add_typer(node_app, name="node")

                @node_app.command()
                def create():
                    pass

                parser = argparse.ArgumentParser()
                commands = parser.add_subparsers()
                account = commands.add_parser("account")
                account_commands = account.add_subparsers()
                user = account_commands.add_parser("user")
                user_commands = user.add_subparsers()
                user_commands.add_parser("show")
                """,
            )
            command_paths = {
                item.command_path for item in code_info.python_cli_commands(path)
            }
            expected = {
                ("cluster",),
                ("cluster", "node"),
                ("cluster", "node", "create"),
                ("account",),
                ("account", "user"),
                ("account", "user", "show"),
            }
            self.assertTrue(expected <= command_paths, command_paths)
            detected = code_info.detect_cli_commands(root)
            self.assertTrue(all(len(item.command_path) <= 3 for item in detected))

    def test_package_scripts_are_not_application_commands(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write(
                root,
                "package.json",
                json.dumps({"scripts": {"build": "tool build", "test": "tool test"}}),
            )
            self.assertEqual(code_info.detect_cli_commands(root), [])
            self.assertEqual(
                [name for _, name in code_info.package_scripts(root)],
                ["build", "test"],
            )
            write(root, "nested/package.json", "[]")
            self.assertEqual(
                [name for _, name in code_info.package_scripts(root)],
                ["build", "test"],
            )

    def test_dynamic_and_test_only_commands_are_not_reported_as_resolved(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write(
                root,
                "cli.py",
                """
                import argparse
                import typer
                app = typer.Typer()
                NAME = "actual"
                @app.command(name=NAME)
                def handler():
                    pass
                parser = argparse.ArgumentParser()
                subcommands = parser.add_subparsers()
                subcommands.add_parser(NAME)
                """,
            )
            write(
                root,
                "tests/test_cli.py",
                """
                import typer
                app = typer.Typer()
                @app.command()
                def fake():
                    pass
                """,
            )
            commands = code_info.detect_cli_commands(root)
            self.assertEqual(len(commands), 2)
            self.assertEqual(
                {item.command_path for item in commands},
                {("<dynamic:command>",), ("<dynamic:handler>",)},
            )
            self.assertTrue(all(item.confidence == "partial" for item in commands))

    def test_dependency_counts_across_supported_ecosystems(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write(
                root,
                "pyproject.toml",
                """
                [project]
                name = "sample"
                dependencies = ["requests>=2"]
                [project.optional-dependencies]
                cli = ["rich>=13"]
                [dependency-groups]
                dev = ["pytest>=8"]
                """,
            )
            write(
                root,
                "poetry.lock",
                """
                [[package]]
                name = "requests"
                version = "2.0"
                [[package]]
                name = "urllib3"
                version = "2.0"
                [[package]]
                name = "pytest"
                version = "8.0"
                """,
            )
            write(
                root,
                "web/package.json",
                json.dumps(
                    {
                        "name": "web",
                        "dependencies": {"left-pad": "1"},
                        "devDependencies": {"vitest": "1"},
                        "optionalDependencies": {"fsevents": "1"},
                    }
                ),
            )
            write(
                root,
                "web/package-lock.json",
                json.dumps(
                    {
                        "packages": {
                            "": {},
                            "node_modules/left-pad": {},
                            "node_modules/transitive": {},
                        }
                    }
                ),
            )
            write(
                root,
                "rust/Cargo.toml",
                """
                [package]
                name = "sample"
                version = "0.1.0"
                [dependencies]
                serde = "1"
                [dev-dependencies]
                tempfile = "3"
                [workspace.dependencies]
                unused = "1"
                """,
            )
            write(
                root,
                "rust/Cargo.lock",
                """
                [[package]]
                name = "sample"
                version = "0.1.0"
                [[package]]
                name = "serde"
                version = "1.0.0"
                [[package]]
                name = "tempfile"
                version = "3.0.0"
                """,
            )
            write(
                root,
                "go/go.mod",
                """
                module example.test/sample
                require (
                    example.test/direct v1.0.0
                    example.test/indirect v1.0.0 // indirect
                )
                """,
            )
            write(
                root,
                "go/go.sum",
                """
                example.test/direct v1.0.0 h1:abc
                example.test/indirect v1.0.0 h1:def
                example.test/stale v0.1.0 h1:old
                """,
            )

            report = code_info.collect_dependencies(root)
            self.assertEqual(len(report.package_roots), 4)
            self.assertEqual(len(report.project_packages), 4)
            self.assertEqual(len(report.direct_runtime), 4)
            self.assertEqual(len(report.direct_development), 3)
            self.assertEqual(len(report.direct_optional), 2)
            self.assertEqual(len(report.resolved or ()), 9)
            self.assertEqual(len(report.transitive or ()), 3)
            self.assertEqual(report.resolution_gaps, ())

    def test_workspace_package_is_not_counted_as_a_resolved_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write(root, "Cargo.toml", '[package]\nname = "sample"\nversion = "0.1.0"\n')
            write(
                root,
                "Cargo.lock",
                '[[package]]\nname = "sample"\nversion = "0.1.0"\n',
            )
            report = code_info.collect_dependencies(root)
            self.assertEqual(report.resolved, frozenset())
            self.assertEqual(code_info.resolved_dependency_value(report), "0")

    def test_missing_lockfile_is_unavailable_not_zero(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write(root, "package.json", json.dumps({"dependencies": {"one": "1"}}))
            report = code_info.collect_dependencies(root)
            self.assertEqual(len(report.direct), 1)
            self.assertIsNone(report.resolved)
            self.assertIsNone(report.transitive)

    def test_package_lock_v1_dependencies_are_resolved(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            lock = write(
                root,
                "package-lock.json",
                json.dumps(
                    {
                        "lockfileVersion": 1,
                        "dependencies": {
                            "one": {"dependencies": {"nested": {"version": "1.0.0"}}}
                        },
                    }
                ),
            )
            self.assertEqual(
                code_info.parse_lockfile(lock),
                {"node:one", "node:nested"},
            )

    def test_mixed_monorepo_marks_resolved_dependency_count_partial(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write(
                root,
                "pyproject.toml",
                '[project]\nname = "sample"\ndependencies = ["requests"]\n',
            )
            write(
                root,
                "web/package.json",
                json.dumps({"name": "web", "dependencies": {"one": "1"}}),
            )
            write(
                root,
                "web/package-lock.json",
                json.dumps({"packages": {"node_modules/one": {}}}),
            )
            report = code_info.collect_dependencies(root)
            self.assertEqual(report.resolution_gaps, ("python:.",))
            self.assertIn("partial", code_info.resolved_dependency_value(report))

    def test_lock_coverage_is_tracked_per_package_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write(
                root,
                "a/package.json",
                json.dumps({"name": "a", "dependencies": {"one": "1"}}),
            )
            write(
                root,
                "a/package-lock.json",
                json.dumps({"packages": {"node_modules/one": {}}}),
            )
            write(
                root,
                "b/package.json",
                json.dumps({"name": "b", "dependencies": {"two": "1"}}),
            )
            report = code_info.collect_dependencies(root)
            self.assertEqual(report.resolution_gaps, ("node:b",))
            self.assertIn("partial", code_info.resolved_dependency_value(report))

    def test_ancestor_lock_requires_declared_workspace_membership(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            root_manifest = write(
                root,
                "package.json",
                json.dumps({"name": "root", "dependencies": {"one": "1"}}),
            )
            write(
                root,
                "package-lock.json",
                json.dumps(
                    {
                        "packages": {
                            "node_modules/one": {},
                            "node_modules/two": {},
                        }
                    }
                ),
            )
            write(
                root,
                "tools/app/package.json",
                json.dumps({"name": "app", "dependencies": {"two": "1"}}),
            )
            report = code_info.collect_dependencies(root)
            self.assertEqual(report.resolution_gaps, ("node:tools/app",))

            root_manifest.write_text(
                json.dumps(
                    {
                        "name": "root",
                        "workspaces": ["tools/*"],
                        "dependencies": {"one": "1"},
                    }
                ),
                encoding="utf-8",
            )
            report = code_info.collect_dependencies(root)
            self.assertEqual(report.resolution_gaps, ())

            write(
                root,
                "tools/app/nested/package.json",
                json.dumps({"name": "nested", "dependencies": {"three": "1"}}),
            )
            report = code_info.collect_dependencies(root)
            self.assertEqual(report.resolution_gaps, ("node:tools/app/nested",))

    def test_cargo_workspace_excludes_are_not_lock_covered(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write(
                root,
                "Cargo.toml",
                '[workspace]\nmembers = ["packages/*"]\nexclude = ["packages/skip"]\n',
            )
            self.assertTrue(
                code_info.workspace_lock_covers(
                    "rust", root, root / "packages" / "included"
                )
            )
            self.assertFalse(
                code_info.workspace_lock_covers(
                    "rust", root, root / "packages" / "skip"
                )
            )

    def test_symlinked_workspace_manifest_cannot_extend_lock_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "project"
            root.mkdir()
            external_manifest = write(
                Path(temp_dir),
                "outside-package.json",
                json.dumps({"workspaces": ["tools/*"]}),
            )
            (root / "package.json").symlink_to(external_manifest)
            write(
                root,
                "package-lock.json",
                json.dumps({"packages": {"node_modules/two": {}}}),
            )
            write(
                root,
                "tools/app/package.json",
                json.dumps({"name": "app", "dependencies": {"two": "1"}}),
            )
            report = code_info.collect_dependencies(root)
            self.assertEqual(report.resolution_gaps, ("node:tools/app",))

    def test_dependency_names_aliases_and_target_tables_are_normalized(self) -> None:
        self.assertEqual(
            code_info.dependency_key("python", "My_Pkg"),
            code_info.dependency_key("python", "my-pkg"),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write(
                root,
                "Cargo.toml",
                """
                [package]
                name = "app"
                version = "0.1.0"
                [dependencies]
                foo = { package = "bar", version = "1" }
                [target.'cfg(unix)'.dependencies]
                nix = "1"
                """,
            )
            write(
                root,
                "Cargo.lock",
                """
                [[package]]
                name = "app"
                version = "0.1.0"
                [[package]]
                name = "bar"
                version = "1.0.0"
                [[package]]
                name = "nix"
                version = "1.0.0"
                """,
            )
            report = code_info.collect_dependencies(root)
            self.assertEqual(report.direct_runtime, {"rust:bar", "rust:nix"})
            self.assertEqual(report.transitive, frozenset())

    def test_valid_but_unexpected_manifest_shapes_do_not_crash(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write(
                root,
                "pyproject.toml",
                '[project]\ndependencies = "requests"\ntool = "unexpected"\n',
            )
            write(root, "Cargo.toml", 'workspace = "unexpected"\n')
            write(root, "setup.cfg", "[options]\ninstall_requires = %(missing)s\n")
            self.assertIsNone(code_info.project_description(root))
            report = code_info.collect_dependencies(root)
            self.assertEqual(len(report.direct), 0)

    def test_remote_url_normalization_removes_embedded_credentials(self) -> None:
        normalized = code_info.normalize_remote_url(
            "https://user:secret@github.com/example/project.git"
        )
        self.assertEqual(normalized, "https://github.com/example/project")
        self.assertNotIn("secret", normalized)

    def test_archive_extraction_is_bounded(self) -> None:
        archive_bytes = io.BytesIO()
        with tarfile.open(fileobj=archive_bytes, mode="w:gz") as archive:
            payload = b"1234"
            member = tarfile.TarInfo("root/file.txt")
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
        archive_bytes.seek(0)
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.object(code_info, "MAX_ARCHIVE_TOTAL_BYTES", 3):
                with self.assertRaisesRegex(
                    code_info.CodeInfoError, "extraction size limit"
                ):
                    code_info.safe_extract_tar_stream(archive_bytes, Path(temp_dir))

    def test_archive_directory_members_are_bounded(self) -> None:
        archive_bytes = io.BytesIO()
        with tarfile.open(fileobj=archive_bytes, mode="w:gz") as archive:
            for name in ("root/one", "root/two"):
                member = tarfile.TarInfo(name)
                member.type = tarfile.DIRTYPE
                archive.addfile(member)
        archive_bytes.seek(0)
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.object(code_info, "MAX_ARCHIVE_MEMBERS", 1):
                with self.assertRaisesRegex(
                    code_info.CodeInfoError, "too many members"
                ):
                    code_info.safe_extract_tar_stream(archive_bytes, Path(temp_dir))

    def test_malformed_coverage_artifacts_do_not_abort_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            invalid_json_shape = write(root, "coverage-summary.json", "[]\n")
            invalid_xml_number = write(
                root, "coverage.xml", '<coverage line-rate="bad"/>\n'
            )
            self.assertIsNone(code_info.parse_coverage(invalid_json_shape))
            self.assertIsNone(code_info.parse_coverage(invalid_xml_number))

    def test_benchmark_schema_and_nearest_selection(self) -> None:
        benchmarks = code_info.load_benchmarks()
        self.assertEqual({item["name"] for item in benchmarks}, {"Redis", "SQLite"})
        selected = code_info.nearest_benchmarks(250_000, benchmarks)
        self.assertEqual(len(selected), 2)
        self.assertEqual(selected[0]["name"], "Redis")
        self.assertEqual(code_info.nearest_benchmarks(0, benchmarks), [])

    def test_local_report_does_not_modify_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = write(root, "main.py", "print('ok')\n")
            write(root, "README.md", "# Sample\n\nDescription.\n")
            before = (source.read_bytes(), source.stat().st_mtime_ns)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = code_info.main(["--path", str(root), "--top", "1"])
            after = (source.read_bytes(), source.stat().st_mtime_ns)
            self.assertEqual(result, 0)
            self.assertEqual(before, after)
            report = output.getvalue()
            self.assertIn("## Size Comparison", report)
            self.assertIn("Other (2 languages)", report)
            language_section = report.split("## LOC Per Language", 1)[1].split(
                "## LOC Per Top-Level Component", 1
            )[0]
            language_table_lines = [
                line for line in language_section.splitlines() if line.startswith("|")
            ]
            self.assertEqual(len(language_table_lines), 3)

    def test_invalid_path_fails_without_traceback(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result = code_info.main(["--path", "/definitely/not/a/project"])
        self.assertEqual(result, 2)
        self.assertIn("path is not a directory", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
