"""Compile-only recipes for the catalog's native dependencies."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess


def install(installer, spec, prefix, values, name):
    def path(value):
        return installer.expand(value, values)

    def run(argv, cwd=None, env=None):
        installer.run(
            [path(str(word)) for word in argv], label=name, cwd=cwd or prefix, env=env
        )

    environment = (
        {
            "PATH": path("{toolchain}/toolkit/bin:{build-tools}/venv/bin:")
            + os.defpath,
            "CUDA_HOME": path("{toolchain}/toolkit"),
            "CUDA_PATH": path("{toolchain}/toolkit"),
            "LD_LIBRARY_PATH": path("{toolchain}/toolkit/lib64"),
        }
        if "toolchain" in values
        else {}
    )
    recipe = spec["recipe"]
    if recipe == "toolchain":
        toolkit = prefix / "toolkit"
        toolkit.mkdir(mode=0o700)
        for item in spec["archives"]:
            unpack = prefix / item["name"]
            unpack.mkdir(mode=0o700)
            installer.archive(item, unpack, name)
            roots = list(unpack.iterdir())
            if len(roots) != 1 or not roots[0].is_dir():
                raise RuntimeError("Unexpected CUDA redistributable layout")
            shutil.copytree(roots[0], toolkit, dirs_exist_ok=True, symlinks=True)
        # Native redistributables use lib; vendor build systems also expect lib64.
        if not (toolkit / "lib64").exists():
            (toolkit / "lib64").symlink_to("lib", target_is_directory=True)
        (toolkit / "version.json").write_text(
            json.dumps({"cuda": {"version": spec["version"]}})
        )
    elif recipe == "cuda":
        source = path("{course.custom-cuda-kernels}")
        architecture = spec["architecture"]
        extra = ["-DCOURSE_ENABLE_SM90A=ON"] if architecture == "sm90a" else []
        run(
            [
                "cmake",
                "-S",
                source,
                "-B",
                str(prefix / architecture),
                "-DCMAKE_BUILD_TYPE=Release",
                "-DCMAKE_CUDA_ARCHITECTURES=90",
                "-DCMAKE_CUDA_COMPILER={toolchain}/toolkit/bin/nvcc",
                "-DCUDAToolkit_ROOT={toolchain}/toolkit",
                "-DCUTLASS_ROOT={cutlass}/source",
                *extra,
            ],
            env=environment,
        )
        run(
            [
                "cmake",
                "--build",
                str(prefix / architecture),
                "--target",
                spec["target"],
                "--parallel",
                "4",
            ],
            env=environment,
        )
    elif recipe == "ucx":
        source = Path(path("{ucx-source}/source"))
        # Each transport owns its configure/build output; never mutate the source receipt.
        shutil.copytree(source, prefix / "source", symlinks=True)
        run(["./autogen.sh"], cwd=prefix / "source", env=environment)
        run(
            [
                "./configure",
                "--prefix={prefix}/ucx",
                "--libdir={prefix}/ucx/lib",
                "--with-cuda={toolchain}/toolkit",
                "--without-rocm",
                "--enable-mt",
            ],
            cwd=prefix / "source",
            env=environment,
        )
        run(["make", "-j4"], cwd=prefix / "source", env=environment)
        run(["make", "install"], cwd=prefix / "source", env=environment)
    elif recipe == "fabric":
        source = (
            next(iter(installer.courses.values())) / "tools/install_fabric_tools.py"
        )
        run(
            [
                "{python}",
                str(source),
                "--prefix",
                "{prefix}",
                "--jobs",
                "4",
                "--tool",
                spec["tool"],
            ],
            env=environment,
        )
    elif recipe == "nccl-tests":
        shutil.copytree(
            Path(path("{nccl-source}/source")), prefix / "source", symlinks=True
        )
        run(
            [
                "make",
                "-j4",
                "MPI=1",
                "CUDA_HOME={toolchain}/toolkit",
                "NCCL_HOME={python-advanced-gpu-communication.nccl}",
                "MPI_HOME=/usr",
            ],
            cwd=prefix / "source",
            env=environment,
        )
        # Reading the plugin list does not allocate or launch a scheduler step.
        result = subprocess.run(
            ["srun", "--mpi=list"],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        modes = re.findall(r"\bpmix(?:_v[0-9]+)?\b", result.stdout + result.stderr)
        if not modes:
            raise RuntimeError(
                "The configured Slurm installation has no PMIx plugin for Open MPI"
            )
        (prefix / "mpi.json").write_text(
            json.dumps({"mode": "pmix" if "pmix" in modes else sorted(modes)[-1]})
        )
    elif recipe == "nixl":
        environment["CMAKE_PREFIX_PATH"] = path("{etcd-client}/installed")
        environment["PKG_CONFIG_PATH"] = path("{etcd-client}/installed/lib/pkgconfig")
        environment["LIBRARY_PATH"] = path("{toolchain}/toolkit/lib64/stubs")
        environment["LD_LIBRARY_PATH"] += path(":{etcd-client}/installed/lib")
        shutil.copytree(
            Path(path("{nixl-source}/source")), prefix / "source", symlinks=True
        )
        run(
            [
                "meson",
                "setup",
                "build-course",
                ".",
                "--prefix={prefix}/nixl",
                "--libdir=lib",
                "--buildtype=release",
                "-Denable_plugins=UCX",
                "-Ducx_path={ucx-nixl}/ucx",
                "-Dnixl_cuda_arch_list=90",
                "-Dbuild_tests=false",
                "-Dbuild_examples=false",
            ],
            cwd=prefix / "source",
            env=environment,
        )
        run(
            ["meson", "compile", "-C", "build-course", "-j", "4"],
            cwd=prefix / "source",
            env=environment,
        )
        run(
            ["meson", "install", "-C", "build-course"],
            cwd=prefix / "source",
            env=environment,
        )
        source = prefix / "source/benchmark/nixlbench"
        run(
            [
                "meson",
                "setup",
                "build-course",
                ".",
                "--prefix={prefix}/nixlbench",
                "--libdir=lib",
                "--buildtype=release",
                "-Dnixl_path={prefix}/nixl",
            ],
            cwd=source,
            env=environment,
        )
        run(
            ["meson", "compile", "-C", "build-course", "-j", "4"],
            cwd=source,
            env=environment,
        )
        run(["meson", "install", "-C", "build-course"], cwd=source, env=environment)
        config = (prefix / "nixlbench/include/nixlbench/config.h").read_text()
        for feature in ("HAVE_ETCD", "HAVE_CUDA"):
            if not re.search(r"^#define\s+" + feature + r"\s+1\s*$", config, re.M):
                raise RuntimeError(f"NIXLBench was built without {feature}")
    elif recipe == "etcd-client":
        run(
            [
                "cmake",
                "-S",
                "{etcd-client-source}/source",
                "-B",
                "{prefix}/build",
                "-DBUILD_ETCD_CORE_ONLY=ON",
                "-DBUILD_ETCD_TESTS=OFF",
                "-DCMAKE_BUILD_TYPE=Release",
                "-DCMAKE_INSTALL_LIBDIR=lib",
                "-DCMAKE_INSTALL_PREFIX={prefix}/installed",
            ]
        )
        run(["cmake", "--build", "{prefix}/build", "--parallel", "4"])
        run(["cmake", "--install", "{prefix}/build"])
    elif recipe == "bridge":
        shutil.copytree(
            Path(path("{bridge-source}/source")), prefix / "source", symlinks=True
        )
        run(
            [
                "{build-tools}/venv/bin/uv",
                "venv",
                "--seed",
                "--python",
                "{python}",
                "{prefix}/venv",
            ],
            env=environment,
        )
        run(
            [
                "uv",
                "pip",
                "install",
                "--python",
                "{prefix}/venv/bin/python",
                *spec["base_packages"],
            ],
            env=environment,
        )
        environment["UV_PROJECT_ENVIRONMENT"] = str(prefix / "venv")
        for options in (
            ["--only-group", "build"],
            ["--no-default-groups", "--extra", "te"],
        ):
            run(
                ["uv", "sync", "--locked", "--inexact", *options],
                cwd=prefix / "source",
                env=environment,
            )
        run(
            ["uv", "pip", "check", "--python", "{prefix}/venv/bin/python"],
            env=environment,
        )
    else:
        raise ValueError(f"Unknown native recipe: {recipe}")
