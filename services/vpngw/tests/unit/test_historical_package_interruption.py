"""The historical fixture must interrupt the product's actual install command."""

import ast
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from nebius_vpngw import python_package_recovery as packages
from nebius_vpngw import runtime_migration as migration


@pytest.fixture
def hook(monkeypatch, tmp_path):
    driver = Path(__file__).parents[1] / "systemd/package_upgrade_driver.py"
    tree = ast.parse(driver.read_text())
    source = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "INTERRUPTION_HOOK" for t in node.targets)
    )
    calls = []
    remote = SimpleNamespace(command=lambda args, **kwargs: calls.append(args) or (0, ""))
    value = {
        "operation": "saved",
        "plan": {},
        "plan_digest": "plan",
        "manifest": {"wheel_name": "product.whl"},
    }
    runtime = SimpleNamespace(
        state=lambda: value,
        verify_staged=lambda plan: tmp_path,
        digest=migration.digest,
        product_install_command=migration.product_install_command,
    )
    monkeypatch.setitem(sys.modules, "_vpngw_ordinary_remote", remote)
    monkeypatch.setitem(sys.modules, "_vpngw_runtime_migration", runtime)
    namespace = {"INTERRUPTION": "product-wheel"}
    exec(source, namespace)
    namespace["Path"] = lambda path: tmp_path / Path(path).name

    def exit_process(code):
        raise SystemExit(code)

    namespace["os"] = SimpleNamespace(_exit=exit_process, fsync=lambda fd: None)
    return remote.command, tmp_path / "payload/product.whl", calls, tmp_path


@pytest.mark.parametrize("owner", ["ha", "ordinary"])
def test_actual_product_install_is_interrupted_after_effect(hook, monkeypatch, owner):
    command, wheel, calls, directory = hook
    value = {"state": "prepared", "before": {}, "target": {}}
    monkeypatch.setattr(packages, "load", lambda *_: value)
    monkeypatch.setattr(packages, "complete", lambda _: True)
    monkeypatch.setattr(packages.storage, "publish", lambda *_: None)
    with pytest.raises(SystemExit) as stopped:
        if owner == "ha":
            packages.install(directory, wheel, command, lambda: None)
        else:
            command(migration.product_install_command(wheel))
    assert stopped.value.code == 86
    assert len(calls) == 1 and calls[0][-1] == str(wheel)
    assert json.loads((directory / "interruption.json").read_text())["operation"] == "saved"
    if owner == "ha":
        assert value["state"] == "installing"


@pytest.mark.parametrize(
    "kind", ["dependency", "foreign-path", "inspection", "online", "projection"]
)
def test_unrelated_commands_do_not_inject(hook, kind):
    command, wheel, calls, directory = hook
    args = migration.product_install_command(wheel)
    if kind == "dependency":
        args[-1] = str(wheel.with_name("dependency.whl"))
    elif kind == "foreign-path":
        args[-1] = str(directory / wheel.name)
    elif kind == "inspection":
        args = [sys.executable, "-B", "-m", "pip", "show", str(wheel)]
    elif kind == "online":
        args.remove("--no-index")
    else:
        args[-1:-1] = ["--root", str(directory / "projection")]
    assert command(args) == (0, "")
    assert calls == [args]
    assert not (directory / "interruption.json").exists()
