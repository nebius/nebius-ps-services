"""Bounded installation subprocesses; no scheduler or GPU qualification commands."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tarfile
import urllib.request

from . import catalog as definitions
from .state import atomic_json, digest, file_digest, private_dir


class Installer:
    def __init__(self, root, courses, catalog, state):
        self.root, self.courses, self.catalog, self.state = (
            root,
            courses,
            catalog,
            state,
        )
        self.records = {}
        self.logs = private_dir(state.root / "setup-logs")

    def environment(self, extra=None):
        environment = {
            **os.environ,
            "DEBIAN_FRONTEND": "noninteractive",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_CACHE_DIR": str(self.state.cache / "pip"),
            "CUDA_VISIBLE_DEVICES": "",
            "CMAKE_CUDA_ARCHITECTURES": "90",
            "TORCH_CUDA_ARCH_LIST": "9.0",
            "CARGO_BUILD_JOBS": "4",
            "MAX_JOBS": "4",
        }
        # Do not propagate a selected course's Python/library state into another installer.
        for name in (
            "PYTHONPATH",
            "PYTHONHOME",
            "VIRTUAL_ENV",
            "LD_PRELOAD",
            "LD_LIBRARY_PATH",
        ):
            environment.pop(name, None)
        environment.update(extra or {})
        return environment

    def run(
        self,
        argv,
        *,
        label="install",
        cwd=None,
        env=None,
        transaction=False,
        capture_output=False,
    ):
        if Path(str(argv[0])).name in {
            "sbatch",
            "srun",
            "ctest",
            "nvidia-smi",
            "nsys",
            "ncu",
        }:
            raise ValueError("Runtime qualification does not belong in setup")
        environment = self.environment(env)
        log = self.logs / (label + ".log")
        fd = os.open(log, os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "a+") as stream:
            start = stream.tell()
            process = None
            interrupted = None
            interruptible = False

            def stop(signum, _frame):
                nonlocal interrupted
                interrupted = signum
                # Defer until Popen has returned the child handle. During cleanup,
                # repeated signals must not release the setup lock prematurely.
                if interruptible and not transaction:
                    raise SystemExit(128 + signum)

            previous = {
                signum: signal.signal(signum, stop)
                for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)
            }
            try:
                process = subprocess.Popen(
                    [str(v) for v in argv],
                    cwd=cwd,
                    env=environment,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                interruptible = True
                if interrupted is not None and not transaction:
                    raise SystemExit(128 + interrupted)
                # A non-root controller cannot terminate sudo's root-owned
                # descendants. Keep the lock until the transaction completes.
                status = process.wait(timeout=None if transaction else 7200)
                if interrupted is not None:
                    raise SystemExit(128 + interrupted)
            except BaseException as exc:
                interruptible = False
                if process is not None:
                    if not transaction:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    process.wait()
                if isinstance(exc, subprocess.TimeoutExpired):
                    raise RuntimeError(
                        f"{label} timed out; inspect .runtime/setup-logs/{label}.log"
                    ) from None
                raise
            finally:
                for signum, handler in previous.items():
                    signal.signal(signum, handler)
            if capture_output and not status:
                stream.seek(start)
                output = stream.read()
        if status:
            raise RuntimeError(
                f"{label} failed (exit {status}); inspect .runtime/setup-logs/{label}.log"
            )
        return output if capture_output else None

    def values(self):
        result = {
            "root": str(self.root),
            "python": sys.executable,
            "tools": str(self.state.root.parent / ".profiling-tools"),
        }
        for name, record in self.records.items():
            result[name] = record["prefix"]
            result.update(
                {f"{name}.{key}": value for key, value in record["values"].items()}
            )
        for slug, course in self.courses.items():
            result[f"course.{slug}"] = str(course)
        return result

    @staticmethod
    def expand(value, values):
        return re.sub(r"\{([^{}]+)\}", lambda match: str(values[match[1]]), value)

    def install(self, name):
        spec = self.catalog["components"][name]
        inputs = definitions.component_inputs(
            self.catalog, self.root, self.courses, name
        )
        inputs["python"] = list(sys.version_info[:2])
        inputs["dependencies"] = {
            key: self.records[key] for key in spec.get("depends", [])
        }
        identity = digest(inputs)
        completed = self.state.completed(name, identity)
        if completed and self.check_installed(spec, completed):
            self.records[name] = completed
            if spec["kind"] == "image":
                self.record_image(name, completed)
            print(f"Reused {name}", flush=True)
            return
        prefix = self.state.generation(name, identity)
        values = {**self.values(), "prefix": str(prefix)}
        print(f"Installing {name}", flush=True)
        kind = spec["kind"]
        python_environment = kind == "python" or spec.get("recipe") == "bridge"
        if kind == "python":
            self.python(spec, prefix, values, name)
        elif kind == "git":
            self.run(
                [
                    "git",
                    "clone",
                    "--filter=blob:none",
                    "--no-checkout",
                    spec["url"],
                    prefix / "source",
                ],
                label=name,
            )
            self.run(
                ["git", "checkout", "--detach", spec["revision"]],
                cwd=prefix / "source",
                label=name,
            )
            actual = subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=prefix / "source",
                text=True,
                timeout=30,
            ).strip()
            if actual != spec["revision"]:
                raise RuntimeError(f"Source revision mismatch: {name}")
            if spec.get("submodules"):
                self.run(
                    ["git", "submodule", "update", "--init", "--recursive"],
                    cwd=prefix / "source",
                    label=name,
                )
        elif kind == "image":
            self.image(spec, prefix, name)
        elif kind == "models":
            self.models(spec, prefix, values, name)
        elif kind == "archive":
            self.archive(spec, prefix, name)
        elif kind == "recipe":
            from .recipes import install

            install(self, spec, prefix, values, name)
        elif kind not in {"commands", "triton"}:
            raise ValueError(f"Unknown installation kind: {kind}")
        if kind == "triton":
            self.triton(spec, prefix, values, name)
        for command in spec.get("commands", []):
            env = {
                key: self.expand(value, values)
                for key, value in command.get("env", {}).items()
            }
            self.run(
                [self.expand(word, values) for word in command["argv"]],
                label=name,
                cwd=self.expand(command.get("cwd", "{prefix}"), values),
                env=env,
            )
        if python_environment:
            self.run(
                [str(prefix / "venv/bin/python"), "-m", "pip", "check"], label=name
            )
            packages = subprocess.check_output(
                [str(prefix / "venv/bin/python"), "-m", "pip", "list", "--format=json"],
                text=True,
                timeout=60,
                env=self.environment(),
            )
            atomic_json(prefix / "packages.json", json.loads(packages))
        exports = {
            key: self.expand(value, values)
            for key, value in spec.get("values", {}).items()
        }
        if python_environment:
            exports["python"] = str(prefix / "venv/bin/python")
            exports["torchrun"] = str(prefix / "venv/bin/torchrun")
            site = prefix / "venv/lib/python3.12/site-packages"
            for library in ("cudnn", "nccl"):
                folder = site / "nvidia" / library
                if folder.is_dir():
                    exports[library] = str(folder)
        if spec.get("recipe") == "nccl-tests":
            exports["mpi"] = json.loads((prefix / "mpi.json").read_text())["mode"]
        artifacts = list(spec.get("artifacts", []))
        if python_environment:
            artifacts.append("packages.json")
            site = prefix / "venv/lib/python3.12/site-packages"
            if (site / "torch").is_dir():
                for relative in (
                    "torch/__init__.py",
                    "nvidia/cudnn/lib/libcudnn.so.9",
                    "nvidia/nccl/lib/libnccl.so.2",
                ):
                    artifacts.append(str((site / relative).relative_to(prefix)))
            artifacts.extend(
                str(p.relative_to(prefix)) for p in site.glob("transformer_engine*.so")
            )
        if kind == "image":
            exports["identity"] = (
                "sif://" + name + "@sha256:" + file_digest(prefix / "image.sif")
            )
            exports["image"] = str(prefix / "image.sif")
        self.records[name] = self.state.commit(
            name, identity, prefix, artifacts, exports
        )
        if kind == "image":
            self.record_image(name, self.records[name])

    def record_image(self, name, record):
        images = private_dir(self.state.root / "images")
        checksum = record["artifacts"]["image.sif"]
        atomic_json(images / f"{name}-{checksum}.json", record)

    def check_installed(self, spec, record):
        if spec["kind"] == "models":
            from .models import validate

            return validate(self.root, record, hashes=True)
        if spec["kind"] == "git":
            source = Path(record["prefix"]) / "source"
            for argv, expected in (
                (["git", "rev-parse", "HEAD"], spec["revision"]),
                (["git", "status", "--porcelain", "--untracked-files=no"], ""),
            ):
                result = subprocess.run(
                    argv,
                    cwd=source,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=False,
                )
                if result.returncode or result.stdout.strip() != expected:
                    return False
        if spec["kind"] == "python" or spec.get("recipe") == "bridge":
            python = Path(record["prefix"]) / "venv/bin/python"
            if not python.is_file():
                return False
            result = subprocess.run(
                [str(python), "-m", "pip", "list", "--format=json"],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
                env=self.environment(),
            )
            if result.returncode:
                return False
            saved = json.loads((Path(record["prefix"]) / "packages.json").read_text())
            if json.loads(result.stdout) != saved:
                return False
        return True

    def python(self, spec, prefix, values, name):
        self.run([sys.executable, "-m", "venv", str(prefix / "venv")], label=name)
        python = str(prefix / "venv/bin/python")
        argv = [python, "-m", "pip", "install"]
        if "requirements" in spec:
            argv += [
                "-r",
                str(
                    definitions.source_path(
                        self.root, self.courses, spec["requirements"]
                    )
                ),
            ]
        argv += spec.get("packages", [])
        argv += spec.get("pip_options", [])
        self.run(
            argv,
            label=name,
            env={
                key: self.expand(value, values)
                for key, value in spec.get("env", {}).items()
            },
        )

    def image(self, spec, prefix, name):
        cache = private_dir(self.state.cache / "apptainer")
        scratch = private_dir(self.state.cache / "apptainer-tmp")
        env = {
            "APPTAINER_CACHEDIR": str(cache),
            "APPTAINER_TMPDIR": str(scratch),
        }
        source = spec["uri"]
        if spec.get("definition"):
            definition = Path(__file__).parent / spec["definition"]
            content = definition.read_text().replace(
                "@BASE@", spec["uri"].removeprefix("docker://")
            )
            (prefix / "container.def").write_text(content)
            source = str(prefix / "container.def")
        # Host-sized compression defaults can exhaust a login pod's cgroup.
        # The build interface applies these bounds to both OCI and recipe images.
        self.run(
            [
                "apptainer",
                "build",
                "--notest",
                "--mksquashfs-args",
                "-mem 1G -processors 2",
                str(prefix / "image.sif"),
                source,
            ],
            label=name,
            env=env,
        )
        atomic_json(
            prefix / "origin.json",
            {"source": spec["uri"], "sha256": file_digest(prefix / "image.sif")},
        )

    def models(self, spec, prefix, values, name):
        from .models import reusable

        python = self.expand(spec["python"], values)
        hub = private_dir(private_dir(self.state.cache / "huggingface") / "hub")
        refresh = not reusable(self.state, name, spec["models"])
        code = (
            "import json,sys; from huggingface_hub import snapshot_download; "
            "models=json.loads(sys.argv[2]); "
            "[snapshot_download(repo_id=m['repo'],revision=m['revision'],cache_dir=sys.argv[1],"
            "force_download=json.loads(sys.argv[3])) for m in models]"
        )
        self.run(
            [
                python,
                "-c",
                code,
                str(hub),
                json.dumps(spec["models"]),
                json.dumps(refresh),
            ],
            label=name,
            env={"HF_HUB_OFFLINE": "0", "TRANSFORMERS_OFFLINE": "0"},
        )
        snapshots, inventory = {}, {}
        for model in spec["models"]:
            snapshot = (
                hub
                / ("models--" + model["repo"].replace("/", "--"))
                / "snapshots"
                / model["revision"]
            )
            if not snapshot.is_dir() or not (snapshot / "config.json").is_file():
                raise RuntimeError(f"Incomplete model snapshot: {model['repo']}")
            snapshots[model["repo"]] = {
                "revision": model["revision"],
                "path": str(snapshot),
            }
            for path in sorted(snapshot.rglob("*")):
                if path.is_file():
                    if not path.resolve().is_relative_to(hub):
                        raise ValueError("Model snapshot escapes the shared cache")
                    inventory[str(path.relative_to(hub))] = file_digest(path)
        atomic_json(prefix / "models.json", snapshots)
        atomic_json(prefix / "inventory.json", inventory)

    def triton(self, spec, prefix, values, name):
        import shutil

        source = Path(self.expand(spec["repository"], values))
        shutil.copytree(source, prefix / "repository")
        model = prefix / "repository/tensorrt_llm/1/model.yaml"
        model.write_text(
            "model: "
            + json.dumps(self.expand(spec["model"], values))
            + "\nbackend: pytorch\ntensor_parallel_size: 1\npipeline_parallel_size: 1\n"
            "triton_config:\n  max_batch_size: 0\n  decoupled: false\n"
        )
        config = prefix / "repository/tensorrt_llm/config.pbtxt"
        if (
            not config.is_file()
            or not (prefix / "repository/tensorrt_llm/1/model.py").is_file()
        ):
            raise RuntimeError(
                "Pinned TensorRT-LLM source does not contain the llmapi repository"
            )
        config.write_text(
            config.read_text()
            + "\nmax_batch_size: 0\nmodel_transaction_policy { decoupled: false }\n"
        )

    def archive(self, spec, prefix, name):
        cached = self.state.cache / (spec["sha256"] + ".tar.gz")
        if not cached.is_file() or file_digest(cached) != spec["sha256"]:
            temporary = prefix / "download.tar.gz"
            with (
                urllib.request.urlopen(spec["url"], timeout=60) as response,
                temporary.open("xb") as output,
            ):
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
            if file_digest(temporary) != spec["sha256"]:
                raise RuntimeError(f"Download checksum mismatch: {name}")
            os.replace(temporary, cached)
        class OwnedTarFile(tarfile.TarFile):
            def chown(self, member, targetpath, numeric_owner):
                # The data filter removes archive ownership. Keep the extracting
                # user's ownership without calling chown(-1, -1): some shared
                # filesystems reject that no-op before tarfile applies modes.
                if member.uid is not None or member.gid is not None:
                    raise tarfile.ExtractError("Archive ownership was not filtered")

        with OwnedTarFile.open(cached, errorlevel=2) as archive:
            archive.extractall(prefix, filter="data")
