# Synthetic quota and pricing evidence

Environment: Dev/Test, CPU-only MK8s. Required shape is called `required-cpu`
with preset `16vcpu-64gb` in this synthetic catalog. These labels and rates
are fixtures, not current provider facts.

The requested deployment adds two 16-vCPU nodes (32 vCPU steady) and needs one
additional node during replacement (48 vCPU maximum incremental allocation).
Existing usage is unrelated infrastructure and will remain allocated.
Storage requirement: 500GiB SFS and 200GiB object backups. Networking requires
one ingress public IP plus egress; those rates and quotas were not inspected.

Region-a tenant CPU limit: 64 vCPU; measured usage: 24; state active, inspection
complete. Project quota is inaccessible: no effective project limit is known.
The supplied project catalog supports the required platform/preset.

Region-b tenant CPU limit: 128 vCPU; measured usage: 0; state active, inspection
complete. Its supplied project catalog does not support `required-cpu`.
No provider evidence establishes physical CPU capacity in either region.

The user supplied only a tenant alias and explicitly requires human-profile
authentication. That profile is unavailable; ambient agent credentials must
not be used. Do not choose an arbitrary project or initiate authentication.

The supplied synthetic node rate is USD 0.40 per node-hour in region-a. Assume
100 running hours per month for the two steady nodes. No verified storage,
network or control-plane rates are supplied. Mark these items unpriced; do not
present a node subtotal as the total system cost.
