# CLI and systemd

Load this reference when the project exposes command-line entrypoints or long-running services.

## CLI Best Practices

- Use `Typer` for command structure and help UX.
- Use `Rich` only for presentation, not as business logic.
- Keep command handlers thin; delegate work to service modules.
- Support machine-readable output mode (for example `--output json`).
- Use explicit exit codes and consistent error surfaces.
- Add `--version` output wired to package version.
- Keep commands idempotent where feasible.

## CLI Structure

```text
src/<package_name>/
├── cli.py           # Typer app and command registration
├── __main__.py      # Entrypoint calling app()
├── settings.py      # typed runtime config
└── services/        # operational logic used by CLI commands
```

## systemd Packaging Model

If shipping service units inside the wheel:

```toml
[tool.setuptools.package-data]
<package_name> = ["systemd/*"]
```

Place units here:

```text
src/<package_name>/systemd/
├── <service>.service
└── <service>.timer
```

Select a fully qualified service module and generate the matching importable
module under `src/`. Do not infer a fixed path such as `<package>.agent.main`.

## systemd Service Hardening

Use these defaults unless they conflict with runtime needs:

- `Type=simple`
- `Restart=on-failure`
- `RestartSec=3`
- `NoNewPrivileges=true`
- `ProtectSystem=strict`
- `ProtectHome=true`
- `PrivateTmp=true`
- `ProtectKernelTunables=true`
- `ProtectControlGroups=true`
- `RestrictSUIDSGID=true`
- `LockPersonality=true`
- `MemoryDenyWriteExecute=true`
- `RuntimeDirectory=<service-name>` for lock/state files under `/run/<service-name>/`

Relax hardening only with explicit justification.

## systemd Operational Notes

- Provision the application from its committed lock in `/opt/<project>` with
  `uv sync --locked --no-dev` before enabling the unit.
- Bind `ExecStart` to the exact project-owned interpreter and matching
  importable module:
  `/opt/<project>/.venv/bin/python -m <service_module>`. Do not use an ambient
  `python`, assume a module path, or require uv itself in the service execution
  path.
- Add command arguments only when the generated service entrypoint implements
  and tests the corresponding parser.
- Avoid shell wrappers unless there is a strict need.
- Keep service logs structured and readable in `journalctl`.
- Define clear stop/reload behavior (`ExecReload` only when truly supported).
- Add a timer for periodic housekeeping tasks instead of loops/sleeps inside service code.

## Minimal Validation

```bash
uv run --locked python -m <package_name> --help
uv run --locked python -m <package_name> --version
uv run --locked python -c "import importlib; importlib.import_module('<service_module>')"
test -x /opt/<project_name>/.venv/bin/python
systemd-analyze verify src/<package_name>/systemd/*.service
```
