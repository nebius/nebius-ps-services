"""Regression tests for the remote handoff source bundle (``handoff_bootstrap``).

The ordinary runtime handoff streams ``streamed_source()`` to the guest and execs
it in a fresh interpreter whose stub packages have ``__path__ == []`` -- so the
guest can only import modules the bundle itself registered in ``sys.modules``.

``deploy/ordinary_route_ownership`` is bundled and, inside ``validate()``, lazily
imports ``ordinary_route_plan`` and ``ordinary_route_conversion`` whenever the
inspected gateway has a non-null route plan. If those modules are absent from the
bundle the guest ``inspect`` raises ``ModuleNotFoundError`` and writes nothing, so
the operator side sees an opaque failure. These tests pin the bundle closed over
that import path.
"""

from __future__ import annotations

import sys

from nebius_vpngw import handoff_bootstrap


def _exec_bundle_isolated() -> dict[str, object]:
    """Exec the streamed guest bundle in isolation; return its nebius_vpngw modules.

    Restores ``sys.modules`` afterwards so the real package is untouched.
    """
    code = handoff_bootstrap.streamed_source()
    saved = dict(sys.modules)
    try:
        exec(compile(code, "<ha-bundle>", "exec"), {})  # noqa: S102 - exercising the real guest bundle
        return {
            name: module
            for name, module in sys.modules.items()
            if name.startswith("nebius_vpngw")
        }
    finally:
        sys.modules.clear()
        sys.modules.update(saved)


def test_bundle_declares_ordinary_route_ownership_lazy_dependencies() -> None:
    bundled = {name for name, _ in handoff_bootstrap.SOURCES}
    # ordinary_route_ownership is bundled and imports these two at runtime.
    assert "nebius_vpngw.deploy.ordinary_route_ownership" in bundled
    assert "nebius_vpngw.deploy.ordinary_route_plan" in bundled
    assert "nebius_vpngw.deploy.ordinary_route_conversion" in bundled


def test_streamed_bundle_execs_and_resolves_route_plan_imports() -> None:
    # Execs the actual guest bundle; this also catches a wrong dependency order
    # (ordinary_route_plan imports ordinary_route_ownership at module scope).
    modules = _exec_bundle_isolated()

    assert "nebius_vpngw.deploy.ordinary_route_ownership" in modules
    assert "nebius_vpngw.deploy.ordinary_route_plan" in modules
    assert "nebius_vpngw.deploy.ordinary_route_conversion" in modules

    # Present and actually exec'd (public symbols that ownership.validate imports).
    assert hasattr(modules["nebius_vpngw.deploy.ordinary_route_plan"], "intent")
    assert hasattr(modules["nebius_vpngw.deploy.ordinary_route_conversion"], "resolve")
