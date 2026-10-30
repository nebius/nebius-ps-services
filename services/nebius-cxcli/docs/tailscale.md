# Tailscale subnet routers

`tailscale-gw` provisions infrastructure **only on Nebius** and joins your existing
hosted Tailscale tailnet. The default is two persistent Ubuntu 24.04 VMs, named
`<name>-a` and `<name>-b`, each with its own public IP and boot disk. Both advertise
the same selected private prefixes. One member is supported by explicitly setting
`inputs.replicas: 1`; it provides no VM failover.

Use the ordinary lifecycle in two stages. The wizard collects access intent and
credential custody without requesting a secret value:

```bash
nebius-cxcli component add --config ./config.yaml
nebius-cxcli validate ./config.yaml
nebius-cxcli render ./config.yaml
nebius-cxcli deploy ./config.yaml
nebius-cxcli tailscale onboarding request ./config.yaml --out /secure/handoff
# Send the generated request to the tailnet administrator and receive approval.
nebius-cxcli tailscale onboarding configure ./config.yaml
nebius-cxcli render ./config.yaml
nebius-cxcli deploy ./config.yaml
nebius-cxcli tailscale doctor ./config.yaml --json
nebius-cxcli tailscale verify ./config.yaml
```

New gateways start with `tailscale.stage: infrastructure`. This deploy installs
and verifies the VMs, pinned SSH identity, Tailscale package and forwarding, and
creates a linked metadata-only SecretStash container when selected. It does not
read OAuth payloads or contact Tailscale. Reports say `INFRASTRUCTURE_READY` with
`gateway_ready: false`; this is not proof of VPN access.

`onboarding request` is pure and works before deployment without cloud or tailnet
credentials. The completed infrastructure report adds exact resource/container
IDs. It prints proposed tags, routes, separate source/destination/protocol/port
rules, scopes, route auto-approvers, ownership, conditional prerequisites and an
intent fingerprint. Export outside Git. Incomplete requests are marked `DRAFT`.

The continuation wizard reviews approved settings and protected credential
references and records the approval reference/fingerprint. It sets `stage: active`
without enrolling. Ordinary deploy requires a durable previous infrastructure
checkpoint and the exact accepted intent. Changes to access intent invalidate
approval. Credentials and discovered resource IDs do not affect that fingerprint.
Enrollment recovery and route approval enforce the same accepted intent and active
stage as deployment. Lock release and unused-key revocation remain available when
approval is stale.
For automation, provide `--no-interactive --approval-reference ...
--accepted-fingerprint ...` and the corresponding credential-reference options;
prepare all other settings in config. Add `--json` for one result document on
success. Missing or stale approval fails closed.

Select `tailscale-gw` in the infrastructure wizard. Configure the expected tailnet
name, server tags, destination prefixes and SSH management CIDRs. Routes must be
RFC1918 IPv4 prefixes contained in resolved subnets of the selected Nebius VPC;
newly discovered networks are never automatically advertised. The placement subnet
must support public allocations and Internet access. Restrict SSH to your operator
or automation egress addresses. SSH remains an independent management path during
VPN recovery; the CLI uses authenticated Nebius host identity and strict SSH pins.

There is no `tailscale install` or `tailscale deploy`, no separate Terraform root,
and no required MysteryBox or ESO component. WireGuard can coexist; verify
operating-system routes when destinations overlap. Existing WireGuard deployments
are not migrated or removed automatically.

Direct Terraform apply checks existing state as well as enabled configuration.
Removing or disabling a gateway row cannot bypass retirement; keep its configuration
until ordinary destroy retires its keys and devices. Policy plan/apply resolve linked
SecretStash container IDs from Terraform outputs and reject conflicting saved IDs,
while retaining the original configuration for the reviewed intent digest.

## Tailnet prerequisites

Create a tailnet and install its client on each user computer. Your administrator
owns user access, tags, device approval, Tailnet Lock signing, grants/ACLs, route
approval and subscription features. cxcli never changes the tailnet's global
route-selection mode. Two VMs do not establish separate Nebius availability zones
or guarantee a failover duration. Existing TCP sessions can be interrupted during
router failover.

### Administrator handoff: manual prerequisites

Use scoped OAuth for the automated lifecycle. A person with authority to administer
the target tailnet performs the following setup; ordinary membership in a tailnet
or access to Nebius cloud does not grant these permissions.

1. **Approve the gateway identity and access.** Register or approve the exact
   gateway tag, who may own it, the advertised private prefixes, and the users or
   groups and destination ports allowed to use them. Follow your organization's
   existing policy approval process.
2. **Issue the delegated credential.** Create an OAuth client restricted to the
   approved gateway tags and the operations in the scope table below. Provide its
   public client ID and deliver its secret through your approved secret store or
   an owner-only runtime file. Give the operator or automation agent the protected
   reference, never a secret pasted into configuration, chat, or command arguments.
3. **Complete approvals that cannot be delegated.** If device approval, Tailnet
   Lock signatures or external route approval are enforced, approve the exact new
   devices/routes when deployment reports them as pending. This step is unnecessary
   for approvals already covered by the credential or approved automatic policy.

The handoff consists of the exact tailnet name, approved tags/prefixes/access,
OAuth client ID, protected secret reference and the chosen route-approval mode.
Send one consolidated generated request to the tailnet administrator. Include
any required cloud IAM reader/writer permissions for the Nebius resource owner;
these may be different people. A scoped credential alone is not a complete
request. The default scope set is `auth_keys`, `devices:core` and
`devices:routes:read`; the administrator owns policy and exact autoApprovers.
Route-write delegation is opt-in. Device approval or Tailnet Lock may still need
per-device action after enrollment, depending on tailnet policy. Company-specific
registration tools are outside cxcli.

| Work after the handoff | Automated behavior |
| --- | --- |
| Infrastructure and enrollment | First deploy verifies infrastructure; after the approved handoff, deploy generates single-use keys and enrolls through pinned SSH. |
| Route approval | With `tailscale.approval: api` and delegated route permissions, deploy approves the exact owned prefixes; otherwise it reports pending external approval. |
| Access policy | `tailscale policy plan` prepares a bounded proposal; an authorized operator reviews it and explicitly invokes `policy apply` with its digest. |
| Repeat operation and recovery | Deploy preserves identities and resumes interrupted enrollment; exact recovery commands handle the documented exceptional checkpoints. |
| Verification | Deploy runs configured client probes; `tailscale verify` performs explicit checks, with failover testing only when requested. |
| Retirement and cleanup | Ordinary destroy revokes outstanding keys, deletes owned devices and removes infrastructure with independent readback. |

The administrator performs the authority handoff once; cxcli performs the recurring
resource and identity lifecycle. Missing permission remains an actionable prerequisite
and never triggers an attempt to grant itself more access.

For automation, create an OAuth client with the minimum scopes for your operations:

| Operation | Tailscale scopes |
| --- | --- |
| Create tagged single-use enrollment keys | `auth_keys` |
| Inspect device identity and expiry | `devices:core:read` |
| Inspect approved routes | `devices:routes:read` |
| Approve exact owned routes | `devices:routes` |
| Retire devices during replacement/destroy | `devices:core` |
| Plan or apply policy changes | `policy_file:read` or `policy_file` respectively |

Limit OAuth tags to the gateway tags. Route permissions are broader than tag-scoped
key creation, so cxcli additionally checks exact journal-owned node IDs and route
sets before writes. `auth check` proves authentication and readable resources;
write permissions are checked when used. Secrets are read directly from an exact SecretStash reference via the Python SDK,
or supplied through `env:NAME` or `file:PATH` references. Credential files must be regular, owned by the current
user and inaccessible to other users. Relative file references resolve against the
original project's directory, including when deploy uses a temporary execution
copy. Never put secret values in configuration or shell arguments.

Administrator-issued keys are an alternative when automated key issuance is
unavailable. This mode requires a manual key handoff for each member. After infrastructure
setup, configure those references, regenerate the administrator request (the
enrollment mode changes its fingerprint), and run `onboarding configure` before
the activation deploy:

```bash
nebius-cxcli tailscale auth configure ./config.yaml --mode auth-key \
  --member-key-ref a=file:credentials/router-a.key \
  --member-key-id a=ADMIN_KEY_ID_A \
  --member-key-ref b=file:credentials/router-b.key \
  --member-key-id b=ADMIN_KEY_ID_B
```

Create a separate **single-use, non-ephemeral, tagged** key for each member.
Supply its exact API key ID alongside its protected reference; the ID is recorded
before delivery so unused or consumed keys can be reconciled during cleanup.
Do not infer the ID from the opaque secret. With OAuth access, enrollment verifies
the key exists and its capabilities match. Retirement requires API access to
confirm removal of each recorded key independently from its device.
The administrator is responsible for these key capabilities when no API credential
is provided. The CLI authenticates without advertising routes, verifies the exact
expected tailnet and tags, and then sets the configured routes. It never enables an
exit node or Tailscale SSH. Existing node state is reused after retries and reboots.
A lost enrollment response is reconciled from the guest; unresolved enrollment
without a recoverable node identity stops instead of creating duplicate devices.
Device or Tailnet Lock approval can leave deployment **PENDING**. Resolve the
reported prerequisite and run ordinary deploy again. For fully automated device
retirement, configure the OAuth device scopes before ordinary destroy.

## Configuration example

Add this component to an existing cxcli config, using your project, network, subnet,
SSH public key, tailnet and permitted management source:

```yaml
infra:
  components:
    - id: tailscale-gw
      instance_id: private-access
      enabled: true
      inputs:
        parent_id: PROJECT_ID
        network_id: NETWORK_ID
        subnet_id: SUBNET_ID
        name: private-access
        replicas: 2
        platform: cpu-d3
        preset: 4vcpu-16gb
        ssh_user_name: ubuntu
        ssh_public_key: SSH_PUBLIC_KEY
        management_cidrs: [192.0.2.1/32]
      tailscale:
        stage: infrastructure
        onboarding:
          owner: network-team
          purpose: Private service access
          environment: development
        tailnet: YOUR_TAILNET_NAME
        tags: [tag:subnet-router]
        routes: [10.20.0.0/24]
        auth:
          mode: oauth
        approval: external
        policy:
          tag_owners: [group:network-admins]
          rules:
            - sources: [group:developers]
              destinations: [10.20.0.0/24]
              protocol: tcp
              ports: ["443"]
          auto_approve: true
        verification:
          required: false
          timeout_seconds: 15
          targets:
            - url: https://PRIVATE_HTTPS_HOST/readyz
              ca_file: credentials/cluster-ca.crt
              min_bytes: 1
```

The subnet router uses SNAT, IP forwarding, stateful Tailscale netfilter rules,
`accept-routes=false`, and `accept-dns=false`. The security group permits SSH only
from management CIDRs, UDP 41641 for direct Tailscale transport, ICMP for path MTU,
and outbound traffic. DERP fallback depends on outbound connectivity. Package
version `1.104.4` is pinned. Routine package and route changes are reconciled one
member at a time, requiring a serving peer and recovery before changing the next.

## SecretStash custody and rotation

The wizard offers a new linked metadata-only container, an existing secret ID,
or protected environment/file references. Only the controller/runner reads the
OAuth pair, never the VM. Store `client_id` and `client_secret` together in the
same immutable SecretStash version. Terraform owns metadata only and never stores
a payload or OAuth credential in state.

```yaml
auth:
  mode: oauth
  secretstash:
    project_id: PROJECT_ID
    secret_id: SECRET_ID
    # version_id: VERSION_ID  # omit to select the approved primary
    nebius_auth:
      profile: OPERATOR_PROFILE
```

The wizard creates `component_instance`/`secret_name` links for a new container.
After infrastructure setup, `onboarding configure --import-to-secretstash` can
validate an administrator-delivered OAuth credential and import it from
`--client-secret-ref env:NAME` or `file:PATH`. The importer uses the Nebius Python
SDK, retains operation IDs for ambiguous responses, verifies exact container and
version identity, and pins the new version. It deliberately does not overwrite a
shared primary version. `--import-operation-id` reconciles an exact lost provider
acknowledgement. Preserve the owner-only local import journal and do not rerun a
create blindly. Existing containers are linked without replacing their contents.

The runner needs `mysterybox.payload-viewer` on the exact secret. Import additionally
requires the appropriate metadata/version write permissions; editor alone does
not grant payload reads. Use the current same-region Nebius IAM constraints.
SDK operator profile/config and credential-file references are explicit; Kubernetes
runners can instead select:

```yaml
nebius_auth:
  workload_identity:
    service_account_id: IAM_SERVICE_ACCOUNT_ID
    token_file: /var/run/nebius/token
```

This exclusive mode exchanges a rotating Kubernetes JWT through the native SDK,
with no Nebius CLI invocation or operator-identity fallback. The reusable
[Nebius workload identity guide](../../../skills/nebius/references/kubernetes-workload-identity.md)
covers exact OIDC trust, resource permits, projected audience `sts.nebius.com`,
renewal and verification. Direct consumers need neither Kubernetes Secrets nor
ESO. ESO synchronization is optional; native workload identity requires ESO
2.10.0+, above cxcli's current 2.4.1 catalog pin. Upgrade through its lifecycle
owner before using that optional path; this feature does not upgrade ESO.

New enrollment operations select the configured immutable version or approved
primary. Unfinished operations bind the original OAuth client, source and version;
retain that client and version until key cleanup is confirmed. Do not replace an
environment/file credential with a different client while work is pending. The
configure commands block such credential replacement. Rotation does not reenroll
healthy nodes. IAM-token renewal and application-secret refresh are separate.

## Policy and route approval

Policy remains administrator-owned unless you explicitly apply a reviewed proposal:

```bash
nebius-cxcli tailscale policy plan ./config.yaml --out ./policy-plan.json
# Review the proposal's document, patch, baseline and printed digest.
nebius-cxcli tailscale policy apply ./config.yaml \
  --plan ./policy-plan.json --sha256 REVIEWED_DIGEST
nebius-cxcli tailscale routes approve ./config.yaml
```

The proposal adds only configured tag ownership, route auto-approvers and explicit
grants. It preserves comments, trailing commas, unknown fields and existing policy
using Tailscale's HuJSON library. Existing conflicting tag ownership requires an
administrator decision. Apply rechecks the intent, digest and exact policy baseline,
validates with Tailscale, writes with `If-Match`, and reads back. A conflict requires
replanning. Keep proposal files private and outside version control: they contain
your tailnet policy. Release wheels bundle the pinned helper for macOS/Linux,
ARM64/AMD64. Source installations build it into the managed cache; if Go is missing,
they download a checksum-pinned toolchain. Offline source installs need that cache
prepared beforehand. If Go is already installed, it must be version 1.26 or newer;
an incompatible compiler produces a specific error before the build starts.

Choose `approval: api` to approve configured routes during deploy using delegated
permissions; the default `external` waits for administrative or policy approval.
A running VM alone is never reported as tailnet readiness. Without OAuth, the CLI
uses the authenticated guest's control-plane approved routes and node expiry; with
OAuth it also checks the exact device, authorization and key-expiry setting.

## Verification and maintenance

`tailscale verify` runs on a connected macOS or Linux client. It checks the expected
tailnet, the longest-prefix `Peer.PrimaryRoutes` selection, the operating-system
route, TLS certificate verification, HTTP status and bounded response size. It
accepts HTTP 200/204/401/403: an authenticated API can legitimately deny an anonymous
probe. Set `min_bytes: 0` for an empty 204 response. This verifies reachability and
server identity; it does not prove Kubernetes user authorization. Supply the cluster
CA when its API does not use a publicly trusted certificate. DNS must resolve only
inside the advertised prefixes. Targets cannot contain credentials or query strings.

`verification.required: true` makes this client check part of deploy. Leave it
false for an automation runner without tailnet connectivity and run verification
from a connected laptop. Reports distinguish infrastructure readiness from client
proof; deployment never silently counts an unrun client test as passed.

```bash
nebius-cxcli tailscale verify ./config.yaml --failover --json
nebius-cxcli deploy ./config.yaml  # Re-run to verify reconciliation
nebius-cxcli destroy ./config.yaml
```

Failover testing is explicit. It requires both owned members to be ready and all
probe destinations to select the same router. A guest-side 120-second timer restores
`tailscaled` even if the CLI disappears. The CLI observes selection of the other
router and successful HTTPS traffic while the first remains stopped, then restores
and checks the first member. This bounded test does not qualify every network
partition or guarantee uninterrupted application sessions.

Use `inputs.member_overrides.a` or `.b` for staged infrastructure changes. A plan
that would replace both routers, or delete a member through configuration removal,
is rejected. Keep the component configured through ordinary destroy: device
retirement precedes VM deletion. Destroy retains ownership history. Retired owners
cannot be silently rebound; use a new instance ID for a fresh installation.

## Interrupted enrollment and retirement

Enrollment uses a versioned operation bound to the VM, boot disk, public allocation,
SSH host identity, tailnet and tags. The original single-use key stays in an
owner-only file below `.nebius-cxcli/tailscale-enrollment/`; preserve this local
runtime directory when resuming deployment. It is not an export or report artifact. Cross-workstation enrollment recovery
is unsupported; SecretStash stores the shared OAuth pair, not pending enrollment keys.
The guest records the operation before authentication and keeps its temporary key
under root-only `/var/lib/nebius-tailscale/`. Confirmed enrollment verifies the node, retires the original API key when API
access exists, and removes temporary key material. Neither secret is written to Terraform, cloud-init or the remote
ownership journal.

A second deploy reads the authenticated guest receipt and resumes the same key and
operation. A lost enrollment response reconciles the exact node instead of creating
another one. Missing receipts, changed identities, expired keys and denied API
reads fail closed. An ambiguous key-creation response is recorded before the API
call; it never triggers blind issuance retries.

Use `tailscale doctor CONFIG_YAML --json` to inspect the enrollment operation ID,
phase and key ID, plus separate key/device retirement progress. Explicit recovery:

```bash
nebius-cxcli tailscale recover ./config.yaml --component ROUTER_INSTANCE \
  --member a --operation-id EXACT_OPERATION_ID --writer-stopped
```

If the original key was never used and cannot be delivered, add
`--replace-unused-key`. Recovery first fences the exact guest operation, then
confirms key revocation. The next ordinary deploy creates a new operation. A
started enrollment with an uncertain device identity remains blocked; restoring
reachability and reconciling that original device is required. Revoking a key does
not remove its enrolled device. If key creation lost its response, `--key-id`
selects a key whose API description and capabilities prove ownership by that exact
operation. It is accepted only with `--replace-unused-key`; an unknown or already
absent unrecorded key cannot establish this proof.

If a previous process crashed while holding the remote gateway lock, stop all its
CLI and Terraform writers before releasing the exact lock:

```bash
nebius-cxcli tailscale recover ./config.yaml \
  --lock-owner EXACT_OWNER --writer-stopped
```

Lock recovery can be combined with the enrollment selectors. It does not discard
ownership or suppress approval requirements. Do not delete or hand-edit a journal
to bypass recovery.

Ordinary destroy has independent retirement preparation. It reconstructs missing
bindings from exact Terraform addresses and authenticated cloud reads, including
disk-only creation and partially destroyed members. It does not check capacity,
issue enrollment keys, or finish provisioning. An unavailable or denied read is
uncertainty, never evidence that a resource is absent. Enrollment is fenced before
keys and exact owned devices are retired. An unreachable guest can be retired only
through an already-proven node identity; unknown identity blocks deletion. Each
completed step is persisted. An owner becomes retired only after Terraform state
and independent cloud reads confirm resource removal, separately from key/device
cleanup. Keep the component configuration until destroy completes.
Destroy rejects omitted gateway configurations even when another gateway remains
configured or only disks remain. A rejected unused-key replacement leaves the
started operation resumable; it never fences that operation as a side effect.

A never-activated gateway can be destroyed without Tailscale credentials only
when the durable journal proves activation never began and exact cloud ownership
is verified. This also permits cleanup when the approved tailnet differs from the
infrastructure-stage draft. A missing node ID or missing journal is not such proof. Journal v3
and controller enrollment v2 are required; older formats are rejected without a
migration shim or automatic destructive reset. Guest contracts remain v1.

## Qualification status

Offline interruption regressions and Terraform mock plans do not qualify a live
deployment. Live acceptance requires a fresh two-member deployment, second no-op
deployment, reboot persistence, private-API TLS and authenticated client reads,
peer-gated replacement, explicit failover and independent cleanup verification.
Use a disposable configuration with its own backend key and external VPC/subnet
references before installing a retained pair. Never run whole-project destroy on
a retained environment merely to test this component.

The tailnet, tags, credential references and policy belong to each operator's
organization. No organization-specific approval system is required by cxcli.

## Vendor references

- [Subnet routers](https://tailscale.com/docs/features/subnet-routers)
- [Route selection and failover](https://tailscale.com/docs/features/route-selection)
- [OAuth clients](https://tailscale.com/docs/features/oauth-clients)
- [Trust credential scopes](https://tailscale.com/docs/reference/trust-credentials)
- [Tailscale up flags](https://tailscale.com/docs/reference/tailscale-cli/up)
- [Official HuJSON parser](https://github.com/tailscale/hujson)
- [Go toolchain minimum versions](https://go.dev/doc/toolchain)
