"""HA controller's durable same-owner tunnel state machine.

The caller holds the HA writer lock before entering this component. Effects must
revalidate authority and take the routing lock in that order. This component has
no cloud mutation port and cannot promote a VM.
"""

from __future__ import annotations

import hashlib
import math
import time
from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from enum import Enum
from pathlib import Path
from time import monotonic
from typing import Any, Generic, Protocol, TypeVar, cast

from ...ha_tunnel_policy import ConnectionPolicy, connection_policies, selections
from .diagnostics import diagnostic
from .models import canonical_json
from .store import CorruptStateError, _envelope, _unwrap, atomic_write_json

CAPABILITY = "connection-tunnel-selection-v1"
STATE_NAME = "tunnel-selection.json"
SCHEMA = "nebius-vpngw/ha-tunnel-selection-v1"


class Health(str, Enum):
    HEALTHY = "healthy"
    DOWN = "down"
    UNKNOWN = "unknown"


class TunnelScopeError(RuntimeError):
    """No effects were made because connection-only suspension was unproven."""


@dataclass(frozen=True)
class Authority:
    cluster: str
    owner: str
    allocation: str
    epoch: str
    incarnation: int
    generation: str

    def __post_init__(self) -> None:
        if any(
            not isinstance(getattr(self, key), str) or not getattr(self, key)
            for key in ("cluster", "owner", "allocation", "epoch", "generation")
        ):
            raise ValueError("HA tunnel authority is incomplete")
        if type(self.incarnation) is not int or self.incarnation < 0:
            raise ValueError("HA tunnel owner incarnation is invalid")

    @property
    def owner_key(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if k not in {"generation", "epoch"}}


@dataclass(frozen=True)
class OrdinaryAuthority:
    """Ordinary authority has no cloud ownership or HA incarnation claim."""

    boot_id: str
    generation: str

    def __post_init__(self) -> None:
        if not self.boot_id or not self.generation:
            raise ValueError("ordinary tunnel authority is incomplete")

    @property
    def owner_key(self) -> dict[str, Any]:
        return {"boot_id": self.boot_id}


TunnelAuthority = Authority | OrdinaryAuthority
AuthorityT = TypeVar("AuthorityT", Authority, OrdinaryAuthority)
EffectAuthorityT = TypeVar("EffectAuthorityT", bound=TunnelAuthority, contravariant=True)


def _authority(value: dict[str, Any]) -> TunnelAuthority:
    return OrdinaryAuthority(**value) if "boot_id" in value else Authority(**value)


class TunnelEffects(Protocol[EffectAuthorityT]):
    def revalidate(self, authority: EffectAuthorityT) -> bool: ...
    def active(self, authority: EffectAuthorityT) -> bool: ...
    def candidate(self, policy: ConnectionPolicy, target: str) -> Health: ...
    def observe(self, policy: ConnectionPolicy, target: str, *, selected: bool) -> Health: ...
    def suspend(self, policy: ConnectionPolicy, authority: EffectAuthorityT) -> None: ...
    def activate(
        self, policy: ConnectionPolicy, target: str | None, authority: EffectAuthorityT
    ) -> None: ...
    def verify(self, policy: ConnectionPolicy, target: str | None) -> Health: ...
    def authority_restored(
        self, policy: ConnectionPolicy, target: str, authority: EffectAuthorityT
    ) -> bool: ...
    def fence(self) -> None: ...


def empty_state() -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "authority": None,
        "selected": {},
        "reasons": {},
        "health": {},
        "operation": None,
        "episode": None,
        "sequence": 0,
    }


def require_tunnel_quiescent(state_dir: Path) -> None:
    from .tunnel_requests import read_request

    request = read_request(state_dir)
    if request is not None and request["result"] == "outstanding":
        raise RuntimeError("HA tunnel request is outstanding; wait for controller convergence")
    operation = read_state(state_dir / STATE_NAME)["operation"]
    if operation is not None and operation["phase"] not in {"failed", "succeeded"}:
        raise RuntimeError("HA tunnel operation is outstanding; wait for controller convergence")


def read_state(path: Path) -> dict[str, Any]:
    import json

    try:
        value = dict(_unwrap(json.loads(path.read_text())))
    except FileNotFoundError:
        return empty_state()
    except (OSError, ValueError) as error:
        raise CorruptStateError("HA tunnel state is unreadable") from error
    if set(value) != set(empty_state()) or value["schema"] != SCHEMA:
        raise CorruptStateError("HA tunnel state has an invalid shape")
    for key in ("selected", "reasons", "health"):
        if not isinstance(value[key], dict):
            raise CorruptStateError("HA tunnel state has invalid connection records")
    for key in ("authority", "operation", "episode"):
        if value[key] is not None and not isinstance(value[key], dict):
            raise CorruptStateError("HA tunnel state has an invalid record")
    if type(value["sequence"]) is not int or value["sequence"] < 0:
        raise CorruptStateError("HA tunnel sequence is invalid")
    try:
        if value["authority"] is not None:
            _authority(value["authority"])
        episode = value["episode"]
        if episode is not None:
            if set(episode) != {
                "owner",
                "started",
                "deadline",
                "last_observed",
                "healthy_since",
                "exhausted",
            }:
                raise ValueError
            _authority(
                {
                    **episode["owner"],
                    "generation": "validation",
                    **({} if "boot_id" in episode["owner"] else {"epoch": "validation"}),
                }
            )
            for key in ("started", "deadline", "last_observed"):
                if type(episode[key]) not in {int, float} or not math.isfinite(episode[key]):
                    raise ValueError
            if (
                episode["deadline"] - episode["started"] != 30
                or type(episode["exhausted"]) is not bool
            ):
                raise ValueError
            if episode["healthy_since"] is not None and (
                type(episode["healthy_since"]) not in {int, float}
                or not math.isfinite(episode["healthy_since"])
            ):
                raise ValueError
        operation = value["operation"]
        if operation is not None:
            if set(operation) != {
                "id",
                "connection",
                "target",
                "verb",
                "requested_verb",
                "requested_target",
                "previous",
                "phase",
                "deadline",
                "attempt_deadline",
                "successes",
                "attempted",
                "result",
                "effects_started",
            }:
                raise ValueError
            if operation["phase"] not in {
                "suspend",
                "activate",
                "restore",
                "verify",
                "verify-unchanged",
                "verify-restore",
                "failed",
                "succeeded",
            }:
                raise ValueError
            for key in ("deadline", "attempt_deadline"):
                if type(operation[key]) not in {int, float} or not math.isfinite(operation[key]):
                    raise ValueError
    except (TypeError, ValueError):
        raise CorruptStateError("HA tunnel authority, operation or episode is malformed") from None
    return value


def effective_config(
    cfg: dict[str, Any], state: dict[str, Any], authority: TunnelAuthority
) -> dict[str, Any]:
    """Do not carry a selection across semantic config or ownership changes."""
    effective = dict(cfg)
    effective["_ha_tunnel_selections"] = (
        dict(state["selected"]) if state["authority"] == asdict(authority) else {}
    )
    selections(effective)  # Reject corrupt/foreign connection or target records.
    operation = state["operation"]
    if (
        state["authority"] == asdict(authority)
        and operation is not None
        and operation["phase"] == "suspend"
    ):
        effective["_ha_suspended_connections"] = [operation["connection"]]
    return effective


class TunnelController(Generic[AuthorityT]):
    def __init__(
        self,
        cfg: dict[str, Any],
        path: Path,
        effects: TunnelEffects[AuthorityT],
        after_persist: Callable[[], None] = lambda: None,
        *,
        preferred_probe: Callable[[ConnectionPolicy, str, AuthorityT], Health] | None = None,
        preferred_observations: dict[str, Any] | None = None,
        backup_probe: Callable[[ConnectionPolicy, str, AuthorityT], Health] | None = None,
        static_recovery_observations: dict[str, Any] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.cfg = cfg
        self.policies: dict[str, ConnectionPolicy] = {p.name: p for p in connection_policies(cfg)}
        self.path = path
        self.effects: TunnelEffects[AuthorityT] = effects
        self.state = read_state(path)
        self.after_persist = after_persist
        self.preferred_probe: Callable[[ConnectionPolicy, str, AuthorityT], Health] | None = (
            preferred_probe
        )
        # Owned by the process adapter, never persisted across a restart. Each
        # observation still binds the full current authority and selected path.
        self.preferred_observations = (
            preferred_observations if preferred_observations is not None else {}
        )

        self.backup_probe: Callable[[ConnectionPolicy, str, AuthorityT], Health] | None = (
            backup_probe
        )
        self.static_recovery_observations = static_recovery_observations
        self.monotonic = monotonic

    def _save(self) -> None:
        atomic_write_json(self.path, _envelope(self.state))
        self.after_persist()

    def bind(self, authority: AuthorityT) -> None:
        previous = self.state["authority"]
        if previous == asdict(authority):
            return
        self._retain_terminal_recovery()
        same_owner = previous is not None and _authority(previous).owner_key == authority.owner_key
        if not same_owner:
            self.state["episode"] = None
        # Configuration changes invalidate an operation, not its consumed episode.
        if not same_owner or previous["generation"] != authority.generation:
            self.state.update(selected={}, reasons={}, health={})
            if self.state["episode"] is not None:
                self.state["episode"]["healthy_since"] = None
        self.state["authority"] = asdict(authority)
        if self.state["operation"] is not None:
            self.state["operation"].update(phase="failed", result="authority-changed")
        self._save()

    def _episode(self, now: float, authority: AuthorityT) -> dict[str, Any]:
        episode = self.state["episode"]
        if episode is None:
            episode = {
                "owner": authority.owner_key,
                "started": now,
                "deadline": now + 30,
                "last_observed": now,
                "healthy_since": None,
                "exhausted": False,
            }
            self.state["episode"] = episode
            self._save()
        return episode

    def _pending(self) -> bool:
        op = self.state["operation"]
        return op is not None and op["phase"] not in {"succeeded", "failed"}

    def prepare_bgp_resume(
        self, authority: AuthorityT, now: float, *, revalidate: Callable[[], bool]
    ) -> None:
        """Prepare desired policy behind the fence; the VM activation owns effects.

        The runtime admits only exact fenced ownership under writer exclusion.
        Never bind stale intent here, execute native effects, or replace another
        connection's operation. Completed selections have no expiry deadline.
        """
        if not math.isfinite(now) or self.state["authority"] != asdict(authority):
            return
        previous = deepcopy(self.state)
        op = self.state["operation"] if self._pending() else None
        changed = False
        policy: ConnectionPolicy
        for policy in self.policies.values():
            if policy.mode != "bgp":
                continue
            pending = op if op is not None and op["connection"] == policy.name else None
            selected = self.state["selected"].get(policy.name)
            if selected is None and pending is None:
                continue
            observations: dict[str, Health] = {}

            def candidate(
                target: str,
                candidate_policy: ConnectionPolicy = policy,
                observations: dict[str, Health] = observations,
            ) -> Health:
                if target not in observations:
                    observations[target] = self.effects.candidate(candidate_policy, target)
                return observations[target]

            release = selected is not None and candidate(selected) is Health.DOWN
            if pending is not None:
                pending["successes"] = 0
                changed = True
                expired = now >= pending["deadline"]
                if pending["verb"] == "release":
                    release = True
                elif expired or (
                    pending["verb"] != "failback" and candidate(pending["target"]) is Health.DOWN
                ):
                    pending.update(
                        verb="release", phase="activate", result="target-failed-restoring-policy"
                    )
                    release = True
                elif pending["verb"] == "failback":
                    release = release or candidate(policy.preferred) is Health.HEALTHY
            if release:
                self.state["selected"].pop(policy.name, None)
                self.state["reasons"][policy.name] = "restart-policy-restoration"
                # Native normal-policy verification has not happened yet.
                self.state["health"][policy.name] = Health.UNKNOWN.value
                changed = True
            elif selected is not None:
                # Candidate health cannot certify active route/export policy.
                self.state["health"][policy.name] = Health.UNKNOWN.value
                changed = True
        if changed:
            if revalidate():
                self._save()
            else:
                self.state = previous

    def request(
        self,
        verb: str,
        target: str | None,
        authority: AuthorityT,
        now: float,
        *,
        operation_id: str | None = None,
    ) -> str:
        if verb not in {"failover", "failback"}:
            raise ValueError("unknown tunnel operation")
        if not self.effects.revalidate(authority):
            raise RuntimeError("HA tunnel authority is not current")
        self.bind(authority)
        pending = self.state["operation"]
        if self._pending():
            if pending["requested_verb"] == verb and (
                target is None or pending["target"] == target
            ):
                return str(pending["id"])
            raise RuntimeError("another HA tunnel operation is outstanding")
        candidates = [
            (p, name)
            for p in self.policies.values()
            if p.eligible
            for name in (
                [p.preferred]
                if verb == "failback"
                else [
                    x.name
                    for x in p.paths
                    if x.name != self.state["selected"].get(p.name, p.preferred)
                ]
            )
            if target is None or target == name
        ]
        if len(candidates) != 1:
            raise ValueError(
                "name one eligible tunnel on the current owner; selection is unavailable or ambiguous"
            )
        policy, resolved = candidates[0]
        unchanged = verb == "failback" and (
            policy.mode == "static"
            and self.state["selected"].get(policy.name, policy.preferred) == resolved
            or policy.mode == "bgp"
            and policy.name not in self.state["selected"]
        )
        episode = self.state["episode"]
        if (
            not unchanged
            and policy.mode == "static"
            and episode is not None
            and (episode["exhausted"] or now >= episode["deadline"])
        ):
            raise RuntimeError(
                "Static recovery budget is consumed; 60 seconds of continuous health is required"
            )
        if (
            policy.mode == "bgp"
            and self.effects.observe(policy, resolved, selected=False) is not Health.HEALTHY
        ):
            raise RuntimeError("manual BGP target health is not proven")
        self._begin(
            policy,
            resolved,
            verb,
            authority,
            now,
            operation_id=operation_id,
            requested_target=target,
            unchanged=unchanged,
        )
        return str(self.state["operation"]["id"])

    def _retain_terminal_recovery(self) -> None:
        operation = self.state["operation"]
        if (
            operation is None
            or operation["requested_verb"] != "automatic-recovery"
            or operation["phase"] not in {"failed", "succeeded"}
            or operation["result"] == "authority-changed"
        ):
            return
        operation_id = operation["id"]
        if len(operation_id) != 64 or any(c not in "0123456789abcdef" for c in operation_id):
            raise CorruptStateError("Static recovery operation identity is malformed")
        receipt = self.path.parent / "static-recovery-results" / f"{operation_id}.json"
        if receipt.is_symlink() or receipt.parent.is_symlink():
            raise CorruptStateError("Static recovery receipt path is unsafe")
        if receipt.exists():
            saved = read_state(receipt)
            if saved["authority"] != self.state["authority"] or saved["operation"] != operation:
                raise CorruptStateError("Static recovery terminal receipt changed")
            return
        # Same strict envelope, immutable operation identity and exact terminal
        # state. Retain the actual result before a new operation can replace it.
        atomic_write_json(receipt, _envelope(self.state))

    def _begin(
        self,
        policy: ConnectionPolicy,
        target: str,
        verb: str,
        authority: AuthorityT,
        now: float,
        *,
        operation_id: str | None = None,
        requested_target: str | None = None,
        unchanged: bool = False,
    ) -> None:
        self._retain_terminal_recovery()
        self.state["sequence"] += 1
        operation_id = (
            operation_id
            or hashlib.sha256(
                canonical_json(
                    {
                        "authority": asdict(authority),
                        "sequence": self.state["sequence"],
                        "connection": policy.name,
                        "target": target,
                        "verb": verb,
                    }
                ).encode()
            ).hexdigest()
        )
        self.state["operation"] = {
            "id": operation_id,
            "connection": policy.name,
            "target": target,
            "verb": verb,
            "requested_verb": verb,
            "requested_target": requested_target,
            "previous": self.state["selected"].get(policy.name, policy.preferred),
            "phase": "verify-unchanged"
            if unchanged
            else "suspend"
            if policy.mode == "static"
            else "activate",
            "deadline": now + 29,
            "attempt_deadline": now + 10,
            "successes": 0,
            "attempted": [target],
            "result": "outstanding",
            "effects_started": False,
        }
        self._save()

    def tick(self, authority: AuthorityT, now: float) -> None:
        if not math.isfinite(now):
            raise ValueError("tunnel clock must be finite")
        started = monotonic()
        tick_started_at = self.monotonic()
        if not self.effects.revalidate(authority):
            if self.static_recovery_observations is not None:
                diagnostic("static-recovery-reset", reason="tick-authority-revalidation")
                self.static_recovery_observations.clear()
            return
        self.bind(authority)
        health: dict[str, str] = {}
        for policy in self.policies.values():
            target = self.state["selected"].get(policy.name, policy.preferred)
            if policy.mode == "bgp":
                # An Established session can withdraw routes. Verify ongoing
                # received/selected/exported coverage for manual overrides too;
                # normal policy can use any healthy path, not just preferred.
                health[policy.name] = self.effects.verify(
                    policy, self.state["selected"].get(policy.name)
                ).value
            elif policy.mode == "static" and not policy.eligible:
                routes = {
                    prefix: path.name for path in policy.paths for prefix in path.destinations
                }
                observed = [
                    self.effects.observe(
                        replace(
                            policy,
                            destinations=frozenset(
                                prefix for prefix, name in routes.items() if name == path.name
                            ),
                        ),
                        path.name,
                        selected=True,
                    )
                    for path in policy.paths
                    if path.name in routes.values()
                ]
                health[policy.name] = (
                    Health.UNKNOWN
                    if not observed or Health.UNKNOWN in observed
                    else Health.DOWN
                    if Health.DOWN in observed
                    else Health.HEALTHY
                ).value
            else:
                health[policy.name] = self.effects.observe(policy, target, selected=True).value
        self.state["health"] = health
        static = list(self.policies.values())
        loss = any(health[p.name] == Health.DOWN.value for p in static)
        healthy = bool(static) and all(health[p.name] == Health.HEALTHY.value for p in static)
        if loss:
            self._episode(now, authority)
        episode = self.state["episode"]
        if episode is not None:
            # Clock rollback cannot renew the persisted budget, even across reboot.
            if now < episode["last_observed"]:
                episode["exhausted"] = True
            if now - episode["last_observed"] > 10:
                episode["healthy_since"] = None
            episode["last_observed"] = max(now, episode["last_observed"])
            observing_unchanged = (
                self._pending() and self.state["operation"]["phase"] == "verify-unchanged"
            )
            if healthy and (not self._pending() or observing_unchanged):
                if episode["healthy_since"] is None:
                    episode["healthy_since"] = now
                elif now - episode["healthy_since"] >= 60:
                    self.state["episode"] = episode = None
            else:
                episode["healthy_since"] = None
            if (
                episode is not None
                and not healthy
                and (episode["exhausted"] or now >= episode["deadline"])
            ):
                episode["exhausted"] = True
                self._save()
                # Expiry is path evidence, never gateway failure. Restore the
                # previous authority durably, then leave native retry running.
                if self._pending() and self.state["operation"]["phase"] not in {
                    "restore",
                    "verify-restore",
                    "verify-unchanged",
                }:
                    op = self.state["operation"]
                    policy = self.policies[op["connection"]]
                    if policy.mode == "bgp":
                        self.state["selected"].pop(policy.name, None)
                        op.update(verb="release", phase="activate", successes=0)
                    elif op["requested_verb"] != "automatic-recovery":
                        op.update(phase="restore", successes=0)
                self._save()
        self._save()
        if self._pending():
            self.preferred_observations.clear()
            self._advance(authority, now, elapsed_seconds=monotonic() - started)
            return
        if self.static_recovery_observations is not None:
            if self._recover_static(authority, health, now, tick_started_at):
                return
        if episode is not None and episode["exhausted"]:
            return
        for policy in self.policies.values():
            if not policy.eligible:
                continue
            if policy.mode == "bgp":
                if (
                    policy.name in self.state["selected"]
                    and health[policy.name] == Health.DOWN.value
                ):
                    self._begin(policy, policy.preferred, "release", authority, now)
                    # Persist release intent, then restore normal policy before
                    # the VM policy can repair a path under the stale override.
                    self._advance(authority, now, elapsed_seconds=monotonic() - started)
                    return
            elif (
                health[policy.name] == Health.DOWN.value
                and self.static_recovery_observations is None
            ):
                self.preferred_observations.pop(policy.name, None)
                current = self.state["selected"].get(policy.name, policy.preferred)
                alternatives = [p.name for p in policy.paths if p.name != current]
                if alternatives:
                    self._begin(policy, alternatives[0], "automatic", authority, now)
                    return
            elif self._preferred_ready(policy, authority, now, health[policy.name]):
                self._begin(policy, policy.preferred, "automatic-failback", authority, now)
                return

    def _recover_static(
        self, authority: AuthorityT, health: dict[str, str], now: float, tick_started_at: float
    ) -> bool:
        """Admit a fresh usable backup after 30s of continuous selected-path loss.

        Observation is process-local and monotonic. Restart, unknown evidence,
        explicit health changes and any authority/selection change restart the delay.
        The accepted operation remains durable and uses its own bounded deadline.
        No exhausted episode is renewed, and no VM/cloud effect is available here.
        """
        records = self.static_recovery_observations
        assert records is not None
        observed_at = self.monotonic()
        if not math.isfinite(observed_at) or not math.isfinite(tick_started_at):
            diagnostic("static-recovery-reset", reason="nonfinite-clock")
            records.clear()
            return False
        if observed_at < tick_started_at:
            diagnostic("static-recovery-reset", reason="clock-backwards-during-observation")
            records.clear()
            return False
        for policy in self.policies.values():
            if policy.mode != "static" or not policy.eligible:
                diagnostic("static-recovery-skip", connection=policy.name, reason="ineligible")
                records.pop(policy.name, None)
                continue
            if health[policy.name] != Health.DOWN.value:
                diagnostic(
                    "static-recovery-reset",
                    connection=policy.name,
                    reason="selected-not-down",
                    health=health[policy.name],
                )
                records.pop(policy.name, None)
                continue
            if not self.effects.active(authority):
                diagnostic(
                    "static-recovery-reset", connection=policy.name, reason="inactive-authority"
                )
                records.pop(policy.name, None)
                continue
            selected = self.state["selected"].get(policy.name, policy.preferred)
            binding = (asdict(authority), selected)
            record = records.get(policy.name)
            reset = None
            if record is None:
                reset = "first-observation"
            elif record["binding"] != binding:
                reset = "binding-changed"
            elif observed_at < record["last"]:
                reset = "clock-backwards"
            if reset is not None:
                diagnostic(
                    "static-recovery-reset",
                    connection=policy.name,
                    selected=selected,
                    reason=reset,
                    gap=0 if record is None else observed_at - record["last"],
                    decision_monotonic=observed_at,
                )
                record = {
                    "binding": binding,
                    "since": observed_at,
                    "last": observed_at,
                    "probe_at": observed_at - 5,
                    "healthy": {},
                    "attempted": {},
                    "next_path": 0,
                }
                records[policy.name] = record
            record = cast(dict[str, Any], record)
            record["last"] = observed_at
            if observed_at - record["since"] < 30:
                diagnostic(
                    "static-recovery-wait",
                    connection=policy.name,
                    selected=selected,
                    reason="continuous-loss-delay",
                    age=observed_at - record["since"],
                    wait_seconds=30 - (observed_at - record["since"]),
                )
                continue
            if self.backup_probe is None or observed_at - record["probe_at"] < 5:
                diagnostic(
                    "static-recovery-wait",
                    connection=policy.name,
                    selected=selected,
                    reason="probe-unavailable" if self.backup_probe is None else "probe-interval",
                    age=observed_at - record["probe_at"],
                )
                continue
            record["probe_at"] = observed_at
            alternatives = [path for path in policy.paths if path.name != selected]
            path = alternatives[record["next_path"] % len(alternatives)]
            attempt = record["attempted"].get(path.name)
            if attempt is not None:
                operation = self.state["operation"]
                if operation is None or operation["id"] != attempt["operation"]:
                    receipt = (
                        self.path.parent
                        / "static-recovery-results"
                        / f"{attempt['operation']}.json"
                    )
                    operation = read_state(receipt)["operation"]
                if (
                    operation is None
                    or operation["phase"] != "failed"
                    or operation["result"]
                    not in {"native-recovery-retained", "previous-path-restored"}
                    or attempt["count"] >= 3
                ):
                    diagnostic(
                        "static-recovery-skip",
                        connection=policy.name,
                        target=path.name,
                        reason="prior-attempt-not-retryable",
                        count=attempt["count"],
                    )
                    record["next_path"] += 1
                    continue
                if attempt["terminal_at"] is None:
                    attempt["terminal_at"] = observed_at
                    record["healthy"].pop(path.name, None)
                if observed_at - attempt["terminal_at"] < 30 * attempt["count"]:
                    diagnostic(
                        "static-recovery-wait",
                        connection=policy.name,
                        target=path.name,
                        reason="retry-backoff",
                        age=observed_at - attempt["terminal_at"],
                        wait_seconds=30 * attempt["count"] - (observed_at - attempt["terminal_at"]),
                    )
                    record["next_path"] += 1
                    continue
                if not self.effects.authority_restored(policy, operation["previous"], authority):
                    diagnostic(
                        "static-recovery-skip",
                        connection=policy.name,
                        target=path.name,
                        reason="previous-authority-not-restored",
                    )
                    record["healthy"].pop(path.name, None)
                    record["next_path"] += 1
                    continue
            # One bounded probe per connection per poll prevents a dead remote
            # with several alternatives from monopolizing the controller tick.
            diagnostic(
                "static-recovery-probe-started",
                connection=policy.name,
                target=path.name,
                selected=selected,
                age=observed_at - record["since"],
            )
            candidate = self.backup_probe(policy, path.name, authority)
            proof_at = self.monotonic()
            if not math.isfinite(proof_at) or proof_at < observed_at:
                diagnostic(
                    "static-recovery-reset",
                    connection=policy.name,
                    target=path.name,
                    reason="invalid-probe-clock",
                )
                records.pop(policy.name, None)
                continue
            record["probe_at"] = proof_at
            record["last"] = proof_at
            diagnostic(
                "static-recovery-probe-result",
                connection=policy.name,
                target=path.name,
                health=candidate.value,
            )
            if candidate is not Health.HEALTHY:
                record["healthy"].pop(path.name, None)
                record["next_path"] += 1
                continue
            previous = record["healthy"].get(path.name)
            confirmations = 1
            if previous is not None and 0 <= proof_at - previous["at"] <= 30:
                confirmations = previous["count"] + 1
            record["healthy"][path.name] = {"at": proof_at, "count": confirmations}
            diagnostic(
                "static-recovery-confirmation",
                connection=policy.name,
                target=path.name,
                confirmations=confirmations,
            )
            if confirmations < 2:
                # Confirm this path next, instead of waiting a topology-sized
                # rotation that can outlive every admissible proof window.
                continue
            selected_health = self.effects.observe(policy, selected, selected=True)
            if selected_health is not Health.DOWN:
                diagnostic(
                    "static-recovery-reset",
                    connection=policy.name,
                    reason="selected-changed-before-admission",
                    health=selected_health.value,
                )
                health[policy.name] = selected_health.value
                records.pop(policy.name, None)
                self._save()
                continue
            if not self.effects.active(authority):
                diagnostic(
                    "static-recovery-reset",
                    connection=policy.name,
                    target=path.name,
                    reason="authority-lost-before-admission",
                )
                records.pop(policy.name, None)
                return False
            admitted_at = self.monotonic()
            if not math.isfinite(admitted_at) or admitted_at < proof_at:
                records.pop(policy.name, None)
                continue
            if previous is None or admitted_at - previous["at"] > 30:
                record["healthy"][path.name]["count"] = 1
                diagnostic(
                    "static-recovery-wait",
                    connection=policy.name,
                    target=path.name,
                    reason="confirmation-expired-before-admission",
                )
                continue
            admission_now = now + admitted_at - tick_started_at
            self._begin(policy, path.name, "automatic-recovery", authority, admission_now)
            diagnostic(
                "static-recovery-admitted",
                connection=policy.name,
                target=path.name,
                selected=selected,
                confirmations=confirmations,
            )
            record["attempted"][path.name] = {
                "count": 1 if attempt is None else attempt["count"] + 1,
                "operation": self.state["operation"]["id"],
                "terminal_at": None,
            }
            record["healthy"].pop(path.name, None)
            record["next_path"] += 1
            return True
        return False

    def _preferred_ready(
        self, policy: ConnectionPolicy, authority: AuthorityT, now: float, selected_health: str
    ) -> bool:
        selected = self.state["selected"].get(policy.name, policy.preferred)
        records = self.preferred_observations
        if (
            policy.mode != "static"
            or self.preferred_probe is None
            or selected == policy.preferred
            or selected_health != Health.HEALTHY.value
            or self.state["reasons"].get(policy.name) == "failover"
        ):
            records.pop(policy.name, None)
            return False
        binding = (asdict(authority), selected)
        record = records.get(policy.name)
        if (
            record is None
            or record["binding"] != binding
            or now < record["last"]
            or now - record["last"] > 10
        ):
            record = {"binding": binding, "last": now - 5, "since": None, "successes": 0}
            records[policy.name] = record
        if now - record["last"] < 5:
            return False
        record["last"] = now
        observed = self.preferred_probe(policy, policy.preferred, authority)
        if observed is not Health.HEALTHY or not self.effects.revalidate(authority):
            record.update(since=None, successes=0)
            return False
        if record["since"] is None:
            record["since"] = now
        record["successes"] += 1
        return record["successes"] >= 2 and now - record["since"] >= 60

    def _expire_selection(self, policy: ConnectionPolicy) -> None:
        op = self.state["operation"]
        if policy.mode == "bgp":
            # Expiry cannot cancel a partially installed policy. Preserve the
            # same operation until normal routing is durably restored.
            if op["verb"] == "release":
                return
            self.state["selected"].pop(policy.name, None)
            op.update(
                verb="release",
                phase="activate",
                successes=0,
                result="target-failed-restoring-policy",
            )
        elif op["phase"] in {"restore", "verify-restore"}:
            return
        else:
            op.update(phase="restore", successes=0, result="recovery-exhausted-restoring")
        self._save()

    def _advance(self, authority: AuthorityT, now: float, *, elapsed_seconds: float = 0.0) -> None:
        started = monotonic()

        def current_time() -> float:
            return now + elapsed_seconds + monotonic() - started

        op = self.state["operation"]
        policy = self.policies[op["connection"]]
        if not self.effects.revalidate(authority):
            return
        if op["phase"] == "verify-unchanged":
            # Selection already matches intent. Verification cannot consume or
            # renew recovery authority, reload policy, or restart native SAs.
            try:
                observed = (
                    self.effects.verify(policy, None if policy.mode == "bgp" else op["target"])
                    if self.effects.active(authority)
                    else Health.UNKNOWN
                )
            except RuntimeError:
                observed = Health.UNKNOWN
            op["successes"] = op["successes"] + 1 if observed is Health.HEALTHY else 0
            if current_time() >= op["deadline"]:
                op.update(phase="failed", result="unchanged-path-unverified")
            elif op["successes"] >= 2:
                op.update(phase="succeeded", result="verified")
            elif observed is Health.DOWN:
                op.update(phase="failed", result="unchanged-path-unverified")
            self._save()
            return
        if policy.mode == "bgp" and not self.effects.active(authority):
            return
        if policy.mode == "static" and op["requested_verb"] != "automatic-recovery":
            episode = self._episode(now, authority)
            op["deadline"] = min(op["deadline"], episode["deadline"] - 1)
        if current_time() >= op["deadline"]:
            self._expire_selection(policy)
        if policy.mode == "bgp" and op["verb"] == "failback":
            # A restart or delayed retry must not reuse old preferred-path proof.
            if self.effects.candidate(policy, policy.preferred) is not Health.HEALTHY:
                op["successes"] = 0
                self._save()
                return
        target = op["target"]
        if policy.mode == "bgp" and op["verb"] in {"failback", "release"}:
            target = None
        try:
            if op["phase"] == "suspend":
                op["effects_started"] = True
                self._save()
                self.effects.suspend(policy, authority)
                op["phase"] = "activate"
            elif op["phase"] in {"activate", "restore"}:
                restore = op["phase"] == "restore"
                target = op["previous"] if restore else target
                if target is None:
                    self.state["selected"].pop(policy.name, None)
                else:
                    self.state["selected"][policy.name] = target
                self._save()  # All render/repair paths see intent before activation.
                self.effects.activate(policy, target, authority)
                op["phase"] = "verify-restore" if restore else "verify"
            else:
                restoring = op["phase"] == "verify-restore"
                if (
                    restoring
                    and self.state["episode"] is not None
                    and self.state["episode"]["exhausted"]
                ):
                    # Recovery health is separate from restored initiation
                    # authority. Do not continually cancel native retries while
                    # waiting for an unavailable remote endpoint to return.
                    if self.effects.authority_restored(policy, op["previous"], authority):
                        op.update(phase="failed", result="native-recovery-retained")
                    self._save()
                    return
                observed = self.effects.verify(policy, op["previous"] if restoring else target)
                if policy.mode == "bgp" and not self.effects.active(authority):
                    op["successes"] = 0
                    self._save()
                    return
                if not restoring and op["verb"] != "release" and current_time() >= op["deadline"]:
                    self._expire_selection(policy)
                    return
                op["successes"] = op["successes"] + 1 if observed is Health.HEALTHY else 0
                if op["successes"] >= 2:
                    failed_target = op["result"] == "target-failed-restoring-policy"
                    op.update(
                        phase="failed" if restoring or failed_target else "succeeded",
                        result="previous-path-restored"
                        if restoring
                        else "normal-policy-restored"
                        if failed_target
                        else "verified",
                    )
                    if not restoring:
                        self.state["reasons"][policy.name] = op["verb"]
                elif (
                    current_time() >= op["attempt_deadline"]
                    and not restoring
                    and observed is Health.DOWN
                ):
                    # Pending probe hysteresis or unavailable evidence cannot
                    # prove candidate failure. The original request deadline
                    # still bounds waiting and retains its expiry cleanup.
                    self._failed_attempt(policy, current_time())
        except TunnelScopeError:
            if op["phase"] == "suspend":
                # The initial scope check preceded every native mutation. Do
                # not convert a rejected local request into gateway suspension.
                op.update(phase="failed", result="suspension-scope-unproven")
            else:
                self._failed_attempt(policy, current_time())
        except RuntimeError:
            if op["requested_verb"] == "automatic-recovery":
                op["result"] = "native-effect-failed-restoring"
            self._failed_attempt(policy, current_time())
        self._save()

    def _failed_attempt(self, policy: ConnectionPolicy, now: float) -> None:
        op = self.state["operation"]
        op["successes"] = 0
        if op["verb"] == "automatic":
            alternatives = [
                p.name
                for p in policy.paths
                if p.name not in op["attempted"] and p.name != op["previous"]
            ]
            if alternatives and now + 3 < op["deadline"]:
                op.update(
                    target=alternatives[0],
                    phase="suspend",
                    attempt_deadline=min(now + 5, op["deadline"] - 2),
                )
                op["attempted"].append(alternatives[0])
                return
        if policy.mode == "bgp":
            # Failed manual target releases its override; normal policy is verified.
            op.update(
                verb="release",
                phase="activate",
                attempt_deadline=op["deadline"],
                result="target-failed-restoring-policy",
            )
        else:
            op.update(phase="restore", attempt_deadline=op["deadline"])
