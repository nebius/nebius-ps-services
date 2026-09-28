# Lab 10: Identify the software layer behind GPU execution

Driver, CUDA runtime, compiler toolkit, and PyTorch versions answer different questions. This lab records them separately and launches a small framework operation on H100. It teaches you to localize compatibility problems instead of assuming that the CUDA version printed by one command describes every software component involved in an application.

## Before you start

Complete [environment setup](../../../README.md#how-to-set-up-the-lab) once. This lab uses the [assigned Grafana dashboard](../grafana/10_compatibility_stack.json).

Use the approved Fundamentals environment on one H100. The NVIDIA management utility and CUDA compiler may have different availability. A missing compiler is relevant to building custom code but does not automatically prevent an installed PyTorch wheel from running.

H100 is compute capability 9.0. Ordinary portable kernels should contain compatible SM90 code or PTX that the installed driver can translate. Architecture-accelerated instructions such as selected SM90a features require an explicit non-forward-compatible target and must be isolated.

## Concepts and code path

A **preflight** is a readiness check before an experiment. Here it answers whether this environment can execute a small PyTorch operation on the allocated H100. The driver connects software to the GPU; the CUDA runtime provides allocation and launch services; the toolkit includes the compiler used to build CUDA source. These components have separate version numbers.

Read each report for its purpose: `nvidia-smi` reports device/driver information, `torch.version.cuda` identifies PyTorch's CUDA build, and `nvcc --version` reports an available compiler. Neither of the first two proves that a compiler is installed, and a compiler version does not prove a successful build. The final framework operation supplies the execution check.

The script obtains PyTorch's version, its associated CUDA runtime version, compiled architecture list, a bounded driver query, and the compiler release when available. It executes a simple CUDA tensor operation as a runtime check. It does not inspect a binary's selected PTX/SASS image or compile an extension.

Given a PyTorch wheel built with a CUDA 13.0 user-mode runtime, a host with a newer compatible driver, and no `nvcc`, import and prebuilt kernels may work. Change the task to compiling a CUDA extension: the missing toolkit now matters. Expected observation: `torch.version.cuda`, the driver report, and `nvcc --version` answer different questions rather than needing identical values.

## Practice

Run the experiment commands on the login node. Save the printed JSON paths; job submission alone is not a result.

Capture one report in the environment you intend to use for subsequent labs. Inspect help without loading GPU dependencies when checking the command from a non-GPU machine.

```bash
umask 077
"$COURSE_PYTHON" labs/10_compatibility_stack.py --help
python3 tools/submit_lab.py --lab 10_compatibility_stack slurm/single_gpu.sbatch labs/10_compatibility_stack.py --profile small
```

Keep a fixed profile for a comparison. If both profiles appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

## Check your results

After the submitted job completes, inspect its state and measured results on the login node. The second command prints the exact JSON paths and numeric fields used by this dashboard. For a direct CPU run, use job `0`.

```bash
sacct -j "${LAB_JOB_ID:?submitted job number}" --format=JobID,State,ExitCode
"$COURSE_PUBLISH_PYTHON" tools/inspect_results.py --lab 10_compatibility_stack --job "$LAB_JOB_ID"
```

Require `framework_kernel_executed`, then read the `layers` fields separately. Distinguish a recorded compiler release from an executed compiler test. An unavailable layer must remain marked unavailable rather than inferred from another version string.

Capture GPU model, compute capability, driver, framework, framework CUDA runtime, compiler, and relevant library versions without credentials or host identity.

Compatibility is proven by the declared application stack loading and executing, not by comparing one pair of version strings.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Correctness of selected results | `correctness` | Boolean pass |

Select two successful, equivalent, unprofiled runs in the same profile. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 10_compatibility_stack \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

Draw the framework-to-runtime-to-driver-to-device stack. Place the toolkit beside the build path, not inside every runtime call. Explain which layer you would inspect first for an import failure versus an unsupported kernel image.

Shipping cubins reduces startup JIT work and fixes generated code, while PTX adds forward reach within its compatibility contract but can introduce JIT latency and driver dependence. Bundled runtimes improve reproducibility but still depend on a sufficiently capable host driver.

**Nsight Systems: not applicable.** This read-only software-stack audit launches no benchmark workload; inspect the compatibility checks, not a GPU execution timeline. Inspect the measured or modeled fields in this lab's dashboard; retain the artifact and its stated scope.

## If something goes wrong

Failure of the tiny CUDA operation means the environment is not ready for performance claims. Preserve the error and ask the cluster owner to resolve driver/device access. Do not install or replace system drivers from a lab.

Installing a toolkit to solve a binary-wheel driver problem changes the wrong layer.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Compatibility is a relationship among layers, not one version label. Use this report when comparing future runs, then perform a separate compile-and-launch preflight in Custom CUDA Kernels when compilation enters the workflow.

Identify the failing operation: packaging/import, runtime/driver loading, architecture code generation, or kernel execution.

Explain why a driver version and `torch.version.cuda` can legitimately differ.
