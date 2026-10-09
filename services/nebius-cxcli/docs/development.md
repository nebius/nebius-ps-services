# Development and releases

[Back to the README](../README.md#development)

Normal users run the installed `nebius-cxcli` directly. Contributors use Make
and uv to keep the checkout environment consistent with `uv.lock`.

## Environment

Required tools are Python 3.12–3.14, Git, Make, and uv `0.12.9` or a compatible
`0.12.x` release. A native toolchain may be needed when Python wheels are
unavailable. Install uv through its
[official guide](https://docs.astral.sh/uv/getting-started/installation/).
Command-specific tools remain listed in the [README](../README.md#credentials-and-tools).

From `services/nebius-cxcli`:

```bash
make lock-check
make env
```

`make lock-check` rejects a missing or stale lock without rewriting it.
`make env` exactly synchronizes the editable project and default development
group; undeclared packages in that environment are removed.

`VENV=/safe/path` selects another environment and maps to
`UV_PROJECT_ENVIRONMENT`. Make rejects whitespace, symlinks, unsafe paths, and
unrecognized directories. Synchronization is serialized per environment and
uses the selected `PYTHON` without automatic Python downloads.

### Why development uses uv run

`uv run` selects the project environment and normally checks its lock and
installed dependencies. It is a development launcher, not part of nebius-cxcli
command syntax. [Official uv documentation](https://docs.astral.sh/uv/guides/projects/#running-commands)

Make owns synchronization. After `make env`, focused checks use locked,
non-syncing execution:

```bash
uv run --locked --no-sync --no-python-downloads nebius-cxcli --help
uv run --locked --no-sync --no-python-downloads ruff check tests/test_docs_alignment.py
NEBIUS_IAM_TOKEN=offline-test-token \
  uv run --locked --no-sync --no-python-downloads pytest -q -p no:cacheprovider \
  tests/test_cli_contract.py tests/test_docs_alignment.py \
  tests/test_docs_restoration.py tests/test_soperator_docs_assets.py
```

The inert token is a test fixture value preventing interactive credential
lookup; the offline network guard remains active. Do not substitute a real
credential for offline tests.

## Quality gates

The CLI composition root supplies dependencies to command modules; those modules
must not import `cli.py`. The lightweight `mk8s-token` app receives its credential
provider from the package entrypoint. Only a cache miss loads the full provider,
while the full CLI binds the same command factory to its existing provider.
Keep the architecture import check and both cold/warm credential tests together
when changing this wiring.

| Target | Purpose |
| --- | --- |
| `make lint` | Ruff checks |
| `make format-check` | Reject new formatting debt |
| `make typecheck` | Enforce package typing-debt ratchet |
| `make test-unit` | Full offline suite, excluding integration-marked tests |
| `make test-integration` | Explicit integration lane; some cases require extra opt-in |
| `make coverage-check` | Global/module branch-coverage non-regression floors |
| `make ci-quality` | Lint, baseline integrity, architecture, format/type ratchets, coverage, offline tests, and diff hygiene |
| `make verify-wheel-cli` | Build and verify the isolated installed CLI artifact |
| `make all` | Shared environment followed by quality and wheel checks |

Use narrow checks during iteration. Baseline files under `scripts/` record
existing debt and must only get stricter; passing does not mean type/format debt
is eliminated. `make test` and `make coverage` are existing aliases of unit and
coverage checks.

Python-backed targets enter the shared environment boundary. `make all`
overlaps quality work with an isolated PEP 517 wheel build after setup. Builds
use hashed build constraints from the lock. Wheel verification installs the
exact artifact into a temporary environment with locked runtime dependencies;
source imports alone do not prove installed-wheel behavior.

The public CLI contract covers groups/leaves, arguments, flags, defaults,
visibility, and selected help clauses. Examples parse with callbacks disabled;
separate tests check version and callback reachability. Regenerate
`tests/fixtures/cli_contract.json` only for intentional CLI metadata changes.
`scripts/generate_cli_contract.py` writes that fixture and has no check-only
mode; documentation-only reorganization keeps it unchanged.

README tests derive command-index completeness from the registered tree and
parse examples across operator guides without executing product callbacks.
Link/anchor and SVG checks cover navigation and diagram semantics. Run Markdown
lint on edited files and `git diff --check` before completion.

## Integration and local product fixtures

The optional official Soperator release sweep needs explicit representatives:

```bash
NEBIUS_CXCLI_TEST_OFFICIAL_SOPERATOR_RELEASES=latest,1.22.0,3.0.4,4.0.5,4.1.7 \
  make test-integration
```

It fetches public artifacts without deploying a customer cluster. Unit fixtures
stub release acquisition at its boundary while keeping validation, admission
hashes, and network blocking active.

`scripts/verify_grafana_persistence.py` uses disposable Docker services to test
dashboard/session persistence across replicas and database restarts.
`scripts/verify_soperator_native_retirement.py` creates/removes its own kind
cluster and needs Docker, kind, kubectl, and Helm. These explicitly invoked
fixtures are not prerequisites for a documentation-only change.

Keep source, installed-wheel, disposable-controller, CI, and live deployment
evidence separate. Local results do not certify a customer environment.

## Releases

`CHANGELOG.md` contains release summaries. Large releases link to their complete
history under `docs/releases/`; documentation checks read both surfaces. Release
preparation may leave `[Unreleased]` empty after moving its notes into a version.

1. From a clean feature branch, run `./publish-release.sh --prep X.Y.Z`.
2. Review and merge the branch to `main` through the repository workflow.
3. From clean synced `main`, run `./publish-release.sh --publish X.Y.Z`.

Prep updates the changelog and publishes the prep branch, setting its upstream
when needed. It requires a strictly clean worktree including untracked files,
and rejects an existing local/remote tag before editing. Repeated prep of the
same unpublished version is idempotent once `[Unreleased]` is empty. Publish
requires the version's nonempty changelog section and checks the source version
before pushing the annotated `nebius-cxcli-vX.Y.Z` tag.

The tag triggers release CI, retaining full main history for ancestry/parent
checks. It runs quality/wheel gates, validates portable sources and wheel
version/catalog, then publishes the GitHub Release. Assets include the wheel
and editable catalog with Terraform refs pinned to the release tag.

Branch CI validates local sources against the checkout. Post-gate checks use
the synchronized environment. Editable runtime versions come from live SCM
rather than treating a stale generated version cache as release authority.

## Extending runtime behavior

- `NEBIUS_CXCLI_RUNTIME_VALIDATION_PLUGINS` adds comma-separated
  `module.path:function` rule packs; bundled validation stays active.
- `NEBIUS_CXCLI_PROVIDER_OPTION_PLUGINS` adds provider-option lookups.
- `NEBIUS_CXCLI_STRICT_PROVIDER_OPTION_CHECKS=1` enables live option-membership
  checks during deployment-readiness validation.

Provider discovery prefers supported operator credentials. Explicit
`CXCLI_NEBIUS_DELEGATE_ID` selects delegated identity without falling back to
the base identity. Preserve these boundaries and keep secrets out of examples.
