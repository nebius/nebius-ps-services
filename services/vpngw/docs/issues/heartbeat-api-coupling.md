# Cloud observations and VM-HA heartbeat availability

The original controller constructed heartbeats synchronously with cloud reads.
Slow requests could delay publication, and peer sampling before blocking reads
could exclude newer heartbeats from a decision snapshot.

This branch separates the publisher and local-health sampler from the controller
and receiver. The protocol reports health freshness separately from advisory
ownership freshness. No additional daemon was added.

The October 30, 2026 live campaign exposed a second coupling: a cloud read error
installed a forwarding-off guard even while heartbeats continued. Commit
`1d8bd5cc` changes retryable cloud observations to retry after five seconds
without disabling established forwarding or invalidating ownership continuity.
Takeover and activation still require fresh cloud proof. Uncertain mutation
outcomes and positive ownership-loss evidence retain fencing.

The retry correction passed offline tests but has not been deployed or retested
live. This branch now also distinguishes local query failure from confirmed bad
health, grants three-failure/ten-second observation grace, preserves remaining
paths on partial connection loss and avoids broad daemon restarts during repair.
These follow-up changes require capability v2 and separate live qualification.

Maintained regressions are in
[heartbeat tests](../../tests/unit/test_vm_ha_heartbeat.py),
[cloud-read retry tests](../../tests/unit/test_vm_ha_cloud_read_retries.py), and
[observation-grace tests](../../tests/unit/test_vm_ha_observation_grace.py).
The [two-node failover tests](../../tests/integration/test_vm_ha_failover.py)
and composed static-path tests cover the transfer and repair boundaries.
See the [validation summary](heartbeat-us-unplanned-static-validation.md).

Raw logs and the original standalone reproducer were removed from the working
tree. They remain recoverable under `services/vpngw/docs/issues/` at Git commit
`1d8bd5cc`. Production tests replace the standalone reproducer.
