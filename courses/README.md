# Performance Engineering Courses

**[Explore the courses](https://nebius.github.io/nebius-ps-services/courses/index.html)**

The website introduces seven courses, an Advanced Labs collection, and one shared
Lab Guide, with a suggested reading order and direct links to every resource.
Soperator uses text and worked questions, GPU Performance Tools is a reference
without exercises, and Advanced Labs teaches through its practical guides.

Start with [Soperator](soperator/index.html), use the [Lab Guide](lab-guide.html)
to prepare for practice, then read [GPU Performance Tools](gpu-performance-tools/index.html)
before [GPU Fundamentals](gpu-fundamentals/index.html) and
[GPU Performance Optimization](gpu-optimizations/index.html). The tools course
introduces the commands and evidence used in practical labs; no cluster is needed
to read it. Continue with
[LLM Training](llm-training/index.html), [LLM Inference](llm-inference/index.html),
or [Custom CUDA Kernels](custom-cuda-kernels/index.html); these specializations
are independent. [Advanced Labs](advanced-gpu-communication/index.html)
owns the multi-GPU, multi-node experiments.

Where a course includes diagrams, they appear immediately after the passage
they explain, within the owning lesson or practical guide.

Every course ends with one Where to Go Next, one A–Z Glossary, and then
Official references when sources are used. Lessons and performance-tool guides share these course-wide
sections; optional lesson References follow Mental model and come last.

[Browse the courses catalog](index.html)

Each practical course offers one combined Grafana dashboards, Small and Large
results ZIP for comparison, plus a link to this lab setup guide. Use
`sync-labs.sh` to copy the original lab scripts and runtime files to the cluster.

## How to set up the lab

[Read this guide online](https://nebius.github.io/nebius-ps-services/courses/lab-guide.html).

The browser edition is generated from this README and contains the same instructions.
**Workstation** means your computer; **Login node** means after SSH. Slurm allocates
GPU workers to execute workloads. Prepare the cluster once, then each course runtime
you need. Commands below use Bash.

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

Soperator runs Slurm on Kubernetes. The five ordinary GPU courses use one full,
non-MIG H100 per allocation. Prepare two one-H100 workers, or reuse the advanced
cluster with one-GPU allocations. Advanced communication needs two eight-H100
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
ID and select its context explicitly. Check the printed context before synchronization;
no-argument synchronization uses it to discover the login node.
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
project and installs them immediately. Add `--overwrite` only for an intentional
update. Open the imported dashboards to inspect their contents. Repeat for other
courses with a separate `course-<course-name>` folder. Publishing measured values
is a later step; empty panels do not prevent the first GPU check.

### Synchronize the sources

**Workstation:** synchronize the sources and open the login shell. Normal sync
ensures Python 3.12 and venv support in the Ubuntu 24.04 shared jail before
opening SSH. `--dry-run` and `--sync-only` do not install anything. Remote-only
results are preserved.

```bash
cd "$COURSES_ROOT"
kubectl config current-context
./sync-labs.sh
```

Use `./sync-labs.sh 'user@<login address>'` for another account or an explicit
endpoint. See `./sync-labs.sh --help` for SSH key, port and sync-only options.
Synchronization verifies transferred source before retiring known old setup
entrypoints; locally modified old files and historical results are preserved.

After SSH opens, continue with [Lab Preparation Scripts](#lab-preparation-scripts)
to install the runtimes needed by your labs.

## Lab Preparation Scripts

A **lab runtime** is the Python environment, libraries, tools and model files a
lab needs to execute. The preparation scripts build these runtimes for you on
the **login node**, before you submit work to Slurm. You do not need to assemble
a separate package-installation recipe for each lab. Jobs later load the saved
runtime automatically; they never install their own dependencies.

Start with regular preparation, then prepare the specialized labs you intend
to run. You do not need to run all five scripts to begin the courses. The table
shows software preparation groups across the complete catalog, not a required
learning order or a promise that your cluster can run every lab.

| Preparation group | Default labs | When to use it |
| --- | ---: | --- |
| [Regular labs](#regular-labs) | 79 | Ordinary Python experiments across the practical courses |
| [CUDA labs](#cuda-labs) | 13 | Compiling and running the Custom CUDA Kernels labs |
| [Communication labs](#communication-labs) | 10 | Specialized fabric, collective and distributed serving experiments |
| [Serving labs](#serving-labs) | 7 | Running real inference engines and their benchmark clients |
| [Transformer Engine lab](#transformer-engine-lab) | 1 | Training Lab 22's floating-point 8-bit experiments |

### Find your course and lab number

Find the course and the number printed in the lab title. Follow its linked
preparation group for the command and prerequisites. Numbers identify existing
labs; they are not the lesson order. A range such as `01–05` includes both ends.

| Course | Lab numbers | Preparation group |
| --- | --- | --- |
| GPU Fundamentals | 01–05, 07–12 | [Regular labs](#regular-labs) |
| GPU Performance Optimization | 01–05, 07, 09–10, 12, 14–16, 19–20 | [Regular labs](#regular-labs) |
| LLM Training | 01–02, 05–07, 13–14, 21, 24–27, 30–32 | [Regular labs](#regular-labs) |
| LLM Training | 22 | [Transformer Engine lab](#transformer-engine-lab) |
| LLM Inference | 08–09, 16–18, 23–29, 32, 35–36 | [Regular labs](#regular-labs) |
| LLM Inference | 10–11, 15, 20, 30, 33–34 | [Serving labs](#serving-labs) |
| Custom CUDA Kernels | 01–13 | [CUDA labs](#cuda-labs) |
| Advanced Labs: Multi-GPUs Multi-Nodes communication optimization | 01–05, 08–09, 11–26, 28 | [Regular labs](#regular-labs) |
| Advanced Labs: Multi-GPUs Multi-Nodes communication optimization | 06–07, 10, 27, 29–34 | [Communication labs](#communication-labs) |

Soperator and GPU Performance Tools are reading courses with no executable labs or preparation script of their own.
The lookup covers all 110 default labs. For the optional CUDA container exercise,
Inference Lab 30's TensorRT-LLM variant, or Dynamo container preflight, follow
[Optional container exercises](#optional-container-exercises) after choosing that
activity in the lab. Default preparation does not include these containers.

### Before running preparation

Use the synchronized sources on the supported **Ubuntu 24.04 x86_64 login
node**, with Python 3.12 and storage shared with workers at the same path.
Normal `sync-labs.sh` prepares Python and opens this shell. If you used
`--sync-only`, Python has not been bootstrapped by that command.
Downloads need network access and enough shared storage for packages, model
weights and builds. Missing selected operating-system packages require root
or passwordless sudo; ask the cluster owner to provide those prerequisites if
your account cannot install them.

The scripts install only the prerequisites needed by the selected, applicable
runtimes. Cluster drivers, system CUDA, Slurm, Nsight and Grafana are managed
through the earlier cluster setup. Native preparation does not require
Apptainer, the container runtime used for optional exercises.

Preparation inspects configured Slurm capacity without allocating workers.
Unsupported subsets are reported as **skipped**; unknown hardware remains
unverified. A real installation or configuration error returns a nonzero exit
status. **Setup complete means software is installed and configured.** It does
not mean a GPU workload, model warmup, network fabric or profiler capture has
passed. The scripts submit no GPU jobs; those checks happen later inside lab
allocations.

### Regular labs

`regular-lab-setup.py` prepares the 79 labs that use the ordinary course Python
runtimes. This includes the regular experiments in GPU Fundamentals,
GPU Performance Optimization, LLM Training and LLM Inference, plus the
communication-course labs that do not need specialized builds.

It installs isolated Python environments and their pinned dependencies,
including PyTorch where required, shared result-publication utilities and the
pinned Qwen2.5-0.5B model snapshot used by the small-model exercises. A snapshot
is one fixed revision of the model files; `0.5B` means about half a billion
parameters. It also creates private result and log directories for practical
labs, including the directories Slurm must open before a batch script starts.
It does not install a CUDA compiler, container images or specialized
communication, serving or Transformer Engine runtimes.

**Login node — no arguments are needed:**

```bash
python3.12 "$HOME/courses/tools/regular-lab-setup.py"
```

Run this after synchronization before beginning regular labs. Its separate
`monitoring` action discovers existing monitoring connections from the
workstation; `organize-history` makes verified copies of older results.
Those actions are explained in [publishing a comparison](#publish-a-measured-comparison)
and [keeping existing results](#keep-existing-results).

### CUDA labs

`cuda-lab-setup.py` prepares all 13 Custom CUDA Kernels labs. These exercises
compile CUDA C++ source into GPU executables, so they need a compiler and
native libraries in addition to Python. The script installs a managed native
CUDA **13.3.0** toolkit, the pinned CUTLASS source used for library comparisons,
Python support and build tools, then compiles the selected lab targets.
CUTLASS supplies reusable GPU linear-algebra building blocks.

```bash
python3.12 "$HOME/courses/tools/cuda-lab-setup.py" --all
```

The toolkit lives in the course-managed installation and does not replace the
cluster driver or system CUDA. Compilation occurs during preparation without
GPU jobs. Correctness, device compatibility and sanitizer checks still run in
the labs. After editing a kernel, rerun preparation for that lab to rebuild it.
The optional CUDA container teaching exercise needs a separate explicit
selection below.

### Communication labs

`communication-lab-setup.py` prepares the 10 advanced labs that need native
communication or distributed-serving components. Communication libraries move
data among GPUs and nodes; their benchmarks also depend on the interconnect
between those devices. The other 24 labs in Advanced Labs belong to regular
preparation, so the course name alone does not determine the script to use.

Depending on the selected lab, the script prepares bandwidth/fabric tools,
NVIDIA Collective Communications Library (NCCL) tests for coordinated GPU
transfers, NVIDIA Inference Xfer Library (NIXL) for inference data movement,
Megatron Bridge for distributed training, or Dynamo for distributed serving.
It includes supporting communication libraries and coordination services only
where needed. Selecting one lab avoids unrelated specialized builds.

```bash
python3.12 "$HOME/courses/tools/communication-lab-setup.py" --all
```

The relevant Dynamo labs share one pinned Qwen3-8B snapshot, about eight billion
parameters; it is not downloaded for unrelated communication labs. These labs
need the advanced cluster described earlier. Preparation can report hardware
skips on a smaller cluster. Installing software does not establish active
InfiniBand links, GPU-to-GPU transfer paths or multi-node correctness; follow
the owning lab's fabric and runtime checks.

### Serving labs

`serving-lab-setup.py` prepares seven LLM Inference labs that run an actual
inference engine. A **serving engine** loads model weights and executes token
generation; a **benchmark client** sends requests and measures the service's
responses. These need different dependencies from the simpler mechanics labs.

The script installs native **vLLM 0.28.0** with its matching vendor dependencies,
**AIPerf 0.12.0** where the selected benchmark uses it, and the required client
and model files. vLLM and AIPerf use separate isolated environments. The job
launchers select the correct executables and give each job private writable
caches, so you do not need to activate or merge these environments yourself.

```bash
python3.12 "$HOME/courses/tools/serving-lab-setup.py" --all
```

Serving reuses the same pinned Qwen2.5-0.5B snapshot as regular preparation.
Only the real speculative-serving lab, `33_speculative_engine_client`, adds
the 1.5B model needed for its larger-model comparison. Inference Lab 16,
`16_model_artifact_audit`, belongs to regular preparation: the lab retrieves
model metadata without downloading weights and needs Hub connectivity when run.
The default serving route is native; the optional TensorRT-LLM variant requires
its own container selection. Server startup, warmup, request correctness and
performance measurement happen in the allocated lab job.

### Transformer Engine lab

`transformer-engine-lab-setup.py` prepares LLM Training Lab 22,
`22_transformer_engine_fp8`. Transformer Engine provides accelerated neural
network layers and manages lower-precision execution. The lab studies
**floating-point 8-bit (FP8)** arithmetic and compares its numerical behavior
with a higher-precision reference.

The script installs an isolated **Transformer Engine 2.19.0** runtime with its
matching PyTorch environment and the native CUDA/build dependencies needed by
the extension. Keeping it isolated allows ordinary training labs to retain
their own environment.

```bash
python3.12 "$HOME/courses/tools/transformer-engine-lab-setup.py" --all
```

Run the lab afterward to check supported hardware, FP8 execution and numerical
agreement. Installation alone does not establish those results.

### Choose one lab or preview the work

A specialized script run **without arguments lists its supported labs and
installs nothing**. Choose one selector: `--lab <lab-id>`,
`--launcher <filename.sbatch>`, or `--all`. Lab IDs omit the `.py` extension;
launcher names include `.sbatch`. Use a course-qualified ID such as
`custom-cuda-kernels/01_vector_add` if needed to distinguish identical names.
The counts above apply to the complete catalog; a standalone course copy lists
and prepares only its available labs, using its own `tools/` paths.

For example, list the CUDA labs, preview Lab 01's dependencies, then prepare it:

```bash
python3.12 "$HOME/courses/tools/cuda-lab-setup.py"
python3.12 "$HOME/courses/tools/cuda-lab-setup.py" --lab 01_vector_add --plan
python3.12 "$HOME/courses/tools/cuda-lab-setup.py" --lab 01_vector_add
```

`--plan` is read-only: it shows selected dependencies without installation or
platform checks. Regular preparation also supports `--plan`. A plan therefore
does not prove storage, permissions or hardware readiness. `--all` selects the
group's default labs and their native diagnostic variants; it excludes optional
container exercises. Each lab's **Before you start** section links here once;
use its course and lab number in the lookup above to select preparation.

### Repeat or repair preparation

All five scripts share one installation store, download cache and lock under
`$HOME/courses/.runtime`. Identical pinned model snapshots are shared rather
than downloaded for each environment. Installation records let the scripts
validate and reuse completed components. Run one preparation command at a time;
a concurrent setup reports that another setup holds the lock.

Rerun the relevant command after synchronizing changed labs, editing compiled
source, moving the checkout or repairing an interrupted installation. Results,
logs and previous complete installations are preserved. Running regular setup
also preserves specialized installations. Jobs restore their own Python and
library paths, including when switching between independent checkouts; no shell
profile edits are needed.

Private installation logs are under `.runtime/setup-logs/`. If package planning
fails, inspect the named `system-packages-plan.log` or `apptainer-plan.log` for
the package manager's dependency or repository error. A development package
may require a different library version from the one already installed; ask
the cluster administrator to align the package sources. Setup does not silently
downgrade libraries or replace repository snapshots. Remove private repository
addresses and credentials before sharing an error excerpt.

Cancellation stops ordinary downloads and builds, but waits for an active
system-package transaction to finish before releasing the lock. Rerun the same
command after the interruption is resolved. If a job reports a missing or stale
runtime, run the exact preparation command in that error on the login node,
then submit a new job.

### Optional container exercises

Prepare these only when following the corresponding optional exercise. They
are excluded from every `--all` invocation:

```bash
# CUDA container build-and-test teaching exercise
python3.12 "$HOME/courses/tools/cuda-lab-setup.py" --launcher build_and_test.sbatch
# TensorRT-LLM inference variant
python3.12 "$HOME/courses/tools/serving-lab-setup.py" --launcher 30_engine_profile.trtllm.sbatch
# Dynamo container preflight
python3.12 "$HOME/courses/tools/communication-lab-setup.py" --launcher dynamo_disaggregated_preflight.sbatch
```

Only these selections prepare Apptainer and cached images. Image creation runs
without GPUs on the login node; the lab runner later resolves the recorded
immutable image identity and enables GPU access inside an allocation. Container
execution, device isolation and profiler access still need the optional lab's
checks.

In a privileged root Soperator jail, setup uses a private mount namespace rooted
at the existing jail, preserving shared paths and leaving other sessions unchanged.
It requires existing `CAP_SYS_ADMIN` and `CAP_SYS_CHROOT` capabilities and Linux
namespace/pivot support; it grants no privileges and retains Apptainer's isolation
checks. A constrained jail needs an administrator-provided supported session.
Image-build scratch files use `.runtime/cache/apptainer-tmp` on shared storage,
which needs room for uncompressed images. Compression is limited to a 1 GiB
buffer budget and two processors to fit smaller login containers.

## How to run the labs

### Check basic GPU execution

**Login node:** inspect the real partitions. An asterisk marks the default;
partition membership does not itself request a GPU. Allocate one GPU explicitly.
Add `--partition=<name>` if your site needs it.

```bash
sinfo --format='%P %a %D %G'
srun --nodes=1 --ntasks=1 --gres=gpu:1 nvidia-smi
source /etc/profile.d/99-nsight.sh
nsys --version
ncu --version
```

`nvidia-smi` shows the NVIDIA driver and the CUDA level it supports.
`nvcc --version`, when installed, shows the **compiler toolkit**. PyTorch has its
own CUDA build version. These need compatibility, not identical numbers. A missing
compiler does not prevent a prebuilt PyTorch wheel from running. Do not upgrade
Soperator's CUDA/driver stack with a generic toolkit install; it belongs to the
supported cluster images. See [NVIDIA compatibility](https://docs.nvidia.com/deploy/cuda-compatibility/minor-version-compatibility.html)
and [Soperator limitations](https://github.com/nebius/soperator/blob/main/docs/limitations.md).

With the Fundamentals runtime prepared, run its compatibility script.
`labs/10_compatibility_stack.py` records:

- PyTorch's version and the CUDA version it was built against.
- The NVIDIA driver version from `nvidia-smi`.
- The CUDA compiler release from `nvcc`, if installed.
- GPU architectures supported by the PyTorch build.

It executes a tiny GPU tensor operation and prints the result JSON path. It does
not compile an extension or prove profiler, container or fabric readiness.

```bash
cd "$HOME/courses/gpu-fundamentals"
srun --nodes=1 --ntasks=1 --gres=gpu:1 bash -c '
  source tools/course_env.sh 10_compatibility_stack --lab
  exec "$COURSE_PYTHON" labs/10_compatibility_stack.py --workload small
'
```

A successful operation and passing correctness fields establish basic GPU execution
in this runtime. Full qualification appears below when you need profiling or
monitored comparisons.

### Select a course and submit a job

**Login node:** select the course directory. Lab numbers identify files; follow
the syllabus for their order. Each launcher restores its own prepared runtime.

```bash
export COURSE='gpu-fundamentals'
cd "$HOME/courses/$COURSE"
```

Each lab gives one native `sbatch` baseline command after a short explanation of
its program. Preparation has already created its log directories. If a lab needs a specialized
runtime, run its preparation command before submitting.
`--chdir` fixes the working directory; `%j` becomes the job number in separate
stdout/stderr filenames. The launcher allocates the workload with `srun` and uses
the selected runtime. The baseline explicitly disables external capture, including
any profiling settings left in the login environment.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/01_cpu_gpu_crossover/logs/%j.out" \
  --error="$PWD/results/01_cpu_gpu_crossover/logs/%j.err" slurm/01_cpu_gpu_crossover.sbatch --workload small
```

Submission means accepted, not completed. Record the printed job number, check
that exact job, then read its own log and the JSON path printed there. Never select
the newest file from an earlier run.

```bash
export LAB_JOB_ID='<job number printed by sbatch>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/01_cpu_gpu_crossover/logs/$LAB_JOB_ID.out"
cat "results/01_cpu_gpu_crossover/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed in that job log>'
cat "$RESULT_JSON"
```

Continue only after `COMPLETED` with exit code `0:0`. Check `lab_id`,
`experiment.slurm_job_id`, `correctness`, profile and instrumentation fields.
Reading JSON does not validate it. Multi-result launchers print several originals
and aggregates; retain all records required by that lab.

### Keep existing results

New jobs write to `results/LAB/jobs/JOB_ID/`, with scheduler logs under
`results/LAB/logs/`. Existing results, reports, screenshots and published bundles
remain unchanged. To organize attributable older runtime files, run this from
the course directory:

```bash
python3.12 tools/regular-lab-setup.py organize-history --course-root "$PWD"
```

It creates verified copies under `results/history/`, leaves every original in
place, reports files whose lab/job cannot be established, and refuses conflicting
copies. History is never selected as evidence of a fresh run.
Reports are copied and hashed in chunks. JSON metadata inspection is limited to
8 MiB; larger files need filename-based attribution or remain unresolved.

### Publish a measured comparison

Original result JSON files retain the measurements. `publish_results.py` validates
an allowed pair of unprofiled results, serializes its selection to Pushgateway and
verifies the published generation. VMAgent scrapes those metrics and native GPU/node
exporters into local VictoriaMetrics; Grafana queries that datasource. Sampled
telemetry provides context; unprofiled benchmark timers measure the experiment.

![Course measurements and GPU telemetry flowing to Grafana](docs/grafana.png)

Arrows show data flow. VMAgent initiates scrapes; Grafana initiates queries.

**Workstation, once before publishing:** the `regular-lab-setup.py monitoring`
action discovers and verifies
cxcli-owned monitoring services, datasource and routing. It installs nothing and
writes private connection settings and a verification receipt. Choose a persistent
lowercase learner identifier and a fresh output directory.

```bash
export COURSE='gpu-fundamentals' # use the course you are preparing
cd "$COURSES_ROOT/$COURSE"
export COURSE_WORKSPACE='<persistent lowercase learner identifier>'
export COURSE_SETUP_DIR="$(dirname "$CLUSTER_CONFIG")/course-monitoring/$CLUSTER_TARGET"
uv venv --python 3.12 --seed "$HOME/.gpu-course-tools"
"$HOME/.gpu-course-tools/bin/pip" install -r tools/profiling-requirements.txt
"$HOME/.gpu-course-tools/bin/python" tools/regular-lab-setup.py monitoring \
  --config "$CLUSTER_CONFIG" --target "$CLUSTER_TARGET" \
  --kubeconfig "$KUBECONFIG" --context "$CLUSTER_CONTEXT" \
  --workspace "$COURSE_WORKSPACE" --output-dir "$COURSE_SETUP_DIR"
source "$COURSE_SETUP_DIR/laptop-environment.sh"
scp "$COURSE_SETUP_DIR/environment.sh" '<user>@<login address>:courses/.course-environment.sh'
```

Match the sync account and SSH key/port overrides. If installation changes,
rediscover into a fresh directory and replace the copied worker environment.
Keep connection files private.

`verify_monitoring.py` checks current services against the receipt, requires one
healthy results scrape and fresh telemetry for all GPUs on both workers. It briefly
opens local forwards; it does not install or repair monitoring.
In a new workstation terminal, restore the connection settings and select the
course's checkout directory before verification. Use the same course you prepared:

```bash
export COURSE_SETUP_DIR='<absolute setup directory>'
source "$COURSE_SETUP_DIR/laptop-environment.sh"
export COURSES_ROOT='<absolute checkout path>/courses'
export COURSE='gpu-fundamentals'
cd "$COURSES_ROOT/$COURSE"
"$HOME/.gpu-course-tools/bin/python" tools/verify_monitoring.py \
  --receipt "$COURSE_SETUP_DIR/monitoring.json" \
  --kubeconfig "$KUBECONFIG" --context "$CLUSTER_CONTEXT"
```

**Login node:** publication dependencies were installed by runtime preparation.
The lab's command loads its runtime and the existing private
`$HOME/courses/.course-environment.sh` worker settings. The no-argument preparation command does not
discover monitoring services or transfer workstation credentials.

Use the lab's publication command and two exact JSON paths. The expected generation
is `0` initially; afterward use the reviewed current selection generation. Keep
original files if publication fails and inspect its cause. The publisher rejects
invalid comparisons, failed correctness and unqualified runtimes.

### Capture and qualify profiling

A version check establishes command availability, not usable worker captures.
Before profiling claims, qualify both workers in the actual execution runtime.
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
in each runtime you intend to profile. For native CUDA, prepare Lab 01, select
its runtime and use the CUDA canary, which compiles a tiny kernel with the
managed toolkit:

```bash
cd "$HOME/courses/custom-cuda-kernels"
source tools/course_env.sh 01_vector_add --lab
source /etc/profile.d/99-nsight.sh
srun --nodes=2 --ntasks=2 --ntasks-per-node=1 --gpus-per-task=1 \
  "$COURSE_PYTHON" tools/readiness.py --workload cuda
```

For native vLLM, prepare the serving lab and select its engine runtime so the
canary uses the engine's PyTorch and libraries:

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

Open **Environment readiness** and both workers' reports using the browsing steps.
Require matching canary kernels, NVTX, Compute counters and publication generation.

For each experiment, first run the baseline without external profiling and check
correctness. Then submit the lab's diagnostic job. Its source exposes the native
`srun ... nsys profile ...` command on the allocated worker. Inspect that exact
job's logs and reports in the prepared lab directory. Coordinated
NIXL/Dynamo diagnostics keep their native `sbatch` allocation and pass visible `nsys profile`
arguments to each GPU worker; their lifecycle driver preserves readiness and cleanup.

Systems reveals CPU/GPU work, transfers and gaps. Inspect the exact report with
`nsys stats <report.nsys-rep>` and the Systems timeline. If a particular kernel
limits the workload, select it from that trace and use the lab's native
`srun ... ncu ...` command. Inspect its counters with `ncu --import <report.ncu-rep>
--page details` and Compute. Each lab specifies its relevant NVTX range and kernel
selection; CPU models and unsupported distributed/server replays explain why
Compute is omitted.

The command-local `COURSE_CAPTURE=1` enables the supplied NVTX annotations and marks
result JSON as diagnostic. `COURSE_PROFILE_TOOL` records the selected tool; neither
variable launches a profiler in these native commands. Report names incorporate
the job, step and worker/rank identity. Distributed capture runs inside each rank;
serving capture runs around the GPU server with explicit start/stop controls.
The complete native commands and lifecycle sources accompany those labs.

After changing one factor, repeat the baseline command without external profiling.
Use these clean runs for performance comparisons: Compute replay and profiler
collection can alter timings. A nonempty report is only a first check; verify it
contains this experiment's kernels, copies or NVTX ranges before interpreting it.
[NVIDIA's triage workflow](https://docs.nvidia.com/nsight-compute/ComputeTriage/)
and [profiling overhead guidance](https://docs.nvidia.com/nsight-compute/ProfilingGuide/index.html#overhead)
explain the Systems-to-Compute investigation and measurement limits.

### Check specialized runtimes in the labs

Run the appropriate [preparation script](#lab-preparation-scripts) first, then
follow the owning lab to establish that its runtime works on allocated hardware:

- [Inference serving](llm-inference/README.md#serving-runtime-preparation):
  native vLLM/AIPerf and pinned models; TensorRT-LLM is an optional container variant.
- [CUDA Lab 13](custom-cuda-kernels/reference/labs/13_h100_preflight.md):
  managed native toolkit, compiled SM90/SM90a binaries, CUTLASS and device checks.
- [Training Lab 22](llm-training/reference/labs/22_transformer_engine_fp8.md):
  isolated Transformer Engine and FP8 numerical checks.
- [Advanced runtime preparation](advanced-gpu-communication/README.md#runtime-preparation):
  fabric, collective and vendor runtime qualification.

For explicitly selected container exercises, the Apptainer runners resolve
recorded immutable identities to cached images and mount the installed Nsight
resources. Successful image creation does not prove worker device isolation,
nested-container support, driver compatibility or usable profiler captures;
those remain explicit checks in the optional exercise.

### Optional agent-assisted runs

From the workstation's `courses/` directory, install the project-scoped skill:

```bash
npx --yes skills add ./skills/run-labs -a codex --yes
```

This optional command requires Node.js/npm. The first `--yes` skips npm's prompt;
the last skips the skill wizard. Do not add `--global`; use `-a claude-code` for
Claude. On a fully qualified environment, `$run-labs run --course gpu-fundamentals`
runs both profiles and collects verified evidence. The
[run-labs skill](skills/run-labs/SKILL.md) validates prepared runtimes and owns recovery, resume and
lab-specific recipes. Its submission/inspection helpers remain for automation;
the learner commands above expose those operations directly.

## Browsing Grafana and Nsight Profilers

### Connect from your workstation

In each new workstation terminal, restore CLUSTER_CONFIG and CLUSTER_TARGET
from your setup notes, or source the generated laptop-environment.sh using its
absolute path after preparing publication. These commands
check the installed services and print their complete connection instructions:

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

Sign in using the username and password obtained above. During first-time setup,
create the course folder and [import its dashboards](#import-course-dashboards).
For completed runs, open **Dashboards**, choose `course-<course-name>`, and open
the lab's dashboard or **Environment readiness**.

Select workspace and profile, verify both
correctness slots and the publication generation, then set the telemetry window
to the experiment interval. Select GPU worker and local GPU index together.
Sampled telemetry provides context; unprofiled measurements determine performance.

### Browse Nsight Compute and Systems

**Login node:** the capture prints the private report path. Copy only a selected
completed native report into the installed report submount. These copies are
readable by viewer users; original results and logs remain private.

```bash
(
set -eu
report='<absolute path to the selected completed report>'
if [ ! -f "$report" ] || [ -L "$report" ] || [ ! -s "$report" ]; then
  printf 'Select a nonempty regular report file.\n' >&2
  exit 1
fi
case "$report" in *.nsys-rep|*.ncu-rep) ;; *) exit 1 ;; esac
viewer_dir="$(mktemp -d /data/nsight-reports/course-view-XXXXXXXX)"
chmod 755 "$viewer_dir"
install -m 644 "$report" "$viewer_dir/$(basename "$report")"
sha256sum "$report" "$viewer_dir/$(basename "$report")"
printf 'Viewer directory: /mnt/reports/%s\n' "$(basename "$viewer_dir")"
)
```

Require identical hashes. `/data/nsight-reports` is the default producer root;
use the actual submount selected by installation. The viewer exposes it at
`/mnt/reports`. Finish report processing before opening its read-only copy.

Sign in to each printed Nsight URL with the installation username and shared
password obtained above.
In Compute, open the copied `.ncu-rep` under `/mnt/reports/<viewer-directory>`
and inspect the kernel and counters specified by the lab. In Systems, open the
copied `.nsys-rep`, expand the process tree, CUDA streams and NVTX rows, and zoom
to the measured interval. Follow the lab's inspection steps; initialization-only
activity does not establish that the experiment was captured.

© 2026 Nebius B.V. Free educational material under [Apache License 2.0](../LICENSE).
