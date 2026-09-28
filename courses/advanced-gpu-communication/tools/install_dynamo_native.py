#!/usr/bin/env python3
"""Build Dynamo's NIXL and NIXL-EP together against its prepared native UCX."""

from __future__ import annotations

import argparse
from email.parser import BytesParser
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import zipfile

SOURCE_COMMIT = "de8115ca97d3f8fb63a4988e9b4d4a038b2e0f72"
PACKAGE = "nixl-cu13"
VERSION = "1.3.2"
HASHED_UCX = re.compile(rb"lib(?:ucp|uct|ucs|ucm)-[0-9a-f]+\.so")
RUNTIME_PROBE = """
import importlib.metadata as metadata, json, sys, torch
print(json.dumps({
    'python': list(sys.version_info[:2]),
    'torch': torch.__version__, 'cuda': torch.version.cuda,
    'packages': {d.metadata['Name'].lower().replace('_', '-'): d.version
                 for d in metadata.distributions()},
    'site': str(metadata.distribution('nixl-cu13').locate_file('.')),
}))
"""


def inspect_wheel(path: Path) -> dict:
    """Reject the incomplete closure that can reintroduce bundled UCX through EP."""
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or any(
            PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts
            for name in names
        ):
            raise ValueError("Wheel has duplicate or escaping members")
        metadata_names = [
            name for name in names if name.endswith(".dist-info/METADATA")
        ]
        if len(metadata_names) != 1:
            raise ValueError("Wheel must have exactly one package metadata record")
        metadata = BytesParser().parsebytes(archive.read(metadata_names[0]))
        if metadata["Name"] != PACKAGE or metadata["Version"] != VERSION:
            raise ValueError("Wheel package or version does not match pinned NIXL")
        required = {
            "NIXL binding": "nixl_cu13/_bindings.cpython-312-x86_64-linux-gnu.so",
            "NIXL-EP binding": (
                "nixl_ep_cu13/nixl_ep_cpp_torch211.cpython-312-x86_64-linux-gnu.so"
            ),
            "NIXL library": ".nixl_cu13.mesonpy.libs/libnixl.so",
            "UCX plugin": ".nixl_cu13.mesonpy.libs/plugins/libplugin_UCX.so",
        }
        for label, name in required.items():
            if name not in names or not archive.read(name).startswith(b"\x7fELF"):
                raise ValueError(f"Wheel missing native {label}")
        native = {}
        for name in names:
            if "nixl_cu13.libs/" in name:
                raise ValueError("Wheel contains a bundled native dependency directory")
            if ".so" in PurePosixPath(name).name:
                raw = archive.read(name)
                if HASHED_UCX.search(raw):
                    raise ValueError("Wheel references a bundled hashed UCX dependency")
                native[name] = hashlib.sha256(raw).hexdigest()
    return {"package": PACKAGE, "version": VERSION, "native_files": native}


def command(
    argv: list[str], *, env: dict | None = None, log: Path | None = None
) -> str:
    result = subprocess.run(argv, env=env, text=True, capture_output=True, check=False)
    if log is not None:
        with log.open("x") as stream:
            stream.write(result.stdout)
            stream.write(result.stderr)
    if result.returncode:
        raise RuntimeError(
            f"{Path(argv[0]).name} failed; inspect the private build log"
        )
    return result.stdout


def runtime_state(python: Path, env: dict) -> dict:
    state = json.loads(command([str(python), "-c", RUNTIME_PROBE], env=env))
    packages = state["packages"]
    expected = {"ai-dynamo": "1.4.2", "vllm": "0.26.0", PACKAGE: VERSION}
    if (
        state["python"] != [3, 12]
        or not state["torch"].startswith("2.11.")
        or not (state["cuda"] or "").startswith("13.")
        or any(packages.get(name) != version for name, version in expected.items())
    ):
        raise ValueError(
            "Expected the pinned Python 3.12 / Dynamo / Torch 2.11 CUDA 13 stack"
        )
    return state


def install(python: Path, ucx: Path, prefix: Path) -> dict:
    os.umask(0o077)
    if prefix.exists() or prefix.is_symlink():
        raise ValueError("Build prefix exists; preserve and inspect the prior attempt")
    if (
        not (ucx / "include/ucp/api/ucp.h").is_file()
        or not (ucx / "lib/libucp.so").is_file()
    ):
        raise ValueError(
            "UCX prefix needs CUDA-aware development headers and libraries"
        )
    if not python.is_file() or not os.access(python, os.X_OK):
        raise ValueError("Select the isolated Dynamo Python executable")
    for tool in ("uv", "git", "nvcc", "ninja", "pkg-config", "c++"):
        if shutil.which(tool) is None:
            raise ValueError(f"Missing owner-prepared build tool: {tool}")
    # Preserve the venv interpreter path; resolving its symlink loses venv selection.
    python = python.absolute()
    ucx = ucx.resolve()
    prefix = prefix.absolute()
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    before = runtime_state(python, env)
    if not re.search(r"release 13\.", command(["nvcc", "--version"], env=env)):
        raise ValueError("The native build needs the target runtime's CUDA 13 compiler")
    prefix.mkdir(mode=0o700)
    source, build_tools, wheels = (
        prefix / name for name in ("source", "build-tools", "wheels")
    )
    command(
        [
            "git",
            "clone",
            "--branch",
            "v1.3.2",
            "https://github.com/ai-dynamo/nixl.git",
            str(source),
        ],
        env=env,
        log=prefix / "source.log",
    )
    actual = command(["git", "-C", str(source), "rev-parse", "HEAD"], env=env).strip()
    if actual != SOURCE_COMMIT:
        raise ValueError("NIXL source revision differs from the reviewed commit")
    command(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(python),
            "--target",
            str(build_tools),
            "meson-python",
            "pybind11",
            "tomlkit",
            "build",
            "types-PyYAML",
            "pytest",
            "setuptools>=80.9.0,<82",
            "patchelf==0.17.2.4",
        ],
        env=env,
        log=prefix / "build-tools.log",
    )
    build_env = dict(
        env,
        PYTHONPATH=str(build_tools),
        PATH=str(build_tools / "bin") + os.pathsep + env["PATH"],
    )
    command(
        [
            str(python),
            str(source / "contrib/tomlutil.py"),
            "--wheel-name",
            PACKAGE,
            str(source / "pyproject.toml"),
        ],
        env=build_env,
        log=prefix / "wheel-metadata.log",
    )
    command(
        [
            str(python),
            "-m",
            "build",
            "--wheel",
            "--no-isolation",
            "--outdir",
            str(wheels),
            f"-Csetup-args=-Ducx_path={ucx}",
            "-Csetup-args=-Denable_plugins=UCX",
            "-Csetup-args=-Dnixl_cuda_arch_list=90",
            "-Csetup-args=-Dbuild_nixl_ep=true",
            "-Ccompile-args=-j8",
            "-Csetup-args=-Dbuild_tests=false",
            "-Csetup-args=-Dbuild_examples=false",
            str(source),
        ],
        env=build_env,
        log=prefix / "build.log",
    )
    candidates = list(wheels.glob("*.whl"))
    if len(candidates) != 1:
        raise ValueError("Expected one complete NIXL wheel")
    wheel = candidates[0]
    receipt = inspect_wheel(wheel)
    command(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(python),
            "--no-deps",
            "--reinstall-package",
            PACKAGE,
            str(wheel),
        ],
        env=env,
        log=prefix / "install.log",
    )
    after = runtime_state(python, env)
    if before["packages"] != after["packages"]:
        raise ValueError(
            "Native installation changed the pinned runtime package versions"
        )
    for name, expected in receipt["native_files"].items():
        if (
            hashlib.sha256((Path(after["site"]) / name).read_bytes()).hexdigest()
            != expected
        ):
            raise ValueError("Installed native library differs from the checked wheel")
    command(
        ["uv", "pip", "check", "--python", str(python)],
        env=env,
        log=prefix / "package-check.log",
    )
    receipt.update(
        source_commit=SOURCE_COMMIT,
        wheel_sha256=hashlib.sha256(wheel.read_bytes()).hexdigest(),
        ucx_prefix=str(ucx),
        runtime_packages=after["packages"],
        native_runtime_qualified=False,
    )
    with (prefix / "installation.json").open("x") as stream:
        json.dump(receipt, stream, indent=2)
        stream.write("\n")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--python", type=Path, required=True, help="Isolated Dynamo venv Python"
    )
    parser.add_argument(
        "--ucx-prefix",
        type=Path,
        required=True,
        help="Prepared UCX for the same CUDA/RDMA runtime",
    )
    parser.add_argument(
        "--prefix",
        type=Path,
        required=True,
        help="New private source/build/receipt directory",
    )
    args = parser.parse_args()
    try:
        install(args.python, args.ucx_prefix, args.prefix)
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
        parser.exit(1, f"ERROR: {exc}\n")
    print(
        "Installed joint NIXL/NIXL-EP candidate. Complete allocated-worker and serving checks."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
