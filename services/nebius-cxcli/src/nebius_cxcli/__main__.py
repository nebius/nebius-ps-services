"""CLI entrypoint for nebius-cxcli."""

from __future__ import annotations

import sys


def _acquire_mk8s_credential(
    *,
    project_id: str,
    client_name: str,
    endpoint: str | None,
    require_renewable_auth: bool = False,
) -> dict[str, str]:
    from .cli import _acquire_mk8s_exec_credential_status

    return _acquire_mk8s_exec_credential_status(
        project_id=project_id,
        client_name=client_name,
        endpoint=endpoint,
        require_renewable_auth=require_renewable_auth,
    )


def main() -> None:
    if sys.argv[1:2] == ["mk8s-token"]:
        from .mk8s_exec import create_app

        create_app(_acquire_mk8s_credential)()
    else:
        from .cli import main as cli_main

        cli_main()


if __name__ == "__main__":
    main()
