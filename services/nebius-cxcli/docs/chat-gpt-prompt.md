# ChatGPT Prompt for the Soperator Jail Upgrade Diagram

This file is an image-generation aid. It is not part of the operator runbook or
the Jail Upgrade execution contract.

## How to Use This Prompt

1. Start a new ChatGPT conversation with image generation enabled.
2. Attach the current `docs/jail-upgrade-workflow.png` as the visual reference.
3. Paste the complete prompt from the next section.
4. Ask ChatGPT to return its six-panel content plan before generating an image.
5. Compare that plan with the final accuracy checklist below.
6. Reply `APPROVED - generate the image` only after every item is correct.
7. Inspect the generated image at full size. Ask ChatGPT to correct any spelling,
   missing component, crossed connection, unreadable label, or inaccurate copy
   arrow.
8. Save the approved PNG as `docs/jail-upgrade-workflow.png`.

## Prompt to Paste into ChatGPT

Use the attached Jail Upgrade diagram only as a visual-style reference. Create a
new, technically accurate replacement infographic titled:

`Soperator Jail Upgrade - safe rootfs slot handoff`

Before generating the image, return a concise six-panel layout plan and confirm
that every rule below is represented. Wait for my approval before creating the
image.

### Output and Visual Style

- Produce a crisp landscape PNG at 1536 x 1024 pixels or a higher-resolution
  image with the same 3:2 aspect ratio.
- Retain the reference image's clean technical-infographic style: white
  background, six panels in a 3 x 2 grid, thin gray dividers, rounded boxes,
  dark navy text, and blue numbered step circles.
- Use green for the active rootfs and in-place persistent mounts, blue for the
  passive slot and planned switch, orange for rollback and optional guarded
  copy operations, and gray for components outside the Jail lifecycle.
- Use simple storage-folder, Kubernetes workload, Service, and database icons.
- Keep all text horizontal and readable at documentation width.
- Do not use gradients, decorative people, vendor logos, provider identifiers,
  IP addresses, cluster IDs, or environment-specific names.
- Spell every component and path exactly as specified below.
- Rootfs means the Slurm Jail mounted inside workloads at `/mnt/jail`; it does
  not mean the Kubernetes container's literal `/`.

### Architectural Rules That Must Be Visible

The Jail rootfs consumers are:

1. Controller
2. Login workloads behind the Login Service
3. REST / `slurmrestd`, when enabled
4. SConfigController
5. Every configured worker NodeSet

Wherever a panel depicts Jail consumers, show exactly two controller pod cards
(`controller-0`, `controller-1`), two login pod cards (`login-0`, `login-1`),
and exactly two example worker NodeSet cards (`worker-0`, `worker-1`). Add a
separate text annotation, `... every configured worker NodeSet`, beside the
worker pair so the image does not imply a fixed cluster-wide worker count. Do
not add a third worker card.

Controller and login use the selected rootfs volume source. REST and
SConfigController follow the canonical `jail` volume-source alias. Every worker
NodeSet uses the selected Jail PVC. Each individual pod mounts exactly one
active rootfs source.

Accounting is not a Jail rootfs consumer. Show it in a separate gray box with
no line to either rootfs slot. Label it exactly:

`Accounting - no Jail slot switch`

Inside that box show:

- `SlurmDBD + MUNGE: container images`
- `MariaDB: dedicated accounting PVC`
- `Continuity: prior guarded SQL handoff`
- `Schema: Helm-bound target image + probed deny-ingress bootstrap`
- `Import: collision-safe cluster IDs + kubectl exec -i + exact marker/history proof`

Do not connect accounting to `slot-a`, `slot-b`, or `legacy-rootfs`.

Add a thin campaign-order banner above the six panels:

`First supported Kubernetes hop -> target chart + accounting handoff -> Jail
Upgrade -> later Kubernetes-only hops`

The six-panel story is the first external-adoption example. Do not imply that
the accounting SQL handoff is performed again during the Jail slot switch.

### Six-Panel Story

#### Panel 1 - Before Refresh: Inventory Consumers and Customer Data

Show one physical Jail SFS containing this first-adoption state:

- Active rootfs source: `legacy-rootfs`
- Physical `slot-a`: present for later alternation, not selected during first adoption
- Passive target: `slot-b`
- Stable persistent paths: `/home`, `/data`, `/scripts`, and `/models`

Connect every Jail consumer listed above to the current active source with solid
green lines in this pre-fence panel. Label the old SConfigController as running
only before the compatibility writer fence; Panels 3-5 must show both source
and target SConfig writers stopped except for the bounded target seed pulse.
Place accounting nearby in its separate gray box with no rootfs connection.

Add this note:

`Each pod has one /mnt/jail rootfs`

#### Panel 2 - Classify Persistent Paths

Create two clearly separated lanes.

Green lane:

`In-place adoption - no copy`

Show `/home`, `/data`, `/scripts`, and `/models` remaining at their existing
external-cluster directories:

- `/mnt/jail/home`
- `/mnt/jail/data`
- `/mnt/jail/scripts`
- `/mnt/jail/models`

Show that these paths become stable mounts/PVCs and are remounted into the new
active rootfs without copying their contents.

Orange lane:

`Optional guarded one-time copy`

Show examples of explicitly declared, relocated customer paths, including both
the user mount and physical Jail-store target:

- `/checkpoints`: `/mnt/jail/checkpoints` to
  `/mnt/jail/shared/checkpoints`
- `/customer`: `/mnt/jail/customer` to `/mnt/jail/shared/customer`

Optionally annotate that the copy Job sees these same paths as
`/store/checkpoints -> /store/shared/checkpoints` and
`/store/customer -> /store/shared/customer`. Never show a copy target outside
`/mnt/jail`.

Label this lane:

`Declared paths only - non-overlapping target - writer hold - verified copy`

Show the copy arrow only in this orange lane. Do not draw copy arrows for the
automatic in-place `/home`, `/data`, `/scripts`, or `/models` paths. `/home` may
appear in the orange lane only as a clearly marked example of a deliberate,
explicit relocation.

Add this warning:

`cxcli does not infer arbitrary legacy-rootfs folders`

#### Panel 3 - Populate Passive Slot and Pass Pre-Switch Guards

Show a Kubernetes populate-Jail Job running the target populate-Jail image and
writing only to `slot-b` mounted at `/mnt/jail`.

At the top-left of this panel, add a compact completed pre-Jail inset:

`Dual-JailedConfig bridge complete: exact source writer original -> 0 -> original; whole Jail classified target-canonical or source-legacy-safe; target CM -> source CM exact CAS; all files + direct Login Slurm health verified`

Make clear this earlier source-writer fence is completed before passive-slot
population and is different from the target-only seed pulse in Panel 4.

Show a small blue compatibility barrier beside the shared Jail:

- `Compatible legacy Slurm config remains in the Jail`
- `Target SConfig writer fenced at size 0`
- `Zero old or target SConfig writer pods`

The target Soperator manager may reconcile the target cluster while this fence
is active, but a manager-regenerated target ConfigMap must not be written into
the shared Jail until the old-rootfs consumers have moved. Do not show the
SConfig writer running in this panel. Show that cxcli has checkpointed hashes
for the complete generated ConfigMap payload, not only `slurm.conf`.

For the green in-place/no-copy lane, keep the existing non-SConfig consumers
connected to `legacy-rootfs` while this Job runs and show at least one ready
Login Service backend. Show both source and target SConfig writers stopped.

For the orange explicit-copy lane, show that cxcli first applies
`maintenance=downscale`, holds consumer writers, performs and verifies the
declared copy, and then populates `slot-b`. Do not show a ready Login backend or
continuous endpoint promise during this intentional writer hold.

Show a compact checklist:

- Slurm job-policy gate
- Passive-slot capacity
- Declared path and PVC identity
- Persistent-mount readiness
- At least one ready Login Service backend, in-place/no-copy lane only
- `maintenance=downscale` writer hold and copy digest verified, explicit-copy
  lane only

Do not show an accounting rootfs change.

#### Panel 4 - Switch Desired Active Slot

Show one prominent desired-state arrow:

`legacy-rootfs` to `slot-b`

Show the active decision updating:

- Controller rootfs reference
- Login rootfs reference
- Canonical `jail` alias for REST and SConfigController
- Jail PVC for every worker NodeSet

Keep the target SConfig desired size at 0 during this desired-state switch. Show
the canonical `jail` alias moving to `slot-b`. Immediately after the exact
slot-b SConfig Deployment rebind, add a small guarded seed sequence before any
consumer-readiness check:

`Pause exact manager -> exact-CAS compatible all-file ConfigMap -> target-SA SConfig 0 -> 1 -> 0 on slot-b -> verify slot-b digest -> prove zero writer pods -> restore exact manager with target SConfig CR still size 0`

Use a narrow blue dashed arrow for this temporary pulse. It is not the final
SConfig release: the target desired size stays 0, the full target config is not
restored, and the writer must be back at zero before Panel 5 begins. The
compatible config already present in `legacy-rootfs` continues serving old
consumers while the pulse seeds the same compatible config into `slot-b`. Show
the manager restored only after the zero proof; its restored reconciliation
must not change the target SConfig CR desired size from 0.

Show the persistent paths attaching to `slot-b`.

Keep accounting gray and outside the Jail data-plane switch. Label its SQL
handoff as an already completed, separate chart-takeover step with both writers
fenced, target-version schema proof, stdin-enabled import, and exact
marker/history verification. Show only
accounting revalidation here because alias reconciliation may restart its
otherwise Jail-independent pod.

#### Panel 5 - Controlled Consumer Rollout

Show a temporary mixture of old and replacement workloads:

- Old workloads connected to the previous source in gray
- Replacement workloads connected to `slot-b` in green

First show that the temporary seed pulse has ended with target SConfig desired
size 0 and zero writer Pods, and that the exact manager is restored at its
checkpointed replica count. Then show controller, login, REST, and every worker
NodeSet verified on `slot-b` while the normal SConfig writer contract remains
fenced. Then show a narrow second step:

`Restore target SConfig size -> start target-SA SConfigController on slot-b ->
verify checkpointed full target config and exact digest in Jail`

Show the Login Service in front of the login workloads. In the green
in-place/no-copy lane, retain at least one green ready backend through the
rolling handoff. In the orange explicit-copy lane, show Login readiness being
restored after the intentional writer hold and slot switch; do not promise a
ready backend during the hold.

Add these four notes:

- `In-place/no-copy: ready Login endpoint maintained through rolling handoff`
- `Explicit copy: writers held; Login readiness restored after switch`
- `Established TCP sessions are pod-bound and do not migrate between pods`
- `No full target config is written into the Jail while any verified consumer
  still uses legacy-rootfs`

Do not claim that an established SSH connection moves from one pod to another.

Show `/home`, `/data`, `/scripts`, `/models`, and declared persistent paths
remaining attached throughout the rollout.

#### Panel 6 - Validate, Resume, and Retain Rollback

Show every Jail consumer connected to active `slot-b` in green:

- Controller
- Login
- REST / `slurmrestd`
- SConfigController
- Every worker NodeSet

Show SConfigController running with the target service account and writing the
checkpointed full target Slurm config and exact digest only after it is itself
on `slot-b`. Then show the desired OpenMetrics setting restored as a later
validation step, not as part of the slot-selection arrow.

Show the previous rootfs source in orange as retained rollback state.

Show this validation checklist:

- Active alias and PVC bindings verified
- Persistent mounts verified
- `scontrol ping`
- `sbatch --test-only`
- `sacctmgr` / `sacct`
- One bounded live Slurm smoke job
- User partitions resumed

Keep accounting outside the rootfs switch and label its accounting validation
as a separate check.

Add this footer:

`Later Jail Upgrades repopulate the passive slot and alternate slot-a <->
slot-b; persistent paths are remounted, not recopied.`

### Final Accuracy Checklist

Before generating, confirm all of the following:

- Controller is shown as a Jail consumer.
- Exactly two controller pod cards are shown: `controller-0` and `controller-1`.
- Login is shown as a Jail consumer.
- Exactly two login pod cards are shown: `login-0` and `login-1`.
- REST / `slurmrestd` is shown as a Jail consumer when enabled.
- SConfigController is shown as a Jail consumer.
- Exactly two example worker NodeSet cards are shown: `worker-0` and `worker-1`.
- The worker pair is labeled as applying to every configured worker NodeSet
  without adding a third worker card or implying a fixed cluster-wide count.
- The target SConfig writer is shown at size 0 with zero writer pods while any
  old-rootfs consumer remains.
- Immediately after the desired-state switch and before consumer readiness, a
  bounded target-SA `0 -> 1 -> 0` pulse seeds the checkpointed compatible
  all-file config into exact slot-b, verifies it, and returns to zero.
- After that exact zero proof, the exact manager is restored at its checkpointed
  replica count while the target SConfig CR desired size remains 0.
- Controller, login, REST, and every worker NodeSet are verified on `slot-b`
  before the target SConfig writer is restored.
- SConfigController is started with the target service account on `slot-b` and
  its checkpointed full target Jail config and exact digest are verified before
  final rootfs handoff completion.
- The desired OpenMetrics setting is restored only after the complete rootfs
  consumer handoff.
- Accounting is explicitly excluded from the Jail slot switch.
- `/home`, `/data`, `/scripts`, and `/models` use the green in-place, no-copy
  path for external first adoption.
- Only explicitly declared, relocated paths use the orange one-time-copy path.
- No arbitrary customer folder is shown as automatically discovered.
- Each pod has one active rootfs source; no pod mounts both slots as active root
  filesystems.
- Continuous Login Service readiness is promised only for the in-place/no-copy
  lane; the explicit-copy lane shows its intentional `maintenance=downscale`
  writer hold and later readiness restoration.
- The Login Service continuity statement does not promise migration of an
  established TCP or SSH session.
- First adoption is `legacy-rootfs` to `slot-b`; later upgrades alternate
  `slot-a` and `slot-b`.
- Accounting SQL dump/import is shown as a prior, separate chart-takeover
  handoff with its guarded schema/import proof, not as part of the Jail alias
  switch.

After I approve the layout plan, generate one image and return no explanatory
prose with it.
