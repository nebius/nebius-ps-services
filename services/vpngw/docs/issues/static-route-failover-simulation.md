# Static-route neighbor failover simulation

This experiment tests a remote neighbor's tunnel failover while the local
gateway remains the exact HA owner. It does not perform a local VM takeover.

## Original behavior

The original controller detects lost static/XFRM readiness and attempts bounded
recovery. It restarts strongSwan and FRR and reloads connections. It does not
explicitly select another usable static tunnel and replace the kernel route.
Therefore, saying that the original code does nothing on failure is too broad.
Restart-based recovery exists, but does not guarantee route reselection.

The original tests exercise controller repair decisions, service restart
commands, and route planning separately. They do not compose a real local route
observation with the controller and repair adapter across a neighbor SA switch.

## Experiment

The new `tests/integration/test_vm_ha_static_path_failover.py` uses the production
static route writer, local observer, readiness calculation, HA controller,
repair adapter, and standby status writer/validator. A stateful command model
supplies kernel routes, XFRM links, SAs and policies, service responses, and FRR
responses. Cloud and peer evidence use existing test fixtures.

The active-owner cases start with a healthy route, change only which local
XFRM interface has matching SA/policy evidence, ask the controller for its
recovery action, execute the actual repair method, and inspect the resulting
route. Healthy recovery requires the controller's two verification samples.

| Scenario | Original `5b73a95b` | Partial static-route fix |
| --- | --- | --- |
| Primary SA disappears, backup becomes usable | Route remains on primary, regression fails | Route moves to backup, passes |
| Backup SA disappears, primary becomes usable | Route remains on backup, regression fails | Route moves to primary, passes |
| No replacement encrypted path exists | Not compared in the historical replay | Repair does not claim readiness and controller reaches forwarding fence, passes |
| Bootstrap route is stale when backup establishes on standby | Not compared in the historical replay | Status rejects repairable readiness, expected failure |

For the historical replay, the service sources were archived directly from
`5b73a95b` without modification. The new test and its kernel helper were copied
into that archive. Only the helper import and constructor argument for the new
static-candidate API were omitted. Neither the original controller nor the
original repair implementation was changed. Both route-movement assertions
failed because the old destination interface remained installed.

The standby case is marked strict `xfail` to record the existing unresolved
defect. Running it with `--runxfail` produces the observed failure:

```text
AssertionError: ['standby-ready-evidence-invalid']
```

## Running the regression

From `services/vpngw` in a development Python environment on
`codex/fix-vpngw-static-route-selection`:

```bash
PYTHONPATH=src python -m pytest -q \
  tests/integration/test_vm_ha_static_path_failover.py

PYTHONPATH=src python -m pytest -q \
  tests/integration/test_vm_ha_static_path_failover.py \
  -k reload --runxfail
```

The first command reports three passes and one expected failure. The second
intentionally exits nonzero until standby readiness is fixed.

## Limits and remaining validation

This is a composed control-path simulation, not real Linux packet forwarding,
strongSwan negotiation, or a two-pair cloud HA trial. No host route, service, VM,
or production ownership was changed. The simulation leaves tunnel availability
independent of service restart success, since restarting the local daemon
cannot make a failed remote member available.

The host is macOS. Docker is installed, but the daemon was unavailable, so a
Linux packet experiment was not run. Further tests should exercise the real
reload path, delayed SA establishment, rekey overlap, both tunnels being up,
neighbor failover while the local member is standby, and packet recovery across
two independently failing-over HA pairs. The warm-standby defect remains open.
