# Static route selection and standby convergence

Status: selection and standby-admission correction integrated on
`codex/fix-vpngw-heartbeat-api-coupling`. Controlled live retest pending.

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

## Composed simulation added after separation

The [neighbor failover simulation](static-route-failover-simulation.md) connects
the real route observer, HA decision, and repair adapter. Both active-owner
route-movement cases fail on the original code and pass with the partial fix.
The no-replacement-path case reaches the forwarding fence. A strict expected
failure records the remaining standby status/admission defect and reproduces
`standby-ready-evidence-invalid` when run with `--runxfail`.


## Standby admission correction on the heartbeat branch

The October 30 live transfer campaign reproduced the expected failure below.
The controller can safely defer a stale static route to candidate preparation
when installed SA and policy evidence proves a usable path for every required
prefix. Its status now reports this condition explicitly as `repairable`.
Fresh standby evidence includes a boolean `static_route_repair_ready`, which
must be true for that condition. Display and planned-transfer validators accept
the same state while retaining passive mode, exact non-owner attachment,
current guard, fresh evidence, healthy BGP and services, and no-pending-operation
requirements.

This does not mark a standby as forwarding-ready or mutate routes in the sampler.
The existing transfer chain still stops the former owner, verifies allocation
ownership, repairs the selected local route while fenced, reconciles cloud
routes, and enables forwarding last. Cold-static tunnel behavior is unchanged.
The former strict expected failure is now a passing regression that also proves
route selection during fenced preparation and rejects missing repair evidence.
