# Lab 10: Identify the software layer behind GPU execution

Driver, CUDA runtime, compiler toolkit, and PyTorch versions answer different questions. This lab records them separately and launches a small framework operation on H100. It teaches you to localize compatibility problems instead of assuming that the CUDA version printed by one command describes every software component involved in an application.

## Before you start

**Theory preparation:** Read Lesson 1 for the host/device boundary. This guide defines the software layers needed for the first check; Lesson 2 explains compilation and compatibility in depth.

Use the approved Fundamentals environment on one H100. The NVIDIA management utility and CUDA compiler may have different availability. A missing compiler is relevant to building custom code but does not automatically prevent an installed PyTorch wheel from running.

H100 is compute capability 9.0. Ordinary portable kernels should contain compatible SM90 code or PTX that the installed driver can translate. Architecture-accelerated instructions such as selected SM90a features require an explicit non-forward-compatible target and must be isolated.

## Concepts and code path

A **preflight** is a readiness check before an experiment. Here it answers whether this environment can execute a small PyTorch operation on the allocated H100. The driver connects software to the GPU; the CUDA runtime provides allocation and launch services; the toolkit includes the compiler used to build CUDA source. These components have separate version numbers.

Read each report for its purpose: `nvidia-smi` reports device/driver information, `torch.version.cuda` identifies PyTorch's CUDA build, and `nvcc --version` reports an available compiler. Neither of the first two proves that a compiler is installed, and a compiler version does not prove a successful build. The final framework operation supplies the execution check.

The script obtains PyTorch's version, its associated CUDA runtime version, compiled architecture list, a bounded driver query, and the compiler release when available. It executes a simple CUDA tensor operation as a runtime check. It does not inspect a binary's selected PTX/SASS image or compile an extension.

## Practice

Given a PyTorch wheel built with a CUDA 13.0 user-mode runtime, a host with a newer compatible driver, and no `nvcc`, import and prebuilt kernels may work. Change the task to compiling a CUDA extension: the missing toolkit now matters. Expected observation: `torch.version.cuda`, the driver report, and `nvcc --version` answer different questions rather than needing identical values.

Run Lab 10, draw the exact stack, and identify the layer that would own an import, compiler, or kernel-launch failure. Use Lab 08 to connect a framework operator chain to its captured CUDA activity; its forward-only workload is not a complete training step. The distributed Lab 00 preflight belongs to Lesson 12.

Capture one report in the environment you intend to use for subsequent labs. Inspect help without loading GPU dependencies when checking the command from a non-GPU machine.

```bash
umask 077
python labs/10_compatibility_stack.py --help
sbatch slurm/single_gpu.sbatch labs/10_compatibility_stack.py --profile smoke
```

## Check your results

Require `framework_kernel_executed`, then read the `layers` fields separately. Distinguish a recorded compiler release from an executed compiler test. An unavailable layer must remain marked unavailable rather than inferred from another version string.

Capture GPU model, compute capability, driver, framework, framework CUDA runtime, compiler, and relevant library versions without credentials or host identity.

Compatibility is proven by the declared application stack loading and executing, not by comparing one pair of version strings.

## Investigate the behavior

Draw the framework-to-runtime-to-driver-to-device stack. Place the toolkit beside the build path, not inside every runtime call. Explain which layer you would inspect first for an import failure versus an unsupported kernel image.

Shipping cubins reduces startup JIT work and fixes generated code, while PTX adds forward reach within its compatibility contract but can introduce JIT latency and driver dependence. Bundled runtimes improve reproducibility but still depend on a sufficiently capable host driver.

## If something goes wrong

Failure of the tiny CUDA operation means the environment is not ready for performance claims. Preserve the error and ask the cluster owner to resolve driver/device access. Do not install or replace system drivers from a lab.

Installing a toolkit to solve a binary-wheel driver problem changes the wrong layer.

## Takeaways and next step

Compatibility is a relationship among layers, not one version label. Use this report when comparing future runs, then perform a separate compile-and-launch preflight in Custom CUDA Kernels when compilation enters the workflow.

Diagnose from the failing boundary: packaging/import, runtime/driver loading, architecture code generation, or kernel execution.

Explain why a driver version and `torch.version.cuda` can legitimately differ.
