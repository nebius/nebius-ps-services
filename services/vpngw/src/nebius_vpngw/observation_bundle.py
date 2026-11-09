"""Bundle the approved upstream VICI client and observation owner for old guests."""

from __future__ import annotations

import base64
import importlib.metadata
from pathlib import Path

import vici

from .ipsec_observation import VICI_VERSION


def streamed_bootstrap() -> str:
    if importlib.metadata.version("vici") != VICI_VERSION:
        raise RuntimeError("The observation bundle requires the approved VICI client")
    root = Path(vici.__file__).parent
    result = "import base64,sys,types\n"
    result += "_vp=types.ModuleType('vici');_vp.__path__=[];sys.modules['vici']=_vp\n"
    sources = [
        (f"vici.{name}", root / f"{name}.py")
        for name in ("exception", "protocol", "command_wrappers", "session", "event_listener")
    ]
    sources += [
        ("vici", root / "__init__.py"),
        ("_vpngw_ipsec_observation", Path(__file__).with_name("ipsec_observation.py")),
    ]
    for name, path in sources:
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        result += (
            f"_m=sys.modules.setdefault({name!r},types.ModuleType({name!r}));"
            f"_m.__package__={name.rpartition('.')[0] or ('vici' if name == 'vici' else '')!r};"
            f"exec(compile(base64.b64decode({encoded!r}),'<vici-probe>','exec'),_m.__dict__)\n"
        )
        if name.startswith("vici."):
            result += f"setattr(_vp,{name.split('.')[1]!r},_m)\n"
    return result


def remote_command() -> str:
    import shlex

    source = streamed_bootstrap() + "\nprint(_vpngw_ipsec_observation.snapshot().json())\n"
    # The module lives in sys.modules, not in the generated script's globals.
    source = source.replace(
        "print(_vpngw_ipsec_observation.", "print(sys.modules['_vpngw_ipsec_observation']."
    )
    encoded = base64.b64encode(source.encode()).decode("ascii")
    return "sudo python3 -c " + shlex.quote(
        "import base64;exec(compile(base64.b64decode(" + repr(encoded) + "),'<vici-probe>','exec'))"
    )
