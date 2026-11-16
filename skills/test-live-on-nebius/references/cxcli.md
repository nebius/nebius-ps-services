# cxcli ownership

Read the selected checkout's component catalog, actual CLI implementation and
module inputs. `component_sources.yaml` declares `infra:vm` and `infra:mk8s`;
capability can change, so verify it against frozen source. Public-looking cxcli
inspection commands may bootstrap IAM or remote state; source-only plan must not
invoke those commands. Use the checkout's tested entrypoint and pinned environment.

For a generic VM, create a fresh run-specific deployment root:

```text
create ROOT --tenant-id ID --project-id ID --region-id REGION
  --infra vm --app none --no-interactive
```

Use the generated v2 config's `client_info.nebius` target and
`infra.components[].inputs`. Set exact discovered platform/preset, 40-GiB
NETWORK_SSD for the simple trial, verified image, public SSH key, network/subnet,
explicit public_ip_mode and security_group_ids. The module's default public IP
mode is dynamic: never leave exposure implicit. Disable deletion protection only
on owned disposable resources. Do not use force against an existing tree.

Sequence: validate config, render config, validate-generated, review Terraform
plan, deploy config, verify, destroy config with its native owner. Deploy does
not automatically render edited configuration. Generic VM destruction belongs
to Terraform. MK8s has a distinct native destroy path with the immutable cluster
`--target`; inspect the current interface before use.

Precreate a new labeled, versioned backend bucket as an SDK fixture; record its
immutable ID. Freeze `NEBIUS_CXCLI_TFSTATE_BUCKET_NAME` to that bucket and
`NEBIUS_CXCLI_TFSTATE_OBJECT_KEY=terraform.tfstate` before render through destroy.
There is no supported local backend switch. Name conflicts never establish
ownership. After native destroy, quiescent writers and independent absence,
delete the owned bucket with zero TTL and verify immediate absence.
[Official bucket deletion](https://docs.nebius.com/object-storage/buckets/manage#how-to-delete-buckets)

cxcli may create/reuse its own persistent runtime service account. Retain it and
canonical agent auth; do not claim the entire lifecycle used one identity. Keep
quota reads pinned to the selected agent profile. SDK network/security-group and
backend fixtures are setup evidence, separate from cxcli VM provisioning proof.
