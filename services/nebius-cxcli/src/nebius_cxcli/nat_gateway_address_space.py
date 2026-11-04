"""Pure, pool-scoped CIDR feasibility; never selects or reserves provider addresses."""

from __future__ import annotations

import ipaddress
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

IPv4 = ipaddress.IPv4Network


class AddressSpaceUnresolved(RuntimeError):
    """Required address-space evidence could not be established."""


@dataclass(frozen=True)
class AddressBlock:
    cidr: IPv4
    max_prefix: int = 32


@dataclass(frozen=True)
class Reservation:
    identity: str
    cidrs: tuple[IPv4, ...]


@dataclass(frozen=True)
class AddressPool:
    identity: str
    blocks: tuple[AddressBlock, ...]
    occupied: tuple[Reservation, ...] = ()


@dataclass(frozen=True)
class AddressDemand:
    identity: str
    pools: tuple[str, ...]
    cidr: str

    @property
    def prefix(self) -> int:
        return int(self.cidr[1:]) if self.cidr.startswith("/") else IPv4(self.cidr).prefixlen


@dataclass(frozen=True)
class PoolCapacity:
    pool: str
    free_addresses: int
    largest_prefix: int | None


@dataclass(frozen=True)
class AddressAssessment:
    status: Literal["feasible", "insufficient", "unresolved"]
    reason: str
    requested_addresses: int = 0
    capacities: tuple[PoolCapacity, ...] = ()

    def message(self) -> str:
        details = " ".join(
            f"Pool {item.pool}: usable free {item.free_addresses}, largest eligible block "
            + (f"/{item.largest_prefix}" if item.largest_prefix is not None else "none")
            + "."
            for item in self.capacities[:8]
        )
        if len(self.capacities) > 8:
            details += f" {len(self.capacities) - 8} additional pools assessed."
        if self.status == "unresolved" and not self.capacities:
            details = "Requested demand and available space could not be confirmed."
        else:
            details = f"Requested {self.requested_addresses} addresses across pool reservations. {details}"
        return (
            f"NAT address space {self.status}: {self.reason} "
            f"{details} "
            "Feasibility is a snapshot, not an address reservation."
        )

    def require_feasible(self) -> None:
        if self.status != "feasible":
            raise RuntimeError(
                self.message()
                + " Resolve inventory access or inputs, or free/extend pool space, then replan."
            )


def subtract(block: IPv4, occupied: IPv4) -> tuple[IPv4, ...]:
    if not block.overlaps(occupied):
        return (block,)
    if block.subnet_of(occupied):
        return ()
    return tuple(block.address_exclude(occupied))


def _free_blocks(
    pools: tuple[AddressPool, ...], spend: Callable[[int], None]
) -> list[tuple[str, AddressBlock]]:
    free: list[tuple[str, AddressBlock]] = []
    identities: set[str] = set()
    for pool in pools:
        spend(1)
        if not pool.identity or pool.identity in identities:
            raise AddressSpaceUnresolved("Duplicate or missing source-pool identity")
        identities.add(pool.identity)
        blocks = pool.blocks
        for index, block in enumerate(blocks):
            spend(index + 1)
            if not block.cidr.prefixlen <= block.max_prefix <= 32:
                raise AddressSpaceUnresolved("Invalid pool maximum mask length")
            if any(block.cidr.overlaps(peer.cidr) for peer in blocks[:index]):
                raise AddressSpaceUnresolved("Ambiguous overlapping source-pool blocks")
            fragments = [block.cidr]
            count = sum(len(reservation.cidrs) for reservation in pool.occupied)
            spend(count * max(1, count.bit_length()))
            occupied = ipaddress.collapse_addresses(
                cidr for reservation in pool.occupied for cidr in reservation.cidrs
            )
            for cidr in occupied:
                spend(len(fragments) + 1)
                fragments = [piece for fragment in fragments for piece in subtract(fragment, cidr)]
            free.extend(
                (pool.identity, AddressBlock(piece, block.max_prefix))
                for piece in fragments
                if piece.prefixlen <= block.max_prefix
            )
    return free


def assess_address_space(
    pools: tuple[AddressPool, ...],
    demands: tuple[AddressDemand, ...],
    *,
    search_limit: int = 10_000,
    work_limit: int = 1_000_000,
) -> AddressAssessment:
    """Fit exact requests then largest prefixes, retaining pool eligibility.

    Only the lowest aligned child of a free CIDR needs trying: the remaining
    prefix requests have no address-specific constraints. Backtrack over distinct
    eligible free blocks/pools; never iterate through individual IP addresses.
    """
    capacities: tuple[PoolCapacity, ...] = ()
    requested = 0
    work = 0

    def spend(amount: int) -> None:
        nonlocal work
        work += amount
        if work > work_limit:
            raise AddressSpaceUnresolved("Address calculation work limit exceeded")

    try:
        if len(demands) > 256:
            raise AddressSpaceUnresolved("Address demand count exceeds the bounded fit limit")
        known = {pool.identity for pool in pools}
        names: set[str] = set()
        for demand in demands:
            if not demand.identity or demand.identity in names:
                raise AddressSpaceUnresolved("Duplicate or missing address-demand identity")
            names.add(demand.identity)
            if not demand.pools or not set(demand.pools) <= known:
                raise AddressSpaceUnresolved("Address demand has unresolved source pools")
            if not 0 <= demand.prefix <= 32:
                raise AddressSpaceUnresolved("Invalid address-demand prefix")
            if not demand.cidr.startswith("/"):
                IPv4(demand.cidr, strict=True)
            requested += 1 << (32 - demand.prefix)
        free = _free_blocks(pools, spend)
        spend(len(pools) * len(free))
        capacities = tuple(
            PoolCapacity(
                pool.identity,
                sum(block.cidr.num_addresses for name, block in free if name == pool.identity),
                min(
                    (block.cidr.prefixlen for name, block in free if name == pool.identity),
                    default=None,
                ),
            )
            for pool in pools
        )
        ordered = sorted(
            demands,
            key=lambda item: (
                item.cidr.startswith("/"),
                item.prefix,
                len(item.pools),
                item.identity,
            ),
        )
        states = 0

        def fit(index: int, remaining: list[tuple[str, AddressBlock]]) -> bool:
            nonlocal states
            states += 1
            if states > search_limit:
                raise AddressSpaceUnresolved("Address fit search limit exceeded")
            if index == len(ordered):
                return True
            demand = ordered[index]
            # Bound sorting, scanning and copying fragments as well as recursion.
            spend(len(remaining) * max(1, len(remaining).bit_length()))
            candidates = sorted(
                enumerate(remaining),
                key=lambda entry: (
                    -entry[1][1].cidr.prefixlen,
                    entry[1][1].max_prefix,
                    entry[1][0],
                    int(entry[1][1].cidr.network_address),
                ),
            )
            for position, (pool, block) in candidates:
                if pool not in demand.pools or not (
                    block.cidr.prefixlen <= demand.prefix <= block.max_prefix
                ):
                    continue
                choice = (
                    IPv4((int(block.cidr.network_address), demand.prefix))
                    if demand.cidr.startswith("/")
                    else IPv4(demand.cidr)
                )
                if not choice.subnet_of(block.cidr):
                    continue
                residual = [
                    (pool, AddressBlock(piece, block.max_prefix))
                    for piece in subtract(block.cidr, choice)
                    if piece.prefixlen <= block.max_prefix
                ]
                spend(len(remaining) + len(residual))
                if fit(index + 1, remaining[:position] + remaining[position + 1 :] + residual):
                    return True
            return False

        feasible = fit(0, free)
        return AddressAssessment(
            "feasible" if feasible else "insufficient",
            "Combined reservations fit the observed pools"
            if feasible
            else "Combined reservations cannot fit the usable contiguous space",
            requested,
            capacities,
        )
    except (AddressSpaceUnresolved, ValueError, TypeError) as exc:
        return AddressAssessment("unresolved", str(exc), requested, capacities)
