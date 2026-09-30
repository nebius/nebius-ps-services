"""Execute rendered pytest and wheel templates in disposable local projects."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[1]


def render(name: str) -> str:
    text = (ROOT / "assets" / name).read_text()
    for key, value in {
        "package_name": "sample_pkg",
        "project_slug": "sample-project",
        "short_description": "Sample",
        "author_name": "Author",
        "default_branch": "main",
    }.items():
        text = text.replace("{{" + key + "}}", value)
    return text


class RenderedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="python-template-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.write(
            "pyproject.toml",
            render("pyproject.toml.template").replace(
                "  # Register contract, e2e and smoke when generating those tests.",
                '  "contract: interface", "e2e: workflow", "smoke: critical subset",',
            ),
        )
        self.write("tests/conftest.py", render("tests-conftest.py.template"))

    def write(self, path: str, content: str) -> None:
        destination = self.root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(textwrap.dedent(content))

    def pytest(self, *args: str, socket_plugin: bool = True, extra_env=None):
        env = {
            **os.environ,
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTEST_ADDOPTS": "",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        if extra_env:
            env.update(extra_env)
        plugins = ["-p", "socket"] if socket_plugin else []
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                *plugins,
                "-p",
                "pytest_cov",
                "-q",
                "-o",
                f"cache_dir={self.root}/cache",
                *args,
            ],
            cwd=self.root,
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
        )

    def passed(self, result, expected: str) -> None:
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(expected, result.stdout)

    def test_default_full_and_layer_selections(self) -> None:
        for layer in ("unit", "integration", "contract", "e2e"):
            self.write(f"tests/{layer}/test_same.py", "def test_behavior(): pass\n")
        self.write(
            "tests/integration/test_properties.py",
            """
            import pytest
            @pytest.mark.slow
            def test_slow(): pass
            @pytest.mark.external
            def test_live(): raise AssertionError('must not run')
        """,
        )
        self.passed(self.pytest(), "3 passed, 3 deselected")
        self.passed(self.pytest("-m", "not external"), "5 passed, 1 deselected")
        self.passed(
            self.pytest("tests/integration", "-m", "not external"),
            "2 passed, 1 deselected",
        )
        result = self.pytest("--collect-only")
        self.passed(result, "3/6 tests collected")
        self.assertIn("tests/contract/test_same.py::test_behavior", result.stdout)
        self.assertNotIn("tests/e2e/test_same.py::test_behavior", result.stdout)

    def test_importlib_and_strict_xpass(self) -> None:
        config = tomllib.loads(render("pyproject.toml.template"))["tool"]["pytest"][
            "ini_options"
        ]
        self.assertIn("--import-mode=importlib", config["addopts"])
        self.write(
            "tests/unit/test_xpass.py",
            """
            import pytest
            @pytest.mark.xfail(reason='known defect')
            def test_fixed(): pass
        """,
        )
        result = self.pytest()
        self.assertEqual(result.returncode, 1)
        self.assertIn("XPASS(strict)", result.stdout)

    def test_unit_network_apis_and_external_flag_stay_blocked(self) -> None:
        self.write(
            "tests/unit/test_network.py",
            """
            import socket
            import pytest
            from pytest_socket import SocketBlockedError
            @pytest.mark.parametrize('family', [socket.AF_INET, socket.AF_INET6])
            @pytest.mark.parametrize('kind', [socket.SOCK_STREAM, socket.SOCK_DGRAM])
            def test_sockets(family, kind):
                with pytest.raises(SocketBlockedError):
                    socket.socket(family, kind)
            def test_dns_and_high_level():
                for operation in (lambda: socket.getaddrinfo('example.invalid', 80),
                                  lambda: socket.create_connection(('127.0.0.1', 80))):
                    with pytest.raises(SocketBlockedError): operation()
        """,
        )
        self.passed(self.pytest("--run-external"), "5 passed")

    def test_network_escape_policy(self) -> None:
        cases = [
            ("@pytest.mark.external", "", "Invalid network permission"),
            ("@pytest.mark.local_network", "", "Invalid network permission"),
            ("@pytest.mark.enable_socket", "", "escape markers"),
            ('@pytest.mark.allow_hosts(["127.0.0.1"])', "", "escape markers"),
            ("", "socket_enabled", "bypasses project"),
            ("@pytest.mark.integration", "", "Conflicting test layer"),
        ]
        for marker, fixture, error in cases:
            with self.subTest(marker=marker, fixture=fixture):
                self.write(
                    "tests/unit/test_escape.py",
                    f"import pytest\n{marker}\ndef test_escape({fixture}): pass\n",
                )
                result = self.pytest()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(error, result.stdout + result.stderr)
        self.write(
            "tests/unit/test_escape.py",
            """
            import pytest
            @pytest.fixture
            def indirect(socket_enabled): pass
            def test_escape(indirect): pass
        """,
        )
        self.assertIn("bypasses project", self.pytest().stderr)

    def test_missing_plugin_global_overrides_and_empty_selection_fail(self) -> None:
        self.write("tests/unit/test_one.py", "def test_one(): pass\n")
        self.assertNotEqual(self.pytest(socket_plugin=False).returncode, 0)
        for option in ("--force-enable-socket", "--allow-hosts=127.0.0.1"):
            result = self.pytest(option)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("global network overrides", result.stderr)
        self.assertEqual(self.pytest("-m", "e2e").returncode, 5)

    def test_external_requires_flag_then_target_before_sockets(self) -> None:
        self.write(
            "tests/integration/test_live.py",
            """
            import socket
            import pytest
            @pytest.mark.external
            def test_live(external_network):
                with socket.socket(): pass  # No outbound connection.
        """,
        )
        node = "tests/integration/test_live.py::test_live"
        result = self.pytest(node, "-m", "external")
        self.assertIn("requires --run-external", result.stdout)
        result = self.pytest(node, "-m", "external", "--run-external")
        self.assertIn("Define external_target", result.stdout)
        self.write(
            "tests/integration/conftest.py",
            """
            import pytest
            @pytest.fixture
            def external_target():
                return 'disposable-test-fixture'
        """,
        )
        self.passed(self.pytest(node, "-m", "external", "--run-external"), "1 passed")
        self.write(
            "tests/unit/test_after.py",
            """
            import socket
            import pytest
            from pytest_socket import SocketBlockedError
            def test_denied():
                with pytest.raises(SocketBlockedError): socket.socket()
        """,
        )
        self.passed(
            self.pytest("tests/integration", "tests/unit", "-m", "", "--run-external"),
            "2 passed",
        )

    def test_controlled_loopback_and_blocked_nonloopback_connect(self) -> None:
        self.write(
            "tests/integration/test_local.py",
            """
            import socket
            import pytest
            from pytest_socket import SocketConnectBlockedError
            @pytest.mark.local_network
            def test_tcp():
                with socket.socket() as server, socket.socket() as client:
                    server.bind(('127.0.0.1', 0))
                    server.listen(1)
                    client.settimeout(1)
                    client.connect(server.getsockname())
                    peer, _ = server.accept()
                    peer.close()
                with socket.socket() as denied:
                    with pytest.raises(SocketConnectBlockedError):
                        denied.connect(('192.0.2.1', 80))
        """,
        )
        self.passed(self.pytest(), "1 passed")

    def test_fixture_lifecycle_and_documented_limits(self) -> None:
        self.write(
            "tests/integration/test_lifecycle.py",
            """
            import socket
            import subprocess
            import sys
            import pytest
            from pytest_socket import SocketBlockedError
            # Collection is deliberately unguarded: no connection is attempted.
            original_connect_ex = socket.socket.connect_ex
            original_sendto = socket.socket.sendto
            with socket.socket(): pass
            @pytest.fixture(scope='session')
            def session_guard():
                with pytest.raises(SocketBlockedError): socket.socket()
                yield
                # pytest-socket restores sockets before fixture finalizers.
                with socket.socket(): pass
            def test_session_setup(session_guard):
                with pytest.raises(SocketBlockedError): socket.socket()
                subprocess.run([sys.executable, '-c', 'import socket; socket.socket().close()'],
                               check=True, timeout=5)
            @pytest.mark.local_network
            def test_allow_hosts_limits():
                assert socket.socket.connect_ex is original_connect_ex
                assert socket.socket.sendto is original_sendto
        """,
        )
        self.passed(self.pytest(), "2 passed")

    def test_unix_socket_exception_and_optional_xdist(self) -> None:
        self.write(
            "tests/unit/test_async.py",
            """
            import asyncio
            import socket
            import pytest
            from pytest_socket import SocketBlockedError
            def test_event_loop():
                async def operation(): return 42
                assert asyncio.run(operation()) == 42
                with pytest.raises(SocketBlockedError): socket.socket(socket.AF_INET)
        """,
        )
        self.passed(
            self.pytest("--allow-unix-socket", "-p", "xdist.plugin", "-n", "2"),
            "1 passed",
        )

    def test_subprocess_coverage_is_explicit(self) -> None:
        config = self.root / "pyproject.toml"
        config.write_text(
            config.read_text().replace(
                'source = ["sample_pkg"]',
                'source = ["sample_pkg"]\npatch = ["subprocess"]',
            )
        )
        self.write("sample_pkg.py", "def child_only():\n    return 42\n")
        self.write(
            "tests/integration/test_child.py",
            """
            import subprocess
            import sys
            def test_child():
                subprocess.run([sys.executable, '-c', 'from sample_pkg import child_only; assert child_only() == 42'],
                               check=True, timeout=5)
        """,
        )
        report = self.root / "coverage.json"
        self.passed(
            self.pytest(
                "--cov=sample_pkg",
                f"--cov-report=json:{report}",
                extra_env={"COVERAGE_FILE": str(self.root / "coverage-data")},
            ),
            "1 passed",
        )
        data = json.loads(report.read_text())
        self.assertIn(2, data["files"]["sample_pkg.py"]["executed_lines"])

    def test_make_targets_match_documented_selections(self) -> None:
        self.write("Makefile", render("Makefile.template"))
        for target, expected in [
            ("test-fast", "pytest\n"),
            ("test-full", 'pytest -m "not external"'),
            ("coverage", "--cov=sample_pkg"),
            ("smoke-wheel", "scripts/smoke-wheel.py"),
        ]:
            result = subprocess.run(
                ["make", "-n", target],
                cwd=self.root,
                text=True,
                capture_output=True,
                check=True,
                timeout=5,
            )
            self.assertIn("uv lock --check", result.stdout)
            self.assertIn("uv sync --locked", result.stdout)
            self.assertIn(expected, result.stdout)


class WheelSmokeTests(unittest.TestCase):
    def test_actual_wheel_and_negative_controls(self) -> None:
        if not shutil.which("uv"):
            self.fail("uv is required for the installed-wheel acceptance test")
        with tempfile.TemporaryDirectory(prefix="wheel-template-test-") as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            package = root / "src/sample_pkg"
            package.mkdir(parents=True)
            (package / "__init__.py").write_text("")
            (package / "cli.py").write_text(
                'def main():\n    print("usage: sample-project --help")\n'
            )
            (package / "required.txt").write_text("packaged resource\n")
            metadata = """
[build-system]
requires = ["setuptools==84.0.0"]
build-backend = "setuptools.build_meta"
[project]
name = "sample-project"
version = "1.0.0"
requires-python = ">=3.11,<3.14"
[project.scripts]
sample-project = "sample_pkg.cli:main"
[tool.setuptools.packages.find]
where = ["src"]
[tool.setuptools.package-data]
sample_pkg = ["required.txt"]
"""
            (root / "pyproject.toml").write_text(metadata)
            helper = render("smoke-wheel.py.template").replace(
                "RESOURCES: tuple[str, ...] = ()", 'RESOURCES = ("required.txt",)'
            )
            (root / "scripts/smoke-wheel.py").write_text(helper)
            env = {
                **os.environ,
                "UV_PYTHON": sys.executable,
                "UV_PYTHON_DOWNLOADS": "never",
            }
            subprocess.run(
                ["uv", "lock"],
                cwd=root,
                env=env,
                check=True,
                capture_output=True,
                timeout=60,
            )

            def execute():
                return subprocess.run(
                    [sys.executable, str(root / "scripts/smoke-wheel.py")],
                    cwd=root,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )

            result = execute()
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertFalse((root / ".venv").exists())
            self.assertFalse((root / "dist").exists())
            # Break the entrypoint without changing metadata or the lock.
            (package / "cli.py").write_text("def renamed(): pass\n")
            result = execute()
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("main", result.stderr)
            (package / "cli.py").write_text("def main(): pass\n")
            (package / "required.txt").unlink()
            result = execute()
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("required.txt", result.stderr)


if __name__ == "__main__":
    unittest.main()
