from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from nebius_cxcli.component_sources import (
    set_component_sources_file_override,
)
from nebius_cxcli.infra_render import _canonical_module_source

WRAPPERS = ("wireguard-gw", "ssh-jumphost", "nfs", "nat-gateway")
CATALOG = Path(__file__).resolve().parents[1] / "component_sources.yaml"


@pytest.mark.parametrize("component", WRAPPERS)
def test_local_wrapper_catalog_source_initializes_sibling_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, component: str
) -> None:
    terraform = shutil.which("terraform")
    if terraform is None:
        pytest.skip("Terraform is required for the real module installation regression")

    # Mirror the catalog/package layout with provider-free modules. Use the actual
    # catalog source rather than constructing the expected package address here.
    checkout = tmp_path / "checkout with spaces"
    catalog = checkout / "services" / "cli" / "component_sources.yaml"
    catalog.parent.mkdir(parents=True)
    catalog.write_text(CATALOG.read_text(encoding="utf-8"), encoding="utf-8")
    wrapper = checkout / "platform-infra" / "modules" / component
    wrapper.mkdir(parents=True)
    vm = wrapper.parent / "vm"
    vm.mkdir()
    (vm / "main.tf").write_text('output "result" { value = "sibling-loaded" }\n')
    (wrapper / "main.tf").write_text(
        'module "vm" { source = "../vm" }\noutput "result" { value = module.vm.result }\n'
    )
    work = tmp_path / "generated" / "infra"
    work.mkdir(parents=True)
    monkeypatch.chdir(work)
    set_component_sources_file_override(catalog)
    try:
        source = yaml.safe_load(catalog.read_text())["components"]["infra"][component]["source"][
            "local"
        ]
        rendered = _canonical_module_source(module_source=source, module_version=None)
    finally:
        set_component_sources_file_override(None)
    (work / "main.tf").write_text(
        f'module "gateway" {{ source = {json.dumps(rendered)} }}\n'
        'output "result" { value = module.gateway.result }\n'
    )
    # No providers or backend; exclude ambient Terraform flags/configuration.
    env = {key: value for key, value in os.environ.items() if not key.startswith("TF_")}
    cli_config = tmp_path / "terraform.rc"
    cli_config.write_text("disable_checkpoint = true\n")
    env["TF_CLI_CONFIG_FILE"] = str(cli_config)
    result = subprocess.run(
        [terraform, "init", "-input=false", "-no-color", "-backend=false"],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    inventory = json.loads((work / ".terraform/modules/modules.json").read_text())
    child = next(item for item in inventory["Modules"] if item["Key"] == "gateway.vm")
    assert (work / child["Dir"] / "main.tf").read_text() == (vm / "main.tf").read_text()
    validated = subprocess.run(
        [terraform, "validate", "-no-color"],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert validated.returncode == 0, validated.stdout + validated.stderr
