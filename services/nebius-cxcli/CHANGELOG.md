# Changelog

All notable changes to this project are tracked here. This changelog follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
[Semantic Versioning](https://semver.org/).

## [Unreleased]

- Fixed the repo Ruff gate so `make lint` and the `nebius-cxcli-ci` workflow now pass again: `cli.py` binds deferred module prompt expansion to the current component loop state, and runtime alias validation uses the simplified single-guard jump-host check expected by Ruff.
- Added optional `wizard_fields` catalog metadata as a progressive-enhancement layer rather than a required mapping contract: generic Terraform modules and Helm charts still work from introspection alone, convention-friendly fields keep inferred dynamic UX, and bundled MK8s now includes one explicit `gpu_nodes_platform` example for ambiguous advanced integration.
- Clarified the architecture docs to explain why `config.yaml` stays the operator contract while Terraform modules and Helm charts are the provisioning contracts, why the Nebius SDK is used as the dynamic integration layer instead of the primary infra reconciler, and why Terraform output aliases plus `handoff` aliases must be treated as a versioned interface once the CLI/runtime consume them.
- Fixed MK8s wizard field prompting so source-defined literal defaults such as `inputs.cpu_nodes_count: 2` remain editable, GPU-prefixed fields stay hidden until `gpu_enabled=true`, and optional provider-backed fields can now be left blank without falling into an invalid-value re-prompt loop.
- Made MK8s cluster handoff access dynamic instead of hardcoded: the bundled `mk8s` source now resolves `handoff.access` from `inputs.mk8s_cluster_public_endpoint`, so local `deploy` / `flux apply` / `flux bootstrap` / `destroy` / `flux destroy` select the public or private control-plane endpoint automatically. Private-endpoint runs now fail early with explicit network-reachability guidance instead of a generic later `kubectl` dead end.
- Added generated-bundle destroy paths: new top-level `destroy <generated-dir>` now deletes rendered app resources first and then runs Terraform destroy, continuing with infra teardown even when the rendered app delete step fails, and new `terraform destroy` / `flux destroy` commands expose the same destructive workflow in infra-only and apps-only form with explicit confirmation or `--yes`.
- Stopped `destroy` and `flux destroy` from updating `~/.kube/config`; they now use only a temporary kubeconfig for cluster handoff during rendered app teardown, while `deploy`, `flux apply`, and `flux bootstrap` keep the persistent local kubeconfig update behavior.
- Added regression coverage proving `publish-release.sh --prep` remains
  idempotent for unreleased versions: reruns for the same version now stay
  no-op once `Unreleased` is empty and the tag has not been created.
- Changed `publish-release.sh --prep` to fail before editing `CHANGELOG.md` if
  the target tag already exists locally or on `origin`, so duplicate release
  preparation for an already-published version stops immediately.
- Fixed source-checkout runtime version fallback for local release tagging when
  `setuptools-scm` is not installed: `nebius-cxcli.__version__` now derives
  from `git describe` before consulting a generated `_version.py`, so
  `publish-release.sh --publish` no longer rejects a fresh exact tag because of
  a stale local dev-version cache.
- Updated the repo CI and release workflows so they now run
  `validate-sources component_sources.yaml` after `make all`, ensuring the real
  portable component catalog, Terraform modules, and Helm chart sources are
  validated in automation instead of relying only on unit tests.
- Hardened `publish-release.sh` so `--prep` now requires a strictly clean worktree, including untracked files, and first-time pushes from a new local release branch automatically set `origin/<branch>` as upstream instead of failing with Git's "no upstream branch" error; `--publish` now fails before tagging if the target changelog section is missing or empty.
- Made `render` transactional: rerenders now build the replacement bundle under a hidden sibling staging directory and swap it into `generated/` only after the new Terraform/Flux/inventory bundle plus generated manifest are complete, so failed rerenders leave the current bundle intact.
- Clarified docs/help that rerender is now a transactional replace action rather than an eager reset, and documented the Flux-safe workflow: rerender locally, then commit/push one final watched-path snapshot instead of unbootstrapping Flux or publishing intermediate manifest-deletion commits.
- Clarified the `deploy` command contract so help/docs now explicitly say it is the local direct-apply path and does not run `flux bootstrap`; added workflow coverage that generated customer apply jobs use `flux bootstrap` rather than `deploy`.
- Removed the last render-time `generated/flux/flux-system` preservation path. `render` now fully resets `generated/` and deletes any stale legacy Flux bootstrap subtree instead of carrying it forward.
- Reworked email delivery to be disabled by default and operator-local: `nebius-cxcli email --setup` now manages `~/.config/nebius-cxcli/email.yaml`, `bootstrap-ci` syncs non-secret SMTP fields into GitHub Environment variables plus credentials into GitHub Environment secrets, and per-client send/no-send is now controlled by `client_info.notifications.email_enabled` in `config.yaml`.
- Tightened `email <generated-dir>` so it sends only the rendered `inventory.md`, fails fast when that file is missing, and masks tenant/project identifiers in the email subject/body down to their last 4 characters.
- Changed the email contract so generated workflows always run the email step after apply and use `client_info.notifications.email_enabled` as the single send/no-send switch; when email is enabled but SMTP is not configured, the command now warns and continues instead of failing the deploy.
- Changed `bootstrap-ci` to reconcile GitHub SMTP settings from local `email --setup` on every run, including removal of stale `SMTP_*` environment variables/secrets when local SMTP is disabled; `--no-auth-bootstrap` now skips only Nebius CI auth bootstrap.
- Fixed `validate-sources` to accept an optional positional catalog path such as `nebius-cxcli validate-sources component_sources.yaml`, instead of requiring only the global `--component-sources-file` override.
- Split runtime and generated validation into explicit visible phases so long-running `validate` and `validate-generated` calls no longer go silent, and optimized portable validation to reuse resolvable local module metadata when available instead of probing every remote module source during catalog load.
- Clarified root CLI help/docs that `--source-profile` defaults to `portable`; local mode remains the explicit workstation override rather than the implicit test/CI path.
- Clarified `--help` target contracts so the first help screen now tells operators whether each command expects a deployments root directory, `config.yaml`, `generated/`, or an optional `component_sources.yaml` path.
- Clarified `discover` help/docs so they match the implementation: the command accepts the deployments root or any narrower directory under it, including one instance directory or `generated/`, and added CLI coverage for that scoped invocation.
- Fixed scoped `discover` resolution so `--all` and changed-only mode both work from narrower instance directories such as `generated/`, instead of only behaving correctly from the deployments root.
- Clarified top-level and `auth --help` command contracts so `validate-generated` is listed with the generated-bundle commands, `auth` is called out as a no-positional-path command, and `auth --validate-profile` now explicitly documents its all-cached-profiles mode when no project/config target is provided.
- Tightened repo-level Dependabot policy so `.github/dependabot.yml` remains responsible for creating GitHub Actions update PRs, while `.github/workflows/dependabot-auto-merge.yml` is the separate gate for auto-approval and auto-merge of eligible workflow-only GitHub Actions updates, including majors, using the dedicated `dependabot-automerge` environment credential.
- Replaced `azure/setup-kubectl` in generated customer workflows with a direct upstream `kubectl` install step, avoiding the GitHub Actions Node 20 deprecation path.
- Switched render-time Terraform lockfile generation to backend-disabled `terraform init -backend=false` and now remove transient `.terraform/` workdir state afterward, so canonical generated bundles no longer retain local Terraform runtime residue from render.
- Simplified generated customer workflows to rely on the generated-bundle CLI commands for `terraform.auto.tfvars.json` recreation instead of carrying a duplicate inline restore script, and now reconcile the deployments-root `.gitignore` during `bootstrap-ci` as well.
- Removed the unused generated inventory JSON sidecars (`infra.json`, `apps.json`, `mk8s.json`, `postgresql.json`, `sfs.json`); the generated inventory contract is now `inventory.md` only, and refreshes delete any stale legacy inventory JSON files.
- Fixed generated `inventory.md` spacing so section headers and lists remain markdownlint-safe, and clarified in docs that email recipients still come from `client_info.notifications.email` in the generated manifest/runtime config.
- Replaced the split `component_sources.yaml` and `component_sources.release.yaml` model with a single dual-source `component_sources.yaml` schema using required `portable_source` plus optional `local_source` per Terraform module.
- Replaced command-local `--render-profile` with the global `--source-profile {portable|local}` override and added `NEBIUS_CXCLI_COMPONENT_SOURCES_PROFILE` for workstation-vs-portable source selection across config-based commands.
- Aligned wheel/release packaging and repo workflows with the single-catalog contract, and hardened release-catalog verification so published portable catalogs reject local filesystem `portable_source` entries.
- Removed recently redundant compatibility branches: generated manifests now require `render.module_sources`, the duplicate manifest `render.portable` flag is gone, app release-name aliases are no longer accepted, and seeded infra project defaults now only honor canonical `parent_id` / `project_id` input keys.

## [nebius-cxcli-v0.1.8] - 2026-03-23

- Fixed the `nebius-cxcli` CI and release workflows to run `nebius_cxcli.release_catalog` checks with the repo `.venv/bin/python` created by `make all`, avoiding bare-runner Python import failures under GitHub Actions.
- Hardened `tests/test_setup_build.py` against ambient GitHub Actions build env leakage so setup/build source-selection and release-ref rewrite tests stay deterministic in CI.

## [nebius-cxcli-v0.1.7] - 2026-03-23

- Removed the standalone `nebius` CLI dependency from MK8s kubeconfig handoff and token retrieval; `deploy`, `flux apply`, `flux bootstrap`, and generated customer workflows now use Nebius SDK-backed exec kubeconfig entries through `nebius-cxcli` itself.
- Generated customer workflows no longer install the standalone `nebius` CLI before Flux bootstrap.
- Aligned the main `nebius-cxcli` CI and release workflows to run the same local `make all` verification contract before wheel verification and release publication.
- Aligned CLI help/doc wording for auth profile/config flags and MK8s handoff behavior with the SDK-based contract.
- Tightened `bootstrap-ci` help/docs so the command and flag contract explicitly matches runtime behavior: target `config.yaml` must already be inside the customer git repo, `--github-repo` is only an auth-bootstrap override, and `--github-token-env` only affects GitHub bootstrap/secrets sync.
- Clarified in help/docs that `--cli-ref` selects the `nebius-cxcli` source ref used by the generated customer workflow, not the branch of the customer target repo; kept the option display aligned with Typer's default `TEXT` metavar.
- Fixed runtime version resolution for source/editable checkouts so `nebius-cxcli` now prefers live `setuptools-scm` git state over a generated `_version.py` cache, and `publish-release.sh --publish` now verifies local runtime version/tag alignment before pushing the release tag.
- Clarified MK8s node-readiness behavior before Flux work: `deploy`, `flux apply`, and `flux bootstrap` now probe first and only announce a wait when nodes are actually not `Ready` yet.
- Kept the local Flux phase under one continuous spinner after MK8s handoff so `deploy`/`flux apply` no longer stop and restart the spinner between cluster reachability, Flux API discovery, manifest apply, and rendered-resource readiness checks.
- Added a non-interactive fallback for those Flux phase updates so GitHub Actions and other non-TTY logs get stable printed phase lines instead of relying on transient spinner frames.

## [nebius-cxcli-v0.1.6] - 2026-03-23

- Simplified `bootstrap-ci` so reruns automatically reconcile the CLI-managed customer workflow to the latest generated contract; `--auth-bootstrap` remains enabled by default and workflow-only runs are now the explicit opt-out via `--no-auth-bootstrap`.
- Added regression coverage that `bootstrap-ci --help` and the command surface keep `--auth-bootstrap` enabled by default.
- Fixed customer-side Terraform plan/apply flows for private repos by persisting rendered tfvars in the generated manifest and recreating ignored `generated/infra/terraform.auto.tfvars.json` from that manifest before Terraform runs, both in CLI-generated bundle commands and generated customer workflows.
- Clarified and tested that `deploy <generated-dir>` remains a local/customer-side bundle operation only and does not auto-run `bootstrap-ci` or mutate GitHub CI workflow/environment state.

## [nebius-cxcli-v0.1.5] - 2026-03-22

- Added PR-side coverage for `bootstrap-ci` workflow generation across both development (`main`) and stable tagged (`nebius-cxcli-v<version>`) default CLI refs.
- Hardened `bootstrap-ci` to fail before writing the customer workflow when GitHub auth-bootstrap prerequisites are missing, and documented `--github-repo` as an override over target-repo auto-detection.
- Added explicit render profiles: generator-side `validate` and `render` now default to portable output, while `--render-profile local-dev` keeps checked-out Terraform module paths for workstation testing.
- Hardened generated-bundle validation and customer workflows with `validate-generated --portable`, so PR/apply pipelines reject non-portable local Terraform module sources before plan/apply.
- Simplified wheel/release packaging to bundle the portable catalog via the build override path instead of rewriting the working-tree root catalog during GitHub Actions builds.
- Aligned the generated customer workflow with the example repo by using a shared Python-version env and compact JSON discovery output for deterministic GitHub Actions matrix handoff.
- Added repo-side coverage that the checked-in local and portable catalogs stay semantically aligned except for Terraform module source addresses.
- Added direct tests for the `validate-sources` CLI command surface and GitHub environment-secret bootstrap helpers so those paths no longer rely only on indirect coverage.

## [nebius-cxcli-v0.1.4] - 2026-03-22

- Fixed packaged/bundled `component_sources.yaml` to always use the portable Git-backed catalog so source installs and customer CI no longer fall back to repo-local Terraform module paths.
- Added `bootstrap-ci --cli-ref` so generated customer workflows can be pinned explicitly to a branch, tag, or commit when validating nebius-cxcli changes end to end.
- Stabilized Flux bootstrap fallback coverage so tests no longer depend on live local `kubectl` state when asserting the bootstrap path.

## [nebius-cxcli-v0.1.3] - 2026-03-21

- Hardened release publishing so tagged wheels use the exact tag version and verify bundled portable component sources through shared release-catalog helpers.
- Limited release catalog ref rewriting to this monorepo's module sources and now fail release validation when external module sources are left on floating refs or local paths.
- Added PR-side coverage for release catalog rendering and wheel verification so release packaging errors are caught before tagging.
- Fixed `publish-release.sh --prep` changelog rewriting so moved release notes preserve Markdownlint-safe blank lines around lists and headings.

## [nebius-cxcli-v0.1.2] - 2026-03-20

- Prepare release `v0.1.2`.

## [nebius-cxcli-v0.1.1] - 2026-03-20

- Split the workflow model into generator-side commands for `config.yaml` and customer-side commands for deploying the rendered `generated/` artifacts.
- Added generated bundle manifests, stricter render reset guardrails, and customer-side validation for portable deployment bundles.
- Hardened local deploy and Flux apply/bootstrap flows with better readiness checks, clearer status output, and safer Flux recovery behavior.
- Aligned release packaging and GitHub workflows so published wheels bundle the rewritten portable release catalog instead of local development sources.

## [nebius-cxcli-v0.1.0] - 2026-02-22

- Initial scaffold for `nebius-cxcli`.
- Added `config.yaml` schema validation and deterministic renderers.
- Added Terraform, Flux, discover, inventory, and email commands.
