"""The public Grafana installation and dashboard commands."""

from __future__ import annotations

import sys
import webbrowser
from collections.abc import Iterator, Sequence
from contextlib import ExitStack, contextmanager, nullcontext
from pathlib import Path
from typing import Annotated, Any

import click
import questionary
import typer
from prompt_toolkit.filters import Condition, IsDone
from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
from prompt_toolkit.layout import ConditionalContainer, FormattedTextControl, HSplit, Window
from questionary.prompts.common import InquirerControl
from rich.console import Console

from .grafana_api import DashboardOwnership, GrafanaClient, GrafanaError, normalize_url, token_auth
from .grafana_cluster import ClusterSession, cluster_session
from .grafana_dashboards import (
    DatasourceMappingRequired,
    json_bytes,
    load_dashboards,
    map_datasources,
    parse_mappings,
)
from .grafana_import import (
    check_import_ownership,
    execute_imports,
    prepare_imports,
    require_complete,
)
from .grafana_progress import GrafanaProgress
from .grafana_project import (
    assert_import_ownership,
    declared_dashboards,
    prepare_attachment,
    publication_identity,
    publish_import,
    recover_import_publication,
)
from .project_bundle_transaction import ProjectBundleTransaction
from .runtime_config import to_plain_data

app = typer.Typer(
    short_help="Install Grafana and manage dashboards.",
    help=(
        "Install Grafana and observability, or import, export and validate Grafana dashboards.\n\n"
        "Replace CLUSTER_TARGET with the target ID from config.yaml. Cluster imports save project "
        "state and install immediately; --attach additionally publishes reusable dashboards to "
        "the source catalog.\n\n"
        "External API access uses a token from the named environment variable. "
        "SSO import prepares files and opens the browser; complete the import manually in Grafana."
    ),
    epilog=(
        "Examples: Import one dashboard into the cluster: "
        "nebius-cxcli grafana import ./gpu.json --config ./config.yaml --target CLUSTER_TARGET  |  "
        "Import one dashboard and attach it to the source catalog: "
        "nebius-cxcli grafana import ./gpu.json --config ./config.yaml "
        "--target CLUSTER_TARGET --attach  |  "
        "Import all dashboards from a directory: "
        "nebius-cxcli grafana import ./dashboards --config ./config.yaml --target CLUSTER_TARGET  |  "
        "Import a directory and subdirectories and attach its dashboards: "
        "nebius-cxcli grafana import ./dashboards --config ./config.yaml "
        "--target CLUSTER_TARGET --recursive --attach  |  "
        "Import into external Grafana using an existing token environment variable: "
        "nebius-cxcli grafana import ./dashboards --url https://grafana.example.com/ "
        "--token-env GRAFANA_TOKEN  |  "
        "Prepare an external SSO import for manual browser completion: "
        "nebius-cxcli grafana import ./dashboards --url https://grafana.example.com/ --sso  |  "
        "Export all accessible dashboards from the cluster: "
        "nebius-cxcli grafana export --config ./config.yaml --target CLUSTER_TARGET "
        "--output-dir ./exported  |  "
        "Validate local JSON without contacting Grafana: "
        "nebius-cxcli grafana validate ./dashboards --recursive"
    ),
    no_args_is_help=True,
)
console = Console()

Config = Annotated[
    Path | None,
    typer.Option("--config", help="Cluster config (default: ./config.yaml); invalid with --url."),
]
Target = Annotated[
    str, typer.Option("--target", help="Exact configured cluster target; interactive mode can ask.")
]
Url = Annotated[
    str, typer.Option("--url", help="External Grafana base URL; no project or catalog access.")
]
Token = Annotated[
    str,
    typer.Option(
        "--token-env",
        help="External API token environment variable; no ambient credential fallback.",
    ),
]
Folder = Annotated[
    str,
    typer.Option(
        "--folder-uid",
        help="Existing Grafana folder UID; import defaults to root, preserving managed folders.",
    ),
]
Overwrite = Annotated[
    bool,
    typer.Option("--overwrite", help="Replace differing content; concurrent changes still fail."),
]
Output = Annotated[
    Path | None,
    typer.Option(
        "--output-dir",
        help="Local output directory (export: ./dashboards; SSO: ./dashboards-prepared).",
    ),
]
Recursive = Annotated[
    bool, typer.Option("--recursive", help="Include JSON files in subdirectories.")
]


def emit(message: str) -> None:
    console.print(message, markup=False, highlight=False)


def interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _guard_datasource_filter(question: questionary.Question) -> None:
    """Questionary restores all choices on a search miss; don't accept that fallback."""
    application = question.application
    control = next(
        (
            item
            for item in application.layout.find_all_controls()
            if isinstance(item, InquirerControl)
        ),
        None,
    )
    if control is None or application.key_bindings is None:
        raise RuntimeError("Unable to initialize datasource search")

    def no_matches() -> bool:
        query = (control.search_filter or "").lower()
        return bool(query) and not any(
            isinstance(choice.title, str) and query in choice.title.lower()
            for choice in control.choices
        )

    guard = KeyBindings()

    @guard.add("enter", filter=Condition(no_matches), eager=True)
    def keep_searching(_event: Any) -> None:
        pass

    application.key_bindings = merge_key_bindings([application.key_bindings, guard])
    application.layout.container = HSplit(
        [
            application.layout.container,
            ConditionalContainer(
                Window(
                    FormattedTextControl(
                        "No matching datasources. Change or clear the filter, or Ctrl+C to cancel."
                    ),
                    dont_extend_height=True,
                    wrap_lines=True,
                ),
                filter=Condition(no_matches) & ~IsDone(),
            ),
        ]
    )


def _select_datasource(required: DatasourceMappingRequired) -> str:
    candidates = sorted(
        required.candidates, key=lambda item: (item["name"].casefold(), item["uid"])
    )
    if not candidates:
        kind = f" of type {required.expected_type!r}" if required.expected_type else ""
        raise ValueError(
            f"No compatible datasources{kind} available for {required.source!r}. "
            "Check the selected Grafana destination and its configured datasources."
        )
    emit(f"Required type: {required.expected_type or 'not specified (all types shown)'}")
    try:
        question = questionary.select(
            f"Select datasource for {required.source!r}",
            choices=[
                questionary.Choice(
                    title=f"{item['name']} · {item['type'] or 'unknown type'} · UID: {item['uid']}",
                    value=item["uid"],
                )
                for item in candidates
            ],
            default=candidates[0]["uid"],
            use_search_filter=True,
            use_jk_keys=False,
            instruction="↑↓ Navigate · Type to filter · Enter Select · Ctrl+C Cancel",
        )
        _guard_datasource_filter(question)
        selected = question.unsafe_ask()
    except (KeyboardInterrupt, EOFError, typer.Abort):
        selected = None
    if selected is None:
        emit("Import cancelled.")
        raise typer.Exit(1)
    if selected not in {item["uid"] for item in candidates}:
        raise ValueError("Select one of the available datasources")
    return selected


def destination_options(
    ctx: typer.Context,
    *,
    config: Path | None,
    target: str,
    url: str,
    token: str,
    sso: bool = False,
    attach: bool = False,
    catalog: Path | None = None,
) -> None:
    if url:
        root = ctx.find_root().params
        if (
            config is not None
            or target
            or attach
            or catalog is not None
            or root.get("component_sources_file") is not None
            or root.get("source_profile") is not None
        ):
            raise ValueError(
                "External --url mode rejects --config, --target, --attach and catalog options"
            )
        normalize_url(url)
    elif token or sso:
        raise ValueError(
            "Cluster mode reads its existing Secret; --token-env and --sso require --url"
        )
    if token and sso:
        raise ValueError("Choose --token-env or --sso, not both")
    if catalog is not None and not attach:
        raise ValueError("--component-sources requires --attach")


def selected_target(config: Path, target: str) -> str:
    if target:
        return target
    if not interactive():
        raise ValueError("Cluster commands require --target in noninteractive mode")
    from .config_loader import load_config
    from .deploy_targets import enabled_cluster_target_refs

    choices = enabled_cluster_target_refs(
        to_plain_data(load_config(config, persist_normalized=False))
    )
    emit("Configured targets: " + ", ".join(choices))
    selected = typer.prompt("Target")
    if selected not in choices:
        raise ValueError("Select one of the configured targets")
    return selected


@contextmanager
def destination(
    *,
    config: Path | None,
    target: str,
    url: str,
    token: str,
    mutating: bool,
    catalog: Path | None = None,
    progress: GrafanaProgress | None = None,
    discovery: ClusterSession | None = None,
) -> Iterator[tuple[GrafanaClient, ClusterSession | None]]:
    notify = progress.update if progress is not None else lambda _: None
    if url:
        notify("Checking Grafana authentication and API")
        client = GrafanaClient(url, token_auth(token))
        client.connect()
        yield client, None
        return
    config_path = (config or Path("config.yaml")).expanduser().absolute()
    with progress.paused(resume=False) if progress is not None else nullcontext():
        selected = selected_target(config_path, target)
    if mutating:
        notify("Checking previous import state")
        recover_import_publication(config_path, selected, catalog)
    with cluster_session(
        config_path,
        selected,
        mutating=mutating,
        progress=notify,
        pause=progress.paused if progress is not None else None,
        discovery=discovery,
    ) as session:
        yield session.client, session
        notify("Closing Grafana connection and releasing operation leases")


def write_outputs(
    dashboards: Sequence[dict[str, Any]], output: Path, *, overwrite: bool
) -> list[Path]:
    output = output.expanduser().absolute()
    if output.is_symlink() or any(parent.is_symlink() for parent in output.parents):
        raise ValueError("Output directory must not traverse symlinks")
    output.mkdir(parents=True, exist_ok=True)
    txn = ProjectBundleTransaction(output)
    generation = publication_identity("output", output)
    txn.recover_expected_generation(generation)
    updates = {}
    preimages = {}
    for dashboard in dashboards:
        path = output / f"{dashboard['uid']}.json"
        if path.is_symlink():
            raise ValueError("Output files must not be symlinks")
        data = json_bytes(dashboard)
        pre = txn.snapshot_preimages([path], read_only=True)[path]
        preimages[path] = pre.sha256
        if pre.content is not None and pre.content != data and not overwrite:
            raise ValueError(f"Output file {path.name} differs; use --overwrite")
        if pre.content != data:
            updates[path] = data
    if updates:
        txn.commit(
            updates,
            expected_preimages={path: preimages[path] for path in updates},
            generation_sha256=generation,
        )
    return [output / f"{dashboard['uid']}.json" for dashboard in dashboards]


@app.command(
    "import",
    epilog=(
        "Examples: nebius-cxcli grafana import ./gpu.json --config ./config.yaml "
        "--target CLUSTER_TARGET  |  "
        "nebius-cxcli grafana import ./dashboards --config ./config.yaml "
        "--target CLUSTER_TARGET --recursive --attach  |  "
        "nebius-cxcli grafana import ./dashboards --url https://grafana.example.com/ "
        "--token-env GRAFANA_TOKEN  |  "
        "nebius-cxcli grafana import ./dashboards --url https://grafana.example.com/ --sso"
    ),
)
def import_command(
    ctx: typer.Context,
    paths: Annotated[
        list[Path], typer.Argument(metavar="PATH...", help="Dashboard JSON files or directories.")
    ],
    config: Config = None,
    target: Target = "",
    url: Url = "",
    token_env: Token = "",
    sso: Annotated[
        bool,
        typer.Option(
            "--sso", help="External interactive browser-assisted import; manual completion remains."
        ),
    ] = False,
    attach: Annotated[
        bool,
        typer.Option(
            "--attach", help="Also register reusable JSON in the catalog (cluster import only)."
        ),
    ] = False,
    component_sources: Annotated[
        Path | None,
        typer.Option(
            "--component-sources",
            help="Catalog to update with --attach; defaults to the active catalog.",
        ),
    ] = None,
    folder_uid: Folder = "",
    recursive: Recursive = False,
    datasource_map: Annotated[
        list[str] | None,
        typer.Option("--datasource-map", help="Map SOURCE=UID; repeat for distinct datasources."),
    ] = None,
    overwrite: Overwrite = False,
    output_dir: Output = None,
) -> None:
    """Save and immediately install dashboards; --attach additionally publishes the catalog.

    --overwrite also permits temporary updates to editable classic file-provisioned
    dashboards. Keep their UID and folder; provisioning remains authoritative.
    Managed cluster copies are manual-only, and cannot use --attach.

    Interactive API import offers a searchable list for unresolved datasources;
    even a single choice requires Enter. Automation uses --datasource-map SOURCE=UID.
    """
    with GrafanaProgress() as progress, ExitStack() as discovery_stack:

        def report(message: str) -> None:
            with progress.paused():
                emit(message)

        try:
            destination_options(
                ctx,
                config=config,
                target=target,
                url=url,
                token=token_env,
                sso=sso,
                attach=attach,
                catalog=component_sources,
            )
            if url and not token_env and not sso:
                if not interactive():
                    raise ValueError(
                        "External automation requires --token-env; interactive import also supports --sso"
                    )
                choice = typer.prompt("Authentication (token/sso)", default="sso")
                if choice == "sso":
                    sso = True
                elif choice == "token":
                    token_env = typer.prompt("Token environment variable", default="GRAFANA_TOKEN")
                else:
                    raise ValueError("Choose token or sso authentication")
            if output_dir is not None and not sso:
                raise ValueError("Import --output-dir applies only to prepared --sso files")
            if not sso:
                progress.update("Loading dashboard files")
            dashboards = load_dashboards(paths, recursive=recursive)
            mappings = parse_mappings(datasource_map or [])
            if sso:
                if not interactive():
                    raise ValueError("--sso is interactive; automation must use --token-env")
                prepared = [
                    map_datasources({**item, "editable": True}, mappings) for item in dashboards
                ]
                files = write_outputs(
                    prepared, output_dir or Path("dashboards-prepared"), overwrite=overwrite
                )
                browser_url = normalize_url(url) + "dashboard/import"
                for path in files:
                    report(f"Prepared: {path}")
                report(f"Open: {browser_url}")
                if folder_uid:
                    report(f"Select destination folder UID {folder_uid} manually in Grafana.")
                report(
                    "Complete SSO and import each prepared file in Grafana. Datasource selection and server overwrite are confirmed there."
                )
                try:
                    webbrowser.open(browser_url)
                except webbrowser.Error:
                    report("Browser could not be opened; use the URL above.")
                report("Prepared; manual import pending. No remote installation was verified.")
                raise typer.Exit(3)
            catalog_path = None
            if attach:
                from .component_sources import resolve_component_sources_file

                catalog_path = resolve_component_sources_file(explicit=component_sources)
            discovered_identity = None
            discovered_source = None
            discovery_session = None
            discovered_ownership = None
            warned: set[str] = set()
            folder_source = ctx.get_parameter_source("folder_uid")
            folder_explicit = folder_source is not None and folder_source.name != "DEFAULT"

            def check_destination(
                current_client: GrafanaClient,
                current_session: ClusterSession | None,
                resources: dict[str, dict[str, Any] | None],
            ) -> dict[str, DashboardOwnership]:
                ownership = {
                    uid: current_client.admit_update(resource, overwrite=overwrite)
                    for uid, resource in resources.items()
                }
                if current_session is not None:
                    assert_import_ownership(
                        to_plain_data(current_session.config),
                        current_session.target,
                        ownership,
                        identity=current_session.identity,
                        namespace=current_client.namespace,
                    )
                for uid, owner in ownership.items():
                    if owner.managed and uid not in warned:
                        report(
                            f"Warning: {uid} remains owned by file provisioning, which may replace "
                            "this update. Saved cluster copies are for manual reuse and are not replayed during deploy."
                        )
                        warned.add(uid)
                return ownership

            if not url:
                config = (config or Path("config.yaml")).expanduser().absolute()
                with progress.paused(resume=False):
                    target = selected_target(config, target)
                # Recover only a previously committed local publication before discovery.
                recover_import_publication(config, target, catalog_path)
                discovery_client, discovery_session = discovery_stack.enter_context(
                    destination(
                        config=config,
                        target=target,
                        url="",
                        token=token_env,
                        mutating=False,
                        progress=progress,
                    )
                )
                resources = check_import_ownership(
                    discovery_client,
                    dashboards,
                    progress=progress.update,
                    overwrite=overwrite,
                    folder=folder_uid,
                    folder_explicit=folder_explicit,
                    attach=attach,
                )
                discovered_ownership = check_destination(
                    discovery_client, discovery_session, resources
                )
                progress.update("Loading available datasources")
                inventory = discovery_client.datasources()
                while True:
                    try:
                        for dashboard in dashboards:
                            map_datasources(dashboard, mappings, inventory)
                        break
                    except DatasourceMappingRequired as exc:
                        if not interactive():
                            raise
                        with progress.paused(resume=False):
                            selected = _select_datasource(exc)
                        mappings.update(parse_mappings([f"{exc.source}={selected}"]))
                        report(f"Datasource mapping: {exc.source} → {selected}")
                if discovery_session is not None:
                    discovered_identity = dict(discovery_session.identity)
                    discovered_source = discovery_session.source[0]
            with destination(
                config=config,
                target=target,
                url=url,
                token=token_env,
                mutating=True,
                catalog=catalog_path,
                progress=progress,
                discovery=discovery_session,
            ) as (
                client,
                session,
            ):
                if session and (
                    session.identity != discovered_identity
                    or session.source[0] != discovered_source
                ):
                    raise GrafanaError(
                        "Grafana destination or configuration changed after discovery; rerun the import"
                    )

                def locked_preflight(resources: dict[str, dict[str, Any] | None]) -> None:
                    nonlocal discovered_ownership
                    ownership = check_destination(client, session, resources)
                    if discovered_ownership is None:
                        discovered_ownership = ownership

                while True:
                    try:
                        plans = prepare_imports(
                            client,
                            dashboards,
                            folder=folder_uid,
                            mappings=mappings,
                            overwrite=overwrite,
                            progress=progress.update,
                            folder_explicit=folder_explicit,
                            attach=attach,
                            expected_ownership=discovered_ownership,
                            preflight=locked_preflight,
                        )
                        break
                    except DatasourceMappingRequired as exc:
                        if session is not None:
                            raise GrafanaError(
                                "Datasource availability changed after selection; rerun the import"
                            ) from None
                        if not interactive():
                            raise
                        progress.update("Waiting for datasource selection")
                        with progress.paused(resume=False):
                            selected = _select_datasource(exc)
                        mappings.update(parse_mappings([f"{exc.source}={selected}"]))
                        report(f"Datasource mapping: {exc.source} → {selected}")
                attachment = None
                project = None
                if attach:
                    assert catalog_path is not None
                    progress.update("Preparing catalog attachment")
                    attachment = prepare_attachment(
                        catalog_path,
                        plans,
                        folder_title=client.folder_title(folder_uid),
                        overwrite=overwrite,
                        org_id=client.org_id or 1,
                    )
                if session:
                    progress.update("Saving project configuration and dashboard files")
                    project = publish_import(
                        root=session.paths.project_dir,
                        config_path=session.paths.config_path,
                        target=session.target,
                        plans=plans,
                        identity=session.identity,
                        namespace=client.namespace,
                        overwrite=overwrite,
                        catalog_keys=attachment.keys if attachment else None,
                        assert_authority=session.fence,
                        expected_config=session.source[0],
                    )
                    session.accept_publication(project.config_postimage)
                    report(
                        f"Saved: {len(plans)} dashboard(s) in the project configuration and JSON files"
                    )
                results = execute_imports(
                    client,
                    plans,
                    checkpoint=project.checkpoint if project else lambda *_: None,
                    emit=report,
                    progress=progress.update,
                )
                require_complete(results)
                if attachment and session:
                    try:
                        progress.update("Publishing catalog attachment")
                        attachment.publish(session.fence)
                    except Exception:
                        raise GrafanaError(
                            "Dashboards installed; catalog attachment remains pending. Rerun the same command."
                        ) from None
                    report(f"Attached: {len(plans)} dashboard(s) to the catalog")
            discovery_stack.close()
            progress.close()
            report(f"Verified: {len(plans)} dashboard(s) available in Grafana")
            if session and any(not plan.ownership.managed for plan in plans):
                report(
                    "Project copies support recovery. With ephemeral Grafana storage, rerun import after database loss."
                )
        except typer.Exit:
            raise
        except (ValueError, RuntimeError, OSError) as exc:
            report(f"Error: {exc}")
            raise typer.Exit(1) from None


@app.command(
    "export",
    epilog=(
        "Examples: nebius-cxcli grafana export --config ./config.yaml "
        "--target CLUSTER_TARGET --output-dir ./exported  |  "
        "nebius-cxcli grafana export --url https://grafana.example.com/ "
        "--token-env GRAFANA_TOKEN --dashboard-uid gpu-overview --output-dir ./exported"
    ),
)
def export_command(
    ctx: typer.Context,
    config: Config = None,
    target: Target = "",
    url: Url = "",
    token_env: Token = "",
    folder_uid: Folder = "",
    dashboard_uid: Annotated[
        list[str] | None,
        typer.Option(
            "--dashboard-uid", help="Export selected UID; repeat for multiple dashboards."
        ),
    ] = None,
    output_dir: Output = None,
    overwrite: Overwrite = False,
) -> None:
    """Download portable JSON; without filters export all accessible dashboards."""
    try:
        destination_options(ctx, config=config, target=target, url=url, token=token_env)
        with destination(
            config=config, target=target, url=url, token=token_env, mutating=False
        ) as (client, _):
            client.folder_title(folder_uid)
            resources = []
            if dashboard_uid:
                if len(dashboard_uid) != len(set(dashboard_uid)):
                    raise ValueError("Duplicate --dashboard-uid")
                for uid in dashboard_uid:
                    resource = client.get(uid)
                    if resource is None:
                        raise ValueError(f"Dashboard not found: {uid}")
                    resources.append(resource)
            else:
                resources = list(client.dashboards())
            dashboards = [
                client.portable(item)
                for item in resources
                if not folder_uid or client.folder(item) == folder_uid
            ]
            if not dashboards:
                raise ValueError("No dashboards match the export selection")
            for path in write_outputs(
                dashboards, output_dir or Path("dashboards"), overwrite=overwrite
            ):
                emit(f"Exported: {path}")
    except (ValueError, RuntimeError, OSError) as exc:
        emit(f"Error: {exc}")
        raise typer.Exit(1) from None


@app.command(
    "validate",
    epilog=(
        "Examples: nebius-cxcli grafana validate ./dashboards --recursive  |  "
        "nebius-cxcli grafana validate --config ./config.yaml --target CLUSTER_TARGET  |  "
        "nebius-cxcli grafana validate ./dashboards --url https://grafana.example.com/ "
        "--token-env GRAFANA_TOKEN --datasource-map DS_METRICS=prometheus"
    ),
)
def validate_command(
    ctx: typer.Context,
    paths: Annotated[
        list[Path] | None,
        typer.Argument(
            metavar="[PATH...]", help="Local JSON inputs; omit for configured dashboard checks."
        ),
    ] = None,
    config: Config = None,
    target: Target = "",
    url: Url = "",
    token_env: Token = "",
    recursive: Recursive = False,
    datasource_map: Annotated[
        list[str] | None,
        typer.Option(
            "--datasource-map",
            help="Map SOURCE=UID for validation; repeat for distinct datasources.",
        ),
    ] = None,
) -> None:
    """Validate JSON offline, or datasource bindings against an explicit destination."""
    try:
        destination_options(ctx, config=config, target=target, url=url, token=token_env)
        dashboards = load_dashboards(paths, recursive=recursive) if paths else []
        mappings = parse_mappings(datasource_map or [])
        if dashboards and config is None and not target and not url:
            for dashboard in dashboards:
                map_datasources(dashboard, mappings)
                emit(f"Valid JSON: {dashboard['uid']}")
            return
        if recursive and not paths:
            raise ValueError("--recursive requires dashboard paths")
        if url and not paths:
            raise ValueError("External validation requires dashboard paths")
        with destination(
            config=config, target=target, url=url, token=token_env, mutating=False
        ) as (client, session):
            if not dashboards and session:
                dashboards = [
                    item
                    for item, _ in declared_dashboards(
                        to_plain_data(session.config), session.paths.project_dir, session.target
                    )
                ]
            available = client.datasources()
            for dashboard in dashboards:
                mapped = map_datasources(dashboard, mappings, available)
                client.canonical(mapped, "")
                emit(f"Valid datasource bindings: {dashboard['uid']}")
            if session and not paths:
                from .grafana_dashboard_validation import validate_grafana_dashboard_fits

                results = validate_grafana_dashboard_fits(
                    session.config, target_ref=session.target, extra_env=session.env
                )
                for result in results:
                    emit(f"{'OK' if result.ok else 'FAILED'}: {result.dashboard_ref}")
                    for error in result.errors:
                        emit(f"  {error}")
                if any(not result.ok for result in results):
                    raise GrafanaError(
                        "Configured dashboard datasource/read-endpoint checks failed"
                    )
                if not dashboards and not results:
                    raise ValueError("No configured dashboards to validate")
    except (ValueError, RuntimeError, OSError) as exc:
        emit(f"Error: {exc}")
        raise typer.Exit(1) from None


@app.command(
    "install",
    epilog=(
        "Examples: Install with saved settings or local defaults: "
        "nebius-cxcli grafana install --config ./config.yaml --target CLUSTER_TARGET | "
        "Replicate metrics and enable batch-result publication: "
        "nebius-cxcli grafana install --config ./config.yaml --target CLUSTER_TARGET "
        "--metrics-storage both --pushgateway --no-interactive"
    ),
)
def install_command(
    config: Annotated[
        Path,
        typer.Option(
            "--config",
            dir_okay=False,
            help="Required managed project config.yaml; settings are saved here.",
        ),
    ],
    target: Annotated[
        str,
        typer.Option("--target", help="Required exact managed cluster target from config.yaml."),
    ],
    metrics_storage: Annotated[
        str | None, typer.Option(help="local, remote or both; default local.")
    ] = None,
    logs_storage: Annotated[
        str | None, typer.Option(help="local, remote or both; default local.")
    ] = None,
    traces_storage: Annotated[
        str | None, typer.Option(help="local, remote or both; default local.")
    ] = None,
    metrics_remote: Annotated[
        str | None,
        typer.Option(
            metavar="URL", help="Metrics WRITE URL; default Nebius. Does not enable export."
        ),
    ] = None,
    logs_remote: Annotated[
        str | None,
        typer.Option(metavar="URL", help="Logs WRITE URL; default Nebius. Does not enable export."),
    ] = None,
    traces_remote: Annotated[
        str | None,
        typer.Option(
            metavar="URL", help="Traces WRITE URL; default Nebius. Does not enable export."
        ),
    ] = None,
    logs_remote_protocol: Annotated[
        str | None, typer.Option(help="otlp-grpc (default) or otlp-http.")
    ] = None,
    traces_remote_protocol: Annotated[
        str | None, typer.Option(help="otlp-grpc (default) or otlp-http.")
    ] = None,
    datasource: Annotated[
        list[str] | None,
        typer.Option(
            "--datasource",
            click_type=click.Tuple([str, str, str]),
            help="Repeatable NAME TYPE READ_URL override/addition; standard datasources are automatic.",
        ),
    ] = None,
    default_datasource: Annotated[
        str | None,
        typer.Option(
            help="Default query connection name; automatic metrics default unless selected."
        ),
    ] = None,
    pushgateway: Annotated[
        bool | None,
        typer.Option(
            "--pushgateway/--no-pushgateway",
            help="Optional job-result bridge; disabled on first install.",
        ),
    ] = None,
    interactive_mode: Annotated[
        bool | None,
        typer.Option(
            "--interactive/--no-interactive",
            help="Open guided storage/datasource setup; defaults to terminal detection.",
        ),
    ] = None,
) -> None:
    """Configure automatic datasources, save settings, render, install and verify Grafana.

    Setup resolves backends only for the selected target. After confirmation,
    render replaces generated artifacts and normal deploy applies pending project changes.
    """
    from .grafana_install import install

    overrides = {
        "metrics_storage": metrics_storage,
        "logs_storage": logs_storage,
        "traces_storage": traces_storage,
        "metrics_remote": metrics_remote,
        "logs_remote": logs_remote,
        "traces_remote": traces_remote,
        "logs_remote_protocol": logs_remote_protocol,
        "traces_remote_protocol": traces_remote_protocol,
        "default_datasource": default_datasource,
        "pushgateway": pushgateway,
    }
    try:
        install(
            config,
            target,
            overrides=overrides,
            datasources=datasource or (),
            interactive=interactive() if interactive_mode is None else interactive_mode,
            emit=emit,
        )
    except (ValueError, RuntimeError, OSError) as exc:
        emit(str(exc))
        raise typer.Exit(1) from exc
