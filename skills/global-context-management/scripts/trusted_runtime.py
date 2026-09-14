"""Closed, verified support groups for personal skill installations.

The reviewed bootstrap injects its no-follow source reader before calling this
module. This module never searches ambient Python paths or executes bytecode.
"""
from __future__ import annotations

import hashlib
from importlib.machinery import ModuleSpec
from pathlib import Path
import sys
from types import ModuleType

SUPPORT_FILES = ("agent_runtime.py", "hook_runtime.py", "trusted_runtime.py", "task_state_permissions.py")
_SOURCES = {
    "agent_runtime": "global-context-management/scripts/agent_runtime.py",
    "hook_runtime": "global-context-management/scripts/hook_runtime.py",
    "task_state_permissions": "global-context-management/scripts/task_state_permissions.py",
    "global_context_state": "global-context-management/assets/global_context_state.py.template",
    "prompt_session_storage": "prompt-session-intake/assets/hooks/prompt_session_storage.py",
    "prompt_session_event": "prompt-session-intake/assets/hooks/prompt_session_event.py",
    "prompt_session_result": "prompt-session-intake/assets/hooks/prompt_session_result.py",
    "prompt_session_state": "prompt-session-intake/assets/hooks/prompt_session_state.py",
    "lib": "sdlc-start/assets/hooks/lib/__init__.py",
    "lib.sdlc_state": "sdlc-start/assets/hooks/lib/sdlc_state.py",
    "lib.sdlc_policy": "sdlc-start/assets/hooks/lib/sdlc_policy.py",
}
_GROUPS = {
    "runtime": ("agent_runtime",),
    "projector": ("agent_runtime", "hook_runtime"),
    "permissions": ("task_state_permissions",),
    "context-dependencies": ("agent_runtime", "task_state_permissions"),
    "global-context": ("agent_runtime", "task_state_permissions", "global_context_state"),
    "prompt-intake": ("agent_runtime", "prompt_session_storage", "prompt_session_event",
                      "prompt_session_result", "prompt_session_state"),
    "sdlc-hook-state": ("agent_runtime", "lib", "lib.sdlc_state", "lib.sdlc_policy"),
}
def _read_source(path):
    raise ImportError("shared support requires its verified bootstrap")


_verified: dict[str, tuple[ModuleType, str, str, tuple]] = {}
_loading: set[str] = set()


def load_support(group: str, *, anchor: tuple[str, Path], source_only: bool = False) -> dict[str, ModuleType]:
    """Load one declared group from one complete catalog or flat hook bundle."""
    kind, root = anchor
    if kind not in {"catalog", "flat"} or (source_only and kind != "catalog"):
        raise ImportError("source-only support requires the reviewed catalog")
    if group not in _GROUPS:
        raise ImportError("unknown shared support group")
    captured = {}
    for name in _GROUPS[group]:
        relative = _SOURCES[name]
        if kind == "flat":
            relative = ("lib/" + Path(relative).name if name.startswith("lib")
                        else Path(relative).name.removesuffix(".template"))
        path = root / relative
        data, identity = _read_source(path)  # injected by verified bootstrap
        digest = hashlib.sha256(data).hexdigest()
        captured[name] = (path, data, identity, digest)
        previous = sys.modules.get(name)
        if name in sys.modules:
            record = _verified.get(name)
            if record != (previous, str(path), digest, identity):
                raise ImportError("unverified or conflicting shared support module: " + name)
    added = []
    attributes = []
    try:
        for name, (path, data, identity, digest) in captured.items():
            if name in _verified:
                continue
            if name in _loading:
                raise ImportError("cyclic shared support dependency: " + name)
            module = ModuleType(name)
            module.__file__ = str(path)
            module.__package__ = name if name == "lib" else name.rpartition(".")[0]
            module.__spec__ = ModuleSpec(name, loader=None, origin=str(path), is_package=name == "lib")
            if name == "lib":
                module.__path__ = []  # only explicitly registered submodules may load
            sys.modules[name] = module
            _verified[name] = (module, str(path), digest, identity)
            added.append((name, module))
            _loading.add(name)
            try:
                exec(compile(data, str(path), "exec"), module.__dict__)
            finally:
                _loading.discard(name)
            if "." in name:
                parent_name, _, child = name.rpartition(".")
                parent = sys.modules[parent_name]
                if hasattr(parent, child):
                    raise ImportError("conflicting shared package attribute")
                setattr(parent, child, module)
                attributes.append((parent, child, module))
        return {name: sys.modules[name] for name in _GROUPS[group]}
    except BaseException:
        for parent, child, module in reversed(attributes):
            if getattr(parent, child, None) is module:
                delattr(parent, child)
        for name, module in reversed(added):
            if sys.modules.get(name) is module:
                del sys.modules[name]
            if _verified.get(name, (None,))[0] is module:
                del _verified[name]
        raise
