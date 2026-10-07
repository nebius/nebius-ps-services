#!/usr/bin/env python3
"""Build pinned advanced-lab tools once in shared user storage; never install drivers."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

PINS = {
    "nvbandwidth": (
        "https://github.com/NVIDIA/nvbandwidth.git",
        "82fc4e8c6afa0babb8687793678f615b3b8d793e",
    ),
    "perftest": (
        "https://github.com/linux-rdma/perftest.git",
        "b513a77278c8061ca6c4dcd1a95d08801c6e7623",
    ),
}


def run(argv, cwd):
    subprocess.run(argv, cwd=cwd, check=True)


def check_pci_development_library():
    """Check the pinned perftest prerequisite without running a PCI operation."""
    compiler = shlex.split(os.environ.get("CC", "cc"))
    if not compiler:
        raise RuntimeError("CC must select a C compiler for the PCI development check")
    flags = {
        name: shlex.split(os.environ.get(name, ""))
        for name in ("CPPFLAGS", "CFLAGS", "LDFLAGS", "LIBS")
    }
    with tempfile.TemporaryDirectory(prefix="course-fabric-pci-") as temporary:
        root = Path(temporary)
        source = root / "probe.c"
        source.write_text(
            "#include <pci/pci.h>\n"
            "int main(void) { struct pci_access *p = pci_alloc(); "
            "pci_init(p); pci_cleanup(p); return 0; }\n"
        )
        command = [
            *compiler,
            *flags["CPPFLAGS"],
            *flags["CFLAGS"],
            str(source),
            "-o",
            str(root / "probe"),
            *flags["LDFLAGS"],
            "-lpci",
            *flags["LIBS"],
        ]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            raise RuntimeError(
                "Could not complete the PCI development check; verify CC and compiler flags"
            ) from None
        if result.returncode:
            raise RuntimeError(
                "PCI development check failed: provide pci/pci.h and libpci "
                "(libpci-dev on Debian/Ubuntu, pciutils-devel on RPM systems), "
                "and verify CC/CPPFLAGS/CFLAGS/LDFLAGS/LIBS; complete the shared README setup's owner preparation"
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prefix",
        type=Path,
        required=True,
        help="Existing shared COURSE_TOOLS directory",
    )
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--tool", choices=tuple(PINS), required=True)
    args = parser.parse_args()
    if args.jobs < 1 or args.jobs > 16:
        parser.error("--jobs must be 1..16")
    for program in (
        "git",
        "cmake",
        "make",
        "g++",
        "nvcc",
        "autoconf",
        "automake",
        "libtoolize",
        "pkg-config",
    ):
        if not shutil.which(program):
            parser.error(
                f"Missing build prerequisite: {program}; complete the shared README setup's owner preparation"
            )
    os.umask(0o077)
    try:
        if args.tool == "perftest":
            check_pci_development_library()
    except (RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    base = args.prefix.expanduser().resolve()
    if not base.is_dir():
        parser.error("Install the shared profiling tools first")
    root = base / "fabric"
    root.mkdir(mode=0o700, exist_ok=False)
    builds = {}
    for name in (args.tool,):
        url, revision = PINS[name]
        source = root / (name + "-source")
        run(
            ["git", "clone", "--filter=blob:none", "--no-checkout", url, str(source)],
            root,
        )
        run(["git", "checkout", "--detach", revision], source)
        actual = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=source, text=True
        ).strip()
        if actual != revision:
            raise SystemExit("Source revision mismatch")
        if name == "nvbandwidth":
            run(
                ["cmake", "-S", ".", "-B", "build", "-DCMAKE_BUILD_TYPE=Release"],
                source,
            )
            run(["cmake", "--build", "build", "--parallel", str(args.jobs)], source)
            binary = source / "build/nvbandwidth"
        else:
            run(["./autogen.sh"], source)
            run(
                [
                    "./configure",
                    "--prefix=" + str(root / "perftest"),
                    "--enable-cudart",
                ],
                source,
            )
            run(["make", "-j", str(args.jobs)], source)
            run(["make", "install"], source)
            binary = root / "perftest/bin/ib_write_bw"
        builds[name] = {
            "revision": revision,
            "binary": str(binary.relative_to(root)),
            "sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        }
    if args.tool == "perftest":
        latency_binary = root / "perftest/bin/ib_read_lat"
        builds["perftest_read_lat"] = {
            "revision": PINS["perftest"][1],
            "binary": str(latency_binary.relative_to(root)),
            "sha256": hashlib.sha256(latency_binary.read_bytes()).hexdigest(),
        }
    (root / "builds.json").write_text(json.dumps(builds, indent=2) + "\n")
    (root / "environment.sh").write_text(
        "export COURSE_TOOLS=" + shlex.quote(str(base)) + "\n"
        'export LD_LIBRARY_PATH="$COURSE_TOOLS/fabric/perftest/lib'
        '${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"\n'
    )
    print(
        f"Built candidate tools: {root / 'builds.json'}. Runtime qualification is still required."
    )


if __name__ == "__main__":
    main()
