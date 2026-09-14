# Storage and managed data services

Reviewed 2026-09-07. APIs: `compute.v1` volumes/images/snapshots,
`storage.v1` buckets, S3 object API, `registry.v1`, and
`msp.postgresql.v1alpha1`. Verify regional support and current API maturity;
PostgreSQL's alpha API namespace must not be rewritten to a guessed `v1`.

## Select storage by durability and access

| Need | Choice | Lifecycle constraint |
| --- | --- | --- |
| Boot or persistent block device | Network disk | Check attachment and retention; VM-managed disks follow VM deletion |
| Reconstructible scratch/cache | Local SSD | Host-local ephemeral data can be lost on stop/delete |
| Shared mounted files | Compute shared filesystem | Same-project attachment and virtiofs/CSI binding |
| Datasets, artifacts, transfers | Object Storage | Bucket policy/version/lifecycle govern retained objects |
| Container images | Container Registry | Registry authorization differs from S3 access keys |
| Relational application data | Managed PostgreSQL | Plan private access, backup retention and restore before creation |

Do not use non-replicated disk types for irreplaceable data. Check current disk
size units, performance scaling and type/region constraints. The volume builders
use standalone network SSD volumes with deletion protection; they do not attach
or format devices. Formatting, snapshots, restores and detach/delete are explicit
data-affecting actions. A snapshot is not proof of an application-consistent backup.

## Filesystems and Kubernetes CSI

Create the filesystem, verify its provider ID/project, attach it to the intended
node-group template, then follow Nebius's CSI driver/PV/PVC workflow. A mount path
or a Bound PVC alone is insufficient: verify filesystem ID, node attachments,
volume handle, storage class/reclaim policy and the consumer Pod. Use
`assets/storage/filesystem-pvc.yaml` as a claim template only
after the nodes and CSI driver are prepared. Verify a retained reclaim policy
in the selected StorageClass before creating the claim. A scoped write/read workload test
requires explicit data-mutation authority. Keep Soperator protected-storage
identity and recovery inside its lifecycle owner.

## Object Storage

Use `assets/storage/buckets.py` for an explicitly authorized versioned regular
bucket. Reusing an existing bucket requires its exact ID and matching project,
name and versioning policy. Mismatches fail; the helper does not silently change
policies. It waits for ACTIVE state and retains created IDs on failure.
Terraform backend setup begins only after bucket readiness; Terraform owns state.

Use Nebius APIs for full bucket configuration and boto3 for object payloads.
Supply an explicit regional endpoint and bounded botocore connection/read
budgets; disable automatic retries for an uncertain data write until reconciled.
Use `assets/storage/object_transfer.py` for bounded regular-bucket byte transfers.
It returns a SHA-256 digest; `download_verified` checks actual bytes against a
trusted digest. ETags are not reliable content hashes. Large datasets need
managed multipart uploads, resumability and explicitly owned abort/cleanup.

S3 compatibility is partial: do not assume object ACLs, S3 notifications,
Object Lock, AWS SSE-KMS, or native replication rules. Filesystem buckets have
additional checksum, condition and versioning restrictions. Consult the current
compatibility table before porting an AWS workflow. Object expiry, transfer
overwrite and bucket policies can destroy or expose data; review exact effects.

## Custom checksum metadata

When a retained backup uses custom S3 metadata, compare metadata names
case-insensitively, treating `Sha256` and `sha256` as equivalent names.
Preserve values exactly and report conflicting entries after name normalization.
This applies to metadata names only; object keys remain case-sensitive.

A missing or differently capitalized metadata field does not establish a failed
upload. Preserve the object and its recorded version. Verify that exact version's
bytes against the trusted source digest. For payloads within its size limit,
use `download_verified` with the recorded `version_id`. Report metadata
inconsistencies separately from content verification; neither matching custom metadata nor an ETag alone proves content
integrity. Custom `sha256` metadata is distinct from native S3 checksum fields
such as `ChecksumSHA256`; preserve their documented encoding and semantics.

See [S3 metadata semantics](https://docs.aws.amazon.com/AmazonS3/latest/userguide/UsingMetadata.html)
and [Nebius object integrity](https://docs.nebius.com/object-storage/objects/manage).

## Registry and PostgreSQL

`assets/storage/managed_services.py` provides typed builders. For registry,
create/wait/read back the registry; use documented local credential-helper or
CI service-account authentication. Verify the image digest and Kubernetes pull
identity independently. Configuring Docker credentials and pushing images are
writes; do not print config files or tokens as verification.

For PostgreSQL, pass an explicit version, private network, resource template,
bootstrap settings and backup window/retention. Keep passwords in a secure
in-memory handoff; never log the generated request. The documented backup
configuration is chosen at creation; verify current mutability before updating.
Check cluster state, authenticated TLS database connectivity and least-privilege
SQL access separately. Restore into a separately authorized target and verify
application data; do not label backup existence as restore proof. Database
migrations and workload schemas remain application-owned.

## Official references

- [Volumes and durability](https://docs.nebius.com/compute/storage/types)
- [Snapshots](https://docs.nebius.com/compute/storage/disk-snapshots)
- [Filesystem CSI](https://docs.nebius.com/kubernetes/storage/filesystem-over-csi)
- [S3 compatibility](https://docs.nebius.com/object-storage/interfaces/s3-api-compatibility)
- [Bucket policies](https://docs.nebius.com/object-storage/buckets/bucket-policy)
- [Object lifecycle](https://docs.nebius.com/object-storage/objects/lifecycles)
- [Transfers](https://docs.nebius.com/object-storage/transfer/overview)
- [Registry authentication](https://docs.nebius.com/container-registry/authentication)
- [PostgreSQL clusters](https://docs.nebius.com/postgresql/clusters/manage)
- [PostgreSQL backups](https://docs.nebius.com/postgresql/backups)
