from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from nebius_vpngw.nebius_pagination import (
    NebiusPaginationError,
    collect_nebius_pages,
)


class _Waitable:
    def __init__(self, response: object) -> None:
        self.response = response

    def wait(self) -> object:
        return self.response


class _BrokenItemsProperty:
    next_page_token = ""

    @property
    def items(self) -> object:
        raise OSError("sensitive provider detail")


class _BrokenIterable:
    def __iter__(self):
        raise ValueError("sensitive provider detail")


def test_collects_all_waitable_and_immediate_pages_in_order() -> None:
    tokens: list[str] = []

    def fetch(token: str) -> object:
        tokens.append(token)
        if token == "":
            return _Waitable(
                SimpleNamespace(items=[SimpleNamespace(id="one")], next_page_token="second")
            )
        return SimpleNamespace(items=[SimpleNamespace(id="two")], next_page_token="")

    result = collect_nebius_pages(
        fetch,
        context="Compute instance",
        item_identity=lambda item: item.id,
    )

    assert [item.id for item in result] == ["one", "two"]
    assert tokens == ["", "second"]


@pytest.mark.parametrize(
    "response",
    [
        SimpleNamespace(next_page_token=""),
        SimpleNamespace(items=7, next_page_token=""),
        SimpleNamespace(items=[], next_page_token=7),
    ],
)
def test_rejects_malformed_response_shape(response: object) -> None:
    with pytest.raises(NebiusPaginationError, match="Compute instance inventory is unavailable"):
        collect_nebius_pages(lambda _token: response, context="Compute instance")


def test_rejects_a_non_adjacent_token_cycle_without_partial_return() -> None:
    pages = {
        "": SimpleNamespace(items=["one"], next_page_token="a"),
        "a": SimpleNamespace(items=["two"], next_page_token="b"),
        "b": SimpleNamespace(items=["three"], next_page_token="a"),
    }

    with pytest.raises(NebiusPaginationError, match="Route table inventory is unavailable"):
        collect_nebius_pages(
            lambda token: pages[token],
            context="Route table",
        )


def test_rejects_duplicate_stable_identity() -> None:
    pages = {
        "": SimpleNamespace(items=[SimpleNamespace(id="same")], next_page_token="next"),
        "next": SimpleNamespace(items=[SimpleNamespace(id="same")], next_page_token=""),
    }

    with pytest.raises(NebiusPaginationError, match="Allocation inventory is unavailable"):
        collect_nebius_pages(
            lambda token: pages[token],
            context="Allocation",
            item_identity=lambda item: item.id,
        )


def test_rejects_failure_after_a_successful_page() -> None:
    def fetch(token: str) -> object:
        if not token:
            return SimpleNamespace(items=["partial"], next_page_token="next")
        raise RuntimeError("provider detail must stay in the exception chain")

    with pytest.raises(NebiusPaginationError, match="Subnet inventory is unavailable") as raised:
        collect_nebius_pages(fetch, context="Subnet")

    assert isinstance(raised.value.__cause__, RuntimeError)
    assert "provider detail" not in str(raised.value)


@pytest.mark.parametrize(
    "response",
    (
        _BrokenItemsProperty(),
        SimpleNamespace(items=_BrokenIterable(), next_page_token=""),
    ),
)
def test_malformed_response_access_stays_inside_sanitized_boundary(response: object) -> None:
    with pytest.raises(NebiusPaginationError, match="Subnet inventory is unavailable") as raised:
        collect_nebius_pages(lambda _token: response, context="Subnet")

    assert "sensitive provider detail" not in str(raised.value)
    assert raised.value.__cause__ is not None


def test_enforces_page_bound_and_safe_context() -> None:
    with pytest.raises(NebiusPaginationError, match="Network inventory is unavailable"):
        collect_nebius_pages(
            lambda token: SimpleNamespace(items=[token], next_page_token=f"{token}x"),
            context="Network",
            max_pages=2,
        )

    with pytest.raises(ValueError, match="fixed safe label"):
        collect_nebius_pages(
            lambda _token: SimpleNamespace(items=[], next_page_token=""),
            context="Allocation: secret detail",
        )


def test_production_nebius_list_calls_use_the_shared_paginator() -> None:
    package_root = Path(__file__).parents[2] / "src" / "nebius_vpngw"
    collection_methods = {
        "List",
        "list",
        "list_by_account",
        "list_by_network",
        "list_members",
    }
    checked: list[str] = []
    unguarded: list[str] = []

    for path in package_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        parents = {
            child: parent
            for parent in ast.walk(tree)
            for child in ast.iter_child_nodes(parent)
        }
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in collection_methods
            ):
                continue
            location = f"{path.relative_to(package_root)}:{node.lineno}"
            checked.append(location)
            ancestor = parents.get(node)
            while ancestor is not None:
                if (
                    isinstance(ancestor, ast.Call)
                    and isinstance(ancestor.func, ast.Name)
                    and ancestor.func.id == "collect_nebius_pages"
                ):
                    break
                ancestor = parents.get(ancestor)
            else:
                unguarded.append(location)

    assert checked, "pagination guard did not inspect any collection calls"
    assert not unguarded, "Nebius collection calls bypass the shared paginator: " + ", ".join(
        unguarded
    )


def test_pagination_audit_covers_the_complete_public_command_tree() -> None:
    from click import Group
    from typer.main import get_command

    from nebius_vpngw.cli import app

    leaves: set[tuple[str, ...]] = set()

    def collect(command: object, prefix: tuple[str, ...] = ()) -> None:
        if isinstance(command, Group):
            for name, child in command.commands.items():
                collect(child, (*prefix, name))
            return
        leaves.add(prefix)

    collect(get_command(app))

    assert leaves == {
        ("add-routes-local",),
        ("apply",),
        ("create-config",),
        ("create-from-peer-config",),
        ("destroy",),
        ("failback", "tunnel"),
        ("failback", "vm"),
        ("failover", "tunnel"),
        ("failover", "vm"),
        ("list-routes-local",),
        ("list-routes-remote",),
        ("prep-network",),
        ("restart-tunnel",),
        ("status",),
        ("validate-config",),
        ("vm-ha",),
    }
