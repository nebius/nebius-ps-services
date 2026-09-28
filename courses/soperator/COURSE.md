# Soperator: A Nebius Slurm cluster running on Kubernetes

Slurm schedules cluster jobs; Soperator maintains Slurm on Kubernetes. In six
lessons, learn to request resources, choose batch or interactive execution, and
follow a job from submission to logs and completion.

You need basic Linux shell knowledge and familiarity with CPUs and GPUs; Slurm
and Kubernetes are introduced from first principles. Read in order, or use the
lesson objectives to check your readiness. This prepares you for the GPU
courses' Slurm labs.

Worked examples and questions require no cluster access or setup. To run an
example later, use an authorized cluster's login node and follow its partition,
account and resource limits.

## 1. Slurm turns resource requests into running work

### Objective

Explain what Slurm schedules and distinguish the login, controller and worker
roles in the path from submitting a job to executing it.

### How it works

Slurm is an open-source workload manager developed and maintained by SchedMD.
It gives applications access to cluster resources, starts and supervises their
work, and queues requests when resources are busy. A **job** is a request to
perform work under an allocation of resources and a time limit. Queuing lets
many users share a cluster without manually choosing an unused machine.

The three roles have different responsibilities:

| Role | Responsibility |
| --- | --- |
| Login node | Provides a shell for preparing files, submitting requests and inspecting results. It is not the place for a long training run. |
| Controller | Runs `slurmctld`, the service that tracks resources and jobs and makes scheduling decisions. It does not execute the training workload. |
| Worker, or compute node | Runs `slurmd`, which accepts assigned work. Its `slurmstepd` processes supervise job steps and their input/output. CPUs and GPUs here execute the application. |

A **partition** is a named collection of workers with scheduling rules, such as
a maximum runtime or permitted users. Partitions may overlap. An **allocation**
is the resources granted to a particular job; a partition is not itself a
reservation for you.

For example, suppose two workers each provide one GPU. A request for one GPU
on one node can fit on either eligible worker. A request for two GPUs on one
node cannot fit merely because the cluster has two GPUs in total. The placement
requirement matters as much as the total count.

Your client command contacts Slurm, the controller selects eligible resources,
and the worker executes the assigned processes. The controller coordinates
work; it is not a relay through which all application data and log bytes flow.

### Practice

A submitted job is waiting while its user can still type on the login node.
Which component decides when it starts, and where will its application run?

Answer: `slurmctld` decides when the allocation can be granted. The application
runs on allocated workers, not in the login shell or controller process.

### Mental model

The login node is your workbench, the controller assigns resources, and workers
execute the application. A request must fit an eligible placement, not just a
cluster-wide total.

## 2. Soperator maintains Slurm inside Kubernetes

### Objective

Explain how Soperator's service roles and worker NodeSets relate to Kubernetes
resources without confusing pod placement with Slurm job scheduling.

### How it works

Soperator is Nebius's open-source Kubernetes operator for Slurm. An **operator**
is a controller that continually compares declared configuration with running
resources and reconciles differences. Kubernetes places containers on provisioned machines and manages their
lifecycle; Nebius infrastructure supplies the underlying compute resources.

A Kubernetes **node** is a host for containers. A **Pod** groups containers
that run together on a node. In Soperator, a Slurm worker is represented by a
worker Pod; it is not the same object as the underlying Kubernetes node. A
Slurm job normally runs within an existing worker environment rather than
creating a new Kubernetes Pod for each submitted job.

The component names below follow Soperator 4.1.8, the release used by the course
setup. A **custom resource** extends Kubernetes with a product-specific object.
`SlurmCluster` declares the Slurm cluster. Its `spec.slurmNodes` configuration,
represented by the `SlurmNodes` type in the source, describes service roles:

| SlurmNodes role | What it provides |
| --- | --- |
| `controller` | The `slurmctld` service and its scheduling configuration/state. |
| `login` | Secure Shell (SSH) access and Slurm client tools for users. |
| `accounting` | When enabled, `slurmdbd` records job/resource history in a database. The database may be external or managed through the MariaDB integration. |
| `rest` | When enabled, `slurmrestd` exposes Slurm's REST application programming interface over HTTP for software clients. Shell commands do not require this interface. |
| `exporter` | When enabled, exposes Slurm metrics for monitoring. It is separate from the scheduler and the accounting database. |

A separate **NodeSet** custom resource describes a group of worker Pods,
including their number, placement and container resources. Soperator manages
those workers through Kubernetes StatefulSets, which maintain Pods with stable
identities. Each worker runs `slurmd` and registers its configured capacity with
Slurm. A NodeSet groups worker infrastructure; a Slurm partition groups workers
for scheduling policy. They serve different purposes even when their memberships
match.

The host's physical capacity, the worker container's resource budget and a
job's Slurm allocation are three different quantities. Kubernetes needs room
for system services and other containers, so the worker budget normally leaves
some CPU and host memory reserved. A container CPU limit can throttle its work;
a memory limit can cause an out-of-memory termination. Administrators must
check both Slurm's advertised capacity and the effective worker limits when
investigating a resource mismatch.

Supporting components make that environment usable. Shared storage supplies the
**jail**, the common Linux filesystem seen by users on login and worker Pods.
It helps keep programs and libraries consistent; special paths such as `/tmp`
and device/process mounts remain node-local. A populate-jail job initializes
that filesystem. Persistent storage also holds configuration-dependent state.
**MUNGE** authenticates Slurm messages between trusted components; it does not
replace SSH access controls or a user's scheduling permissions.

Health checks help detect unhealthy workers, which may be drained so new jobs
avoid them. The optional Slurm exporter and NVIDIA Data Center GPU Manager
(DCGM) telemetry provide different views: scheduler state and GPU measurements.
Pod recovery does not by itself restore a training process's lost state; the
application still needs checkpoints and an appropriate restart strategy.

There are therefore two scheduling decisions: Kubernetes places the worker
Pods on hosts, then Slurm assigns application resources within eligible workers.
A running Pod alone does not prove its Slurm worker is available for jobs.

### Practice

A worker Pod is running, but Slurm marks its worker as drained. Should a user
expect a new GPU job to start there?

Answer: no. Kubernetes availability and Slurm eligibility are different states.
Slurm excludes the drained worker from new allocations; the operator or
administrator must investigate the reason.

### Mental model

Soperator maintains the Slurm environment on Kubernetes. SlurmNodes configures
services, NodeSets define workers, and Slurm schedules jobs on the resulting
eligible resources.

## 3. Describe the work before requesting resources

### Objective

Distinguish jobs, steps and tasks, and calculate the resources requested by a
small GPU job without confusing host memory with GPU memory.

### How it works

A **job step** is an execution launched within a job allocation, usually with
`srun`. A **task** is one process that Slurm launches for that step. A task can
create threads or child processes; that does not automatically increase its
allocation. For example, one job can run a preparation step, then a training
step, reusing its allocated resources.

Write down the process layout before choosing flags:

| Option | Meaning |
| --- | --- |
| `--nodes=2` | Request two nodes. |
| `--ntasks=2` | Request two Slurm tasks in total. |
| `--ntasks-per-node=1` | Request one task on each selected node. Use totals that agree when specifying both task options. |
| `--cpus-per-task=4` | Request four configured Slurm CPU resources for each task. |
| `--gpus-per-node=1` | Request one GPU on each node. |
| `--mem=16G` | Request 16 GiB of host memory per node, not GPU memory. |
| `--time=00:10:00` | Set a ten-minute job time limit. |
| `--partition=NAME` | Select an eligible partition. |
| `--account=NAME` | Charge the job to an authorized Slurm account. |

Two nodes, one task per node, four CPUs per task and one GPU per node requests
two tasks, eight CPUs and two GPUs in total. With `--mem=16G`, each node must
supply the requested host memory. These are illustrative requests, not universal
CPU-to-GPU sizing recommendations. A Slurm CPU can represent a core or hardware
thread depending on site configuration.

Increase `--cpus-per-task` or `--mem` when a job needs more of the worker's
available resources. These flags request a larger Slurm allocation; they do
not enlarge the worker container's Kubernetes limits or the underlying machine.
If the worker budget is configured too small, the administrator must correct
that configuration before a larger job can use the host's remaining capacity.

GPU request options have different scopes. `--gpus-per-task=1` ties GPU count
to tasks; `--gpus-per-node=1` ties it to nodes. The older generic-resource form
`--gres=gpu:1` also requests GPUs per node. Use the site's supported form rather
than stacking equivalent flags. Slurm's `--gpus*` options require the appropriate
resource-selection configuration. GPU visibility and binding depend on the
allocated resources and the site's isolation settings; do not overwrite
`CUDA_VISIBLE_DEVICES` to reach unallocated GPUs.

An administrator grants your login identity and Slurm account/partition access.
A Linux group controls filesystem permissions; a Slurm account controls
scheduling/accounting associations. They are not interchangeable. Keep code,
inputs and the required software environment in locations available to workers.
Slurm transfers the batch script, not all files it refers to. Soperator's shared
jail helps with access, but a login node's local temporary file is still a poor
place for a worker's required input.

### Practice

A one-node job requests two tasks, three CPUs per task and one GPU per task.
How many CPUs and GPUs does it need? Does raising `--mem` enlarge GPU memory?

Answer: six Slurm CPUs and two GPUs on that node. Raising `--mem` requests more
host memory; it cannot enlarge a GPU's physical memory.

### Mental model

Describe processes first, then reserve their CPUs, GPUs, host memory and time.
Resource counts and placement must agree, and requesting capacity does not
parallelize the application automatically.

## 4. Choose batch or interactive execution

### Objective

Choose between `sbatch`, `salloc` and `srun`, and explain where commands execute
and where their output is written.

### How it works

`sbatch` submits a script and returns a job ID once Slurm accepts it. Acceptance
is not completion, and the job may wait in the queue. Slurm later runs one copy
of the batch script on an allocated worker. An `srun` inside that script creates
a step and launches its tasks.

Consider this small example, saved as `job.sbatch` in a shared project directory.
It requests one GPU and prints device information; it is not a performance test.
The examples use an authorized default partition/account. If your site has no
suitable default, add its supplied `--partition` and `--account` values.

```bash
#!/bin/bash
#SBATCH --job-name=device-check
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --gpus-per-node=1
#SBATCH --mem=4G
#SBATCH --time=00:05:00
#SBATCH --output=logs/%x-%j.out
#SBATCH --error=logs/%x-%j.err
set -euo pipefail
srun bash -euc 'hostname; nvidia-smi --query-gpu=name,memory.total --format=csv'
```

The example's project directory is `~/slurm-examples/device-check`. Create its
log directory before submitting: Slurm opens output files before the script
body can create directories. `umask 077` makes newly created files private to
your user; it does not change existing files.

```bash
umask 077
mkdir -p ~/slurm-examples/device-check/logs
cd ~/slurm-examples/device-check
# Save the script above as job.sbatch in this directory, then submit it.
sbatch job.sbatch
```

`%x` becomes the job name and `%j` the job ID. For synthetic job ID `12345`,
standard output is `logs/device-check-12345.out` and standard error is
`logs/device-check-12345.err`. The batch working directory defaults to the
submission directory; `--chdir` can select another worker-accessible directory.
Without an explicit output path, Slurm normally writes `slurm-JOBID.out` in that
working directory, which explains stray files after submitting from home.
`#SBATCH` lines are Slurm directives: shell variables such as `$HOME` are not
expanded there. Put directives before executable shell lines.

For an interactive session, **allocation** and **execution** are separate steps.
`salloc` waits for resources and starts a command or shell for the allocation.
Its default shell normally stays on the submitting host; reserving a GPU has
not moved that shell to a worker. `srun` launches the worker-side shell:

```bash
salloc --nodes=1 --ntasks=1 --cpus-per-task=2 \
  --gpus-per-node=1 --mem=4G --time=00:10:00
srun --ntasks=1 --pty bash
hostname
nvidia-smi
exit  # Leave the worker shell.
exit  # Leave the salloc shell and release the allocation.
```

Run these interactively in order, waiting for the allocation before starting
the step. `--pty` supplies a pseudo-terminal for the worker shell. A standalone
`srun` can also request an allocation and execute a command in one operation;
it may wait for resources. In an existing allocation, it normally starts a step
using that allocation. Interactive output returns to the terminal; batch output
follows the configured files. Disconnecting from SSH is not a substitute for
explicitly ending an interactive allocation.

### Practice

A user runs `salloc` successfully and then types `python train.py` at the
resulting login-host prompt. Why is that a problem, and what changes the
execution location?

Answer: a plain command runs in that shell on the login host. `srun python
train.py` launches the application as a worker-side step, assuming the program
and environment are available there. The allocation alone does not move it.

### Mental model

`sbatch` schedules a script, `salloc` reserves resources for an interactive
session, and `srun` launches tasks. Create log destinations before submission
and distinguish an accepted request from finished work.

## 5. Count Slurm tasks and PyTorch workers separately

### Objective

Read a two-node PyTorch launch and calculate how many Slurm launcher tasks and
GPU training processes it creates.

### How it works

A distributed application coordinates multiple processes. A **rank** identifies
one process within that application's group; **world size** is the group's
process count. Slurm and the application's launcher can create different layers
of processes, so their counts need not match.

PyTorch's `torchrun` launches training workers and supplies their distributed
environment. One common layout uses one Slurm task per node, with each task
running a `torchrun` launcher that starts one child worker per local GPU.

For two nodes with eight allocated GPUs each, that layout has two Slurm tasks
and sixteen training processes: two launchers times eight children. It needs
the separate two-eight-GPU environment described in the GPU courses, not the
two-one-GPU base cluster. This example explains launch mechanics, not network
performance or a complete training program.

Read the following as the body of a batch script whose directives request
`--nodes=2`, `--ntasks=2`, `--ntasks-per-node=1` and `--gpus-per-node=8`, plus
appropriate per-launcher CPUs, host memory and time. The application `train.py`
must already implement distributed training and select its local GPU correctly;
this text course does not supply that application.

```bash
mapfile -t hosts < <(scontrol show hostnames "$SLURM_JOB_NODELIST")
export MASTER_ADDR="${hosts[0]}"
export MASTER_PORT=29500  # Choose an available, site-permitted port.
srun --ntasks=2 --ntasks-per-node=1 bash -euc '
  torchrun --nnodes=2 --nproc-per-node=8 \
    --node-rank="$SLURM_PROCID" \
    --master-addr="$MASTER_ADDR" --master-port="$MASTER_PORT" \
    train.py
'
```

Both launchers contact the same first allocated host and port to establish the
process group. That address must be reachable between the workers and the port
must not collide with another job; it is not a public listener to expose on the
internet. The single quotes let each launched shell expand its own
`SLURM_PROCID`, giving launcher ranks zero and one. `scontrol show hostnames`
expands Slurm's compressed node list into individual hostnames.

| Variable | Meaning in this layout |
| --- | --- |
| `SLURM_JOB_ID` | The allocation's job ID. |
| `SLURM_JOB_NODELIST` | The allocated Slurm node names. |
| `SLURM_NTASKS` | Two Slurm launcher tasks, not sixteen training ranks. |
| `SLURM_PROCID` | Each Slurm task's global index, used here as the launcher node rank. |
| `RANK`, `LOCAL_RANK`, `WORLD_SIZE` | Set by `torchrun` for each child: its global index, local GPU/process index and sixteen-process group size. |

A direct Slurm-per-GPU layout is also possible when the application supports
that initialization path. Do not combine sixteen Slurm tasks with eight
`torchrun` children per task: that would start 128 training processes and
oversubscribe the intended sixteen GPUs. `torchrun` does not automatically
translate every Slurm variable into a correct distributed setup.

### Practice

A new cluster has four nodes with four GPUs each. Using one launcher per node
and one child per GPU, what are the Slurm task count and training world size?

Answer: four Slurm tasks and sixteen training processes. Request the four-node
allocation, launch one task on each node, and give each launcher four children.
The total stays sixteen, but the placement and inter-node communication change.

### Mental model

Count launcher processes and training processes separately. Slurm provides the
allocation and launcher placement; `torchrun` provides the child-worker ranks
for the application.

## 6. Follow a job from queue to completion

### Objective

Select the right command for pending, running and completed jobs, then combine
scheduler records with application output before deciding what happened.

### How it works

Start with the question you need to answer. `sinfo` describes partitions and
nodes; `squeue` describes current jobs. Use your own user filter and a small
output format instead of repeatedly requesting the entire cluster state.

```bash
sinfo -o '%P %a %l %D %t %G'
squeue --me -o '%.18i %.12j %.10T %.10M %.6D %R'
```

The `sinfo` columns show partition, availability, time limit, node count, node
state and generic resources such as GPUs. A star beside a partition marks the
default. In the `squeue` example, columns are job ID, name, state, elapsed time,
node count and reason or allocated nodes. These are scheduler facts, not GPU
utilization measurements.

A pending job has not started. `Resources` indicates that the requested
resources are currently unavailable; `Priority` means higher-priority jobs are
ahead; `Dependency` means another condition must finish first. Inspect the
request and reason before changing resource counts. A drained or down worker
is an administrator investigation, not something a learner should force back
into service.

Set `job_id` to the actual ID returned by your submission; `12345` below is a
synthetic example. `scontrol show job` exposes details such as requested
resources, working directory, output paths, state and reason:

```bash
job_id=12345
scontrol show job "$job_id"
squeue --steps --jobs="$job_id"
```

For a running step, `sstat` can report sampled process-memory and CPU statistics
when the site's accounting plugins collect them. `--allsteps` includes the job's
running steps rather than silently selecting one:

```bash
sstat --jobs="$job_id" --allsteps \
  --format=JobID,AveCPU,MaxRSS
```

**RSS** is resident set size, a process-memory measure. `MaxRSS` is not total
GPU memory and is not a sum across ranks. Missing values can mean unavailable
collection, an already-finished step or sampling that missed short work; they
do not prove zero resource use. Follow the output path reported by `scontrol`.
For the previous batch example, once the file exists:

```bash
tail -n 40 "logs/device-check-${job_id}.out"
tail -n 40 "logs/device-check-${job_id}.err"
```

After a job leaves `squeue`, use `sacct` if persistent accounting is enabled.
It reports both the allocation and its steps, including the batch script and
numbered `srun` steps:

```bash
sacct --jobs="$job_id" \
  --format=JobID,JobName,State,ExitCode,Elapsed,AllocCPUS,MaxRSS
```

`COMPLETED` means Slurm observed successful completion; `FAILED`, `TIMEOUT`,
`OUT_OF_MEMORY` and `CANCELLED` require different explanations. `ExitCode`
contains an exit status and signal separated by a colon; `0:0` means neither
indicated failure. Inspect the step records as well as the batch row: a script
that ignores a failed command may still exit successfully. Even a clean exit
does not prove a correct model, valid output or improved performance. Check the
application's own completion and correctness evidence. If accounting is disabled,
retain logs and application artifacts; an empty history is not success.

To stop one of your own jobs, first verify its ID and identity with `scontrol`,
then cancel that exact job:

```bash
scancel "$job_id"
```

Cancellation ends work; it is not a diagnostic read. Avoid user-wide cancellation
when you mean one job. Likewise, launching another `srun` step into a busy
allocation consumes resources and may wait; it is not passive monitoring.
Use queue, accounting and log queries first. Avoid tight polling loops that
unnecessarily load the controller.

### Practice

A job disappears from `squeue`. Its batch row says `COMPLETED`, but an `srun`
step says `FAILED` and the expected result is missing. Was the workload
successful? State the next two things to inspect.

Answer: no success has been established. Inspect that step's exit code and the
application error log; then correct the script's failure propagation before
resubmitting. The controller's completion record cannot replace application
correctness evidence.

### Mental model

Use `sinfo` for capacity, `squeue` and `scontrol` for current state, `sstat` for
available live step statistics, and `sacct` plus logs for history. Always match
the evidence to the exact job and verify application success separately.
