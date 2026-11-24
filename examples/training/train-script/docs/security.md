# Security and licensing

The project is a local/Slurm research tool, not a multi-tenant service. The operator
owns the source checkout, input artifacts, output parent directory and shared
filesystem permissions. Never run it against untrusted writable directories or
untrusted checkpoints. Restricted `torch.load(weights_only=True)` narrows code
execution risk but does not protect against resource exhaustion or all malformed
binary files. Model weights must be safetensors; remote model code is disabled.

Downloads are explicit network operations against Hugging Face. They require
immutable revisions and record declared asset licenses. They never recursively
delete an existing path, treat partial content as complete, or persist a login.
Use a new output directory after a failed download. Keep HF credentials in the
environment/secret manager, and avoid shell tracing or sharing raw provider errors.
Training does not print examples, token values or full configuration errors.
Use network isolation where an offline policy must be enforced beyond Python APIs.

`pyproject.toml` and the generated `uv.lock` are the dependency authority. The
PyTorch CPU index is explicit and applies only to torch when selecting the CPU
extra. Other packages resolve from PyPI using uv's default first-index behavior.
Do not use an unrestricted extra index or disable TLS verification.

## Licensing boundaries

- This Nebius project uses Apache-2.0. The standard LICENSE ships in wheels.
- Documentation uses original prose and an original workflow diagram, with links
  to primary technical documentation.
- Dependencies retain their own notices. Their declared licenses are reviewed
  separately from vulnerability findings; a clean advisory scan is not a license
  determination. CUDA/NVIDIA libraries have additional proprietary terms when
  selecting the GPU installation; they are not relicensed by this repository.
- Downloaded models, tokenizers and datasets are not distributed here. Review
  their exact revision's license, acceptable-use terms and data provenance before
  use or redistribution. A caller-provided license string is an audit record,
  not proof that those obligations are fulfilled.
- Synthetic test data and randomly initialized smoke models are original fixtures.

See [validation](validation.md) for actual review scope and outstanding limits.

## Dependency review, 2026-11-23

The installed macOS environment's 87 distributions passed `pip-audit`. A separate
PyPI version-metadata advisory check covered all 107 unique public distribution
and version pairs in the lock, including the Linux GPU dependency graph, and
reported no known advisories. The PyTorch CPU local-version wheel is supplied by
the official PyTorch index; its base release was included in that metadata check.
Advisory databases are time-sensitive and do not establish absence of vulnerabilities.

The direct Python dependencies declare Apache-2.0, MIT or BSD licenses. The
transitive graph also includes ISC, PSF, MPL-2.0 (`certifi`) and composite NumPy
notices. Dependencies are installed separately, with their original licenses;
this repository does not vendor them or change their licenses.

Installed Linux GPU wheel metadata and bundled license paths were checked where
PyPI metadata lacked license fields. NVIDIA runtime wheels retain proprietary
license files. `cuda-toolkit` is a metadata-only package that installs components,
as described by its [publisher](https://pypi.org/project/cuda-toolkit/).
The [CUDA EULA](https://docs.nvidia.com/cuda/eula/index.html) and each component's
bundled terms apply. Packaging a redistributable GPU image requires checking those
distribution terms for the exact bundled components. No GPU image or third-party
model/data bundle is published by this project.
