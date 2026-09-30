# Project workflows

[Back to the command guide](../README.md)

Examples assume an installed CLI, the intended operator identity, and these
paths adjusted to your project:

```bash
CONFIG=./deployments/tenant/project/config.yaml
GENERATED="$(dirname "$CONFIG")/generated"
```

## Authentication

Project-aware commands ensure the canonical `nebius-cxcli-sa` service account
with project `admin` permissions. First-time setup and confirmed IAM-drift
recovery require operator IAM authority. Healthy runtime credentials are reused;
normal commands do not rotate healthy keys or create another Nebius CLI profile.
Missing operator permission fails with recovery guidance.

Supported operator profiles/environments bootstrap authentication. Interactive
profile refresh can use the existing Nebius browser-login flow; automation
needs an explicit supported credential source. Canonical project caches live
under `~/.config/nebius-cxcli/projects/PROJECT_ID-DIGEST/` with owner-only
permissions. Do not copy private keys or metadata into project files/reports.

```bash
nebius-cxcli auth --project-config "$CONFIG"
nebius-cxcli auth --validate-profile
```

The first command ensures one project's identity. The second validates caches;
without a target it checks all canonical project profiles. `auth --recreate`
explicitly rotates an authorized key. Normal setup can recover a confirmed
missing key; provider failures do not trigger arbitrary credential replacement.

`auth --bootstrap-ci` ensures and syncs credentials to the selected GitHub
Environment. Clean runners validate project bindings and import supplied keys;
a missing runner cache does not authorize cloud IAM creation. `bootstrap-ci`
is the full workflow setup command.

Planning/validation commands can initialize runtime files, authenticate,
reconcile canonical project access where allowed, or prepare a backend.
Distinguish those effects from applying desired infrastructure.

## Create and edit a project

```bash
nebius-cxcli create ./deployments
nebius-cxcli component list --config "$CONFIG"
nebius-cxcli component add infra:vm --config "$CONFIG"
```

Creation resolves tenant/project identity beneath the deployments root. Reusing
an existing project through `create` starts it from scratch after confirmation.
`--force` approves replacement of that folder, including all generated and other
files; it does not mean reconcile. Use component commands or edit config for
day-2 changes. Review live effects of removals before applying a new generation.

Folder names are not identity fallbacks. Collisions with another project fail
before overwrite. One deployments root owns the managed `.gitignore` block for
its projects; nested managed roots are rejected.

## Validate and render

```bash
nebius-cxcli validate "$CONFIG"
nebius-cxcli render "$CONFIG"
nebius-cxcli validate-generated "$GENERATED"
```

`render` takes the config file; bundle commands take `generated/`. A new bundle
is staged completely before replacement. Failed rendering leaves the previous
bundle intact. Replacing populated artifacts prompts interactively and needs
`--force` noninteractively.

Version config and deployable generated artifacts in a private repository.
Respect runtime ignores, especially tfvars, credentials, Terraform state/cache,
and SSH trust files. cxcli reconstructs tfvars from its manifest; raw Terraform
from a fresh clone does not supply that runtime wiring.

Rendering is not a live destroy, but applying the new desired state can replace
or delete resources. For GitOps, push one complete reviewed snapshot, not an
intermediate commit deleting watched manifests during local replacement.

## Deployment and recovery

```bash
nebius-cxcli deploy "$CONFIG"
```

Progress follows **Prepare → Assess changes → Deploy → Verify → Result**.
Deployment validates/applies the generated snapshot, resolves Terraform outputs,
reconciles apps, and runs required checks. It does not rerender edited YAML.

Plain deploy selects all generated targets; `--target TARGET_ID` narrows it and
`--all-targets` spells out the default. Flux commands require one of these
selectors when multiple targets need Kubernetes access.

The selected immutable cluster identity determines the connection. cxcli uses
isolated kubeconfig material and verifies endpoint/CA identity when reusing a
context. Private endpoints need an existing route. Local deploy/bootstrap can
persist exec-based access for later use; CI skips persistence, or disable it
with `NEBIUS_CXCLI_PERSIST_LOCAL_KUBECONFIG=false`. Direct `flux apply` does not
change the local current context.

### Interrupted operations

1. Read the failure and operation state; resolve the reported cause.
2. Preserve the original generated bundle. If changed, restore that exact
   generation; rendering similar YAML is not equivalent.
3. Rerun `deploy` with original targets and execution controls, including skip
   flags and job wait/refresh settings.
4. Let the product finish its pending transition and verify readiness.

Soperator upgrade recovery uses this entry point. Upgrade defaults to
`--job-wait-timeout 0s`, whereas deploy defaults to `1h`; pass the original value
explicitly. Repeating `soperator upgrade` selects new intent. Status identifies
the recovery entry point but cannot reconstruct all original options.

Do not edit receipts, bypass ownership checks, or run a separate printed
Terraform apply while deployment is active. Controllers may continue after the
local process exits. Permanent/unknown errors stop execution; recognized
transient reads have bounded retries.

For protected Soperator changes, deploy asks for job policy in a prompt-capable
terminal. Unattended deploy defaults to `wait-then-cancel` with a `1h` timeout;
choose explicit controls before automation. This differs from Soperator upgrade's
`requeue-hold-all` and no-deadline wait defaults.

### Locking

Terraform owns remote state and native Object Storage locking. cxcli deployment
checkpoints and mutation locks are local; there are no shared S3 deployment
journals or execution leases. Soperator's in-cluster operation fence separately
uses a Kubernetes Lease.

Serialize whole workflows for one backend across machines/repositories.
Terraform locking alone does not serialize Helm, Flux, and SDK effects.
Generated CI uses non-cancelling concurrency; independent runners need shared
scheduling.

For eligible non-Soperator workflows, inspect a suspected stale lock:

```bash
nebius-cxcli terraform unlock "$GENERATED"
```

This requires `aws`, checks active processes and lock ownership, and asks for
guarded confirmation. Do not use it as routine cleanup or bypass active/foreign
locks. Confirmed eligible destroy flows can use the same stale-lock checks and
retry once.

## Terraform and Flux operations

`terraform plan/apply` uses generated inputs and runtime/backend setup.
Dedicated Terraform mutations reject protected Soperator scope; use deploy.
`flux apply` reconciles apps directly and waits for current-generation readiness.
Local deploy installs missing Flux controllers without requiring the Flux CLI.

Ordinary app updates on an accepted Soperator target:

```bash
nebius-cxcli render "$CONFIG" --force
nebius-cxcli flux apply "$GENERATED" --target TARGET_ID
```

Admission checks accepted state, ownership, pending operations, and unchanged
protected configuration/artifacts. It applies ordinary apps without Terraform
or Slurm maintenance; field ownership conflicts stop rather than forcing writes.

For eligible non-Soperator GitOps targets:

```bash
nebius-cxcli flux bootstrap "$GENERATED" --target TARGET_ID
```

Bootstrap resolves the GitHub repository from `GITHUB_REPOSITORY` or Git origin,
separately from the local bundle path. Commit and push the generated Flux path
so continuous reconciliation can reproduce it. Controllers alone do not mean
GitOps is bootstrapped; the required Git objects must also exist. Flux
bootstrap/destroy rejects Soperator targets.

Use [MK8s destruction](mk8s.md#destruction) for whole-cluster retirement.
Terraform destroy is restricted to projects without MK8s; Flux destroy only
removes eligible rendered app resources.

## VM access

Day-2 commands require matching configured/deployed components. After changing
selection, render and deploy first. `--component` selects an instance ID before
a component type; a type is accepted only when one enabled instance matches.

Before privileged helpers run, put independently verified host keys in
`generated/ssh_known_hosts` or use `--ssh-known-hosts-file` for a persistent
alternative. cxcli does not fetch first-seen keys or use machine-global trust.
Render replaces the default file; restore it afterward or keep it outside
`generated/`.

### SSH jump host

`inputs.allowed_cidrs` seeds first-boot SSH reachability. The wizard can offer
the detected operator public IPv4 as a `/32`; otherwise supply it manually.
At least one CIDR is required. Day-2 updates use the VM-local helper:

```bash
nebius-cxcli ssh-jumphost --list-allowed-cidrs "$CONFIG" \
  --component ssh-jumphost@bastion --ssh-known-hosts-file ./known_hosts
nebius-cxcli ssh-jumphost --add-allowed-cidrs "$CONFIG" \
  --component ssh-jumphost@bastion --allowed-cidr 192.0.2.10/32 \
  --ssh-known-hosts-file ./known_hosts
```

Replace the documentation CIDR with the actual intended source. Removal uses
`--remove-allowed-cidrs`. `--ssh-user` and `--ssh-private-key` override configured
user and normal key selection. WireGuard gateways disable SSH forwarding; use a
dedicated jump host for ProxyJump.

### WireGuard

```bash
nebius-cxcli wireguard --gen-client-conf "$CONFIG" \
  --component wireguard-gw@vpn --client-name laptop \
  --ssh-known-hosts-file ./known_hosts
```

The gateway allocates a free tunnel address and returns a file under the
project's ignored `wireguard-clients/` directory. Files contain private keys;
use one per device/user. Names must be lowercase wg-quick-safe interface names,
at most 15 characters. The command prints connection/disconnection commands.

Generation accepts DNS, keepalive, output-directory and overwrite options,
plus repeated `--local-subnet`. Route-default updates instead take one
comma-separated value:

```bash
nebius-cxcli wireguard --add-local-subnets "$CONFIG" \
  --component wireguard-gw@vpn --local-subnet 10.20.0.0/16,10.30.0.0/16 \
  --ssh-known-hosts-file ./known_hosts
```

Removal uses `--remove-local-subnets`. Changes affect future files, not already
distributed clients. Choose non-overlapping private tunnel space before first
deploy; changing it later requires redeployment and new client files. Protect
private keys during storage and distribution.

## CI and reporting

```bash
nebius-cxcli bootstrap-ci "$CONFIG"
```

Bootstrap reconciles workflow, auth, and SMTP settings into the selected GitHub
Environment. Supply `GH_TOKEN`, `GITHUB_TOKEN`, or `--github-token-env` through a
protected runtime environment. Existing deployment restrictions are preserved.
Use `--github-repo` when automatic selection would choose the wrong repository.

The workflow watches config and generated paths, validates config, renders,
validates the bundle, then deploys. `--cli-ref` selects the cxcli source ref,
not the customer's branch. Tags/SHAs give repeatable code; `main` follows its
latest changes. An optional persistent `NEBIUS_CXCLI_REF` repository or
organization variable overrides the generated ref. Local deploy does not
create workflows or synchronize CI secrets.

Reports stay in `generated/reports/`. The deploy report combines inventory,
app/access handoff, and validations; command-specific reports retain their own
scope. Creation/render alone does not produce a live deploy report.

`email --setup` configures the user-global SMTP profile. `email CONFIG_YAML`
sends the existing report using recipient/runtime settings from the generated
manifest. It does not rerender edited config or preview mail. Per-client
`email_enabled` controls delivery. Protect SMTP secrets and verify recipients.
