## MK8s compatibility and image-selection guidance

Use this reference when a task involves Nebius MK8s GPU node groups, driver presets, image-family choices, or cluster/node-group operational readiness.

For operator-install decisions, Nebius driverful-image behavior, and GPUDirect
RDMA cluster checks, also load `references/mk8s-gpu-setup.md`.

### Primary use cases

- Choose a compatible GPU `drivers_preset` for a given Kubernetes version and GPU platform.
- Decide whether a chosen GPU preset supports InfiniBand / GPU clustering, and therefore whether `infiniband_fabric` should be set at all.
- Decide whether a platform should use a newer CUDA/image family or stay on an older one.
- Review whether a planned node-group update has enough quota and public-IP headroom for rolling replacement.
- Troubleshoot MK8s autoscaling or provisioning issues that may be caused by stale node infra versions rather than workload config.
- Explain or validate MK8s maintenance behavior for GPU-backed nodes.

### Compatibility workflow

1. Start with the live compatibility matrix via the SDK API, not a static mapping:

   ```python
   from nebius.api.nebius.mk8s.v1 import (
       GetNodeGroupCompatibilityMatrixRequest,
       NodeGroupServiceClient,
   )

   response = (
       NodeGroupServiceClient(sdk)
       .get_compatibility_matrix(
           GetNodeGroupCompatibilityMatrixRequest(
               cluster_kubernetes_version=k8s_version,
           )
       )
       .wait()
   )
   ```

2. Use the CLI only for ad hoc manual inspection when needed:

   ```bash
   nebius mk8s node-group get-compatibility-matrix \
     --cluster-kubernetes-version <k8s-version> \
     --platform <gpu-platform>
   ```

3. Read the matrix for:
   - supported `compatible_platforms`
   - supported `os`
   - supported `drivers_preset`

4. Query the live compute platform preset inventory before deciding whether GPU clustering is valid:

   ```python
   from nebius.api.nebius.common.v1 import GetByNameRequest
   from nebius.api.nebius.compute.v1 import PlatformServiceClient

   platform = (
       PlatformServiceClient(sdk)
       .get_by_name(
           GetByNameRequest(
               parent_id=project_id,
               name=platform_name,
           )
       )
       .wait()
   )

   clusterable_presets = {
       preset.name
       for preset in list(platform.spec.presets)
       if getattr(preset, "allow_gpu_clustering", False)
   }
   ```

5. Treat GPU clustering as a preset-level capability, not a platform-level assumption.
   - `infiniband_fabric` is valid only when the exact chosen preset is in the live `clusterable_presets` set.
   - If the chosen preset is not clusterable, keep `infiniband_fabric` unset.
   - If a stale config still has `infiniband_fabric` set for a non-clusterable preset, fail early and clear/fix it before apply.

6. Prefer the exact `drivers_preset` string returned by the matrix.
   - Good: `cuda13.0`
   - Avoid: `cuda13`
   - Avoid: `latest`

7. If the matrix yields exactly one valid `drivers_preset`, treat that as the safe default.
   - Keep the field overrideable.
   - Persist the chosen value so later render/deploy steps do not fall back to stale module defaults.

8. If the matrix yields multiple valid presets, present those exact options to the operator instead of guessing.

9. If the matrix yields no preset for the selected platform/version, treat that as a compatibility gap to resolve before apply.

### Image-family guidance

- Use image-family recommendations only after the compatibility matrix confirms the platform/version combination.
- Prefer exact image-family tags over floating aliases.
- Newer GPU platforms may require newer CUDA families after firmware/image rollout; do not assume an older CUDA family remains valid forever just because it worked previously.
- For CPU-only flows, prefer the current recommended driverless family for the target platform instead of carrying older image families forward by habit.

### Rolling-update and quota/public-IP headroom

- MK8s rolling updates usually need temporary headroom for at least one extra node.
- Public-IP pools for pinned-address node groups must cover:
  - fixed node count, or autoscaling max count
  - plus rolling-update surge headroom
- If quota or public IPs are fully utilized, a safe staged update is:
  1. reduce size or max size
  2. apply the spec change
  3. restore the original size

### Node infra-version checks

- If Cluster Autoscaler or node provisioning behaves unexpectedly, inspect node-group infra versions before assuming the workload or scheduler is at fault.
- Outdated node infra versions can be the hidden cause of scale-up failures or inconsistent behavior.
- Compare the node-group’s current infra version against the latest rollout for the target region.

### Maintenance expectations

- MK8s may cordon and later drain nodes as part of maintenance reconciliation.
- For GPU nodes, MK8s may wait for GPU workloads to finish, but it can still stop-start the node before the SLA deadline once the node is clear enough to proceed.
- “Replaced before the deadline” is not automatically a service fault; verify the maintenance timeline first.

### Decision rules

- Source of truth for compatibility: live MK8s compatibility matrix.
- Source of truth for GPU clustering eligibility: live compute platform preset metadata, especially `allow_gpu_clustering` on the selected preset.
- Source of truth for image preference: current recommended image-family guidance.
- Preferred automation path: Nebius SDK API.
- CLI is acceptable for manual/operator inspection, but it should not be the primary automation interface when the SDK is available.
- Do not decide InfiniBand / GPU-cluster eligibility from platform name alone.
- Do not hardcode platform-to-driver assumptions without a live recheck.
- When in doubt, fail early and ask for a concrete compatible preset instead of letting Terraform discover the mismatch at apply time.
