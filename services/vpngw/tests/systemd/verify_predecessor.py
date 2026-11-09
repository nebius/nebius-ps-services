"""Verify the cached source image before any scenario-specific fixture setup."""

import argparse
import hashlib
import json
import platform
import subprocess
import zipfile
from importlib import metadata
from pathlib import Path

import nebius_vpngw


def observe():
    wheels = list(Path("/predecessor").glob("*.whl"))
    assert len(wheels) == 1
    wheel = wheels[0]
    package = Path(nebius_vpngw.__file__).parent
    files = {}
    with zipfile.ZipFile(wheel) as archive:
        for name in archive.namelist():
            if name.startswith("nebius_vpngw/") and not name.endswith("/"):
                installed = package / name.removeprefix("nebius_vpngw/")
                expected = hashlib.sha256(archive.read(name)).hexdigest()
                assert hashlib.sha256(installed.read_bytes()).hexdigest() == expected
                files[name] = expected
    versions = [
        d.version
        for d in metadata.distributions()
        if d.metadata["Name"].lower().replace("_", "-") == "nebius-vpngw"
    ]
    assert len(versions) == 1 and wheel.name == f"nebius_vpngw-{versions[0]}-py3-none-any.whl"
    os_release = platform.freedesktop_os_release()
    assert os_release["ID"] == "ubuntu" and os_release["VERSION_ID"] == "24.04"
    assert not Path("/etc/nebius-vpngw").exists()
    audit = subprocess.check_output(["dpkg", "--audit"], text=True)
    assert not audit.strip()
    return {
        "schema": "nebius-vpngw.predecessor-image.v1",
        "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "version": versions[0],
        "files": files,
        "python_packages": sorted(
            (d.metadata["Name"], d.version) for d in metadata.distributions()
        ),
        "native_packages": subprocess.check_output(["dpkg-query", "-W"], text=True),
        "architecture": subprocess.check_output(
            ["dpkg", "--print-architecture"], text=True
        ).strip(),
        "os": os_release["PRETTY_NAME"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", action="store_true")
    parser.add_argument("--source-wheel", type=Path)
    args = parser.parse_args()
    path = Path("/opt/vpngw-predecessor.json")
    observed = json.loads(json.dumps(observe()))
    if args.source_wheel:
        assert (
            observed["wheel_sha256"] == hashlib.sha256(args.source_wheel.read_bytes()).hexdigest()
        )
    if args.record:
        assert not Path("/opt/qualification-controller").exists()
        with path.open("x") as output:
            json.dump(observed, output, sort_keys=True, indent=2)
    else:
        assert observed == json.loads(path.read_text()), "Cached predecessor changed"
    print(json.dumps({k: observed[k] for k in ("version", "wheel_sha256", "architecture")}))


if __name__ == "__main__":
    main()
