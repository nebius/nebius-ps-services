"""Shared repair presentation and user input; grants no execution authority."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

import typer


def confirm(
    groups: Iterable[tuple[str | None, Iterable[Mapping[str, str]]]],
    *,
    regional: bool = False,
) -> bool:
    typer.echo(
        "Normal apply is recommended for routine convergence.\n"
        "You selected software repair, which rebuilds the managed\n"
        "software on the existing VMs and interrupts VPN traffic.\n"
        "VMs and disks will be retained.\n\n"
        "Targets:"
    )
    for region, targets in groups:
        if region is not None:
            typer.echo(f"  Region {region}:")
        indent = "    " if region is not None else "  "
        for target in targets:
            typer.echo(
                f"{indent}{target['name']}: VM {target['compute']}, retained disk {target['disk']}"
            )
    typer.echo(
        "\nRebuilds: VPNGW, strongSwan, FRR, managed node certificates,\n"
        "configuration and operational state.\n"
        "Retains: Ubuntu, IP allocations, SSH access and external\n"
        "peer credentials.\n\n"
        "Repair has no overall outage limit. VPN traffic remains\n"
        "interrupted until recovery completes.\n"
    )
    question = (
        "Continue with software repair in both regions?"
        if regional
        else "Continue with software repair?"
    )
    return typer.confirm(question, default=False)
