"""Additive regional-pair commands; ordinary deployment owners do the work."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import typer
import yaml

from . import pair_apply, pair_config
from .config_loader import load_local_config

EXAMPLES = {
    ("pair",): (
        "nebius-vpngw pair create-config ./regional --left a.config.yaml --right b.config.yaml --name regional",
    ),
    ("pair", "create-config"): (
        "nebius-vpngw pair create-config ./regional --left a.config.yaml --right b.config.yaml --name regional",
    ),
    ("pair", "apply"): ("nebius-vpngw pair apply regional/pair.json --dry-run",),
    ("pair", "status"): ("nebius-vpngw pair status regional/pair.json",),
}


def _examples(*path: str) -> str:
    return "Examples:\n\n" + "\n\n".join("  " + value for value in EXAMPLES[path])


app = typer.Typer(
    help="Create, deploy and inspect a reciprocal regional VPN pair.",
    no_args_is_help=True,
    epilog=_examples("pair"),
)


def _run(manifest: dict, path: Path, side: str, command: str, *options: str) -> None:
    selected = path.parent / manifest["deployments"][side]["config"]
    typer.echo(f"Region {side}: {manifest['deployments'][side]['region']}")
    result = subprocess.run(
        [*pair_apply.command(), command, "--local-config-file", str(selected), *options],
        env=pair_apply.environment(manifest, side),
        timeout=1800,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"Regional {command} failed for side {side}")


def _observe_both(
    manifest: dict, path: Path, sides: list[str], command: str, *options: str
) -> None:
    failed = []
    for side in sides:
        try:
            _run(manifest, path, side, command, *options)
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            failed.append(side)
            typer.echo(f"Region {side}: {command} unavailable; readiness unknown", err=True)
    if failed:
        raise typer.Exit(1)


@app.command("create-config", epilog=_examples("pair", "create-config"))
def create_config(
    directory: Path = typer.Argument(
        ..., help="New private directory for both configurations and pair.json"
    ),
    left: Path = typer.Option(
        ...,
        exists=True,
        readable=True,
        help="Side A infrastructure/prefix seed; original remains unchanged",
    ),
    right: Path = typer.Option(
        ...,
        exists=True,
        readable=True,
        help="Side B infrastructure/prefix seed; original remains unchanged",
    ),
    name: str = typer.Option(..., help="New pair name; deployment names become NAME-a and NAME-b"),
    mode: str = typer.Option("bgp", help="Connection routing: bgp or static"),
    profile_a: str | None = typer.Option(
        None, help="Persist side A operator CLI profile; omitted uses inherited authentication"
    ),
    profile_b: str | None = typer.Option(
        None, help="Persist side B operator CLI profile; omitted uses inherited authentication"
    ),
) -> None:
    """Generate reciprocal Single/HA configurations with persistent credentials.

    Topology and workload prefixes come from each seed. Crypto comes from side A.
    New public allocations are selected by pair apply --prepare-network.
    """
    try:
        seeds = [
            load_local_config(path, allow_missing_placeholders=True, validate_schema=False)
            for path in (left, right)
        ]
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError):
        # YAML errors can include a source line containing a PSK.
        raise typer.BadParameter("Pair seed could not be read as a valid configuration") from None
    try:
        result = pair_config.create(
            directory, name, *seeds, mode=mode, profile_a=profile_a, profile_b=profile_b
        )
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise typer.BadParameter("Pair configuration was not published: " + str(error)) from None
    typer.echo(f"Pair manifest: {result}")


@app.command("apply", epilog=_examples("pair", "apply"))
def apply(
    manifest: Path = typer.Argument(..., exists=True, readable=True, help="Generated pair.json"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Preview both regional child plans without mutation"
    ),
    prepare_network: bool = typer.Option(
        False,
        "--prepare-network",
        help="Prepare both regional networks and bind reciprocal endpoints, then stop before deployment",
    ),
    approve_a: str | None = typer.Option(
        None, help="Exact side A disruption digest from its preview"
    ),
    approve_b: str | None = typer.Option(
        None, help="Exact side B disruption digest from its preview"
    ),
    timeout: int = typer.Option(
        1800, min=300, max=7200, help="Bound for the paired deployment, in seconds"
    ),
) -> None:
    """Preview, deploy or resume both regions through their ordinary apply owners.

    Both regions prepare before owner unlock; both unlock before convergence waits.
    A failed invocation retains each child's original journal and exact approval.
    """
    manifest = manifest.expanduser().absolute()
    if prepare_network and (dry_run or approve_a is not None or approve_b is not None):
        raise typer.BadParameter("Network preparation is a separate resumable phase")
    if prepare_network:
        with pair_apply.writer(manifest):
            value, _ = pair_config.load(manifest)
            for side in pair_config.SIDES:
                _run(value, manifest, side, "prep-network", "--no-interactive")
            pair_config.bind_endpoints(manifest)
        typer.echo(
            "Both networks prepared and reciprocal endpoints bound. Preview pair apply --dry-run next."
        )
        return
    value, configs = pair_config.load(manifest)
    unresolved = any(
        "${" in tunnel["remote_public_ip"]
        for config in configs.values()
        for connection in config["connections"]
        for tunnel in connection["tunnels"]
    )
    if unresolved:
        if dry_run:
            typer.echo(
                json.dumps(
                    {
                        "phase": "network-preparation-required",
                        "deployments": value["deployments"],
                        "next": "pair apply MANIFEST --prepare-network",
                    },
                    sort_keys=True,
                )
            )
            return
        raise typer.BadParameter(
            "Prepare both networks first with pair apply MANIFEST --prepare-network"
        )
    pair_config.load(manifest, resolved=True)
    if dry_run:
        _observe_both(value, manifest, list(pair_config.SIDES), "apply", "--dry-run")
        return
    result = pair_apply.apply(manifest, approvals={"a": approve_a, "b": approve_b}, timeout=timeout)
    typer.echo(
        json.dumps(
            {
                "phase": result["phase"],
                "regions": {
                    side: {"apply": child["result"], "readiness": child["readiness"]}
                    for side, child in result["children"].items()
                },
            },
            sort_keys=True,
        )
    )


@app.command("status", epilog=_examples("pair", "status"))
def status(
    manifest: Path = typer.Argument(..., exists=True, readable=True, help="Generated pair.json"),
) -> None:
    """Report each deployment and connection through read-only regional status."""
    manifest = manifest.expanduser().absolute()
    value, configs = pair_config.load(manifest)
    resolved = []
    for side in pair_config.SIDES:
        if any(
            "${" in tunnel["remote_public_ip"]
            for connection in configs[side]["connections"]
            for tunnel in connection["tunnels"]
        ):
            typer.echo(f"Region {side}: endpoints not bound; connection readiness unknown")
        else:
            resolved.append(side)
    _observe_both(value, manifest, resolved, "status")
