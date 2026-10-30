"""Named choices for ordinary MK8s shared-filesystem attachments."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any

import typer
from rich.markup import escape

from .component_instances import component_instance_id, component_type_id
from .provider_options import OptionChoice


def node_group_sfs_choices(payload: Mapping[str, Any], *, target_ref: str) -> list[OptionChoice]:
    """Offer only associations supported by the Terraform attachment renderer."""
    infra = payload.get("infra")
    components = infra.get("components") if isinstance(infra, Mapping) else None
    if not isinstance(components, list):
        return []
    rows = [
        row
        for row in components
        if isinstance(row, Mapping) and row.get("enabled") and component_instance_id(row)
    ]
    sfs_rows = [row for row in rows if component_type_id(row) == "sfs"]
    matching = [row for row in sfs_rows if component_instance_id(row) == target_ref]
    if matching:
        row = matching[0]
    elif len(sfs_rows) == 1 and sum(component_type_id(row) == "mk8s" for row in rows) <= 1:
        row = sfs_rows[0]
    else:
        return []

    inputs = row.get("inputs")
    inputs = inputs if isinstance(inputs, Mapping) else {}
    filesystems = inputs.get("filesystems")
    names: dict[str, str] = {}
    if isinstance(filesystems, Mapping) and filesystems:
        for key, spec in filesystems.items():
            key = str(key).strip()
            if key:
                name = spec.get("name") if isinstance(spec, Mapping) else None
                names[key] = str(name or key).strip() or key
    else:
        names["default"] = str(inputs.get("name") or component_instance_id(row)).strip()

    counts = Counter(names.values())
    number_entries = any(count > 1 for count in counts.values())
    return [
        OptionChoice(
            value=key,
            label=f"{index}. {name}" if number_entries else name,
        )
        for index, (key, name) in enumerate(names.items(), start=1)
    ]


def parse_storage_selection(raw: str, choices: list[OptionChoice]) -> list[str]:
    """Resolve displayed indexes; blank accepts all, and none explicitly clears."""
    if not raw:
        return [choice.value for choice in choices]
    if raw.casefold() == "none":
        return []
    selected: list[str] = []
    for token in raw.split(","):
        token = token.strip()
        if not token.isdecimal():
            raise ValueError("Enter comma-separated indexes, or none.")
        index = int(token)
        if not 1 <= index <= len(choices):
            raise ValueError(f"Choose indexes from 1 to {len(choices)}, or none.")
        value = choices[index - 1].value
        if value not in selected:
            selected.append(value)
    return selected


def prompt_node_group_shared_storage(
    *, group_name: str, choices: list[OptionChoice]
) -> tuple[object, bool]:
    from . import cli

    title = f'Attach shared storage to node group "{group_name}":'
    cli.console.print(
        "This configures filesystem attachments to the worker nodes. "
        "Mount configuration, CSI, and PV/PVC setup are separate steps."
    )
    cli.console.print(
        "All available filesystems are selected. "
        "Uncheck any filesystem you do not want attached to this node group."
    )
    selected: list[str] | None = None
    if cli._is_tty_session():
        try:
            import questionary

            cli._configure_questionary_checkbox_symbols()
            answer = cli._ask_questionary_with_wizard_navigation(
                questionary.checkbox(
                    title,
                    choices=[
                        questionary.Choice(title=choice.label, value=choice.value, checked=True)
                        for choice in choices
                    ],
                    instruction="Space=toggle; q=back; qq=quit; Enter=confirm.",
                    qmark="",
                )
            )
            if answer is None or answer == cli._WIZARD_QUIT_CHOICE:
                return None, True
            if answer == cli._WIZARD_BACK_CHOICE:
                return cli._WIZARD_BACKTRACK, False
            selected = list(answer)
        except (KeyboardInterrupt, EOFError, typer.Abort):
            return None, True
        except Exception:
            pass

    if selected is None:
        cli.console.print(escape(title))
        for index, choice in enumerate(choices, start=1):
            cli.console.print(escape(f"  [x] {index}. {choice.label}"))
        while True:
            try:
                raw = typer.prompt(
                    "Storage indexes (comma-separated; Enter accepts all; none clears; "
                    "q=back; qq=quit)",
                    default="",
                    show_default=False,
                ).strip()
            except (KeyboardInterrupt, EOFError, typer.Abort):
                return None, True
            if raw == cli.WIZARD_ABORT_TOKEN:
                return None, True
            if raw == cli.WIZARD_EXIT_TOKEN:
                return cli._WIZARD_BACKTRACK, False
            try:
                selected = parse_storage_selection(raw, choices)
                break
            except ValueError as exc:
                cli.console.print(f"{cli.error_markup('Invalid selection')}. {escape(str(exc))}")

    labels = {choice.value: choice.label for choice in choices}
    summary = ", ".join(labels[key] for key in selected) or "none"
    cli.console.print(escape(f'Shared storage for "{group_name}": {summary}'))
    return selected, False
