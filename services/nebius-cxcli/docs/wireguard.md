# WireGuard administrator access

New WireGuard gateways use SecretStash for team-owned SSH access. Choose an
existing administrator group in the wizard. cxcli lists groups live through the
Nebius SDK in the selected tenant and project, shows names plus exact scope/ID,
and revalidates the selected identity before granting access. Incomplete or denied
discovery stops setup. Group membership remains your organization's responsibility.
No group name is fixed or inferred. The selected group represents trusted gateway
operators with SSH and sudo access, and any member may issue client configurations.

SecretStash is a managed Nebius service, currently in preview. It does not need to
be installed in Kubernetes. WireGuard does not select external-secrets or add its
SSH secrets to the generic mysterybox Kubernetes-sync inventory.

## VPN destination subnets

`inputs.local_subnets` contains the private destination IPv4 CIDRs that clients
should reach over the VPN. They become the default client `AllowedIPs`.
`inputs.subnet_id` instead selects the subnet where the gateway VM is placed;
`inputs.wireguard_tunnel_cidr` is the VPN's own client-address network.

The wizard reads live subnets through the Nebius SDK for the selected project
and VPC. It displays names, exact subnet IDs and effective CIDRs, then saves
the chosen CIDRs. Shared network pools are labelled explicitly: selecting one
routes its whole range, which may include other subnets. Duplicate CIDRs appear
once with their matching subnet labels. New destinations start unselected.
Saved routes remain selected and are labelled when absent from the live list.
Use Space to toggle choices in a terminal, or comma-separated indexes in the
text prompt. The exclusive **Tunnel access only** choice clears saved routes;
terminal users can also uncheck all destinations.

Choose manual entry for planned or otherwise reachable networks. An unresolved
planned VPC, an empty live inventory and a failed lookup have distinct messages;
discovery failures never masquerade as a successful empty listing. Listing
requires network-get and subnet-list access. Selection does not prove gateway
reachability: routing and security rules must permit traffic. With NAT disabled,
destination networks also need return routes to the VPN client network.

The SDK is initialized with cxcli's versioned `user_agent_prefix` in both the
human-token and explicit credential-file paths. This identifies requests and
avoids the SDK's future-required-argument warning without suppressing warnings
or changing authentication. See the
[Nebius SDK initialization documentation](https://docs.nebius.com/sdk/python/install-auth).

## Installation

Configure the gateway's ordinary network, sizing, disk and username inputs, plus
`access.admin_group_id`. The wizard also persists `access.secretstash_ref` and a
visible, enabled `mysterybox` infrastructure row dedicated to this gateway. For
example, a `vpn` gateway references `mysterybox@vpn-access` with:

```yaml
- id: mysterybox
  instance_id: vpn-access
  enabled: true
  inputs:
    parent_id: project-example
    create_initial_versions: false
    secrets:
      - name: vpn-access-login
      - name: vpn-access-host
      - name: vpn-access-connection
```

Skipping a newly added gateway also discards its newly generated access row;
existing, retained or shared dependencies are preserved. Long gateway instance
names receive a deterministic shortened dependency ID so it stays within the
component-name limit. The stored reference remains the exact identity.

The SSH username must be a valid Linux account name other than `root` or `admin`.
The UDP listen port must be an integer from 1 through 65535; default client
keepalive must be an integer from 0 through 65535. cxcli validates these module
constraints before Terraform execution; keepalive 0 disables periodic keepalives.

Then render and deploy through the normal cxcli workflow. Loading or rendering a
hand-authored configuration never silently invents this dependency. The reference
must resolve to one enabled, same-project, metadata-only component owned by exactly
one gateway. This contract supports fresh installations; existing SDK-owned
generations need a separately designed migration and are rejected before adoption.

The provisioning identity needs SecretStash create/update and payload access,
service-account/group/membership/permit creation, Compute provisioning and the
existing regular Object Storage backend permissions. An editor role alone does
not grant secret payload access.
Permit creation must be authorized on the selected group's scope as well as the
secret. A project-scoped installer may list a tenant group while lacking authority
to grant it access; use an authorized provisioning identity for that selection.
Deployment reports direct tenant/project-wide payload or admin grants on the
selected group, but does not revoke them. Such grants include the host secret and
unrelated secrets; narrow WG grants do not restrict broader existing access.
Inherited roles can also grant access independently of the selected group.

Terraform applies `generated/infra-access` first. This root uses the regular
backend bucket with state key `<main-key>.wireguard-access`; the VM and other
infrastructure stay in `generated/infra`. The project lease and every affected
gateway journal lock are held before applying access infrastructure, through READY.
The SDK independently verifies the public resource IDs and grants before creating
payload versions and publishing their primary versions. Both roots are captured
in the generated manifest and immutable recovery generation, validated, and
included in output lookup. Inspect the access resources with
`terraform -chdir=generated/infra-access state list`. No routine targeted apply is used.

Deployment prepares:

| Resource | Consumer |
| --- | --- |
| Login Ed25519 pair | Selected administrator group, payload-viewer on this secret |
| Host Ed25519 pair | Dedicated attached VM service account, payload-viewer on this secret |
| Versioned connection record | Selected administrator group, payload-viewer on this secret |
| VM service account and its narrow group/permit | The gateway, via metadata-issued access tokens |

No downloadable VM service-account private credential is created. Human
administrators use their own Nebius CLI profile. Tokens stay in memory. Explicit
automation can select a renewable credentials file with
`--credentials-file PATH` on WireGuard commands, or
`CXCLI_WIREGUARD_CREDENTIALS_FILE` for installation/destruction. Do not commit that
file or share one automation identity among human administrators.

The regular Terraform backend bucket holds a separate conditional operation
journal under `cxcli/wireguard/`. Its key is bound to project and component
identity across laptops and attempts. It contains references and progress only.
Filesystem buckets and anonymous policies overlapping this namespace are rejected.
Locks do not expire or transfer automatically.
Secret publication preserves the metadata returned by SecretStash, checks the
expected primary version before updating, and verifies it afterward. SecretStash
may omit a resource version: the journal then serializes cxcli writers, but cannot
provide atomic conflict protection against out-of-band secret updates.
Version retries use the exact successful operation's recorded version ID and
verify its parent and active state; SecretStash does not retain version labels.
For a new container, an unset SDK primary version means no version is published
yet. Initial publication accepts that state; retrying an already published version
is a no-op, while a different unexpected primary still stops deployment.

Terraform receives a frozen public bootstrap map: generation, VM service-account
ID, pinned host-secret/version IDs, and the two public keys. Private SSH keys do
not enter Terraform state, plans, cloud-init, source configuration or Git. Initial
administrator authorization is withheld until the VM retrieves and validates its
expected host pair using its metadata token. Socket activation is disabled; the
SSH service depends on the identity gate. A retrying setup service completes the
WireGuard installation. Subsequent boots validate the persisted host identity and
preserve rotated authorization without fetching SecretStash.
Embedded file contents use cloud-init's `gz+b64` encoding to fit the Compute
user-data limit; scripts and permissions are restored before execution.
Guest HTTP requests identify cxcli explicitly because the API edge rejects the
default urllib client. The metadata token and host-only IAM grant still authorize
the payload read; proxies and redirects remain disabled.

After exact Compute binding and pinned SSH/full-setup checks, deployment publishes
a READY connection record and adds `access.connection_secret_id` and
`access.generation` to source configuration. Share those nonsecret references with
the administrator team. Concurrent source edits stop reference publication rather
than overwriting changes; rerender and resume with the intended source.

Preview/render and quota observation never create these credentials or IAM
resources, including quota checks inside an active deployment. A preview before
initial preparation reports unresolved bootstrap references. Actual deployment
prepares them explicitly after read-only admission and before its final Terraform
plan. An interrupted installation reuses the retained generation and owned resources.

## Optional application synchronization

Selecting MysteryBox never selects ESO. Choose `external-secrets` explicitly for
each Kubernetes target that needs it. With eligible application secrets, selecting
ESO preserves automatic sync setup: a ClusterSecretStore, namespace-scoped
ExternalSecrets and the existing runtime credential flow. Set the target's
`secrets.mysterybox.enabled: false` to opt out. An explicitly enabled sync or
application requirement without a selected controller produces an actionable
validation error. WireGuard metadata-only containers are excluded from payload
prompts, primary-version writeback and Kubernetes sync, including mixed projects.

## Client distribution

```bash
nebius-cxcli wireguard --gen-client-conf "$CONFIG" \
  --component wireguard-gw@vpn --client-name laptop
```

The command reads source configuration and the current connection payload directly
through the SDK, obtains the pinned login version, and rechecks the connection.
It creates temporary owner-only credential files, pins the expected server key,
and disables inherited SSH configuration, agents and connection reuse. It needs
no generated deployment bundle, Terraform backend, Compute read, Kubernetes
access, personal SSH public-key installation or original deployer files.

The optional `--ssh-known-hosts-file` is an additional constraint and must agree
with SecretStash. There is no `--ssh-private-key` or agent fallback. An explicit
`--ssh-user` must match the authenticated connection record; otherwise its username
is used. These SSH assertions apply to client and subnet modes only.

The existing guest client-allocation lock prevents duplicate tunnel allocation.
Local client directories use mode 0700 and `.conf` files use 0600. Administrators
distribute one configuration per device through an appropriate private channel.
Recipients need VPN configuration only, not SSH or SecretStash access.
Existing output is rejected before remote allocation unless `--force` is selected;
a concurrent output writer is also protected from overwrite. An ambiguous remote
response or local save failure reports the requested client name. Inspect that
client on the gateway before retrying; cxcli does not automatically allocate
another peer. `--force` permits local replacement, not remote peer replacement.

## Offboarding and login rotation

Removing a user from the IAM group prevents future payload reads. It does not
revoke a private SSH key they already copied. A provisioning administrator runs:

```bash
nebius-cxcli wireguard --rotate-login-key "$CONFIG" --component wireguard-gw@vpn
```

Rotation creates a nonactive version, stages its public key, proves candidate-only
login, makes issuance unavailable, removes the old key, verifies explicit SSH
authentication rejection of that key, then publishes the new READY version.
Rerun the same command to resume interrupted phases. After completion, another
rotation invocation intentionally starts a new rotation.

The bootstrap map stays unchanged, so rotation does not replace the VM/disk or
reset VPN client state. Gateway administrators have full sudo: separate payload
grants reduce routine API exposure, but do not confine an administrator who can
read root-owned guest files. Revoke existing VPN peers separately when appropriate;
SSH login rotation does not revoke already distributed VPN configurations.

## Interrupted operations

Privileged recovery and rotation require the existing backend's S3 credentials
in `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` or
`NEBIUS_S3_ACCESS_KEY_ID`/`NEBIUS_S3_SECRET_ACCESS_KEY`. Supply them through a
protected process environment. These commands do not bootstrap backend access;
ordinary client issuance needs no backend credentials.

Ordinary retry reuses recorded resource identities. Unconfirmed asynchronous cloud
effects retain the remote lock. Independently stop the previous process and all
children before explicitly reconciling its exact reported owner:

```bash
nebius-cxcli wireguard --recover-access "$CONFIG" \
  --component wireguard-gw@vpn --lock-owner OWNER_UUID --prior-writer-stopped
```

Recovery checks any recorded provider operation is terminal and successful, or
inspects the guest under its maintenance lock for an interrupted SSH mutation,
before releasing ownership. Terraform owns container and IAM recovery through
its retained access state. For secret-version writes, supply `--operation-id` only
after locating the exact operation independently; cxcli verifies its idempotency identity.
Pending, failed or unidentifiable effects retain the lock
and require resource-specific investigation. cxcli never invents a replacement
key or adopts a same-name foreign resource to get past uncertainty. Resume the
original deploy/rotation command after successful recovery. An intent saved before
dispatch preparation is safe to retry. An interruption after dispatch preparation
but before an identifiable provider acknowledgement stays locked until explicit
recovery, even if the request might never have been sent.

An access-root Terraform lock is separate from the lifecycle journal. After
independently proving its writer stopped, use
`nebius-cxcli terraform unlock GENERATED_PATH --root infra-access`; the default
`--root infra` addresses the main state. Unlocking either Terraform root does not
unlock the WireGuard journal. Missing journals with existing access state are not
authority to adopt or regenerate credentials.

## Destruction and availability

Use the normal explicit destroy workflow while retaining the gateway component in
configuration. It retires the connection record before Terraform deletion and
retains the access root and journals by default. After Terraform and independent
Compute readback confirm VM absence, cxcli sets the source gateway to
`enabled: false` with `access.lifecycle: retained`, preserving its dependency and
public generation references. Later renders retain those resources even when the
former VPC is removed. Keep both declarations: removal, rebinding and re-enabling
a retired generation are rejected. There is no automatic purge workflow.
Ordinary deploy rejects gateway replacement
or removal that would bypass retirement. A retired generation cannot be reused;
use a new component instance for a fresh gateway. The selected human group is
never deleted or modified as cleanup.

A SecretStash outage blocks new client issuance. Existing WireGuard connections
continue; a correctly initialized gateway can reboot using its persisted keys.
Local tests do not prove provider permissions, image boot ordering or VPN traffic.
Qualify fresh deployment, issuance by any selected-group member, denied access,
concurrent client allocation, rotation and reboot on a selected nonproduction
target. No second human is required. An account with inherited admin access does
not prove group-only isolation. Verify a fresh VPN handshake, bidirectional
transfer and an authenticated private-service request through the configured
routes; then disconnect and restore the laptop's routing and DNS baseline.

For an unresolved attempt, preserve its journal, locks and secrets until exact
reconciliation is possible. A deliberately fresh deployment uses a new component
instance with independent secrets and journal. A rendered access owner stays
tracked in source; removing component rows is not a resource cleanup mechanism.

## Provider references

- [SecretStash overview](https://docs.nebius.com/mysterybox/index)
- [SecretStash permissions](https://docs.nebius.com/iam/authorization/roles#secretstash)
- [Custom groups and resource access](https://docs.nebius.com/iam/authorization/groups#custom-groups)
- [VM service-account metadata](https://docs.nebius.com/compute/virtual-machines/instance-metadata#getting-service-account-information)
- [Secret payload API](https://docs.nebius.com/rest-api/mysterybox/v1/payloads/get)
- [Primary-version updates](https://docs.nebius.com/mysterybox/secrets/make-version-primary)
- [Conditional Object Storage headers](https://docs.nebius.com/object-storage/interfaces/s3-api-compatibility#supported-s3-headers)

## MTU repair and client verification

The gateway derives its tunnel MTU as `min(1420, uplink MTU - 80)` and writes
that value explicitly into server and generated client configurations. Optional
`inputs.wireguard_mtu` must be an integer from 1280 through 1420 and cannot exceed
the detected uplink limit. Missing or ambiguous default uplinks fail clearly.
Forwarded TCP SYN packets crossing `wg0` receive scoped bidirectional MSS clamps.
These rules persist through `wg-quick` hooks and do not open additional ports.
MSS clamping protects TCP; explicit MTU remains necessary for other traffic.

Render freezes the canonical networking helper from the selected module source.
Deploy applies that revision through authenticated pinned SSH, preserving the VM,
disk, public address, SSH/WireGuard identities, peer allocations, and routes.
The original boot payload is retained as a digest-bound object in the existing
protected backend and passed as a runtime-only Terraform input. The shared VM
replacement guard remains intact. Other first-boot settings cannot silently
change: use their supported day-2 operation or explicitly retire the gateway.
Keep the backend bootstrap artifact for the lifetime of the gateway.

Bootstrap clients are optional. First boot persists an empty peer registry so
deployment reconciliation also works before any client is issued. A missing
registry on an existing gateway remains an error; repair does not infer that
the gateway has no peers.

For an already deployed gateway, rerender and deploy, or use the bounded repair:

```bash
nebius-cxcli render ./config.yaml
nebius-cxcli wireguard --reconcile-network ./config.yaml
nebius-cxcli wireguard --refresh-client-conf ./config.yaml --client-config ./wireguard-clients/wg-client.conf
sudo wg-quick down ./wireguard-clients/wg-client.conf
sudo wg-quick up ./wireguard-clients/wg-client.conf
nebius-cxcli wireguard --verify-client ./config.yaml --client-config ./wireguard-clients/wg-client.conf
```

Refresh identifies the existing registered client and changes only its local MTU,
limited by both the gateway and local uplink. Keys, routes and DNS are preserved.
It does not activate the VPN. An interrupted guest update retains protected
recovery evidence and fences new client changes; resume the same rendered
revision before changing intent. It never automatically restores stale keys.

Deploy automatically checks an identifiable connected client from the project's
`wireguard-clients/` directory against selected managed Kubernetes targets sharing
the gateway's declared VPC binding. Explicit verification supports other routed
access when that automatic binding cannot be established. A selected cluster
outside the identified client's destination routes remains `PENDING` automatically;
explicit verification of that cluster fails until its intended route is configured.
Use explicit `--verify-client` for a custom path, with `--target`, `--kubeconfig`
and `--context` when selection is ambiguous. The existing context must match the
provider's private endpoint and CA, with no TLS or proxy override. Verification
never changes current-context or creates workloads. It checks the actual local
route/interface, MTU, gateway peer identity, verified TLS, authenticated identity,
an uncompressed discovery response larger than 4 KiB, and fresh bidirectional
peer transfer. Authentication uses a nonpersistent Kubernetes SelfSubjectReview.

Current-run results are written to `generated/reports/wireguard-client-verification.json`
and `.md`, separately from infrastructure validation. No connected client or
missing/ambiguous prerequisites means `PENDING`, not a VPN success; automatic
`PENDING` does not fail infrastructure deployment. An exercised client-path
failure means `FAIL` and nonzero deploy exit. Explicit verification requires
`PASS` for zero exit. With no selected managed Kubernetes target the check is
`NOT_APPLICABLE`; WireGuard does not acquire an implicit Kubernetes dependency.

The MTU policy follows the [WireGuard wg-quick interface](https://git.zx2c4.com/wireguard-tools/about/src/man/wg-quick.8)
and its [Linux uplink calculation](https://git.zx2c4.com/wireguard-tools/tree/src/wg-quick/linux.bash).
[RFC 2923](https://www.rfc-editor.org/rfc/rfc2923.txt) explains why small packets
can succeed while larger TLS transfers stall when path-MTU feedback is ineffective.
