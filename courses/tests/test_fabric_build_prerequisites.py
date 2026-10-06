"""Reject missing fabric development libraries before creating partial builds."""

import os
import shlex
import shutil
import subprocess

import pytest
from test_course_review_fixes import load_lab


INSTALLER = "advanced-gpu-communication/tools/install_fabric_tools.py"


def test_missing_pci_stops_before_clone_or_prefix_mutation(
    tmp_path, monkeypatch, capsys
):
    with load_lab(INSTALLER) as installer:
        monkeypatch.setattr(
            "sys.argv", ["install_fabric_tools.py", "--prefix", str(tmp_path), "--tool", "perftest"]
        )
        monkeypatch.setattr(installer.shutil, "which", lambda _: "/available")
        monkeypatch.setenv("CC", "course-test-cc")

        def run(argv, **kwargs):
            if argv[0] == "course-test-cc":
                return subprocess.CompletedProcess(argv, 1, "", "pci/pci.h: not found")
            pytest.fail("A network/build command ran before the PCI prerequisite check")

        monkeypatch.setattr(installer.subprocess, "run", run)
        with pytest.raises(SystemExit) as failure:
            installer.main()
        assert failure.value.code == 2
        assert "pci/pci.h" in capsys.readouterr().err
        assert not (tmp_path / "fabric").exists()


def test_pci_probe_accepts_owner_compiler_and_private_library_paths(
    tmp_path, monkeypatch
):
    compiler, archiver = shutil.which("cc"), shutil.which("ar")
    if not compiler or not archiver:
        pytest.skip("A native C compiler and archiver are required")
    prefix = tmp_path / "private development files"
    include = prefix / "include" / "pci"
    include.mkdir(parents=True)
    library = prefix / "lib"
    library.mkdir()
    (include / "pci.h").write_text(
        "struct pci_access;\nstruct pci_access *pci_alloc(void);\n"
        "void pci_init(struct pci_access *);\nvoid pci_cleanup(struct pci_access *);\n"
    )
    source = prefix / "pci.c"
    source.write_text(
        "struct pci_access { int unused; };\nstatic struct pci_access fixture;\n"
        "struct pci_access *pci_alloc(void) { return &fixture; }\n"
        "void pci_init(struct pci_access *p) { (void)p; }\n"
        "void pci_cleanup(struct pci_access *p) { (void)p; }\n"
    )
    obj = prefix / "pci.o"
    subprocess.run([compiler, "-c", str(source), "-o", str(obj)], check=True)
    subprocess.run([archiver, "rcs", str(library / "libpci.a"), str(obj)], check=True)
    monkeypatch.setenv("CC", shlex.quote(compiler))
    monkeypatch.setenv("CPPFLAGS", "-I" + shlex.quote(str(include.parent)))
    monkeypatch.setenv("LDFLAGS", "-L" + shlex.quote(str(library)))
    monkeypatch.setenv("CFLAGS", "")
    monkeypatch.setenv("LIBS", "")
    with load_lab(INSTALLER) as installer:
        installer.check_pci_development_library()


@pytest.mark.parametrize("previous", [None, "", "/owner/cuda/lib:/owner/mpi/lib"])
def test_installed_environment_finds_cuda_validation_plugin(
    tmp_path, monkeypatch, previous
):
    prefix = tmp_path / "course tools 'quoted'"
    prefix.mkdir()
    with load_lab(INSTALLER) as installer:
        monkeypatch.setattr(
            "sys.argv", ["install_fabric_tools.py", "--prefix", str(prefix), "--tool", "perftest"]
        )
        monkeypatch.setattr(installer.shutil, "which", lambda _: "/available")
        monkeypatch.setattr(installer, "check_pci_development_library", lambda: None)

        def build(argv, cwd=None):
            if argv[0:2] == ["git", "clone"]:
                installer.Path(argv[-1]).mkdir(parents=True)
            for name in [
                "nvbandwidth-source/build/nvbandwidth",
                "perftest/bin/ib_write_bw",
                "perftest/bin/ib_read_lat",
            ]:
                binary = prefix / "fabric" / name
                binary.parent.mkdir(parents=True, exist_ok=True)
                binary.write_bytes(b"build fixture")

        monkeypatch.setattr(installer, "run", build)
        monkeypatch.setattr(
            installer.subprocess,
            "check_output",
            lambda argv, cwd, **kwargs: installer.PINS[
                cwd.name.removesuffix("-source")
            ][1],
        )
        installer.main()
    environment = dict(os.environ)
    environment.pop("LD_LIBRARY_PATH", None)
    if previous is not None:
        environment["LD_LIBRARY_PATH"] = previous
    result = subprocess.run(
        [
            "bash",
            "-c",
            'set -eu; source "$1"; printf "%s\\n%s" "$COURSE_TOOLS" "$LD_LIBRARY_PATH"',
            "test",
            str(prefix / "fabric/environment.sh"),
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    expected = str(prefix / "fabric/perftest/lib")
    if previous:
        expected += ":" + previous
    assert result.stdout.splitlines() == [str(prefix), expected]
