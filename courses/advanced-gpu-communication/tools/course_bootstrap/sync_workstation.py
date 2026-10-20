"""Internal laptop handoff for sync-labs.sh; source transfer stays in Bash."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys
import time
import uuid
from types import SimpleNamespace

from . import handoff
from .state import atomic_json, private_dir, read_json, regular, setup_lock

PROFILE_SCHEMA = "course-sync-profile/v1"
SETUP_INPUTS = {"config": "CLUSTER_CONFIG", "target": "CLUSTER_TARGET",
                "kubeconfig": "KUBECONFIG", "context": "CLUSTER_CONTEXT"}


def execute(command, *, input=None, capture_output=False, text=False, check=False, timeout=None, env=None):
    """Own a pre-login process group, including SSH/rsync grandchildren."""
    terminal = None
    try:
        terminal = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY | os.O_CLOEXEC)
        foreground = os.tcgetpgrp(terminal)
        if foreground != os.getpgrp():
            os.close(terminal)
            terminal = None
    except OSError:
        if terminal is not None:
            os.close(terminal)
        terminal = None

    def foreground_group(group):
        if terminal is not None:
            handler = signal.signal(signal.SIGTTOU, signal.SIG_IGN)
            try:
                os.tcsetpgrp(terminal, group)
            finally:
                signal.signal(signal.SIGTTOU, handler)

    cancellation = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    previous = {number: signal.getsignal(number) for number in cancellation}
    child = None

    def stop(number):
        denied = None
        try:
            os.killpg(child.pid, number)
        except ProcessLookupError:
            pass
        except PermissionError as exc:
            denied = exc  # Darwin may report EPERM for an all-zombie group.
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            child.poll()
            try:
                os.killpg(child.pid, 0)
            except ProcessLookupError:
                break
            except PermissionError as exc:
                denied = exc
                break
            time.sleep(0.05)
        # Group survival is independent of the leader: a grandchild may ignore
        # TERM after its parent exits. Always escalate the remaining group.
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError as exc:
            denied = exc
        try:
            child.wait(timeout=1)
        except subprocess.TimeoutExpired:
            if denied is not None:
                raise denied
            raise
    def cancel(number, _frame):
        # Do not call Popen.wait inside its own signal-interrupted wait: its
        # internal waitpid lock is not reentrant. Cleanup waits after unwinding.
        for item in cancellation:
            signal.signal(item, signal.SIG_IGN)
        try:
            os.killpg(child.pid, number)
        except (ProcessLookupError, PermissionError):
            pass  # Cleanup after unwinding must still reap the owned child.
        print("Sync cancelled. Repeat sync before submitting jobs.", file=sys.stderr)
        raise SystemExit(128 + number)
    # The workstation controller is single-threaded. Keep cancellation blocked
    # until the child group, handlers and terminal handoff have cleanup ownership.
    mask = signal.pthread_sigmask(signal.SIG_BLOCK, cancellation)

    def prepare_child():
        os.setpgrp()
        signal.pthread_sigmask(signal.SIG_SETMASK, mask)

    try:
        try:
            child = subprocess.Popen(command, stdin=subprocess.PIPE if input is not None else None,
                                     stdout=subprocess.PIPE if capture_output else None,
                                     stderr=subprocess.PIPE if capture_output else None,
                                     text=text, preexec_fn=prepare_child, env=env)
            for number in cancellation:
                signal.signal(number, cancel)
            foreground_group(child.pid)
            # The child may have tried to read its terminal before the handoff.
            if terminal is not None:
                try:
                    os.killpg(child.pid, signal.SIGCONT)
                except ProcessLookupError:
                    pass
            signal.pthread_sigmask(signal.SIG_SETMASK, mask)
            stdout, stderr = child.communicate(input=input, timeout=timeout)
            if check and child.returncode:
                raise subprocess.CalledProcessError(child.returncode, command)
            if child.returncode:
                stop(signal.SIGTERM)
            return subprocess.CompletedProcess(command, child.returncode, stdout, stderr)
        except BaseException:
            for number in cancellation:
                signal.signal(number, signal.SIG_IGN)
            if child is not None:
                stop(signal.SIGTERM)
            raise
    finally:
        signal.pthread_sigmask(signal.SIG_BLOCK, cancellation)
        try:
            if terminal is not None:
                try:
                    foreground_group(foreground)
                finally:
                    os.close(terminal)
        finally:
            for number, handler in previous.items():
                signal.signal(number, handler)
            signal.pthread_sigmask(signal.SIG_SETMASK, mask)


def parser():
    cli = argparse.ArgumentParser(add_help=False)
    cli.add_argument("ssh_target", nargs="?")
    for flag in ("dest", "port", "identity"):
        cli.add_argument("--" + flag)
    for flag in ("configure", "local-only", "dry-run", "sync-only"):
        cli.add_argument("--" + flag, action="store_true")
    return cli


def profile_directory(source):
    return Path.home() / ".gpu-course-sync" / handoff.sha(str(source.resolve()))[:20]


def read_profile(source):
    parent = profile_directory(source)
    if not os.path.lexists(parent):
        return {}
    for path in (parent.parent, parent):
        os.close(handoff.directory(path, private=True))
    if not os.path.lexists(parent / "profile.json"):
        return {}
    profile = read_json(parent / "profile.json")
    if not isinstance(profile, dict) or profile.get("schema") != PROFILE_SCHEMA:
        raise ValueError("Invalid saved sync profile")
    for key in (*SETUP_INPUTS, "ssh_target", "dest", "port", "identity"):
        if not isinstance(profile.get(key, ""), str) or any(c in profile.get(key, "") for c in "\x00\n\r"):
            raise ValueError("Invalid saved sync input")
    if profile.get("content") is not None:
        handoff.parse(profile["content"])
    return profile


def options(args, saved):
    result = {key: getattr(args, key) if getattr(args, key) is not None else saved.get(key, "")
              for key in ("ssh_target", "dest", "port", "identity")}
    result["dest"] = result["dest"] or "courses"
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", result["dest"]):
        raise ValueError("Invalid destination")
    if result["port"] and (not result["port"].isdigit() or not 1 <= int(result["port"]) <= 65535):
        raise ValueError("Invalid SSH port")
    if result["port"]:
        result["port"] = str(int(result["port"]))
    if result["identity"]:
        key = Path(result["identity"]).expanduser().absolute()
        regular(key)
        result["identity"] = str(key)
    if result["ssh_target"]:
        result["ssh_target"] = ssh_target(result["ssh_target"])
    return result


def ssh_target(value):
    user, host = value.split("@", 1) if "@" in value else ("root", value)
    host = host.removeprefix("[").removesuffix("]")
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", user) or not (
        re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", host)
        or (host.count(":") >= 2 and re.fullmatch(r"[A-Za-z0-9:.%_-]+", host))
    ):
        raise ValueError("Invalid SSH target")
    return user + "@" + host


def ask(label, default=""):
    if not sys.stdin.isatty():
        raise ValueError("Missing setup input; run normal sync in a terminal")
    answer = input(f"{label}" + (f" [{default}]" if default else "") + ": ").strip()
    return answer or default


def connection(args, saved):
    result = {}
    for key, variable in SETUP_INPUTS.items():
        value = (os.environ.get(variable) or saved.get(key, "")) if args.configure else (
            saved.get(key) or os.environ.get(variable, ""))
        if not value or args.configure:
            value = ask(variable, value)
        if not value or any(c in value for c in "\x00\r\n"):
            raise ValueError("All four setup inputs are required")
        result[key] = value
    for key in ("config", "kubeconfig"):
        path = Path(result[key]).expanduser().absolute()
        regular(path)
        result[key] = str(path)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", result["target"]):
        raise ValueError("Invalid cluster target")
    return result


def login(connection):
    command = ["kubectl", "--kubeconfig", connection["kubeconfig"], "--context", connection["context"],
               "get", "svc", "-n", "soperator", "-l", "app.kubernetes.io/component=login",
               "--request-timeout=15s", "-o", "json"]
    response = execute(command, check=True, capture_output=True, text=True, timeout=30)
    services = [s for s in json.loads(response.stdout)["items"] if s["spec"].get("type") == "LoadBalancer"]
    if len(services) != 1:
        raise ValueError("Login discovery is ambiguous; supply [user@]TARGET")
    ports = services[0]["spec"].get("ports", [])
    addresses = {row.get("ip") or row.get("hostname") for row in services[0].get("status", {}).get("loadBalancer", {}).get("ingress", [])}
    if len(ports) != 1 or ports[0].get("protocol", "TCP") != "TCP" or len(addresses) != 1 or not all(addresses):
        raise ValueError("Login discovery requires one endpoint and TCP port; supply [user@]TARGET and --port")
    port = ports[0]["port"]
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Invalid discovered SSH port")
    return ssh_target(addresses.pop()), str(port)


def ssh_options(profile):
    result = ["-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3"]
    if profile["port"]:
        result += ["-p", profile["port"]]
    if profile["identity"]:
        result += ["-i", profile["identity"]]
    return result


def remote(profile, action, payload=None):
    if action == "inspect":
        # Trusted local stdlib code can inspect a fresh destination before sync.
        command = shlex.join(["python3", "-B", "-c", Path(handoff.__file__).read_text(), action, profile["dest"]])
    else:
        code = 'exec python3 -B "$HOME/$1/tools/course_sync.py" _receive "$1"'
        command = shlex.join(["sh", "-c", code, "course-sync", profile["dest"]])
    result = execute(["ssh", "-T", *ssh_options(profile), profile["ssh_target"], command],
                            input=json.dumps(payload) if payload is not None else None,
                            text=True, capture_output=True, timeout=90)
    if result.returncode:
        raise RuntimeError("Remote private-settings operation failed; inspect destination safety and retry sync")
    if len(result.stdout) > 256 * 1024:
        raise ValueError("Oversized private-settings response")
    return json.loads(result.stdout)


def discover(source, profile, private):
    environment = private / "environment"
    if not environment.exists():
        execute(["uv", "--no-config", "venv", "--python", "3.12", str(environment)],
                       check=True, capture_output=True, timeout=300)
    os.close(handoff.directory(environment))
    python = environment / "bin/python"
    execute(["uv", "--no-config", "pip", "sync", "--python", str(python),
                    str(source / "tools/workstation-requirements.txt")], check=True,
                   capture_output=True, timeout=300)
    result = execute([str(python), "-B", str(source / "tools/course_sync.py"), "_discover"],
                            input=json.dumps({key: profile[key] for key in SETUP_INPUTS}),
                            text=True, capture_output=True, check=True, timeout=300)
    return json.loads(result.stdout)


def source_manifest(source):
    from verify_source_sync import identity
    files = ["tools/course_sync.py", "tools/verify_source_sync.py", "tools/course_publication_state.py",
             "tools/publish_results.py", "tools/course_execution.py"]
    files += [p.relative_to(source).as_posix() for p in (source / "tools/course_bootstrap").iterdir()
              if p.suffix in {".py", ".json"}]
    return {"schema": "course-source-sync/v1", "files": {p: identity(source / p) for p in files}}


def synchronize(source, profile):
    command = ["bash", str(source / "sync-labs.sh"), "--sync-only", "--dest", profile["dest"]]
    for key in ("port", "identity"):
        if profile[key]:
            command += ["--" + key, profile[key]]
    execute([*command, "--", profile["ssh_target"]], check=True,
            env={**os.environ, "_COURSE_SYNC_INTERACTIVE_TRANSFER": "1"})


def deliver(profile, request):
    acknowledged = False
    try:
        acknowledgment = remote(profile, "receive", request)
        expected = None if request.get("local_only") else handoff.sha(request["content"])
        acknowledged = acknowledgment == {"digest": expected}
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError):
        print("Settings acknowledgment interrupted or rejected; checking delivery by digest.", flush=True)
    try:
        if not acknowledged and (request.get("local_only") or request.get("previous_digest") == handoff.sha(request["content"])):
            raise ValueError("Receiver validation was not acknowledged")
        observed = remote(profile, "inspect")
        expected = None if request.get("local_only") else handoff.sha(request["content"])
        if request.get("local_only"):
            handoff.unconfigured(observed)
        if observed["digest"] != expected:
            raise ValueError("Delivery digest differs")
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as exc:
        raise RuntimeError("Settings delivery unresolved; login was not opened. Repeat sync to reconcile; complete settings remain intact.") from exc


def run(source, args):
    if not sys.stdin.isatty():
        raise ValueError("An interactive terminal is required; use --sync-only for automation")
    saved = read_profile(source)
    profile = options(args, saved)
    if not args.local_only:
        profile.update(connection(args, saved))
    if not profile["ssh_target"]:
        if args.local_only:
            raise ValueError("--local-only requires an explicit SSH target on first use")
        profile["ssh_target"], port = login(profile)
        profile["port"] = profile["port"] or port
    snapshot = remote(profile, "inspect")
    if args.local_only:
        handoff.unconfigured(snapshot)
        # No profile/dependency/settings mutation for local-only mode.
        synchronize(source, profile)
        deliver(profile, {"local_only": True, "sources": source_manifest(source)})
    else:
        private = profile_directory(source)
        private_dir(private.parent)
        private_dir(private)
        with setup_lock(private):
            # Do not overwrite a concurrently updated workstation profile.
            if read_profile(source) != saved:
                raise ValueError("Sync profile changed; repeat sync")
            workspace = None
            for previous in (saved.get("content"), snapshot["content"]):
                if previous is not None:
                    previous_workspace = handoff.parse(previous)[0]["COURSE_WORKSPACE"]
                    if workspace is not None and workspace != previous_workspace:
                        raise ValueError("Saved workspace differs from destination")
                    workspace = previous_workspace
            workspace = workspace or "workspace-" + uuid.uuid4().hex
            print("Preparing workstation discovery dependencies and checking installed monitoring.", flush=True)
            receipt = discover(source, profile, private)
            content = handoff.render(workspace, receipt)
            for previous in (saved.get("content"), snapshot["content"]):
                handoff.compatible(previous, content)
            profile.update(schema=PROFILE_SCHEMA, content=content)
            # Persist validated identity before delivery so a lost acknowledgment
            # cannot generate another workspace on the next invocation.
            atomic_json(private / "profile.json", profile)
            atomic_json(private / "monitoring.json", receipt)
            synchronize(source, profile)
            deliver(profile, {"content": content, "previous_digest": snapshot["digest"],
                              "sources": source_manifest(source)})
        print("Publication settings delivered and confirmed.", flush=True)
    setup_path = '"$HOME"/' + shlex.quote(profile["dest"] + "/tools/lab-setup.py")
    print(f"On the login node, prepare all available labs: python3.12 {setup_path}", flush=True)
    code = 'set -eu; cd "$HOME/$1"; bash tools/ensure_python312.sh; exec "${SHELL:-/bin/sh}" -il'
    command = shlex.join(["sh", "-c", code, "course-sync", profile["dest"]])
    os.execvp("ssh", ["ssh", "-t", *ssh_options(profile), profile["ssh_target"], command])


def main(source, argv):
    if argv and argv[0] == "_discover":
        from .support import discover as discover_monitoring
        values = json.load(sys.stdin)
        values.update(config=Path(values["config"]), kubeconfig=Path(values["kubeconfig"]), workspace="discovery")
        print(json.dumps(discover_monitoring(SimpleNamespace(**values))))
        return
    if argv and argv[0] == "_receive":
        handoff.remote_main("receive", argv[1])
        return
    source_only = bool(argv and argv[0] == "_options")
    args = parser().parse_args(argv[1:] if source_only else argv)
    if args.configure and (args.dry_run or args.sync_only or args.local_only):
        raise ValueError("--configure requires normal configured synchronization")
    if source_only:
        saved = read_profile(source)
        values = options(args, saved)
        for key in ("ssh_target", "dest", "port", "identity"):
            sys.stdout.buffer.write(values[key].encode() + b"\0")
        for key in ("kubeconfig", "context"):
            sys.stdout.buffer.write(saved.get(key, "").encode() + b"\0")
    else:
        run(source, args)
