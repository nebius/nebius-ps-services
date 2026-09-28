"""Install-only shared filesystem choices; no persistence or provider mutations."""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .filesystem_mount_tags import mount_tag_error
from .provider_options import OptionChoice

SFS_ROLES = ("accounting", "controller-spool", "jail")
_ROLE_LABELS = {"accounting": "Accounting", "controller-spool": "Controller spool", "jail": "Jail"}
_NEW_FIELDS = ("name", "size_gib", "block_size_kib", "mount_tag", "forbid_deletion")
_FIELD_LABELS = {
    "source": "Create new or use existing",
    "name": "Name",
    "size_gib": "Size (GiB)",
    "block_size_kib": "Block size (KiB)",
    "mount_tag": "Mount tag",
    "forbid_deletion": "Deletion protection",
    "existing_id": "Existing filesystem",
    "type": "Type for new filesystems",
}


@dataclass(frozen=True)
class SfsPrompt:
    role: str | None
    field: str
    current: object
    choices: tuple[OptionChoice, ...] = ()

    @property
    def label(self) -> str:
        section = _ROLE_LABELS[self.role] if self.role else "Shared filesystems"
        return f"{section} / {_FIELD_LABELS[self.field]}"

    @property
    def type_hint(self) -> str:
        if self.field in {"size_gib", "block_size_kib"}:
            return "number"
        return "bool" if self.field == "forbid_deletion" else "string"


def _role_filesystems(inputs: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    filesystems = inputs.get("filesystems")
    if not isinstance(filesystems, dict) or any(
        not isinstance(filesystems.get(role), dict) for role in SFS_ROLES
    ):
        raise ValueError(
            "Soperator SFS configuration requires accounting, controller-spool, and jail"
        )
    return filesystems


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _field_error(
    filesystems: Mapping[str, Mapping[str, Any]], role: str, field: str, value: object
) -> str | None:
    if field == "mount_tag" and (error := mount_tag_error(value)):
        return error
    if field in {"name", "existing_id", "mount_tag"}:
        if not _text(value):
            return f"{_FIELD_LABELS[field]} must not be empty."
        if field in {"existing_id", "mount_tag"} and any(
            other != role and _text(spec.get(field)) == _text(value)
            for other, spec in filesystems.items()
        ):
            return f"{_FIELD_LABELS[field]} is already used by another filesystem role."
    if field in {"size_gib", "block_size_kib"}:
        minimum = 1 if field == "size_gib" else 4
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or value < minimum
            or value % 1 != 0
        ):
            return f"{_FIELD_LABELS[field]} must be an integer >= {minimum}."
    if field == "forbid_deletion" and not isinstance(value, bool):
        return "Deletion protection must be true or false."
    return None


def prompt_sfs_filesystems(
    inputs: Mapping[str, Any],
    *,
    prompt: Callable[[SfsPrompt], tuple[object, bool]],
    filesystem_choices: Callable[[], tuple[list[OptionChoice], str | None]],
    type_choices: Callable[[], list[OptionChoice]],
    report_error: Callable[[str], None],
    backtrack: object,
) -> tuple[object, bool]:
    """Return a complete draft or navigation outcome, never a partially edited input."""
    draft = copy.deepcopy(dict(inputs))
    filesystems = _role_filesystems(draft)
    modes = {
        role: "existing" if _text(filesystems[role].get("existing_id")) else "new"
        for role in SFS_ROLES
    }

    def visible_steps() -> list[tuple[str | None, str]]:
        steps: list[tuple[str | None, str]] = []
        for role in SFS_ROLES:
            steps.append((role, "source"))
            fields = _NEW_FIELDS if modes[role] == "new" else ("existing_id", "mount_tag")
            steps.extend((role, field) for field in fields)
        if "new" in modes.values():
            steps.append((None, "type"))
        return steps

    index = 0
    while index < len(steps := visible_steps()):
        role, field = steps[index]
        choices: tuple[OptionChoice, ...] = ()
        current: Any
        if field == "source":
            assert role is not None
            current = modes[role]
            choices = (
                OptionChoice("new", "Create new"),
                OptionChoice("existing", "Use existing"),
            )
        elif field == "type":
            current = draft.get("type", "NETWORK_SSD")
            choices = tuple(type_choices())
            if not choices:
                raise ValueError("No filesystem types are configured for Soperator installation")
        else:
            assert role is not None
            current = filesystems[role].get(field)
            if field == "existing_id":
                available, error = filesystem_choices()
                if error or not available:
                    report_error(error or "No shared filesystems found in the current project.")
                    index = steps.index((role, "source"))
                    continue
                # Provider mount-tag suggestions are not properties of a filesystem.
                choices = tuple(
                    OptionChoice(
                        choice.value,
                        f"{choice.metadata['name']} ({choice.value})"
                        if choice.metadata.get("name")
                        else choice.value,
                    )
                    for choice in available
                )
        value, stopped = prompt(SfsPrompt(role, field, current, choices))
        if stopped:
            return inputs, True
        if value is backtrack:
            if index == 0:
                return backtrack, False
            index -= 1
            continue
        if choices and value not in {choice.value for choice in choices}:
            report_error("Select one of the listed options.")
            continue
        if field == "source":
            assert role is not None
            modes[role] = str(value)
            if value == "new":
                filesystems[role].pop("existing_id", None)
        elif field == "type":
            draft["type"] = value
        else:
            assert role is not None
            value = value.strip() if isinstance(value, str) else value
            error = _field_error(filesystems, role, field, value)
            if error:
                report_error(error)
                continue
            filesystems[role][field] = value
        index += 1
    return draft, False


def sfs_summary_rows(inputs: Mapping[str, Any]) -> list[tuple[str, str, str, str]]:
    """Summarize intent; profile defaults are never reported as live reuse metadata."""
    filesystems = _role_filesystems(inputs)
    rows: list[tuple[str, str, str, str]] = []
    for role in SFS_ROLES:
        spec = filesystems[role]
        existing_id = _text(spec.get("existing_id"))
        for field in ("mount_tag", "existing_id") if existing_id else ("mount_tag",):
            if error := _field_error(filesystems, role, field, spec.get(field)):
                raise ValueError(f"{_ROLE_LABELS[role]}: {error}")
        details = existing_id or (
            f"{spec.get('name', role)}; {spec.get('size_gib')} GiB; "
            f"{spec.get('type') or inputs.get('type') or 'NETWORK_SSD'}"
        )
        rows.append(
            (
                _ROLE_LABELS[role],
                "Use existing" if existing_id else "Create new",
                details,
                _text(spec.get("mount_tag")),
            )
        )
    return rows


def print_sfs_summary(inputs: Mapping[str, Any]) -> None:
    from rich.markup import escape
    from rich.table import Table

    from . import cli

    table = Table("Filesystem", "Action", "Details", "Mount tag", title="Shared filesystem plan")
    for row in sfs_summary_rows(inputs):
        table.add_row(*(escape(cell) for cell in row))
    cli.console.print(table)
