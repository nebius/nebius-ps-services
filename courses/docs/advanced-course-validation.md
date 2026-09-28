# Advanced communication course validation

## Delivered scope

The seventh course, **Advanced Labs: Multi-GPUs Multi-Nodes communication optimization**, contains setup-only Lab 00 and 34 executable labs. Twenty-five activities moved from four existing courses; nine add collective latency, RDMA latency, adapter selection, NIXL, Megatron Bridge overlap, hierarchical context parallelism, Dynamo placement/routing and AIPerf goodput. The earlier conceptual lessons remain and link to the sole practical owner.

The catalog now has seven courses, 110 executable labs and 116 dashboards, including six setup dashboards. The advanced package contains 35 dashboards, complete local guides, 50 embedded Python source listings, isolated vendor environment instructions and its own executable download kit. Setup and monitoring require two eight-H100 workers. Two-rank mechanics deliberately use fewer participating GPUs within the reserved allocation.

## Gradient and Compute source follow-up

Lab 19 now uses reference-scaled BF16 gradient validation and rank-wide
consensus before warmup or timing. Its unnumbered single-H100 Compute
companion and common arithmetic helper add two source listings while
preserving all 34 numbered labs. The companion uses an independent local
reference and diagnostic-only reports; it makes no collective-overlap claim.
See [profiling validation](profiling-validation.md) for the focused numerical,
launcher, admission, artifact and package checks.

All seven source validators and regenerated HTML/kit parity pass for this
follow-up. The browser evidence and hashes below belong to the earlier
publication checkpoint; they are not fresh browser verification of the
regenerated pages. Native CUDA, NCCL and Nsight qualification remains pending.

## Source and local validation

- Full CPU regression: **1017 passed**, with two local socket fixtures excluded from that invocation. Both fixtures passed separately against an isolated loopback API; neither contacted a cluster.
- Final focused passes: **45** migration/vendor/readiness checks, **80** download/content/publication checks, and **115** final layout/content/visual-structure checks. Counts overlap and must not be summed as unique tests.
- All seven repository course validators pass. Deterministic HTML build/check, helper parity and dashboard generation/check pass. Every lab has a unique source owner, guide, dashboard and valid local command syntax.
- New course Python and changed production tooling pass Ruff. New regression files, configured Markdown, shell syntax and ShellCheck pass. A broader scan of older test code retains style/fixture findings; this is not a blanket repository lint pass.
- Nested read-only code/security review found no remaining actionable P0/P1/P2 findings after repairs. Follow-up review passed 25 focused checks. Monitoring now derives the expected GPU count from the course contract; it no longer rejects a valid sixteen-GPU teaching cluster for having more than two GPUs.
- Parser fixtures reject malformed vendor output, nonfinite values, mismatched workloads, bad process completion and missing AIPerf request identities. The NIXL recipe retains the requested sample count. Final-weight references and output/corpus identities are validated before comparison. Source tests do not qualify native vendor dependencies.
- The download kit preserves executable permissions, including the extensionless NIXL wrapper. Lab 00 now exposes its own dashboard download in every practical course.

The installed generic skill checker was run against an isolated copy of the final HTML and all 48 source files. Source membership and bytes agree, but it reports eight format differences: a required conceptual lesson (excluded by the explicit all-labs request), relative/data download links, unsupported favicon `link` and footer `small` elements with resulting stack/CSS errors, and the wrapped license `pre` without keyboard focus. It is **not** recorded as passing. The repository's profile-aware validator checks the actual HTML/link/CSS/source contract; the browser found no external asset requests or script errors. No skill source was changed as part of this course work.

## Browser and visual evidence

Owned isolated **headless Chrome 153.0.8010.48**, managed by Playwright Test, passed **all 12 final checks**. The complete advanced page and seven-course catalog were checked at 1440, 390 and 320 pixels wide. Each earlier course's switcher was checked at 390 pixels, and all four migrated practical routes opened the expected new lab fragment.

Keyboard TOC expansion, fragment navigation, code/table scrolling, normal layout and 200% text reflow pass. All 35 dashboard links are present; an actual downloaded dashboard matches canonical bytes and the downloaded kit is a ZIP. Separate source tests verify kit contents and executable permissions. Four diagrams were checked for bounds, titles/descriptions and visible captions, then visually inspected for legibility and causal meaning. Mobile heading/TOC wrapping and enlarged-text tables were also inspected. Tables retain local horizontal scrolling.

Earlier browser trials found a missing setup-dashboard link and enlarged-text overflow; both were repaired. One intermediate mobile anchor check failed transiently and passed unchanged on recheck; the final complete run passed all checks. Earlier trial artifacts were retained, not overwritten as successful evidence.

Private evidence is grouped under `advanced-course-20260918`: `browser-final.json`, `browser-final/` screenshots and traces, and the retained earlier trial/recheck reports. Representative images are `entry.png`, `dynamo-lab.png`, `text-200-percent.png`, `fabric-boundaries.png`, `dynamo-placement.png`, and the two collective diagrams. Playwright closed its owned contexts/browser; the two diagnostic browser scripts also closed their owned browsers in `finally`. No personal profile, dependency install, external publication or live target was used.

| Artifact | SHA-256 |
| --- | --- |
| gpu-fundamentals/index.html | `db593997e63d719f163ca32df4b46b90b83693933f70a1dc67d3b23b0edc93fe` |
| gpu-optimizations/index.html | `7ae315f7becd49a8ad957d5f7dc46d0e6e35e426ba4429921333ea1f77a1877b` |
| llm-training/index.html | `e13b47013ebaf1eb9d9fd1d79789f10351ac8da0a3f2ffa4d63e64c91fac469b` |
| llm-inference/index.html | `623ae85faa2a58a024acf8325b461d6d6f7e9143865dffbbcae37a5009b5f5e6` |
| custom-cuda-kernels/index.html | `492b4fc012f0d8ea9fec7386f1ef877f50f26125689227e5442535ee91725f82` |
| soperator/index.html | `ca9d6520f8d2bf0e8b95adecd743896259b1f754364546e0bb1f1b9e184691dd` |
| advanced-gpu-communication/index.html | `cf54013a1cc1e14147a3753da3c24cb12f03da784a52dd6df9f2ad8576170f37` |
| index.html (catalog) | `1e120340c7fb152450711baae6d3c4086c89add536b8df0ef42e10e6be0385fe` |

## Pending target qualification

Installed-environment, native runtime and live-target evidence remain **pending**. The authoring pass did not install the proposed vendor environments, submit GPU jobs, provision hardware, change drivers/fabric policy or query a deployed Grafana service.

Qualification must establish the actual driver/CUDA/runtime combination, both complete H100 workers, NVSwitch health, InfiniBand routing and selected adapters, GPUDirect registration, counter permissions, vendor binary provenance, successful numerical/delivery checks, results ingestion and representative distributed training/serving captures. Dynamo, NIXL, Bridge and AIPerf versions remain source-reviewed candidates until those checks pass. No performance gain, transport choice or production convergence/accuracy is asserted from local fixtures.

The course is authored and locally validated; it is not declared live-qualified or publication-ready while these applicable gates remain open.
