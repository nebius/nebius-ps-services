# Static route selection and standby convergence

Status: partial implementation, not ready for deployment as a complete fix.

Branch: `codex/fix-vpngw-static-route-selection`, based on `5b73a95b`.

An explicit VM-HA gateway with redundant static tunnels must select a usable
encrypted path for each destination. Configuration order previously allowed an
unavailable tunnel to overwrite the route installed through a usable tunnel.

The extracted implementation selects one route per prefix using XFRM SA and
policy evidence, prefers a usable backup over an unavailable preferred path,
detects stale kernel routes without mutating them, and supports guarded route
repair during candidate preparation and owner repair. It preserves ordinary
gateway route selection.

## Remaining issues observed on the US standby

After a strongSwan restart, route installation can run before either UK tunnel
has an SA. The selector installs its preferred bootstrap path, `xfrm1`. When
only backup `xfrm2` later establishes, the route remains on `xfrm1` until repair.

The controller treats a repairable static path as transfer-ready, but standby
status represents only cold or fully warm paths. The repairable state is
reported as `not-standby` and rejected as `standby-ready-evidence-invalid`.
This prevents `vm-ha` and failback preparation from reaching the repair step.

The focused unit tests cover selection, local observation, and guarded repair.
They do not prove convergence through the real standby status and CLI admission
path. Complete that coverage and fix convergence before treating this issue as
resolved. No production takeover is part of this branch separation.
