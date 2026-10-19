# Performance Engineering Courses

**[Explore the courses](https://nebius.github.io/nebius-ps-services/courses/index.html)**

The website introduces eight courses, an Advanced Labs collection, and one shared
Lab Guide, with a suggested reading order and direct links to every resource.
Soperator uses text and worked questions, GPU Performance Tools is a reference
without exercises, PyTorch for GPU Performance Engineering uses visual worked
examples without labs or practice, and Advanced Labs teaches through its practical guides.

Start with [Soperator](soperator/index.html), use the [Lab Guide](lab-guide.html)
to prepare for practice, then read [GPU Performance Tools](gpu-performance-tools/index.html)
and [PyTorch for GPU Performance Engineering](pytorch-gpu-performance-engineering/index.html)
before [GPU Fundamentals](gpu-fundamentals/index.html) and
[GPU Performance Optimization](gpu-optimizations/index.html). The tools course
introduces the commands and evidence used in practical labs. The three-hour PyTorch reading course
builds tensor-reading and optimization reasoning with small commented examples;
neither reading course needs a cluster. Continue with
[LLM Training](llm-training/index.html), [LLM Inference](llm-inference/index.html),
or [Custom CUDA Kernels](custom-cuda-kernels/index.html); these specializations
are independent. [Advanced Labs](advanced-gpu-communication/index.html)
owns the multi-GPU, multi-node experiments.

[Browse the courses catalog](index.html)

Each practical course provides complete lab sources, commands and a combined
Grafana dashboards, Small and Large results ZIP. Use the ZIP for comparison;
use `sync-labs.sh` to copy runnable sources to the cluster. This guide owns shared
setup, submission and access; each lab explains its experiment and results.

## How to set up the lab

[Read this guide online](https://nebius.github.io/nebius-ps-services/courses/lab-guide.html).

The browser edition is generated from this README and contains the same instructions.
**Workstation** means your computer; **Login node** means after SSH. Slurm allocates
GPU workers to execute workloads. Commands below use Bash and the default
`$HOME/courses` destination on the login node; adjust that path if you selected
another sync destination.

For a new environment, follow setup in order. If the cluster and tools are
already installed, [synchronize the sources](#synchronize-the-sources),
[prepare the available labs](#lab-preparation), then [run a lab](#how-to-run-the-labs).
Normal synchronization configures Grafana publication before preparation.

### Prepare your workstation

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), Git, kubectl,
SSH and rsync. Install and configure the [Nebius CLI](https://docs.nebius.com/cli/install)
for your project. The login account needs SSH-key access and storage shared with
workers at the same path.

Clone the sources and install cxcli in its own Python 3.12 tool environment.
Editable installation uses this checkout's source. `update-shell` adds uv's command
directory to future shells; the PATH line enables it in this shell.

```bash
git clone https://github.com/nebius/nebius-ps-services.git
cd nebius-ps-services
uv tool install --python 3.12 --editable ./services/nebius-cxcli
uv tool update-shell
export PATH="$(uv tool dir --bin):$PATH"
nebius-cxcli --version
cd courses
export COURSES_ROOT="$PWD"
```

### Create or select Soperator

Soperator runs Slurm on Kubernetes. The five ordinary GPU courses use one full
H100 per allocation, with Multi-Instance GPU (MIG) partitioning disabled. Prepare
two one-H100 workers, or reuse the advanced cluster with one-GPU allocations. Advanced communication needs two eight-H100
SXM workers, local NVLink/NVSwitch and active InfiniBand. TCP/IP alone does not
qualify that route. Check quota and hardware availability before provisioning.

Run the wizard, then use its printed configuration path and exact target name.
Keep local monitoring and persistent `/data` storage for profiler reports.
`render` generates deployment artifacts; `deploy` applies the configuration.
For an existing prepared cluster, reuse its accepted configuration instead.

```bash
nebius-cxcli soperator create ./deployments
export CLUSTER_CONFIG='<absolute path to config.yaml>'
export CLUSTER_TARGET='<exact configured Soperator target>'
nebius-cxcli render "$CLUSTER_CONFIG"
nebius-cxcli deploy "$CLUSTER_CONFIG"
```

Save Kubernetes credentials to a private kubeconfig. Use this deployment's cluster
ID and select its context explicitly. Normal synchronization saves this kubeconfig and context for both monitoring and
login-node discovery; later changes to the current context do not redirect it.
See [Nebius cluster connections](https://docs.nebius.com/kubernetes/connect).

```bash
export KUBECONFIG='<absolute path to this cluster kubeconfig>'
nebius mk8s cluster get-credentials --id '<cluster ID>' --external --kubeconfig "$KUBECONFIG"
export CLUSTER_CONTEXT='<context for this cluster>'
kubectl config use-context "$CLUSTER_CONTEXT"
kubectl config current-context
```

### Install profiling and Grafana

**Workstation:** install the paired Nsight tools and private viewers before
monitoring changes or dashboard imports. Profiling installation requires an accepted
deployment matching the configuration and rendered state.

```bash
nebius-cxcli soperator profiling install "$CLUSTER_CONFIG" --target "$CLUSTER_TARGET" --interactive
```

First-time credentials use masked prompts. Valid credentials are reused automatically;
use `--no-interactive` when a rerun must require existing credentials. The installer
prints connection instructions. For later access, use the
[show commands](#browsing-grafana-and-nsight-profilers).

Review pending project changes before installing Grafana: this command saves
monitoring settings, regenerates artifacts and runs the **whole project deployment**.
It installs Grafana and Pushgateway and configures the native VMAgent results scrape.
Keep metrics storage **local**; **both** also supports course readback. Remote-only
storage is insufficient. Reuse an accepted installation if already configured.

```bash
nebius-cxcli grafana install --config "$CLUSTER_CONFIG" --target "$CLUSTER_TARGET" --pushgateway
```

### Import course dashboards

Connect using the [Grafana browsing instructions](#browsing-grafana-and-nsight-profilers).
In Grafana use **Dashboards → New → New folder** to create `course-gpu-fundamentals`,
or select its existing folder. Copy the UID from `/dashboards/f/<uid>/...` in the
address bar; the folder name is not its UID.

```bash
export COURSE='gpu-fundamentals'
export COURSE_GRAFANA_FOLDER_UID='<observed folder UID>'
cd "$COURSES_ROOT/$COURSE"
nebius-cxcli grafana import ./reference/grafana --recursive \
  --config "$CLUSTER_CONFIG" --target "$CLUSTER_TARGET" \
  --folder-uid "$COURSE_GRAFANA_FOLDER_UID"
```

In the wizard, select the installed local metrics datasource for
`course-soperator-metrics`. The import saves dashboard declarations in the deployment
project and installs them immediately. After syncing changed dashboards, repeat
the import with `--overwrite` for an intentional update. Open the imported dashboards to inspect their contents. Repeat for other
courses with a separate `course-<course-name>` folder. Publishing measured values
is a later step; empty panels do not prevent the first GPU check.

### Synchronize the sources

**Workstation:** after platform setup and dashboard import, run the two
[cxcli show commands](#browsing-grafana-and-nsight-profilers) and follow their
printed access instructions. Keep any forwarding terminals open on the laptop;
`show` itself does not start forwarding.

```bash
cd "$COURSES_ROOT"
./sync-labs.sh
```

Normal sync prepares its pinned workstation dependencies in an isolated uv
environment, checks the installed monitoring services, saves private connection
settings and transfers them with the course sources. It ensures Python 3.12 and
venv support on the login node, then opens SSH. Source and settings verification
must succeed before the shell opens. Sync rejects an incomplete setup toolset
before contacting the destination. Remote-only results and runtime history are
preserved.

First use reads `CLUSTER_CONFIG`, `CLUSTER_TARGET`, `KUBECONFIG` and
`CLUSTER_CONTEXT` from the setup terminal, prompting for missing values. The
private profile remembers validated inputs, SSH options and a generated workspace
identifier, so later syncs work in fresh terminals without restoring exports.
Use `--configure` to revisit setup inputs. The same explicit kubeconfig/context
is used for monitoring and login discovery.

Use `./sync-labs.sh 'user@<login address>'` for another account or an explicit
endpoint. `--identity`, `--port` and `--dest` apply to both sources and settings;
see `./sync-labs.sh --help`. `--dry-run` previews and `--sync-only` transfers only
sources: neither installs dependencies, changes monitoring settings or opens a
shell. `--local-only` permits normal sync to an unconfigured destination without
publication. It refuses a destination already configured for publication; use
`COURSE_AUTO_PUBLISH=0` to opt out for an individual job.

**Login node:** follow [Lab Preparation](#lab-preparation) to prepare all available
labs with one command, then read its completion summary before submitting a lab.
Setup initializes configured publication when its dependencies are ready, even
if unrelated components fail. No separate monitoring command,
exports, `source` or `scp` is required. Repeated preparation preserves installed
software, publication identity, sequence numbers, journals and results.

A successful setup confirms prepared software and publication configuration;
hardware and live monitoring qualification remain separate. Unconfigured setup
keeps results local. If settings arrive later through normal sync, rerun the same
preparation selection. Settings require the versioned monitoring identity written
by normal sync; unmarked settings are rejected without rewriting them. Invalid
settings or a changed cluster/backend binding fail without resetting history.
If delivery acknowledgment is interrupted, sync checks
the destination digest; an unresolved result blocks login until a repeat sync
confirms delivery.

## Lab Preparation

A **lab runtime** contains the Python environment, libraries, tools and model
files needed by a lab. Prepare software on the **login node** before submitting
work; jobs restore the saved runtime and never install dependencies.

### Before running preparation

Use the synchronized sources on the supported **Ubuntu 24.04 x86_64 login
node**, with Python 3.12 and storage shared with workers at the same path.
Normal `sync-labs.sh` prepares Python and opens SSH; `--sync-only` does neither.
Downloads and builds need network access and enough shared storage for packages,
model weights, compiled libraries and uncompressed container images. Image scratch
files also use the shared course filesystem. Missing operating-system packages
require root or passwordless sudo; otherwise ask the cluster owner to install them.

Default preparation includes Apptainer and the cached images for the optional
container exercises. A privileged root Soperator jail needs existing mount-namespace
and chroot capabilities; a constrained jail needs an administrator-provided supported
session. Preparation grants no privileges and does not replace cluster drivers,
system CUDA, Slurm, Nsight or Grafana. Worker device isolation, nested-container
support and profiler access still require the corresponding lab's checks.

### Prepare all available labs

After synchronizing the sources and opening SSH, run:

```bash
python3.12 "$HOME/courses/tools/lab-setup.py"
```

The command discovers the complete catalog from its own location, even if your
terminal is inside a course directory. In a standalone practical course copy,
run `python3.12 tools/lab-setup.py` from that course to prepare its available
labs and dependencies. Reading courses have no executable labs and are skipped.

An ASCII spinner shows the current phase/component, completed-component count,
elapsed time and log location. Redirected output uses periodic text progress.
Downloads, compilation and image preparation can take time; setup gives no time
estimate. Detailed output stays in private `.runtime/setup-logs/` files.

Preparation creates private result, log and job directories before Slurm needs
them. Reruns preserve existing files, tighten safe owned result directories to
`0700`, and reject symlinks, foreign ownership or shared-write directories.

### What gets prepared

The default selection includes every declared lab variant, profiling path and
auxiliary launcher in the available courses. Shared dependencies and pinned model
snapshots are reused; incompatible packages retain separate environments.
Setup also builds native extensions inside those environments; no manual
environment activation or compiler configuration is needed.

| Software | Purpose |
| --- | --- |
| Ordinary Python/PyTorch environments and publication tools | Fundamentals, optimization, training, inference and ordinary advanced experiments |
| Managed native CUDA toolkit, build tools and pinned CUTLASS sources | Compile CUDA C++ exercises without running GPU checks or replacing system CUDA |
| Fabric tools, NCCL tests, NIXL, Megatron Bridge and Dynamo | Applicable advanced communication, distributed training and serving experiments |
| Native vLLM and AIPerf in separate pinned environments | Inference engines, clients and benchmarking |
| Isolated Transformer Engine/PyTorch and native build dependencies | Training Lab 22's FP8 versus higher-precision experiment |
| Apptainer and immutable CUDA, TensorRT-LLM and Dynamo images | Optional container learning activities |

Small-model exercises share the pinned Qwen2.5-0.5B snapshot; speculative serving
adds the 1.5B model, and relevant Dynamo labs share Qwen3-8B. These labels indicate
approximately half a billion, 1.5 billion and eight billion parameters. The model
artifact audit retrieves metadata at execution time without downloading weights.

Container exercises remain optional activities for the learner, but their software
is included in default preparation. Setup builds images on the login node without
GPUs; jobs use the recorded immutable image identity. Normal execution, server
startup, warmup, numerical agreement and profiler captures remain lab work.

### Prepare without containers

To defer container image downloads and builds, run:

```bash
python3.12 "$HOME/courses/tools/lab-setup.py" --exclude containers
```

This prepares native software and skips runtimes that require an image, including
their image-only dependencies. Add `--plan` to preview the selection. The summary
marks those runtimes **excluded**; their container exercises still need preparation
before use. Shared native dependencies remain included. Existing images and runtime
records are preserved. Later, run the default command to prepare the omitted
software; valid completed components and images are reused without downloading or
building them again.

### Read the completion summary

The course summary reports **installed**, **reused**, **hardware-skipped**,
**excluded**, **failed** and **dependency-blocked** runtimes. The aggregate report is
`.runtime/setup.json`; component and runtime receipts remain the authority for
activation. A reused runtime passed integrity checks against its current inputs.

Preparation checks configured Slurm capacity without allocating workers. Unsupported
subsets are explicitly hardware-skipped, while unknown hardware remains unverified.
Advanced multi-node experiments still need the two eight-H100 workers described
above; a smaller cluster may skip those runtimes.

Native components are prepared first, followed by images and their dependents in
an isolated worker. Ordinary failures block their dependents while independent
components continue. A container namespace failure preserves completed native
work. Unsafe package plans, broken package-manager state, invalid shared state
or failed source verification stop preparation.

Installation or configuration failures return a nonzero exit status. Successfully
prepared labs remain usable: inspect the report and the named log before choosing
a lab. Configured automatic publication initializes when its prepared dependencies
are available, even if an unrelated component failed. Publication identity and
history are preserved. Without private monitoring settings, results stay local;
normal laptop synchronization delivers those settings.

**Setup complete means software preparation, not a passed GPU, model, fabric or
profiler check.** Continue with [basic execution](#check-basic-gpu-execution) and
then the selected lab's native submission and qualification steps.

### Safe reruns and targeted repair

Run one setup command at a time. All components share one `.runtime` installation
store, cache and setup lock. Rerun the same command after synchronization, source
edits or an interrupted installation. Valid completed generations, models and
images are reused; unchanged successful reruns perform integrity checks without
package installation, downloads, compilation or image rebuilds. Existing results,
logs, publication sequences and journals are preserved.

Preview all preparation without writes or platform checks:

```bash
python3.12 "$HOME/courses/tools/lab-setup.py" --plan
```

The plan is JSON and does not prove readiness. For a targeted repair, use `--lab`
or `--launcher`, with a course-qualified identity to avoid ambiguous names. Lab
IDs omit the source extension; launcher names include `.sbatch`:

```bash
python3.12 "$HOME/courses/tools/lab-setup.py" --lab custom-cuda-kernels/01_vector_add --plan
python3.12 "$HOME/courses/tools/lab-setup.py" --lab custom-cuda-kernels/01_vector_add
python3.12 "$HOME/courses/tools/lab-setup.py" --lab gpu-fundamentals/01_cpu_gpu_crossover --profiler all
python3.12 "$HOME/courses/tools/lab-setup.py" --launcher llm-inference/30_engine_profile.sbatch --experiment trtllm
```

Targeted commands default to normal execution with the default experiment. Match
`--experiment`, `--profiler`, `--device` and, for sanitizer experiments,
`--sanitizer` to the selected lab; these flags require `--lab` or `--launcher`.
`--profiler all` prepares diagnostic runtimes without running captures. Jobs that
report missing or stale software print the exact repair command; run it on the
login node, then submit a new job. After editing a kernel, rerun its targeted
setup to compile it; correctness, device compatibility and sanitizer checks still
run in the lab.

`--exclude containers` also applies to targeted preparation. If the selected
target needs only a container runtime, setup reports the conflict before making
changes; omit the exclusion to prepare that target.

Inspect `.runtime/setup-logs/` on failure. Package dependency or repository
conflicts need the cluster owner's repair; setup does not silently downgrade
libraries. Cancellation stops new work and waits for an active system-package
transaction to finish before releasing the lock. Finalization checks committed
receipts so a stopped rerun keeps previously prepared, unchanged software usable.
Rerun after cleanup completes.
Remove credentials and private addresses before sharing an error excerpt.

## How to run the labs

### Check basic GPU execution

**Login node:** inspect the partitions and request one GPU explicitly. An asterisk
marks the default partition; add `--partition=<name>` to `srun` or `sbatch` if
required by your site.

```bash
sinfo --format='%P %a %D %G'
srun --nodes=1 --ntasks=1 --gres=gpu:1 nvidia-smi
```

With software preparation complete, submit the compatibility lab through its
normal launcher:

```bash
cd "$HOME/courses/gpu-fundamentals"
sbatch slurm/10_compatibility_stack.sbatch --workload small
```

Use the returned job ID and the [inspection steps below](#select-a-course-and-submit-a-job),
with `10_compatibility_stack` as the lab ID. The lab records PyTorch, driver,
CUDA build/compiler versions and supported GPU architectures, then checks a tiny
GPU tensor operation. Passing correctness establishes basic GPU execution in
that runtime; it does not qualify profilers, containers or the fabric.

`nvidia-smi` reports the driver's supported CUDA level; `nvcc --version` reports
the compiler toolkit, when installed; PyTorch has its own CUDA build version.
They need compatibility, not identical numbers. A prebuilt PyTorch wheel does
not require a compiler. Do not replace Soperator's CUDA/driver stack with a
generic toolkit install; see [NVIDIA compatibility](https://docs.nvidia.com/deploy/cuda-compatibility/minor-version-compatibility.html)
and [Soperator limitations](https://github.com/nebius/soperator/blob/main/docs/limitations.md).

### Select a course and submit a job

**Login node:** work from the course directory. Follow the course's learning
route; lab numbers identify files rather than prerequisite order.

```bash
export COURSE='gpu-fundamentals'
cd "$HOME/courses/$COURSE"
```

Each lab shows its complete source and normal/profiling submissions. Launchers
restore the prepared runtime; their `#SBATCH` directives select logs and reserve
the largest supported allocation. Each `srun` step uses its declared resources,
so some allocated GPUs can remain idle. Preparation creates log directories
before submission.

```bash
# Normal experiment: use its unprofiled measurements
sbatch slurm/01_cpu_gpu_crossover.sbatch --workload small
# Separate diagnostic run: supported Systems then Compute captures
sbatch slurm/01_cpu_gpu_crossover.sbatch --workload small --profiler all
```

`--workload small|large` chooses the lab's workload preset. `--profiler all`
runs supported diagnostic stages, **not the normal benchmark**; an inapplicable
profiler is recorded as skipped. Use `--profiler nsys` or `--profiler ncu` for a
single supported capture. Inspect the normal run before investigating it, then
repeat an unprofiled run to evaluate a change.

Each submission returns its own job ID. Submission is not completion: use
`squeue` for queue progress and `sacct` for job/step state and exit codes.
After the selected job finishes, inspect its summary:

```bash
JOB_ID='<job ID returned by sbatch>'
sacct -j "$JOB_ID" --format=JobID,State,ExitCode
cat "results/01_cpu_gpu_crossover/jobs/$JOB_ID/result.json"
```

Use the matching lab ID for another job. `result.json` records requested and
actual scope, stage outcomes, correctness, measurements, report paths and separate
publication status. Require successful scheduler completion **and** passing lab
checks. Missing or incomplete results are not a pass.

On failure, inspect `results/LAB/logs/JOB_ID.out` and `.err`, then the affected
stage under `results/LAB/jobs/JOB_ID/stages/STAGE/`. Keep those files and submit
a fresh job after repair. Original measurement JSON and native reports remain
in the stage directories; the combined summary and profiler timings are not
benchmark-publication inputs.

Use optional selections only where the lab documents them:

| Selection | Purpose |
| --- | --- |
| `--experiment trials` | Independent trials in the three capstones |
| `--experiment two-node` | Distributed-scaling variant |
| `--experiment aiperf` | Streaming-client benchmark |
| `--experiment trtllm` | Engine-profile container variant |
| `--experiment sanitizer --sanitizer memcheck` | CUDA check; alternatives are `racecheck`, `initcheck`, `synccheck`; cannot combine with Nsight |
| `--device cpu` or `--device cuda` | Learning/inference basics where supported |

Put program options after `--` when they overlap launcher options, and match
explicit device/topology choices to the capture. Fabric labs may also require
qualified `SERVER_HCA`/`CLIENT_HCA` interfaces or `TRAINING_REFERENCE`/`CONTEXT_REFERENCE`
checkpoints. Follow the owning lab: a checkpoint must come from its matching
`--reference-only` run and printed `final-weights.pt`, not arbitrary model weights.

### Keep existing results

New jobs use `results/LAB/jobs/JOB_ID/` and `results/LAB/logs/`; earlier results,
reports and published bundles remain unchanged. Optional history organization,
run from the course directory, makes verified copies without moving originals:

```bash
python3.12 tools/lab-setup.py organize-history --course-root "$PWD"
```

Copies go under `results/history/`. The command reports unattributed files and
rejects conflicts. History never counts as evidence of a fresh run.

### Publish lab results

With [monitoring configured by synchronization](#synchronize-the-sources) before
preparation, normal `sbatch` jobs publish eligible results automatically.
`publish_results.py` validates original records and sends declared metrics to
Pushgateway; VMAgent scrapes them into local VictoriaMetrics for Grafana.
Unprofiled benchmark timers measure the experiment; sampled GPU/node telemetry
provides context.

![Course measurements and GPU telemetry flowing to Grafana](docs/grafana.png)

Arrows show data flow. VMAgent initiates scrapes; Grafana initiates queries.

The job's `result.json` records publication separately from execution. Missing
monitoring or failed publication does not invalidate a benchmark. Acknowledged
writes may appear later if readback was not confirmed. Set `COURSE_AUTO_PUBLISH=0`
in the submitted environment to disable automatic publication. All courses in
one prepared root share an installation identity and ordering state.

For independent monitoring qualification, maintainers use the existing
`verify_monitoring.py` tool to check live services, results scraping and worker
telemetry. Synchronization configures publication; successful jobs and exact
metrics readback establish whether results are visible.

**Login node, to retry publication for a registered job:** select the matching
course, restore its runtime and use the exact original job ID.

```bash
cd "$HOME/courses/gpu-fundamentals"
source tools/course_env.sh 01_cpu_gpu_crossover --lab
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 01_cpu_gpu_crossover --job JOB_ID
```

Retries preserve run order and cannot replace a newer successful result. Keep
the originals and private publication journal. Jobs from before initialization
cannot be replayed as new latest runs.

For a deliberate baseline/candidate selection, use two eligible original,
unprofiled JSON files from the same lab. The generation is `0` initially;
afterward use the reviewed current selection generation:

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 01_cpu_gpu_crossover \
  --baseline '<original baseline JSON>' --candidate '<original candidate JSON>' \
  --expected-generation '<reviewed generation>'
```

Invalid comparisons, failed correctness and unqualified runtimes are rejected.
Correctness evidence must include a passed check and no failed checks; numerical
diagnostics alone do not establish success.
If delivery is uncertain, the configuration pauses publication while jobs keep
saving results. Do not delete state or repeatedly retry. An operator must stop
outstanding publishers and establish that queued requests have drained or been
cleared through the monitoring owner before using the reported key/generation:

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py \
  --resume-publication KEY --expected-generation N --attest-quiescent
```

The flag records that operator attestation; metrics readback alone cannot prove
quiescence. Recovery revalidates the newest eligible saved candidate, and another
uncertain write keeps publication paused.

### Capture and qualify profiling

Before claiming usable profiling, qualify both workers in the actual execution
runtime. Nsight Systems (`nsys`) traces application activity; Nsight Compute
(`ncu`) collects kernel counters. A version check proves only availability.
`readiness.py` runs a tiny canary, captures Systems/Compute, checks matching
kernels, NVTX and counters, and prints JSON/report paths.

```bash
cd "$HOME/courses/gpu-fundamentals"
source tools/course_env.sh 10_compatibility_stack --lab
source /etc/profile.d/99-nsight.sh
srun --nodes=2 --ntasks=2 --ntasks-per-node=1 --gpus-per-task=1 \
  "$COURSE_PYTHON" tools/readiness.py --workload torch
```

Require one full GPU visible to each task. If an eight-GPU worker exposes other
devices to `nvidia-smi`, have the owner qualify device isolation;
`CUDA_VISIBLE_DEVICES` alone does not establish that boundary. Repeat the check
in each runtime you intend to profile. After shared preparation, activate native
CUDA Lab 01's runtime and use the CUDA canary, which compiles a tiny kernel with the
managed toolkit:

```bash
cd "$HOME/courses/custom-cuda-kernels"
source tools/course_env.sh 01_vector_add --lab
source /etc/profile.d/99-nsight.sh
srun --nodes=2 --ntasks=2 --ntasks-per-node=1 --gpus-per-task=1 \
  "$COURSE_PYTHON" tools/readiness.py --workload cuda
```

For native vLLM, activate the prepared serving runtime so the canary uses the
engine's PyTorch and libraries:

```bash
cd "$HOME/courses/llm-inference"
source tools/course_env.sh 30_engine_profile --lab
source /etc/profile.d/99-nsight.sh
srun --nodes=2 --ntasks=2 --ntasks-per-node=1 --gpus-per-task=1 \
  "$COURSE_PYTHON" tools/readiness.py --workload torch
```

These checks qualify a tiny capture in the selected runtime, not a full model
service or every kernel. Save each pair of result paths before switching
runtimes and publish that pair from its owning course directory. Optional
container variants need their own runtime and capture checks from the owning
lab; native qualification does not qualify a container.

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab environment_readiness \
  --baseline "${WORKER_ONE_RESULT:?first worker JSON path}" \
  --candidate "${WORKER_TWO_RESULT:?second worker JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

Open **Environment readiness** in Grafana and require the matching publication
generation. Check both workers' canary results for kernels, NVTX and Compute
counters. These standalone readiness checks retain their reports at the printed
private paths; automatic viewer delivery applies to the lab `sbatch` runners.
Qualify browser viewing with a fresh profiling lab job in each required runtime.

For profiler selection, native flags, NVTX regions and report interpretation,
use [GPU Performance Tools](gpu-performance-tools/index.html). A missing tool,
permission denial, timeout or missing/empty native report fails the stage and
stops later stages. Preserve the failed job's evidence, repair its runtime or
permissions through the owning preparation/administration path, then submit a
fresh job. Never treat a nonempty report alone as proof that it captured the
experiment's measured work.

### Check specialized runtimes in the labs

Complete [Lab Preparation](#lab-preparation) with the single setup command and
check its completion summary, then
follow the owning lab to establish that its runtime works on allocated hardware:

- [Inference serving](llm-inference/README.md#serving-runtime-preparation):
  native vLLM/AIPerf and pinned models; TensorRT-LLM is an optional container variant.
- [CUDA Lab 13](custom-cuda-kernels/reference/labs/13_h100_preflight.md):
  managed native toolkit, compiled SM90/SM90a binaries, CUTLASS and device checks.
- [Training Lab 22](llm-training/reference/labs/22_transformer_engine_fp8.md):
  isolated Transformer Engine and FP8 numerical checks.
- [Advanced runtime preparation](advanced-gpu-communication/README.md#runtime-preparation):
  fabric, collective and vendor runtime qualification.

### Optional agent-assisted runs

From the workstation's `courses/` directory, install the project-scoped skill:

```bash
npx --yes skills add ./skills/run-labs -a codex --yes
```

This optional command requires Node.js/npm. The first `--yes` skips npm's prompt;
the last skips the skill wizard. Do not add `--global`; use `-a claude-code` for
Claude. On a fully qualified environment, `$run-labs run --course gpu-fundamentals`
runs both workload presets and collects verified evidence. The
[run-labs skill](skills/run-labs/SKILL.md) validates prepared runtimes and owns recovery, resume and
lab-specific recipes. Use that workflow for campaign selection and recovery details.

## Browsing Grafana and Nsight Profilers

### Connect from your workstation

During one-time setup, run these commands with the configuration and target
selected above. For later access from a new terminal, reuse those same setup
values. The commands check installed services and print connection instructions:

```bash
nebius-cxcli grafana show --config "$CLUSTER_CONFIG" --target "$CLUSTER_TARGET"
nebius-cxcli soperator profiling show "$CLUSTER_CONFIG" --target "$CLUSTER_TARGET"
```

1. Copy each printed `kubectl port-forward` command into its own workstation
   terminal and keep it running: one for Grafana and one for each Nsight viewer.
   Keep the explicit cluster selection, loopback address and both HTTP/TURN port
   mappings supplied for each Nsight viewer.
2. In another terminal, run the printed password-retrieval commands. Grafana has
   its own credentials; the two Nsight viewers share the profiling password.
   Keep the displayed passwords private.
3. Open the browser URLs printed by cxcli. Use the printed Grafana username and
   the Nsight username chosen during installation (`admin` by default).

The `show` commands print instructions; they do not start forwards or display
passwords. They require access to the cluster and its credential Secrets. If a
service is unavailable, resolve the reported setup problem before continuing.
If a forward exits, rerun `show` and start its printed command again; forwarding
ends when the selected Pod terminates. See the
[kubectl port-forward reference](https://kubernetes.io/docs/reference/kubectl/generated/kubectl_port-forward/).

### Browse Grafana

Open **Dashboards → course-<course-name>**, then the lab dashboard or
**Environment readiness**. If missing, [import the current dashboards](#import-course-dashboards).
Select your workspace, course installation, workload and experiment configuration.

- **Latest results**: newest successful clean run for each supported configuration;
  independent trials remain separate.
- **Diagnostics and run status**: stage outcomes and declared diagnostic measurements.
- **Selected comparison**: the explicitly chosen baseline and candidate; check both
  correctness slots and the publication generation.

Check the source job and completion time, and set the telemetry window to its
experiment interval. Select GPU worker and local GPU index together. Configuration
suffixes identify nodes (`n`), tasks (`t`), GPUs per task (`g`), CPUs per task (`c`)
and, where applicable, workers per node (`w`). Sampled telemetry supplies context;
unprofiled measurements determine performance.

### Browse Nsight Compute and Systems

**Login node:** submit the lab's profiling command. Each successful profiling
stage automatically delivers its completed native reports to the installed
viewer directory and prints `Nsight report: /mnt/reports/...` in the job log.
There is no copy command or additional export to run. The runner discovers the
configured destination from cxcli's installation metadata; `/data/nsight-reports`
is only the default producer root.

Profiling stages activate the installed managed profiler environment automatically,
including in non-login batch shells. Learners do not need to source a profile or
modify `PATH`; the prepared Python and native toolchain remain selected.

Copies become readable by the existing viewer audience after the complete stage
has been verified. Private originals, measurements and logs remain under the
exact job directory. Nested worker and rank paths are preserved.

The job's `result.json` records `viewer_delivery` separately from capture status
and Grafana `publication`. Delivery failure makes the job fail while retaining
successful captures. A completed Systems delivery remains available if Compute
later fails. An `unconfirmed` delivery stops later captures and requires operator
inspection; do not assume a report is ready from capture success alone.

If viewer preflight fails, an operator must rerun the existing profiling install
command and resolve its storage or permission diagnostic before submitting a
fresh profiling job. New installation roots are sticky and shared; existing
unsafe directories require a scoped operator migration. Course jobs never change
these permissions. Normal unprofiled jobs do not require a viewer installation.

Sign in to each printed Nsight URL with the installation username and shared
password obtained above.
In Compute, open the printed `.ncu-rep` path under `/mnt/reports`
and inspect the kernel and counters specified by the lab. In Systems, open the
printed `.nsys-rep`, expand the process tree, CUDA streams and NVTX rows, and zoom
to the measured interval. Follow the lab's inspection steps; initialization-only
activity does not establish that the experiment was captured.

© 2026 Nebius B.V. Free educational material under [Apache License 2.0](../LICENSE).
