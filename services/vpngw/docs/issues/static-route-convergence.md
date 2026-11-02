# Static route selection and standby convergence

Configuration-order route installation could leave a static prefix on an XFRM
interface without a usable SA, even when another tunnel could carry it. Original
restart-based repair did not guarantee route reselection.

The integrated correction selects one route per prefix using XFRM SA and policy
evidence. Owner repair and fenced candidate preparation can move stale routes to
a usable tunnel. A standby with a provably repairable path is reported explicitly
as `repairable`, without claiming forwarding readiness.

Promotion still requires the former owner Stopped, exact candidate allocation
ownership, local preparation and cloud route reconciliation before forwarding.
Cold-static standby behavior is unchanged.

The maintained [composed regression](../../tests/integration/test_vm_ha_static_path_failover.py)
covers both route-switch directions, no replacement path, repairable standby
admission and fenced candidate preparation. The former standby expected failure
is now a passing regression on this branch.

Controlled US takeover/failback on October 30, 2026 observed route repair while
forwarding remained off, followed by activation on the selected backup. A
separate CLI early-failure issue remains. See the
[validation summary](heartbeat-us-unplanned-static-validation.md).

Earlier simulation notes and raw evidence remain recoverable under
`services/vpngw/docs/issues/` at Git commit `1d8bd5cc`.
