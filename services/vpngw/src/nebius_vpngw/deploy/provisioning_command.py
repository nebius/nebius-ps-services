"""Compose fresh ordinary resources with the existing apply child owners."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any

from .. import apply_approval
from . import (
    ordinary_apply,
    provisioning_discovery,
    provisioning_execute,
    provisioning_guest,
    provisioning_receipts,
    provisioning_routes,
    provisioning_trust,
)
from .apply_command import CommandJournal
from .apply_plan import CommandPlan, Effect, Progress
from .ordinary_command import guest_effect, guest_key
from .ordinary_route_command import prepare as prepare_routes
from .ordinary_route_discovery import Discovery
from .ordinary_route_ownership import RouteJournal
from .provisioning_journal import ProvisioningJournal, requests_from_plan
from .route_manager import RouteManager
from .security_group_apply import changed as attachments_changed
from .ssh_policy import require_vm_ha_ssh_policy


def pending(journal: CommandJournal, local: dict, artifact: str | None = None) -> dict | None:
    snapshot = journal.snapshot()
    transaction = snapshot["transaction"] if snapshot else None
    if transaction is None or transaction["phase"] == "complete":
        return None
    plan = CommandPlan.parse(transaction["plan"])
    if not any(effect.owner == "ordinary-provisioning" for effect in plan.effects):
        return None
    if plan.configuration_sha256 != apply_approval.digest(local) or (
        artifact is not None and plan.artifact_sha256 != artifact
    ):
        raise RuntimeError("Pending provisioning requires its original configuration and artifact")
    assert snapshot is not None
    resources = snapshot["bindings"].get("provisioning", {})
    recipes = requests_from_plan(resources)
    guests = {item.guest for item in recipes if item.guest is not None}
    expected = {
        "ssh": "ssh-bootstrap",
        "provisioning": "ordinary-provisioning",
        "routes": "ordinary-provisioned-routes",
    }
    for effect in plan.effects:
        if effect.key.startswith("guest:"):
            expected[effect.key] = (
                "ordinary-provisioned-runtime"
                if effect.key.removeprefix("guest:") in guests
                else "ordinary-runtime"
            )
        elif effect.key == "attachments":
            expected[effect.key] = "security-groups"
    if (
        {effect.key: effect.owner for effect in plan.effects} != expected
        or set(snapshot["bindings"]) != set(expected)
        or not {guest_key(name) for name in guests} <= set(expected)
        or resources["configuration_sha256"] != plan.configuration_sha256
        or resources["artifact_sha256"] != plan.artifact_sha256
        or (resources["project_id"], resources["gateway_name"])
        != (journal.project, journal.gateway)
    ):
        raise RuntimeError("Pending provisioning owner scope changed")
    return snapshot


def original_guests(snapshot: dict) -> set[str]:
    return set(snapshot["bindings"]["provisioning"]["bootstraps"])


def package_manifests(snapshot: dict) -> dict:
    plan = CommandPlan.parse(snapshot["transaction"]["plan"])
    return {
        effect.key.removeprefix("guest:"): {
            **deepcopy(snapshot["bindings"][effect.key]),
            "command_approval": apply_approval.bind(plan.approval, plan.mapping()),
        }
        for effect in plan.effects
        if effect.owner == "ordinary-runtime"
    }


def policy(manager: Any, deployment: Any, scope: Any, desired: dict):
    """Reconstruct the original publication scope, including already-created slots."""
    names = set(desired["members"])
    return require_vm_ha_ssh_policy(
        tuple(sorted(desired["targets"].items())),
        enrollment_hosts=names,
        retained_hosts=set(desired["targets"]) - names,
        management_key_path=manager._management_key_path,
        management_public_key=deployment.gateway_group.vm_spec.get("ssh_public_key"),
        trust_scope=scope,
        allow_managed_repair=True,
        persist_default_host_keys=True,
    )


def bootstrap_template(manager: Any, deployment: Any, index: int) -> str:
    spec = deployment.gateway_group
    return manager._build_cloud_init(
        ssh_key=spec.vm_spec.get("ssh_public_key"),
        local_prefixes=deployment.gateway.get("local_prefixes"),
        vm_ha=False,
        ingress=spec.ingress_policies[index],
    )


@dataclass
class ProvisioningCommand:
    journal: CommandJournal
    plan: CommandPlan
    resumed: bool
    intents: dict
    manager: Any
    deployment: Any
    local: dict
    trust_scope: Any
    routes: Any = None
    route_receipt: dict | None = None

    @property
    def approval(self) -> str:
        return self.plan.approval

    @property
    def guests(self) -> set[str]:
        return set(self.intents["provisioning"]["bootstraps"])

    def resources(self) -> ProvisioningJournal:
        return ProvisioningJournal(self.journal.project, self.journal.gateway)

    def begin(self) -> None:
        self.journal.begin(self.plan, intents=self.intents)

    def publish_trust(self):
        key = "ssh"
        if self.journal.state["transaction"]["progress"][key] != Progress.VERIFIED.value:
            self.journal.started(key)
        desired = self.intents[key]

        def observe():
            return policy(self.manager, self.deployment, self.trust_scope, desired)

        receipt = provisioning_trust.publish(
            self.journal,
            provisioning_trust.TrustJournal(self.journal.project, self.journal.gateway),
            desired,
            observe,
        )
        self.journal.verified(key, receipt)
        return observe()

    def provision(self, ssh_policy: Any) -> dict[str, str]:
        trust = provisioning_trust.TrustJournal(self.journal.project, self.journal.gateway)
        selected = trust.snapshot()
        parent = self.journal.state["transaction"]
        if (
            selected is None
            or selected["transaction"] is None
            or selected["transaction"]["phase"] != "complete"
            or selected["transaction"]["approval"] != self.approval
            or selected["transaction"]["execution_id"] != parent["execution_id"]
            or selected["transaction"]["intent"] != self.intents["ssh"]
            or selected["transaction"]["selected"]
            != provisioning_trust.selection(ssh_policy, tuple(self.intents["ssh"]["members"]))
        ):
            raise RuntimeError("Provisioning requires its exact selected SSH identity receipt")
        key = "provisioning"
        if self.journal.state["transaction"]["progress"][key] != Progress.VERIFIED.value:
            self.journal.started(key)
        desired = self.intents[key]
        instances = {item.hostname: item for item in self.deployment.iter_instance_configs()}

        def bootstrap(name):
            template = bootstrap_template(
                self.manager, self.deployment, instances[name].instance_index
            )
            return provisioning_execute.Bootstrap(
                ordinary_apply.sha(template.encode()),
                apply_approval.digest(self.intents["ssh"]),
                self.manager._render_gateway_ssh_enrollment_cloud_init(
                    template, ssh_policy.identity_for(name)
                ),
            )

        sdk = self.manager._get_client()
        child = self.resources()
        receipt = provisioning_execute.execute(
            sdk, child, self.journal, desired, bootstrap=bootstrap
        )
        self.journal.verified(key, receipt)
        targets = {}
        for effect in self.plan.effects:
            if effect.owner == "ordinary-runtime":
                name = effect.key.removeprefix("guest:")
                current = self.manager.inspect_ordinary_targets([name]).get(name)
                if current != effect.targets:
                    raise RuntimeError("Retained gateway identity changed during provisioning")
                targets[name] = current[1]
        for name in sorted(self.guests):
            resource = provisioning_receipts.compute(sdk, self.journal, child, name)
            target = self.manager._vm_public_ip_from_object(resource)
            if not target:
                raise RuntimeError("Created gateway has no authoritative public transport")
            targets[name] = target
        return targets

    def bind_policy(self, ssh_policy: Any, targets: dict):
        if set(targets) != {item.hostname for item in self.deployment.iter_instance_configs()}:
            raise RuntimeError("Provisioning transport scope is incomplete")
        # provision() proved each transport from the exact successful creation or
        # retained identity. Preserve pins and private keys; only transport moves.
        ssh_policy.assert_current()
        return replace(ssh_policy, transport_targets=tuple(sorted(targets.items())))

    def prepare_guest(self, ssh: Any, instance: Any):
        key = guest_key(instance.hostname)
        if self.journal.state["transaction"]["progress"][key] != Progress.VERIFIED.value:
            self.journal.started(key)
        return provisioning_guest.prepare(
            self.manager._get_client(),
            self.journal,
            self.resources(),
            provisioning_guest.GuestJournal(
                self.journal.project, self.journal.gateway, instance.hostname
            ),
            ssh,
            instance,
            self.local,
        )

    def complete_guest(self, ssh: Any, instance: Any) -> dict:
        prepared = self.prepare_guest(ssh, instance)
        try:
            return provisioning_guest.complete(
                self.journal,
                self.resources(),
                provisioning_guest.GuestJournal(
                    self.journal.project, self.journal.gateway, instance.hostname
                ),
                prepared,
            )
        finally:
            prepared.close()

    def bind_routes(self, ssh_policy: Any) -> None:
        bound = provisioning_routes.bind(self.intents["routes"], self.resources(), self.journal)
        routes = prepare_routes(
            self.manager,
            self.deployment,
            self.local,
            ssh_policy=ssh_policy,
            runtime_changes=True,
        )
        completed = routes.completed
        if completed is not None and completed["approval"] == self.approval:
            if (
                completed["intent"] != bound
                or not routes.verifies_completed()
                or self.journal.state["transaction"]["progress"]["routes"] == Progress.PLANNED.value
            ):
                raise RuntimeError("Provisioned routes lack exact completion proof")
            self.routes = routes
            self.route_receipt = {
                "approval": self.approval,
                "intent": apply_approval.digest(bound),
                "selection": apply_approval.digest(completed["plan"]),
            }
            parent = self.journal.state["transaction"]
            if parent["progress"]["routes"] == Progress.VERIFIED.value and parent["receipts"][
                "routes"
            ] != {
                "owner": "ordinary-provisioned-routes",
                "sha256": apply_approval.digest(self.route_receipt),
            }:
                raise RuntimeError("Provisioned route receipt changed")
            return
        if self.journal.state["transaction"]["progress"]["routes"] == Progress.VERIFIED.value:
            raise RuntimeError("Completed provisioned routes lost their child receipt")
        if routes.intent != bound:
            raise RuntimeError("Provisioned route scope changed from the original approved intent")
        # This child exists even when its eventual request set is empty. It owns
        # deferred BGP selection and exact post-provisioning destination coverage.
        routes.required = True
        routes.begin(self.approval)
        self.routes = routes

    def finish_routes(self) -> None:
        if self.routes is None:
            raise RuntimeError("Provisioned routes have no bound intent")
        if self.route_receipt is None:
            self.journal.started("routes")
            receipt = self.routes.finish(self.approval)
            self.route_receipt = {key: receipt[key] for key in ("approval", "intent", "selection")}
        self.journal.verified("routes", self.route_receipt)


def inspect(
    journal: CommandJournal,
    *,
    manager: Any,
    deployment: Any,
    local: dict,
    targets: dict,
    plans: dict,
    artifact: str,
    ssh_policy: Any,
    trust_scope: Any,
    attachments: dict | None,
    attachment_pending: dict | None,
) -> ProvisioningCommand:
    previous = pending(journal, local, artifact)
    instances = tuple(deployment.iter_instance_configs())
    if deployment.vm_ha is not None:
        raise RuntimeError("Ordinary provisioning cannot own HA lifecycle effects")
    if previous is not None:
        root = previous["transaction"]
        approved = CommandPlan.parse(root["plan"])
        saved = previous["bindings"]
        names = original_guests(previous)
        retained = {item.hostname for item in instances} - names
        if set(plans) != retained or set(targets) - names != retained:
            raise RuntimeError("Provisioning retained member inventory changed")
        for effect in approved.effects:
            if effect.owner != "ordinary-runtime":
                continue
            name = effect.key.removeprefix("guest:")
            prepared = plans[name]
            if targets[name] != effect.targets:
                raise RuntimeError("Provisioning retained target changed")
            if prepared.noop:
                terminal = prepared.observation.get("runtime_completion")
                if root["progress"][effect.key] == Progress.STARTED.value and (
                    not terminal
                    or terminal.get("approval")
                    != apply_approval.bind(approved.approval, approved.mapping())
                    or terminal.get("manifest") != effect.request_sha256
                ):
                    raise RuntimeError("Retained guest lacks its exact completion receipt")
            elif root["progress"][effect.key] == Progress.VERIFIED.value:
                raise RuntimeError("Verified retained guest has new runtime drift")
            elif (
                not prepared.observation.get("runtime_pending")
                and replace(guest_effect(name, prepared, targets[name]), requires=effect.requires)
                != effect
            ):
                raise RuntimeError("Retained guest predecessor changed before activation")
        if attachment_pending is not None and (
            attachment_pending["approval"] != approved.approval
            or attachment_pending["operation_id"] != root["execution_id"]
        ):
            raise RuntimeError("Provisioning attachments belong to another command")
        return ProvisioningCommand(
            journal, approved, True, saved, manager, deployment, local, trust_scope
        )
    if journal.pending() is not None or attachment_pending is not None:
        raise RuntimeError("Resume the pending command before fresh provisioning")
    names = {item.hostname for item in instances} - set(targets)
    if not names or set(plans) != set(targets):
        raise RuntimeError("Provisioning requires exact absent and retained gateway members")
    trust = provisioning_trust.intent(ssh_policy, tuple(sorted(names)))
    bootstraps = {
        item.hostname: {
            "template_sha256": ordinary_apply.sha(
                bootstrap_template(manager, deployment, item.instance_index).encode()
            ),
            "trust_sha256": apply_approval.digest(trust),
        }
        for item in instances
        if item.hostname in names
    }
    resources = provisioning_discovery.resources(
        manager,
        manager._get_client(),
        deployment.gateway_group,
        tuple(sorted(item.instance_index for item in instances if item.hostname in names)),
        configuration_sha256=apply_approval.digest(local),
        artifact_sha256=artifact,
        bootstraps=bootstraps,
    )
    discovery = Discovery(
        manager,
        deployment,
        local,
        RouteJournal(journal.project, journal.gateway),
        RouteManager(manager.project_id, auth_token=manager.auth_token, ssh_policy=ssh_policy),
    )
    routes = provisioning_routes.prepare(discovery, resources)
    intents = {"ssh": trust, "provisioning": resources, "routes": routes}
    effects = [
        Effect(
            "ssh",
            "ssh-bootstrap",
            "publish",
            tuple(sorted(trust["targets"])),
            apply_approval.digest(trust),
            apply_approval.digest(trust),
            False,
        ),
        Effect(
            "provisioning",
            "ordinary-provisioning",
            "create",
            tuple(sorted(names)),
            apply_approval.digest(resources),
            apply_approval.digest(resources["observed"]),
            True,
            ("ssh",),
        ),
    ]
    for item in sorted(instances, key=lambda item: item.hostname):
        key = guest_key(item.hostname)
        if item.hostname in names:
            value = provisioning_guest.descriptor(resources, item)
            effect = Effect(
                key,
                "ordinary-provisioned-runtime",
                "apply",
                (item.hostname,),
                apply_approval.digest(value),
                apply_approval.digest(value),
                True,
                ("provisioning",),
            )
        else:
            prepared = plans[item.hostname]
            value = {
                key: deepcopy(value)
                for key, value in prepared.manifest.items()
                if key not in {"command_approval", "ha_parent"}
            }
            effect = replace(
                guest_effect(item.hostname, prepared, targets[item.hostname]),
                requires=("provisioning",),
            )
        effects.append(effect)
        intents[key] = value
    effects.append(
        Effect(
            "routes",
            "ordinary-provisioned-routes",
            "reconcile",
            tuple(sorted(item.hostname for item in instances)),
            apply_approval.digest(routes),
            apply_approval.digest(routes),
            True,
            tuple(effect.key for effect in effects if effect.key != "ssh"),
        )
    )
    if attachments is not None and attachments_changed(attachments):
        intents["attachments"] = attachments
        effects.append(
            Effect(
                "attachments",
                "security-groups",
                "reconcile",
                tuple(sorted(targets)),
                apply_approval.digest(attachments),
                apply_approval.digest(attachments),
                True,
                tuple(effect.key for effect in effects),
            )
        )
    plan = CommandPlan(
        journal.project, journal.gateway, apply_approval.digest(local), artifact, tuple(effects)
    )
    return ProvisioningCommand(
        journal, plan, False, intents, manager, deployment, local, trust_scope
    )
