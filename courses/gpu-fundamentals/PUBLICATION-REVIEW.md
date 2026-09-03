# Publication review

Status: public-safe static review and browser-rendered layout QA complete; live
cluster execution remains pending.

## Public-safety review

- The release contains only generic public course content and no non-public source metadata or environment-specific identifiers.
- Examples use locally generated synthetic tensors and generic environment variables; no credentials or private endpoints are included.
- External references are limited to public official NVIDIA, PyTorch, and Slurm documentation.
- Generated results, Slurm logs, caches, and environments are excluded by the course `.gitignore`.
- The source audit is represented only by the generic, public-safe topic map in `reference/source-coverage.md`; source names and identifiers are not retained.

## Artifact review

- `index.html` is self-contained and embeds diagrams and canonical lab source.
- Lab source must match the embedded copies; `tools/validate_course.py` enforces parity.
- Diagrams include accessible titles and descriptions.
- Every lesson and lab uses explicit prediction, evidence, and interpretation prompts.

## Verification boundary

Static validation can check HTML structure, source parity, Python syntax, help paths, and Slurm launcher declarations. The following are not yet proven because the target cluster does not exist:

- H100 device detection and non-MIG configuration.
- CUDA/PyTorch compatibility on the provisioned nodes.
- NCCL initialization and inter-node collective behavior.
- Numerical tolerances, memory use, and timing distributions on H100.
- Site-specific profiler access and fabric configuration.
- Triton compiler compatibility and launch-geometry results under the pinned cluster environment.

After the foundations-first redesign, the course was served from a temporary
loopback HTTP server and reviewed in the in-app browser at desktop and
390-pixel viewport widths. The review covered the simplified header, contents,
new H100/SM and bottleneck lessons, every diagram geometry rule, tables, labs,
references, and footer. Content stayed within the page at both widths; wide
diagrams, tables, and source listings used contained horizontal scrolling.
The HTML validator separately checked anchors, title and contents alignment,
accessible SVG titles, lesson structure, and embedded-source parity.

Follow [reference/cluster-smoke-test.md](reference/cluster-smoke-test.md) after provisioning. Update this status only after preserving the resulting evidence.
