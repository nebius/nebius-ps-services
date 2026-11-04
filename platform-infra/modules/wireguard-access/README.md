# WireGuard access infrastructure

Creates the VM service account, dedicated group, membership, and three narrow
`mysterybox.payload-viewer` permits for one WireGuard gateway. Supply three distinct
SecretStash container IDs: the VM group reads `host`; the selected administrator
group reads `login` and `connection`. The existing administrator group is external
to this module.

Requires Terraform >= 1.11 and Nebius provider >= 0.6.76, < 0.7.
See [the minimal example](examples/minimal/main.tf).

cxcli composes this module with metadata-only `mysterybox` in
`generated/infra-access`. It uses the main backend bucket with a distinct state key
ending in `.wireguard-access`. Apply this root before the VM root. SDK code owns
all payload versions and primary publication; this module contains only public
identities and grants. Its `access_resources` output supplies exact IDs for
independent SDK verification before payload creation.

Retain this root after retiring and destroying the gateway VM. Reuse the same
root/state for retries; never recreate credential containers to recover a missing
resource. A deliberately fresh gateway uses a distinct component instance.

Run `terraform init -backend=false` and `terraform test` for offline provider-mock
checks. These checks do not qualify live IAM authorization or SecretStash access.
