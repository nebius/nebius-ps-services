# Seven-course standardization and preservation review

This report preserves an earlier standardization pass and its original artifact
identities, counts and qualification limits. For the current presentation
contract, see [Maintaining the courses](maintaining-courses.md#educational-approach).
Each course's `PUBLICATION-REVIEW.md` records its latest reviewed HTML and evidence.

## Outcome

Applied the create-learning-course standard across all seven course profiles,
then aligned the affected authoring tools, standalone validation, documentation
and generated pages. This pass preserves the existing teaching context and
practical work. It does not replace the courses with abbreviated outlines.

The five conceptual GPU courses retain 78 numbered lessons. Soperator retains
six text-only lessons with reading checks and no labs or diagrams. Advanced
communication retains 34 executable labs, setup-only Lab 00 and no conceptual
lesson placeholders. Across the six practical packages there are still 110
executable labs and 116 Grafana dashboards, including six setup dashboards.

## Teaching changes

- Every numbered GPU lesson now states its stage-specific Practice activity.
  Reading previews, full execution and purposeful revisits are explicit where
  the syllabus distinguishes them. All 134 existing lesson-to-lab links remain;
  their order follows the taught activity where necessary.
- Advanced definitions now explain rank, accumulation window, DDP, MLP,
  tensor/pipeline degrees, TTFT, RDMA, NIC/HCA and memory registration locally.
  Existing arithmetic and hardware limitations remain intact.
- Checkpoint continuation and accumulation/recomputation gain worked examples
  with explicit assumptions. Measured results are not invented, and the
  activation-checkpoint lab retains its narrower correctness scope.
- Five overview diagrams now show directed peer paths, registration choices,
  collective dependencies, fixed-work accumulation and serving ownership.
  Their identities and primary lesson homes remain stable.
- The InfiniBand lesson now agrees with its RDMA-only lab: endpoint validation,
  registration and vendor timing provide evidence; Nsight Systems is explicitly
  inapplicable. Other applicable profiling assignments remain unchanged.
- Final summaries no longer introduce unrelated memory-synchronization domains,
  expert routing or bucket sizing. Their substantive teaching elsewhere remains.
  The optional CUDA objective states what the supplied cluster probe can prove.

The shared renderer now retains introductory Practice prose and authored link
order while requiring every assigned guide exactly once. The standalone
validator checks both the prose and the links against canonical source.
Unknown targets, wrong titles, omissions, duplicate links and stale rendered
activity text fail checks. Course helper copies and downloadable kits use the
same contract. The guide section remains **Practice**, honoring the explicit
series convention; no legacy label or second authoring path was introduced.

## Preservation evidence

A before/after comparison found **560 protected source files byte-identical**:
lab programs, launchers, environments, runtime helpers, authored guides,
dashboard JSON, detailed SVG assets and course/observability metadata. This
includes all 110 authored executable-lab guides and all 116 dashboards. All
84 numbered lesson identities and all existing lesson-to-lab associations are
unchanged. No protected source file was removed.

The base two-worker, one-H100-per-worker route still serves local experiments.
The separate two-worker, eight-H100-per-worker route still owns distributed
fabric practice. Soperator remains a text-only introduction. Lab 00 remains
setup-only. Workload-size profiles, clean timing paths, capture recipes,
correctness gates and publication behavior were not modified.

## Validation

| Evidence lane | Current result |
| --- | --- |
| Course validators | All seven pass, including standalone-package checks. |
| Focused regression suite | 430 passed; covers content, sequencing, rendering, figures, profiles, guide preservation, catalog and renamed standalone packages. |
| Generated parity | HTML, canonical helper copies and all dashboard JSON match their generators. |
| Preservation | 560 protected sources unchanged; titles and all 134 lesson-to-lab links retained. |
| Static quality | Scoped Ruff, formatting, Markdown lint and whitespace checks pass. |
| Changed diagrams | Ten desktop/narrow-mobile checks pass for the five revised figures; each was visually inspected for readable labels and meaningful connectors. |
| Whole-page browser | 24 final cases pass: all seven courses and the catalog at 1440, 390 and 320 pixels in owned isolated headless Chrome 153.0.8010.48. Exact current HTML hashes match the evidence. |
| Code/security review | No blocking issue in the changed scope; HTML escaping and exact lab ownership remain enforced. No new dependency, credential flow or exposure. |

The first regression run exposed nine old bare-link test fixtures and five
stale-generated-page failures. The fixtures now express the current Practice
contract and the pages were rebuilt; the subsequent 430-test run passed.
The final browser run checks keyboard TOC use, heading visibility, exact local
anchors, sibling navigation, applicable JSON/ZIP downloads, local code/table
scrollers, figure bounds and whole-page 200% text reflow. No external browser
request or page script error occurred. Representative desktop/mobile pages and
all five revised figures were visually inspected. Playwright Test owned and
closed its isolated contexts and browser processes; screenshots and traces
remain in the private validation artifacts.

The initial browser run passed 23 cases and failed the Optimizations 320px
keyboard-anchor check: the fragment changed while the viewport remained at
the TOC. Twelve diagnostic repetitions did not reproduce it. The final harness
waits for the focused link to become visible and settle before pressing Enter;
all 24 cases then passed without changing page content or scrolling the target
manually. The initial failure is retained. This is final browser evidence, not
a claim that a page defect or browser timing cause was proven and repaired.

Tests do not establish comprehensive grammatical or pedagogical correctness;
semantic review covered the lessons, special profiles, activity progression
and revised diagram meanings separately.

The official [PyTorch DDP documentation](https://docs.pytorch.org/docs/2.14/generated/torch.nn.parallel.DistributedDataParallel.html)
confirmed the `no_sync` forward/backward boundary. NVIDIA's
[GPUDirect RDMA documentation](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/gpu-operator-rdma.html)
confirmed the separate DMA-BUF and peer-memory registration paths. These
source checks do not qualify the installed cluster combination.

## Generic skill-checker differences

The skill's unmodified `check_course.py` was run on all seven exact generated
pages with a manifest of their embedded source listings. It **does not pass**
these project profiles. The following existing differences are explicit:

- Local catalog/sibling navigation and embedded JSON/ZIP downloads are outside
  its fragment-or-HTTPS link allowlist.
- The pages use an embedded data-URI favicon and a `small` license-footer
  element. Its restricted tag parser rejects those elements and consequently
  reports unbalanced/unclosed structure. The advanced profile also receives an
  external-CSS classification for the favicon's nonempty data URI; it has no
  external stylesheet or browser asset request.
- Soperator intentionally has no figures, and the advanced course intentionally
  has no conceptual lessons. Adding placeholders to satisfy the generic checker
  would violate the user-requested course profiles.

The installed skill was not changed and these results were not relabeled as a
pass. Project validators and browser checks exercise the actual profile,
content, navigation, download and asset contracts. No publication-ready claim
is made from the generic check.

## Reviewed HTML identities

| Course | SHA-256 |
| --- | --- |
| `gpu-fundamentals` | `9d569fbcc158bfe6eb59bbb8b70218dafd468ab89b09670e0ce17d201bfab3ba` |
| `gpu-optimizations` | `7a734241836433d60fc3429dc0724d97475d89fe9ac044da00a9916c5351336a` |
| `llm-training` | `aa144eea32d39de116a2f4afcb0cd6d5c20cb4d4d67ae6c5b90c4485d490a245` |
| `llm-inference` | `83cf1fffddda440c7553722346e70eb21bc1c1917bef34beb087267ef5a27340` |
| `custom-cuda-kernels` | `ded5f073c2306d37611479e20d276401d9c39c1f4523eafdf08a39f667bd3625` |
| `soperator` | `5dee93bc7acc0f33da112c9353ad2124bb1d3cd4bab98590892850f88eb8c1b3` |
| `advanced-gpu-communication` | `0a8cf98bc546caeeb9ae27ec5f6aff691f41a4615feba0b500f96ced0ba337ee` |

## Remaining qualification boundaries

No Linux/H100 runtime, Slurm job, native Nsight capture, performance counter,
fabric transport, private Grafana import or metrics-ingestion qualification
was performed in this editorial pass. Source, CPU-fixture and browser evidence
remain separate from installed and live behavior. The earlier
[all-lab profiling audit](lab-profiling-audit.md) records capture/dashboard
coverage; the earlier [format review](course-format-validation.md) is historical
layout evidence rather than the identity of these newly generated pages.
