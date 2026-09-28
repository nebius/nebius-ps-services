"""Self-contained jail program, executed under the profiling owner's fence.

Admission downloads artifacts and computes a bounded package transaction. Install
consumes that exact transaction. Admission refreshes isolated signed Ubuntu
indexes; it never replaces jail sources or performs an unconstrained upgrade.
This file must use only the Python standard library.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import shlex
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request
import zlib
from pathlib import Path

PACKAGES = {
    "nsys": ("nsight-systems-cli-2026.4.1", "2026.4.1.191-264138605071v0", "2026.4.1"),
    "ncu": ("nsight-compute-2026.2.1", "2026.2.1.5-1", "2026.2.1"),
}
ARTIFACTS = {
    "amd64": {
        "nsys": (
            "NsightSystems-linux-cli-public-2026.4.1.191-3860507.deb",
            "b896cb2b9586ddf617c363a43bababad0a015dff4c77d8f0fbb9c26144056a69",
        ),
        "ncu": (
            "nsight-compute-2026.2.1_2026.2.1.5-1_amd64.deb",
            "6829651ceeb0c3f65890b9f727b74d1e550fed58c454e11c2c87442295e4eb70",
        ),
    },
    "arm64": {
        "nsys": (
            "nsight-systems-cli-2026.4.1_2026.4.1.191-1_arm64.deb",
            "f31d26665910cc654aa2644e6055703562024360c930a47dcf24f1404c234048",
        ),
        "ncu": (
            "nsight-compute-2026.2.1_2026.2.1.5-1_arm64.deb",
            "6851c5621e7957975ede8600e051314e768c828b68a7d90e469c7e565de2bdc9",
        ),
    },
}
DEPENDENCIES = frozenset(
    {
        "libc6",
        "libglib2.0-0",
        "libglib2.0-0t64",
        "libpcre2-8-0",
        "libffi8",
        "libmount1",
        "libselinux1",
        "libblkid1",
        "zlib1g",
        "libgcc-s1",
        "gcc-12-base",
        "gcc-13-base",
        "libstdc++6",
    }
)
CACHE = Path("/var/cache/nebius-cxcli/nsight")
PROFILE = Path("/etc/profile.d/99-nsight.sh")
RECEIPT = Path("/var/lib/nebius-cxcli/nsight.json")
INTENT = Path("/var/lib/nebius-cxcli/nsight-install-intent.json")
DPKG_INFO = Path("/var/lib/dpkg/info")


def run(args, *, check=True):
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
        env={**os.environ, "LC_ALL": "C", "DEBIAN_FRONTEND": "noninteractive"},
    )
    if check and result.returncode:
        raise RuntimeError(f"Nsight package operation failed: {args[0]}")
    return result.stdout.strip() if check else result


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint(value):
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )


def encode_payload(value):
    """One bounded canonical transport for requests and Nsight journal fields."""
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    encoded = base64.b64encode(zlib.compress(raw, 9)).decode()
    if len(raw) > 8 * 1024 * 1024 or len(encoded) > 64 * 1024:
        raise RuntimeError("Nsight payload exceeds its bounded execution capacity")
    return "nsight-zlib-v1:" + encoded


def decode_payload(encoded):
    if (
        not isinstance(encoded, str)
        or not encoded.startswith("nsight-zlib-v1:")
        or len(encoded) > 64 * 1024 + 15
    ):
        raise RuntimeError("Nsight payload has an invalid transport or size")
    try:
        stream = zlib.decompressobj()
        raw = stream.decompress(
            base64.b64decode(encoded.split(":", 1)[1], validate=True), 8 * 1024 * 1024 + 1
        )
        if len(raw) > 8 * 1024 * 1024 or not stream.eof or stream.unused_data:
            raise ValueError("size or trailing data")
        result = json.loads(raw)
    except (ValueError, zlib.error) as exc:
        raise RuntimeError("Nsight payload is invalid") from exc
    if not isinstance(result, dict):
        raise RuntimeError("Nsight payload must be an object")
    return result


def platform():
    release = dict(
        line.split("=", 1)
        for line in Path("/etc/os-release").read_text().splitlines()
        if "=" in line
    )
    version = release.get("VERSION_ID", "").strip('"')
    arch = run(["dpkg", "--print-architecture"])
    if (
        release.get("ID", "").strip('"') != "ubuntu"
        or version not in {"22.04", "24.04"}
        or arch not in ARTIFACTS
    ):
        raise RuntimeError("Nsight supports Ubuntu 22.04/24.04 jails on amd64 or arm64")
    return "ubuntu" + version.replace(".", ""), arch


def download(artifact, *, cached_only=False):
    parsed = urllib.parse.urlsplit(artifact["url"])
    if (
        parsed.scheme != "https"
        or parsed.hostname
        not in {
            "developer.download.nvidia.com",
            "archive.ubuntu.com",
            "security.ubuntu.com",
            "ports.ubuntu.com",
        }
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("Nsight artifact must come from an official HTTPS package repository")
    if not re.fullmatch(r"[0-9a-f]{64}", artifact["sha256"]):
        raise RuntimeError("Nsight artifact has no SHA256 admission")
    CACHE.mkdir(parents=True, exist_ok=True, mode=0o755)
    if CACHE.is_symlink():
        raise RuntimeError("Nsight cache cannot be a symlink")
    path = CACHE / (artifact["sha256"] + ".deb")
    if path.is_symlink():
        raise RuntimeError("Nsight cached package cannot be a symlink")
    if not path.is_file() or sha(path) != artifact["sha256"]:
        if cached_only:
            raise RuntimeError("Nsight installation requires its checksum-verified admitted cache")
        with (
            urllib.request.urlopen(artifact["url"], timeout=90) as source,
            tempfile.NamedTemporaryFile(dir=CACHE, suffix=".partial", delete=False) as out,
        ):
            temp = Path(out.name)
            size = 0
            while chunk := source.read(1024 * 1024):
                size += len(chunk)
                if size > 1024 * 1024 * 1024:
                    raise RuntimeError("Nsight package exceeds the bounded artifact size")
                out.write(chunk)
        if sha(temp) != artifact["sha256"]:
            raise RuntimeError("Nsight downloaded artifact failed its frozen SHA256")
        temp.chmod(0o644)
        temp.replace(path)
    fields = run(
        ["dpkg-deb", "--show", "--showformat=${Package}\n${Version}\n", str(path)]
    ).splitlines()
    if fields != [artifact["package"], artifact["version"]]:
        raise RuntimeError("Nsight package metadata differs from admission")
    return str(path)


def parse_transaction(output):
    result = {}
    for line in output.splitlines():
        if line.startswith("Remv "):
            raise RuntimeError("Nsight cannot remove packages")
        if not line.startswith(("Inst ", "Conf ")):
            continue
        match = re.match(
            r"(?:Inst|Conf) ([a-z0-9+.-]+)(?::[a-z0-9]+)?(?: \[([^]]+)\])? \(([^ )]+)", line
        )
        if not match:
            raise RuntimeError("Unrecognized Nsight package transaction")
        name, old, version = match.groups()
        tools = {p[0]: p[1] for p in PACKAGES.values()}
        if name in tools:
            if version != tools[name]:
                raise RuntimeError("Nsight transaction changed a pinned tool version")
        elif name not in DEPENDENCIES or old:
            raise RuntimeError("Nsight refuses unrelated packages or dependency upgrades")
        if name in result and (not line.startswith("Conf ") or result[name] != version):
            raise RuntimeError("Duplicate Nsight package transaction entry")
        result[name] = version
    return result


def transaction(paths):
    return parse_transaction(
        run(
            [
                "apt-get",
                *apt_options(),
                "--simulate",
                "--no-remove",
                "--no-install-recommends",
                "install",
                *paths,
            ]
        )
    )


def apt_options():
    return [
        "-o",
        f"Dir::Etc::sourcelist={CACHE / 'ubuntu.list'}",
        "-o",
        "Dir::Etc::sourceparts=-",
        "-o",
        f"Dir::State::lists={CACHE / 'apt-lists'}",
        "-o",
        f"Dir::Cache::pkgcache={CACHE / 'pkgcache.bin'}",
        "-o",
        f"Dir::Cache::srcpkgcache={CACHE / 'srcpkgcache.bin'}",
        "-o",
        "Acquire::AllowInsecureRepositories=false",
        "-o",
        "Acquire::AllowDowngradeToInsecureRepositories=false",
        "-o",
        "APT::Get::AllowUnauthenticated=false",
    ]


def refresh_indexes(distro, arch):
    """Use signed official distro metadata without changing the jail's APT setup."""
    suite = {"ubuntu2204": "jammy", "ubuntu2404": "noble"}[distro]
    keyring = Path("/usr/share/keyrings/ubuntu-archive-keyring.gpg")
    if not keyring.is_file():
        raise RuntimeError("Nsight admission requires the Ubuntu archive signing keyring")
    (CACHE / "apt-lists" / "partial").mkdir(parents=True, exist_ok=True)
    archive = (
        "https://ports.ubuntu.com/ubuntu-ports"
        if arch == "arm64"
        else "https://archive.ubuntu.com/ubuntu"
    )
    security = archive if arch == "arm64" else "https://security.ubuntu.com/ubuntu"
    options = f"[arch={arch} signed-by={keyring}]"
    sources = (
        "\n".join(
            f"deb {options} {url} {name} main"
            for url, name in (
                (archive, suite),
                (archive, suite + "-updates"),
                (security, suite + "-security"),
            )
        )
        + "\n"
    )
    atomic_write(CACHE / "ubuntu.list", lambda stream: stream.write(sources))
    run(["apt-get", *apt_options(), "-o", "APT::Update::Error-Mode=any", "update"])


def admit():
    distro, arch = platform()
    baseline = package_state()
    require_clean_baseline(baseline)
    preimages = {"profile": file_state(PROFILE), "receipt": file_state(RECEIPT)}
    artifacts = []
    for tool, (filename, checksum) in ARTIFACTS[arch].items():
        package, version, _ = PACKAGES[tool]
        artifacts.append(
            {
                "package": package,
                "version": version,
                "sha256": checksum,
                "url": f"https://developer.download.nvidia.com/devtools/repos/{distro}/{arch}/{filename}",
            }
        )
    paths = [download(item) for item in artifacts]
    refresh_indexes(distro, arch)
    planned = transaction(paths)
    for package, version in planned.items():
        if package in {p[0] for p in PACKAGES.values()}:
            continue
        lines = run(
            ["apt-get", *apt_options(), "--print-uris", "download", f"{package}={version}"]
        ).splitlines()
        uris = [shlex.split(line) for line in lines if line.startswith("'")]
        if len(uris) != 1 or len(uris[0]) != 4:
            raise RuntimeError("Dependency repository must provide one exact artifact")
        url = uris[0][0]
        if url.startswith("http://"):
            url = "https://" + url[len("http://") :]
        artifact = {
            "package": package,
            "version": version,
            "url": url,
            "sha256": dependency_checksum(package, version, arch),
        }
        paths.append(download(artifact))
        artifacts.append(artifact)
    # Verify local artifacts resolve exactly the same bounded transaction.
    if transaction(paths) != planned:
        raise RuntimeError("Nsight dependency closure changed during admission")
    for artifact, path in zip(artifacts, paths, strict=True):
        artifact["architecture"] = run(["dpkg-deb", "--field", path, "Architecture"])
        artifact["controlSha256"] = artifact_controls(path)
    if package_state() != baseline or preimages != {
        "profile": file_state(PROFILE),
        "receipt": file_state(RECEIPT),
    }:
        raise RuntimeError("Nsight package/profile baseline changed during admission")
    return {
        "schema": "nebius-cxcli.nsight-packages.v2",
        "distro": distro,
        "arch": arch,
        "artifacts": artifacts,
        "transaction": planned,
        "baseline": baseline,
        "preimages": preimages,
    }


def file_state(path):
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise RuntimeError("Nsight managed file must be a regular, non-symlink file")
    return {"sha256": sha(path)} if path.is_file() else {"missing": True}


def package_state():
    output = run(
        [
            "dpkg-query",
            "-W",
            "-f=${Package}\t${Architecture}\t${Version}\t${Status}\t${Triggers-Awaited}\t${Triggers-Pending}\\n",
        ]
    )
    result = {}
    for line in output.splitlines():
        fields = line.split("\t")
        # run() strips the final output's trailing tabs.
        fields += [""] * (6 - len(fields))
        if len(fields) != 6 or not fields[0] or not fields[1]:
            raise RuntimeError("Nsight cannot authenticate the dpkg package inventory")
        name, arch, version, status, awaited, pending = fields
        key = name + ":" + arch
        if key in result:
            raise RuntimeError("Nsight package inventory contains duplicate identities")
        result[key] = {
            "package": name,
            "architecture": arch,
            "version": version,
            "status": status,
            "awaited": awaited,
            "pending": pending,
        }
    return result


def require_clean_baseline(baseline):
    if any(
        row["status"]
        not in {
            "install ok installed",
            "hold ok installed",
            "deinstall ok config-files",
            "purge ok not-installed",
        }
        or row["awaited"]
        or row["pending"]
        for row in baseline.values()
    ):
        raise RuntimeError("Nsight requires a clean package baseline without pending work")


def artifact_controls(path):
    result = subprocess.run(
        ["dpkg-deb", "--ctrl-tarfile", path], capture_output=True, timeout=60, check=False
    )
    if result.returncode:
        raise RuntimeError("Nsight cannot inspect admitted package control files")
    controls = {}
    with tarfile.open(fileobj=io.BytesIO(result.stdout), mode="r:") as archive:
        for item in archive:
            name = item.name.removeprefix("./")
            if name not in {"preinst", "postinst", "prerm", "postrm", "triggers"}:
                continue
            if not item.isfile() or name in controls:
                raise RuntimeError("Unsafe Nsight package control metadata")
            controls[name] = hashlib.sha256(archive.extractfile(item).read()).hexdigest()
    return controls


def check_package_preconditions(admission):
    if (
        admission.get("schema") != "nebius-cxcli.nsight-packages.v2"
        or not isinstance(admission.get("baseline"), dict)
        or not isinstance(admission.get("preimages"), dict)
    ):
        raise RuntimeError("Nsight recovery requires its original package and profile preimages")
    baseline = admission["baseline"]
    require_clean_baseline(baseline)
    current = package_state()
    owned = {a["package"]: a for a in admission["artifacts"]}
    for key in set(baseline) | set(current):
        previous, row = baseline.get(key), current.get(key)
        if row == previous:
            continue
        if row is None or row["package"] not in owned:
            raise RuntimeError("Nsight refuses changed or incomplete unrelated packages")
        artifact = owned[row["package"]]
        if (
            row["version"] != artifact["version"]
            or row["architecture"] != artifact["architecture"]
            or row["awaited"]
            or row["pending"]
            or row["status"]
            not in {"install ok installed", "install ok unpacked", "install ok half-configured"}
        ):
            raise RuntimeError("Nsight partial package state is not safely resumable")
        if row["status"] in {"install ok unpacked", "install ok half-configured"}:
            for name in ("preinst", "postinst", "prerm", "postrm", "triggers"):
                checksum = artifact["controlSha256"].get(name)
                paths = [DPKG_INFO / (key + "." + name), DPKG_INFO / (row["package"] + "." + name)]
                existing = [path for path in paths if path.exists() or path.is_symlink()]
                if checksum is None and not existing:
                    continue
                if len(existing) != 1 or existing[0].is_symlink() or sha(existing[0]) != checksum:
                    raise RuntimeError("Nsight interrupted package control ownership changed")
    # A durable write-ahead intent distinguishes this attempt's partial effects
    # from an unrelated administrator installation that happens to match versions.
    preimages = {"profile": file_state(PROFILE), "receipt": file_state(RECEIPT)}
    intent = {"admissionSha256": fingerprint(admission), "preimages": admission["preimages"]}
    if (current != baseline or preimages != admission["preimages"]) and (
        file_state(INTENT).get("missing") or json.loads(INTENT.read_text()) != intent
    ):
        raise RuntimeError("Nsight partial installation lacks its original ownership intent")
    if preimages != admission["preimages"]:
        if not RECEIPT.is_file() or RECEIPT.is_symlink():
            raise RuntimeError("Nsight current profile has no owned receipt")
        receipt = json.loads(RECEIPT.read_text())
        if receipt.get("admissionSha256") != fingerprint(admission) or (
            PROFILE.exists()
            and receipt.get("profileSha256") != sha(PROFILE)
            and file_state(PROFILE) != admission["preimages"]["profile"]
        ):
            raise RuntimeError("Nsight profile receipt belongs to another admission")
    return intent


def dependency_checksum(package, version, arch):
    # APT prefers SHA512 for --print-uris on modern Ubuntu. Freeze SHA256 from
    # the exact signed Packages record, independently of that display choice.
    metadata = run(["apt-cache", *apt_options(), "show", f"{package}={version}"])
    checksums = set()
    for paragraph in metadata.split("\n\n"):
        fields = dict(
            line.split(": ", 1)
            for line in paragraph.splitlines()
            if ": " in line and not line.startswith(" ")
        )
        if (fields.get("Package"), fields.get("Version")) == (package, version) and fields.get(
            "Architecture"
        ) in {arch, "all"}:
            checksums.add(fields.get("SHA256", ""))
    if len(checksums) != 1 or not re.fullmatch(r"[0-9a-f]{64}", next(iter(checksums))):
        raise RuntimeError("Dependency repository must provide an exact signed SHA256 artifact")
    return checksums.pop()


def binaries():
    result = {}
    for tool, (package, version, expected) in PACKAGES.items():
        actual = run(["dpkg-query", "-W", "-f=${Status}\n${Version}", package]).splitlines()
        if actual != ["install ok installed", version]:
            raise RuntimeError("Nsight installed package version differs from admission")
        if run(["dpkg", "--verify", package]):
            raise RuntimeError("Installed Nsight package files have been modified")
        paths = [
            Path(p)
            for p in run(["dpkg-query", "-L", package]).splitlines()
            if p.startswith("/opt/") and p.endswith("/" + tool)
        ]
        candidates = sorted(
            [p for p in paths if p.is_file() and os.access(p, os.X_OK)],
            key=lambda p: (len(str(p)), str(p)),
        )
        if not candidates:
            raise RuntimeError("Nsight package exposes no executable in /opt")
        path = candidates[0]
        if expected not in run([str(path), "--version"]):
            raise RuntimeError("Nsight executable version differs from installed package")
        result[tool] = str(path)
    return result


def profile_content(paths):
    directories = ":".join(str(Path(paths[tool]).parent) for tool in PACKAGES)
    if not re.fullmatch(r"[A-Za-z0-9_./:-]+", directories):
        raise RuntimeError("Nsight package paths cannot be safely added to PATH")
    return f'# Managed by nebius-cxcli Nsight profiling.\nexport PATH="{directories}:$PATH"\n'


def atomic_write(path, writer):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
            temp = Path(stream.name)
            writer(stream)
            stream.flush()
            os.fchmod(stream.fileno(), 0o644)
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_json(path, value):
    def write(stream):
        json.dump(value, stream, sort_keys=True)
        stream.write("\n")

    atomic_write(path, write)


def verify(admission):
    if admission.get("schema") != "nebius-cxcli.nsight-packages.v2":
        raise RuntimeError("Nsight verification requires current package admission")
    check_package_preconditions(admission)
    inventory = package_state()
    for artifact in admission["artifacts"]:
        row = inventory.get(artifact["package"] + ":" + artifact["architecture"], {})
        if row.get("version") != artifact["version"] or row.get("status") != "install ok installed":
            raise RuntimeError("Nsight admitted package version or configured state changed")
    paths = binaries()
    if PROFILE.is_symlink() or PROFILE.read_text() != profile_content(paths):
        raise RuntimeError("Nsight shell profile differs from the installed package paths")
    for tool, path in paths.items():
        resolved = run(["bash", "-lc", f"command -v {tool}"])
        if resolved != path:
            raise RuntimeError("Login PATH selects another Nsight installation")
    return {
        "schema": "nebius-cxcli.nsight-customization.v1",
        "admissionSha256": fingerprint(admission),
        "binaries": paths,
        "profileSha256": sha(PROFILE),
        "binarySha256": {name: sha(Path(path)) for name, path in paths.items()},
    }


def install(admission):
    if (admission["distro"], admission["arch"]) != platform():
        raise RuntimeError("Nsight package admission belongs to another jail platform")
    if PROFILE.is_symlink() or (PROFILE.exists() and not RECEIPT.is_file()):
        raise RuntimeError("Existing 99-nsight.sh is not owned by this installer")
    if PROFILE.exists():
        previous = json.loads(RECEIPT.read_text())
        if previous.get("profileSha256") != sha(PROFILE) and not (
            previous.get("admissionSha256") == fingerprint(admission)
            and file_state(PROFILE) == admission.get("preimages", {}).get("profile")
        ):
            raise RuntimeError("Existing managed Nsight profile was modified outside its owner")
    paths = [download(item, cached_only=True) for item in admission["artifacts"]]
    intent = check_package_preconditions(admission)
    remaining = transaction(paths)
    if any(admission["transaction"].get(name) != version for name, version in remaining.items()):
        raise RuntimeError("Nsight package plan changed after admission")
    if remaining:
        # APT may prefer its remote index entry over an explicit local .deb,
        # even with --no-download. Consume only the already-admitted artifacts
        # through dpkg; dependency resolution was completed and rechecked above.
        states = package_state()
        selected = [
            path
            for artifact, path in zip(admission["artifacts"], paths, strict=True)
            if artifact["package"] in remaining
            and states.get(artifact["package"] + ":" + artifact["architecture"], {}).get("status")
            not in {"install ok unpacked", "install ok half-configured"}
        ]
        if not set(remaining) <= {a["package"] for a in admission["artifacts"]}:
            raise RuntimeError("Nsight transaction has an unbound package artifact")
        file_state(INTENT)
        atomic_json(INTENT, intent)
        if selected:
            run(["dpkg", "--unpack", *selected])
        run(["dpkg", "--configure", *remaining])
    else:
        file_state(INTENT)
        atomic_json(INTENT, intent)
    content = profile_content(binaries())
    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    # Establish file ownership before publishing PATH; interrupted installation
    # can recover without claiming an unrelated administrator-owned profile.
    atomic_json(
        RECEIPT,
        {
            "admissionSha256": fingerprint(admission),
            "profileSha256": hashlib.sha256(content.encode()).hexdigest(),
        },
    )
    PROFILE.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(PROFILE, lambda stream: stream.write(content))
    receipt = verify(admission)
    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(RECEIPT, receipt)
    return receipt


def ensure_reports(mount, relative):
    """Create only the selected directory; refuse symlinks at every component."""
    parts = relative.split("/")
    if not parts or any(not part or part in {".", ".."} for part in parts):
        raise RuntimeError("Unsafe Nsight reports directory")
    fd = os.open(mount, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for index, part in enumerate(parts):
            mode = 0o1777 if index == len(parts) - 1 else 0o755
            try:
                os.mkdir(part, mode, dir_fd=fd)
                created = True
            except FileExistsError:
                created = False
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            if created:
                os.fchmod(child, mode)
            os.close(fd)
            fd = child
    finally:
        os.close(fd)


def validate_runtime_privileges():
    status = Path("/proc/self/status").read_text()
    capabilities = dict(line.split(":", 1) for line in status.splitlines() if ":" in line)
    if any(
        int(capabilities[name].strip(), 16) & (1 << 21)
        for name in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")
    ):
        raise RuntimeError("Nsight package execution must not retain mount capability")
    if capabilities.get("NoNewPrivs", "").strip() != "1":
        raise RuntimeError("Nsight package execution requires no_new_privs")


def main():
    request = decode_payload(sys.argv[1])
    if request.get("require_mount_capability_dropped"):
        validate_runtime_privileges()
    action = request["action"]
    if action == "admit":
        result = admit()
    elif action == "install":
        if request.get("reports"):
            ensure_reports(request["reports"]["mount"], request["reports"]["subpath"])
        result = install(request["admission"])
    elif action == "verify":
        result = verify(request["admission"])
    else:
        raise RuntimeError("Unknown Nsight jail operation")
    print("CXCLI_NSIGHT=" + json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
