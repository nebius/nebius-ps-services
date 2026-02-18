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

- Prefer `ExecStart=/usr/bin/env python -m <package>.agent.main ...`.
- Avoid shell wrappers unless there is a strict need.
- Keep service logs structured and readable in `journalctl`.
- Define clear stop/reload behavior (`ExecReload` only when truly supported).
- Add a timer for periodic housekeeping tasks instead of loops/sleeps inside service code.

## Minimal Validation

```bash
python -m <package_name> --help
python -m <package_name> --version
systemd-analyze verify src/<package_name>/systemd/*.service
```
