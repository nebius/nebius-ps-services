"""Select a new guest's package closure under its approved creation authority."""

from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from .. import apply_approval
from . import ordinary_apply, provisioning_receipts
from .apply_command import CommandJournal
from .apply_plan import CommandPlan, Progress
from .operator_keys import safe_name
from .provisioning_journal import ProvisioningJournal, requests_from_plan
from .replacement import Journal, metadata_only


def descriptor(resources: dict, instance: Any) -> dict:
    recipe = next(
        (item for item in requests_from_plan(resources) if item.guest == instance.hostname), None
    )
    if recipe is None:
        raise RuntimeError("New guest has no approved Compute creation slot")
    return {
        "provisioning": apply_approval.digest(resources),
        "slot": recipe.key,
        "guest": instance.hostname,
        "config_sha256": ordinary_apply.sha(instance.config_yaml.encode()),
        "wheel_sha256": resources["artifact_sha256"],
        "bootstrap": resources["bootstraps"][instance.hostname],
    }


class GuestJournal(Journal):
    schema = "nebius-vpngw.provisioning-guest.v1"

    def __init__(self, project: str, gateway: str, guest: str, *, root: Path | None = None):
        self.guest = safe_name(guest)
        super().__init__(
            project,
            gateway,
            root=root
            or Path.home()
            / ".local/state/nebius-vpngw/provisioning-guest"
            / project
            / gateway
            / guest,
        )

    def validate(self, state: dict) -> None:
        metadata_only(state)
        try:
            if (
                set(state)
                != {"schema", "project_id", "gateway_name", "revision", "bindings", "transaction"}
                or state["schema"] != self.schema
                or state["project_id"] != self.project
                or state["gateway_name"] != self.gateway
                or type(state["revision"]) is not int
                or state["revision"] < 0
                or state["bindings"]
            ):
                raise ValueError("Invalid guest selection identity")
            current = state["transaction"]
            if current is None:
                return
            if (
                set(current) != {"authority", "manifest", "predecessor", "phase"}
                or current["phase"] not in {"selected", "complete"}
                or set(current["authority"]) != {"descriptor", "creation", "command", "execution"}
                or re.fullmatch(r"[a-f0-9]{64}", current["predecessor"]) is None
                or any(
                    re.fullmatch(r"[a-f0-9]{64}", current["authority"][key]) is None
                    for key in ("descriptor", "creation")
                )
                or re.fullmatch(r"[a-f0-9]{32}", current["authority"]["execution"]) is None
            ):
                raise ValueError("Invalid guest selection receipt")
            apply_approval.validate(current["authority"]["command"])
            if current["manifest"].get("command_approval") != current["authority"]["command"]:
                raise ValueError("Guest manifest has different command authority")
        except (KeyError, ValueError, TypeError, AttributeError) as error:
            raise ValueError("Provisioned guest journal is invalid") from error

    def select(self, authority: dict, prepared: ordinary_apply.OrdinaryPlan) -> None:
        current = self.state["transaction"]
        if current is not None:
            if current["authority"] == authority:
                if current["manifest"] != prepared.manifest:
                    raise RuntimeError("Selected guest closure changed")
                return
            if current["phase"] != "complete":
                raise RuntimeError("Selected guest requires its original command")
            self.archive()
        candidate = {
            **self.state,
            "transaction": {
                "authority": deepcopy(authority),
                "manifest": deepcopy(prepared.manifest),
                "predecessor": apply_approval.digest(prepared.observation),
                "phase": "selected",
            },
        }
        self.validate(candidate)
        self.state = candidate
        self.save()

    def receipt(self) -> dict:
        current = self.state["transaction"]
        if current is None:
            raise RuntimeError("Provisioned guest has no selected closure")
        return {
            "authority": apply_approval.digest(current["authority"]),
            "manifest": apply_approval.manifest_identity(current["manifest"]),
        }


def _authority(command: CommandJournal, resources: ProvisioningJournal, instance: Any) -> tuple:
    state = provisioning_receipts.completed(command, resources)
    desired = descriptor(state["transaction"]["plan"], instance)
    parent = command.state["transaction"]
    plan = CommandPlan.parse(parent["plan"])
    key = "guest:" + instance.hostname
    effect = next((item for item in plan.effects if item.key == key), None)
    if (
        effect is None
        or effect.owner != "ordinary-provisioned-runtime"
        or effect.targets != (instance.hostname,)
        or effect.request_sha256 != apply_approval.digest(desired)
        or command.state["bindings"].get(key) != desired
        or "provisioning" not in effect.requires
        or parent["progress"][key] not in {Progress.STARTED.value, Progress.VERIFIED.value}
    ):
        raise RuntimeError("Provisioned guest differs from its approved command")
    return (
        parent,
        effect,
        {
            "descriptor": apply_approval.digest(desired),
            "creation": apply_approval.digest(state["bindings"][desired["slot"]]),
            "command": apply_approval.bind(plan.approval, plan.mapping()),
            "execution": parent["execution_id"],
        },
    )


def prepare(
    sdk: Any,
    command: CommandJournal,
    resources: ProvisioningJournal,
    child: GuestJournal,
    ssh: Any,
    instance: Any,
    local: dict,
) -> ordinary_apply.OrdinaryPlan:
    """Freeze once after exact creation; reinspection cannot re-resolve packages."""
    from .vm_manager import VMManager

    parent, effect, authority = _authority(command, resources, instance)
    if (child.project, child.gateway, child.guest) != (
        command.project,
        command.gateway,
        instance.hostname,
    ):
        raise RuntimeError("Provisioned guest journal belongs to another member")
    resource = provisioning_receipts.compute(sdk, command, resources, instance.hostname)
    target = VMManager._vm_public_ip_from_object(resource)
    if not target or getattr(resource.status.state, "name", "") != "RUNNING":
        raise RuntimeError("Created guest has no running authoritative transport")
    expected = descriptor(command.state["bindings"]["provisioning"], instance)
    # remote() uses the existing SSH owner and its pinned identity, never TOFU.
    with child:
        selected = child.state["transaction"]
        if selected is not None and selected["authority"] != authority:
            raise RuntimeError("Provisioned guest selection belongs to another execution")
        if parent["progress"][effect.key] == Progress.VERIFIED.value and (
            selected is None
            or selected["phase"] != "complete"
            or parent["receipts"][effect.key]
            != {"owner": effect.owner, "sha256": apply_approval.digest(child.receipt())}
        ):
            raise RuntimeError("Completed guest lacks its exact child receipt")
        prepared = ordinary_apply.inspect_plan(
            ssh,
            target,
            instance,
            local,
            target_identity=resource.metadata.id,
            **({"resume_manifest": selected["manifest"]} if selected else {}),
        )
        try:
            if any(
                prepared.manifest.get(key) != expected[key]
                for key in ("wheel_sha256", "config_sha256")
            ):
                raise RuntimeError("Provisioned guest artifact or configuration changed")
            observed = prepared.observation
            if selected is None:
                if prepared.noop or any(
                    observed.get(key)
                    for key in (
                        "config_sha256",
                        "operation",
                        "runtime_pending",
                        "runtime_completion",
                    )
                ):
                    raise RuntimeError("New guest already has unapproved runtime state")
                prepared.manifest["command_approval"] = authority["command"]
            else:
                terminal = observed.get("runtime_completion")
                if prepared.noop:
                    if (
                        not terminal
                        or terminal.get("approval") != authority["command"]
                        or terminal.get("manifest")
                        != apply_approval.manifest_identity(selected["manifest"])
                    ):
                        raise RuntimeError("Provisioned guest lacks exact runtime completion")
                elif selected["phase"] == "complete":
                    raise RuntimeError("Completed provisioned guest has new runtime drift")
                elif (
                    not observed.get("runtime_pending")
                    and apply_approval.digest(observed) != selected["predecessor"]
                ):
                    raise RuntimeError("Provisioned guest predecessor changed before activation")
            if _authority(command, resources, instance)[2] != authority:
                raise RuntimeError("Provisioned guest authority changed during inspection")
            child.select(authority, prepared)
            return prepared
        except Exception:
            prepared.close()
            raise


def complete(
    command: CommandJournal,
    resources: ProvisioningJournal,
    child: GuestJournal,
    prepared: ordinary_apply.OrdinaryPlan,
) -> dict:
    """Record only a fresh exact runtime completion observed through prepare()."""
    _parent, _effect, authority = _authority(command, resources, prepared.instance)
    if (child.project, child.gateway, child.guest) != (
        command.project,
        command.gateway,
        prepared.instance.hostname,
    ):
        raise RuntimeError("Provisioned guest journal belongs to another member")
    if not prepared.noop:
        raise RuntimeError("Provisioned guest needs independent final inspection")
    with child:
        selected = child.state["transaction"]
        if (
            selected is None
            or selected["authority"] != authority
            or selected["manifest"] != prepared.manifest
        ):
            raise RuntimeError("Provisioned guest completion differs from selected closure")
        terminal = prepared.observation.get("runtime_completion")
        if (
            not terminal
            or terminal.get("approval") != authority["command"]
            or terminal.get("manifest") != apply_approval.manifest_identity(selected["manifest"])
        ):
            raise RuntimeError("Provisioned guest lacks exact runtime completion")
        if selected["phase"] != "complete":
            selected["phase"] = "complete"
            child.save()
        return child.receipt()
