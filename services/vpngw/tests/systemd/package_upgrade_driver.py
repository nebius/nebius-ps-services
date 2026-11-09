"""Installed-candidate package qualification; never claims VPN connectivity."""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from email.parser import BytesParser
from pathlib import Path

from package_diagnostics import failure_diagnostic, loader_code
from package_repeat import verify_repeat
from packaging.utils import parse_wheel_filename

from nebius_vpngw import handoff_bootstrap, ordinary_bootstrap
from nebius_vpngw.deploy import ordinary_apply, vm_ha_package


def system_python(code: str, request: dict | None = None, *, expected_exit: int = 0) -> dict:
    # Stdin carries code and request separately, as the real SSH transports do.
    loader = "import base64,sys;exec(compile(base64.b64decode(sys.stdin.buffer.readline()),'<qualification>','exec'))"
    payload = base64.b64encode(code.encode()) + b"\n" + json.dumps(request or {}).encode() + b"\n"
    result = subprocess.run(
        ["/usr/bin/python3", "-B", "-c", loader],
        input=payload,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=600,
        env=clean_environment(),
    )
    if result.returncode != expected_exit:
        detail = result.stderr.decode()[-5000:] or result.stdout.decode()[-2000:]
        raise RuntimeError(f"Expected exit {expected_exit}, observed {result.returncode}: {detail}")
    if expected_exit:
        return {"exit": result.returncode}
    return json.loads(result.stdout)


def clean_environment():
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH" and not k.startswith("PIP_")}
    env["PIP_CONFIG_FILE"] = os.devnull
    return env


def remote(request):
    result = subprocess.run(
        ["/usr/bin/python3", "-B", "-c", loader_code(ordinary_bootstrap.LOADER)],
        input=ordinary_bootstrap.source_frame() + json.dumps(request).encode(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=clean_environment(),
        timeout=600,
    )
    if result.returncode:
        raise RuntimeError(
            "Package transport rejected request: "
            + result.stdout.decode()[-2000:]
            + "; staging observation: "
            + failure_diagnostic(result.stderr)
        )
    return json.loads(result.stdout)


# This exercises the ordinary install components. It deliberately stops before
# activation, routes and runtime confirmation; there is no package-only apply CLI.
ORDINARY_COMPONENT = r"""
import json,sys,time,os,zipfile
from pathlib import Path
import _vpngw_ordinary_operations as ops
import _vpngw_ordinary_remote as remote
import _vpngw_runtime_migration as migration
request=json.load(sys.stdin)
manifest=request['manifest']
directory=migration.verify_staged(manifest['runtime_plan'])/'payload'
remote.DEADLINE=time.monotonic()+540
manager=ops.Manager(remote.DEADLINE)
pending=migration.pending(manifest.get('ha_parent'))
identity=pending['operation'] if pending else os.urandom(16).hex()
migration.begin(manifest['runtime_plan'],identity,manifest)
operation=ops.Operation.begin(request=identity,config=manifest['config_sha256'],
    artifact=manifest['package_identity'],predecessor=ops.digest(manifest),
    manager=manager,network=manager.network(),previous=ops.digest(ops.Journal().read()))
remote.OPERATION=operation
migration.COMMAND,migration.CHECK=remote.command,operation.check
try:
    migration.inhibit()
    for unit in (*ops.MANAGEMENT,'strongswan-starter.service','strongswan.service'):
        if not manager.unit(unit).get('missing') or not manager.stopped(unit):
            operation.service('stop',unit)
        assert manager.stopped(unit)
    operation.phase('install')
    migration.install()
    dependencies=[str(directory/name) for name in manifest['dependency_wheels']]
    if dependencies:
        remote.command([sys.executable,'-B','-c',remote.DEPENDENCY_INSTALL_SCRIPT,*dependencies],limit=120)
    remote.command(migration.product_install_command(directory/manifest['wheel_name']),limit=120)
    operation.phase('publish')
    with zipfile.ZipFile(directory/manifest['wheel_name']) as archive:
        for path,item in manifest['assets'].items():
            raw=archive.read(item['member'])
            assert remote.sha(raw)==item['sha256']
            remote.publish(Path(path),raw,item['mode'])
    operation.reload_manager()
    operation.complete()
finally:
    operation.fail()
    remote.OPERATION=None
    migration.COMMAND=migration.CHECK=None
print(json.dumps({'package_components':'complete','runtime_inhibited':(migration.ROOT/'inhibited').exists()}))
"""


INTERRUPTION_HOOK = r"""
import os,json,sys
from pathlib import Path
import _vpngw_ordinary_remote as remote
import _vpngw_runtime_migration as migration
original_command=remote.command
def interrupt_after_effect(arguments,*args,**kwargs):
    result=original_command(arguments,*args,**kwargs)
    value=migration.state()
    wheel=migration.verify_staged(value['plan'])/'payload'/value['manifest']['wheel_name']
    hit=(INTERRUPTION=='native-unpack' and arguments[:2]==['dpkg','--unpack']) or (
        INTERRUPTION=='product-wheel'
        and arguments[:5]==[sys.executable,'-B','-m','pip','install']
        and '--no-index' in arguments and '--no-deps' in arguments
        and not any(a in {'--root','--target','--prefix','--user'} for a in arguments)
        and arguments[-1]==str(wheel))
    if hit:
        receipt={'boundary':INTERRUPTION,'operation':value['operation'],
            'plan_digest':value['plan_digest'],'manifest_digest':migration.digest(value['manifest'])}
        with Path('/qualification/interruption.json').open('x') as output:
            json.dump(receipt,output);output.flush();os.fsync(output.fileno())
        os._exit(86)
    return result
remote.command=interrupt_after_effect
"""

INTERRUPTION_OBSERVATION = r"""
import hashlib,json,sys,time
from pathlib import Path
import _vpngw_ordinary_operations as ops
import _vpngw_runtime_migration as migration
import _vpngw_ha_repair as repair
request=json.load(sys.stdin)
receipt=json.loads(Path('/qualification/interruption.json').read_text())
value=migration.state()
assert value['operation']==receipt['operation']
assert value['manifest']==request['manifest']
assert value['plan_digest']==receipt['plan_digest']
assert migration.digest(value['manifest'])==receipt['manifest_digest']
migration.verify_inhibited()
journal=ops.Journal().read();manager=ops.Manager(time.monotonic()+30)
assert not ops.alive(journal['owner'])
assert all(not ops.alive(effect['writer']) for effect in journal['effects'])
assert all(ops.process_group_empty(effect['process']) for effect in journal['effects'] if effect['kind']=='process')
if request['topology']=='ha':
    assert ops.handoff_recoverable(journal,manager)
    expected=json.loads(Path('/qualification/expected-lock.json').read_text())
    repair.exact_lock(expected)
    guard=json.loads((repair.STATE/'guard.json').read_text())
    assert guard['guard_boot_id']==ops.BOOT.read_text().strip()
    assert guard['data_plane_mode']=='blocked'
    assert repair.FORWARDING.read_text().strip()=='0'
else:
    assert ops.recoverable(journal,manager)
assert manager.quiet((*ops.MANAGEMENT,*ops.HA_WRITERS,*ops.DATAPLANE))
if receipt['boundary']=='native-unpack':
    assert value['phase']=='packages'
    assert any(migration.packages().get(name,[None,None])[1]=='unpacked' for name in value['plan']['install'])
else:
    assert value['phase']=='installed'
    import importlib.metadata
    assert importlib.metadata.version('nebius-vpngw')==value['manifest']['version']
    assert any(not Path(path).exists() or hashlib.sha256(Path(path).read_bytes()).hexdigest()!=item['sha256'] for path,item in value['manifest']['assets'].items())
print(json.dumps(receipt))
"""


def package_attempt(code: str, request: dict, manifest: dict, args) -> dict:
    Path("/qualification/package-request.json").write_text(json.dumps(request, sort_keys=True))
    source = handoff_bootstrap.streamed_source()
    if args.interrupt_after:
        system_python(
            source + "\nINTERRUPTION=" + repr(args.interrupt_after) + "\n" + INTERRUPTION_HOOK + code,
            request, expected_exit=86,
        )
        observation = system_python(
            source + INTERRUPTION_OBSERVATION,
            {"manifest": manifest, "topology": args.topology},
        )
        Path("/qualification/interruption-observed.json").write_text(json.dumps(observation))
    # No resolution, staging, changed predecessor or new parent on retry.
    result = system_python(source + code, request)
    if args.interrupt_after:
        resumed = system_python(source + r"""
import json
import _vpngw_runtime_migration as migration
value=migration.state()
print(json.dumps({'operation':value['operation'],'plan_digest':value['plan_digest'],
    'manifest_digest':migration.digest(value['manifest'])}))
""")
        assert resumed == {k: observation[k] for k in resumed}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--topology", choices=("ordinary", "ha"), required=True)
    parser.add_argument("--interrupt-after", choices=("native-unpack", "product-wheel"))
    args = parser.parse_args()
    assert sys.prefix == "/opt/qualification-controller"
    assert "PYTHONPATH" not in os.environ
    baseline = json.loads(Path("/qualification/baseline.json").read_text())
    config = Path("/etc/nebius-vpngw/config-resolved.yaml").read_text()
    manifest = (vm_ha_package.artifact if args.topology == "ha" else ordinary_apply.artifact)(
        args.candidate, config
    )
    before = remote({"action": "inspect-package", "manifest": manifest})["observation"]
    assert before["package_version"] == baseline["version"]
    ordinary_apply.verify_asset_predecessors(manifest, before)
    manifest["runtime_plan"] = before["runtime_plan"]
    manifest["ha_parent"] = "package-qualification"
    with tempfile.TemporaryDirectory(prefix="package-selection-") as directory:
        dependencies = ordinary_apply.resolve_dependencies(manifest, before, Path(directory))
        manifest["dependency_wheels"] = {
            p.name: ordinary_apply.sha(p.read_bytes()) for p in dependencies
        }
        manifest["expected_dependencies"] = {
            **before["dependencies"],
            "nebius-vpngw": manifest["version"],
        }
        manifest["expected_requirements"] = {
            **before["requirements"],
            "nebius-vpngw": manifest["requirements"],
        }
        for path in dependencies:
            name, version, _build, _tags = parse_wheel_filename(path.name)
            manifest["expected_dependencies"][str(name)] = str(version)
            with zipfile.ZipFile(path) as archive:
                member = next(n for n in archive.namelist() if n.endswith(".dist-info/METADATA"))
                manifest["expected_requirements"][str(name)] = (
                    BytesParser().parsebytes(archive.read(member)).get_all("Requires-Dist") or []
                )
        Path("/qualification/selected-manifest.json").write_text(
            json.dumps(manifest, sort_keys=True)
        )
        remote(
            {
                "action": "stage-package",
                "manifest": manifest,
                "wheel": base64.b64encode(args.candidate.read_bytes()).decode(),
                "dependency_wheels": {
                    p.name: base64.b64encode(p.read_bytes()).decode() for p in dependencies
                },
            }
        )
    if args.topology == "ha":
        package = {"desired": manifest, "predecessor": vm_ha_package.package_predecessor(before)}
        result = package_attempt(
            "\nimport _vpngw_handoff_remote as owner\nowner.main()\n",
            {
                "action": "prepare-guarded",
                "package": package,
                "expected": json.loads(Path("/qualification/expected-lock.json").read_text()),
            },
            manifest, args,
        )
        system_python(
            r"""
import json,sys,time
from pathlib import Path
from nebius_vpngw import checkpoint_upgrade as upgrade,ha_repair as repair,ordinary_operations as ops
request=json.load(sys.stdin)
expected=json.loads(Path('/qualification/expected-lock.json').read_text())
manager=ops.Manager(time.monotonic()+30)
assert all(manager.stopped(unit) for unit in (*ops.MANAGEMENT,*ops.HA_WRITERS))
with repair.writer_locks(deadline=time.monotonic()+30):
    arguments=dict(expected=expected,artifact=request['artifact'],state_dir=repair.STATE,
        boot_id=repair.BOOT.read_text().strip(),repair=repair)
    receipt=upgrade.convert_checkpoint(**arguments)
    before=(repair.STATE/'controller-checkpoint.json').read_bytes()
    assert upgrade.convert_checkpoint(**arguments)==receipt
    assert (repair.STATE/'controller-checkpoint.json').read_bytes()==before
print(json.dumps({'checkpoint_conversion':'verified','sequence':upgrade.inspect_checkpoint(json.loads(before)).sequence}))
""",
            {"artifact": manifest["wheel_sha256"]},
        )
        boundary = "ha-guarded-package-preparation-without-cloud-activation"
    else:
        result = package_attempt(ORDINARY_COMPONENT, {"manifest": manifest}, manifest, args)
        boundary = "ordinary-shared-package-components-without-apply-activation"
    after = remote({"action": "inspect-package", "manifest": manifest})["observation"]
    assert after["files"] == manifest["files"]
    assert after["package_version"] == manifest["version"]
    assert after["dependencies"] == manifest["expected_dependencies"]
    assert after["requirements"] == manifest["expected_requirements"]
    assert after["assets"] == {p: item["sha256"] for p, item in manifest["assets"].items()}
    assert after["asset_modes"] == {
        p: [item["mode"], 0, 0] for p, item in manifest["assets"].items()
    }
    assert after["config_sha256"] == before["config_sha256"]
    assert after["boot_id"] == before["boot_id"]
    probe = system_python(r"""
import importlib.metadata as m,json,sys,subprocess,platform
from pathlib import Path
import nebius_vpngw
from nebius_vpngw import checkpoint_upgrade
raw=json.loads(Path('/qualification/predecessor-checkpoint.json').read_text())
checkpoint=checkpoint_upgrade.inspect_checkpoint(raw)
assert checkpoint.sequence==7
assert not nebius_vpngw.__file__.startswith(('/workspace/','/opt/qualification-controller'))
versions=[d.version for d in m.distributions() if d.metadata['Name'].lower().replace('_','-')=='nebius-vpngw']
assert len(versions)==1
audit=subprocess.run(['dpkg','--audit'],capture_output=True,text=True,check=True)
assert not audit.stdout.strip(),audit.stdout
subprocess.run([sys.executable,'-m','pip','check'],check=True,stdout=subprocess.DEVNULL)
os_release=platform.freedesktop_os_release()
assert os_release['ID']=='ubuntu' and os_release['VERSION_ID']=='24.04'
architecture=subprocess.check_output(['dpkg','--print-architecture'],text=True).strip()
print(json.dumps({'versions':versions,'import_path':nebius_vpngw.__file__,'checkpoint_sequence':checkpoint.sequence,'dpkg_audit':'clean','pip_check':'passed','os_release':os_release['PRETTY_NAME'],'architecture':architecture,'kernel':platform.release(),'python':platform.python_version()}))
""")
    # Replay the real preparation only after initial postconditions and checkpoint
    # conversion. Reuse the saved request, without re-resolution, staging or a
    # second fault injection. The product wheel and streamed owners stay frozen.
    repeat_source = Path(__file__).with_name("package_repeat.py").read_text()
    observation_code = (
        repeat_source + "\nprint(json.dumps(observe(" + repr(args.topology) + ")))\n"
    )
    repeat_before = system_python(observation_code)
    Path("/qualification/repeat-before.json").write_text(json.dumps(repeat_before, sort_keys=True))
    request = json.loads(Path("/qualification/package-request.json").read_text())
    code = (
        "\nimport _vpngw_handoff_remote as owner\nowner.main()\n"
        if args.topology == "ha" else ORDINARY_COMPONENT
    )
    repeated = system_python(handoff_bootstrap.streamed_source() + code, request)
    Path("/qualification/repeat-execution.json").write_text(json.dumps(repeated, sort_keys=True))
    repeat_after = system_python(observation_code)
    Path("/qualification/repeat-after.json").write_text(json.dumps(repeat_after, sort_keys=True))
    repeat = verify_repeat(repeat_before, repeat_after)
    report = {
        "schema": "nebius-vpngw.package-qualification-result.v1",
        "status": "passed",
        "predecessor": baseline["version"],
        "candidate": manifest["version"],
        "candidate_sha256": manifest["wheel_sha256"],
        "mode": baseline["mode"],
        "topology": args.topology,
        "boundary": boundary,
        "probe": probe,
        "selected_dependencies": manifest["dependency_wheels"],
        "package_receipt": result,
        "interruption": args.interrupt_after,
        "repeat": repeat,
        "not_qualified": [
            "connectivity",
            "cloud-authority",
            "HA-activation",
            "VM-reboot",
            "full-apply-no-op",
            "traffic-interruption",
        ],
    }
    Path("/qualification/result.json").write_text(json.dumps(report, sort_keys=True, indent=2))
    print(
        json.dumps(
            {
                k: report[k]
                for k in ("status", "predecessor", "candidate", "mode", "topology", "boundary")
            }
        )
    )


if __name__ == "__main__":
    main()
