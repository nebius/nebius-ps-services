# Performance Engineering Courses

**[Explore the courses](https://nebius.github.io/nebius-ps-services/courses/index.html)**

The website introduces six courses, an Advanced Labs collection, and one shared
Lab Guide, with a suggested reading order and direct links to every resource.

Start with [Soperator](soperator/index.html), use the [Lab Guide](lab-guide.html)
to prepare for practice, then study [GPU Fundamentals](gpu-fundamentals/index.html) → [GPU Performance Tools](gpu-performance-tools/index.html)
and [GPU Performance Optimization](gpu-optimizations/index.html). Continue with
[LLM Training](llm-training/index.html), [LLM Inference](llm-inference/index.html),
or [Custom CUDA Kernels](custom-cuda-kernels/index.html); these specializations
are independent. [Advanced Labs](advanced-gpu-communication/index.html)
owns the multi-GPU, multi-node experiments.

Diagrams appear immediately after the passage they explain, within the owning
lesson or practical guide.

Every course ends with one Where to Go Next, one A–Z Glossary, and then
Official references. Lessons and performance-tool guides share these course-wide
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

### Synchronize and prepare Python

**Workstation:** verify the context, then synchronize. The script finds one login
LoadBalancer in that context, copies sources to `~/courses`, and opens SSH there
as `root`. It installs no dependencies and preserves remote-only results.

```bash
cd "$COURSES_ROOT"
kubectl config current-context
./sync-labs.sh
```

Use `./sync-labs.sh 'user@<login address>'` for another account or an explicit
endpoint. See `./sync-labs.sh --help` for SSH key, port and sync-only options.
With `--receipt FILE`, the private connection receipt records the port used for
the sync, including an SSH alias's configured port. Use a new receipt path for
each sync; existing receipts are never overwritten.
Rerun synchronization after source changes.

**Login node:** select a course and check Python. Use **3.12** explicitly;
`python3` alone may select an older interpreter.

```bash
export COURSE='gpu-fundamentals'
cd "$HOME/courses/$COURSE"
python3.12 --version
```

If missing, an administrator can install Python on supported Ubuntu 24.04:

```bash
sudo apt update
sudo apt install -y python3.12 python3.12-venv
```

Prepare the entire synchronized catalog once. This standard-library command creates
private result, log and profiler-report directories for every practical lab, plus
the shared `.runtime` and `.profiling-tools` directories; it
preserves existing results and rejects unsafe existing directories. Rerun it after
synchronizing new labs or replacing the checkout. It does not install dependencies,
submit jobs, or contact monitoring services.

```bash
python3.12 "$HOME/courses/tools/course_setup.py" prepare --courses-root "$HOME/courses"
```

For an independently copied course, use its `tools/course_setup.py prepare
--course-root "$PWD"` instead. Directory preparation cannot be deferred to a batch
script: Slurm opens its log files before that script starts.

Generic `python3` on another Ubuntu release does not guarantee 3.12. Keep package
installation separate from jobs. Fundamentals, Optimizations, Training and Advanced
Communication use `requirements.txt`; Inference's local mechanics use
`requirements-mechanics.txt`. For Custom CUDA Kernels, skip this Python block and
use the specialized container/build instructions below.

```bash
requirements='requirements.txt'
if [ "$COURSE" = llm-inference ]; then requirements='requirements-mechanics.txt'; fi
python3.12 -m venv "$HOME/courses/.venvs/$COURSE"
export COURSE_PYTHON="$HOME/courses/.venvs/$COURSE/bin/python"
"$COURSE_PYTHON" -m pip install -r "$requirements"
export COURSE_TORCHRUN="$HOME/courses/.venvs/$COURSE/bin/torchrun"
export COURSE_CUDNN_LIB="$("$COURSE_PYTHON" -c 'import importlib.util; print(next(iter(importlib.util.find_spec("nvidia.cudnn").submodule_search_locations)) + "/lib")')"
test -f "$COURSE_CUDNN_LIB/libcudnn.so.9"
export LD_LIBRARY_PATH="$COURSE_CUDNN_LIB${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
declare -p COURSE_PYTHON COURSE_TORCHRUN COURSE_CUDNN_LIB > "$HOME/courses/.runtime/$COURSE.sh"
```

The cuDNN lookup finds the library installed with the selected Python environment;
`LD_LIBRARY_PATH` makes it visible to native loaders. The final file records runtime
selectors so a later login can restore the same environment.

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
source "$HOME/courses/.runtime/gpu-fundamentals.sh"
srun --nodes=1 --ntasks=1 --gres=gpu:1 \
  "$COURSE_PYTHON" labs/10_compatibility_stack.py --workload small
```

A successful operation and passing correctness fields establish basic GPU execution
in this runtime. Full qualification appears below when you need profiling or
monitored comparisons.

## How to run the labs

### Select a course and submit a job

**Login node:** restore the selected runtime. Lab numbers identify files; follow
the syllabus for their order. Python courses also restore the cuDNN library path.

```bash
export COURSE='gpu-fundamentals'
cd "$HOME/courses/$COURSE"
source "$HOME/courses/.runtime/$COURSE.sh"
export LD_LIBRARY_PATH="${COURSE_CUDNN_LIB:+$COURSE_CUDNN_LIB:}${LD_LIBRARY_PATH:-}"
```

Each lab gives one native `sbatch` baseline command after a short explanation of
its program. The one-time preparation above has already created its log directories.
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
python3 tools/course_setup.py organize-history --course-root "$PWD"
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

**Workstation, once before publishing:** `course_setup.py` discovers and verifies
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
"$HOME/.gpu-course-tools/bin/python" tools/course_setup.py monitoring \
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

**Login node:** prepare publishing dependencies once, separately from the workload
runtime. Restore the connection file and runtime settings before later publication.

```bash
source "$HOME/courses/.course-environment.sh"
export COURSE_TOOLS="$HOME/courses/.profiling-tools"
python3.12 -m venv "$COURSE_TOOLS/venv"
"$COURSE_TOOLS/venv/bin/pip" install -r tools/profiling-requirements.txt
export COURSE_PUBLISH_PYTHON="$COURSE_TOOLS/venv/bin/python"
declare -p COURSE_TOOLS COURSE_PUBLISH_PYTHON >> "$HOME/courses/.runtime/$COURSE.sh"
```

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
source /etc/profile.d/99-nsight.sh
srun --nodes=2 --ntasks=2 --ntasks-per-node=1 --gpus-per-task=1 \
  "$COURSE_PYTHON" tools/readiness.py
```

Require one full GPU visible to each task. If an eight-GPU worker exposes other
devices to `nvidia-smi`, have the owner qualify device isolation;
`CUDA_VISIBLE_DEVICES` alone does not establish that boundary. For CUDA, replace
the Python invocation with
`"$COURSE_CONTAINER_RUNNER" "$CUDA_IMAGE_DIGEST" python3 tools/readiness.py --workload cuda`
and export `COURSE_RUNTIME_ID="$CUDA_IMAGE_DIGEST"` first. For inference containers,
use `"$VLLM_IMAGE_DIGEST" python3 tools/readiness.py --workload torch` through the same
runner and export `COURSE_RUNTIME_ID` as that digest. Repeat for each runtime.

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab environment_readiness \
  --baseline "${WORKER_ONE_RESULT:?first worker JSON path}" \
  --candidate "${WORKER_TWO_RESULT:?second worker JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

Open **Environment readiness** and both workers' reports using the browsing steps.
Require matching canary kernels, NVTX, Compute counters and publication generation.

For each experiment, first run the baseline without external profiling and check
correctness. Then use the lab's explicit `srun ... nsys profile ...` command to
capture execution on its allocated GPU worker. This command waits for resources
and stays attached until the diagnostic finishes. Keep that terminal open; the
per-job logs and reports remain in the prepared lab directories. Coordinated
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

### Prepare specialized runtimes when needed

Prepare these **before** the affected course's first job:

- [Inference serving](llm-inference/README.md#serving-runtime-preparation): immutable images and the separate serving client.
- [CUDA Lab 13](custom-cuda-kernels/reference/labs/13_h100_preflight.md): container, CUTLASS, compilation/execution checks and the completed build directory.
- [Training Lab 22](llm-training/reference/labs/22_transformer_engine_fp8.md): compatible Transformer Engine, CUDA headers and runtime compiler.
- [Advanced runtime preparation](advanced-gpu-communication/README.md#runtime-preparation): fabric tools, runtime selectors and vendor environments.

The CUDA and inference example runners use Apptainer. They validate immutable image
digests, use `managed_profilers.py` to discover complete installed profiler packages
for read-only mounts, and execute the requested command with GPU access. An
administrator may install Apptainer on supported Ubuntu with the
[official PPA instructions](https://apptainer.org/docs/admin/main/installation.html):

```bash
sudo apt update
sudo apt install -y software-properties-common
sudo add-apt-repository -y ppa:apptainer/ppa
sudo apt update
sudo apt install -y apptainer
apptainer version
```

Check the prepared runner inside a Slurm GPU allocation with its approved image.
A login-node version check does not prove worker, GPU, user-namespace or nested
container support. Preserve the site's supported runtime and driver configuration.

### Optional agent-assisted runs

From the workstation's `courses/` directory, install the project-scoped skill:

```bash
npx --yes skills add ./skills/run-labs -a codex --yes
```

This optional command requires Node.js/npm. The first `--yes` skips npm's prompt;
the last skips the skill wizard. Do not add `--global`; use `-a claude-code` for
Claude. On a fully qualified environment, `$run-labs run --course gpu-fundamentals`
runs both profiles and collects verified evidence. The
[run-labs skill](skills/run-labs/SKILL.md) owns preparation, recovery, resume and
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
