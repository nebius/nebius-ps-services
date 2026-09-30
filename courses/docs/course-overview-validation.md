# Course overview and access-guide validation

## Current local verification: 2026-09-29

All seven courses use three short overview paragraphs: purpose and outcomes,
audience and prerequisites, then practical scope and the next step. Six
canonical preambles were rewritten; Soperator already followed this pattern
and its source and HTML remain unchanged. The shared README owns common
operations and generates the Lab Guide. Advanced Labs retains its measurement
conventions and expanded final-investigation rubric in its embedded course guide.

The connection section uses the current Grafana and profiling show commands.
Learners run the returned forwards, password-retrieval commands and URLs,
retaining explicit cluster selection, loopback binding and both Nsight ports.
The Nsight username remains the installation choice, admin by default.

## Source and preservation

- 261 focused tests pass across shared-guide, complete-content, text-course,
  advanced-course, catalog, presentation and profiling contracts.
- All seven native course validators pass, including generated HTML/archive
  freshness; the synchronized helper check passes.
- Scoped Ruff, configured Markdown lint, shell-fence syntax and whitespace
  checks pass. Independent read-only content/code/security review found no
  blocking issue in this task's changes.
- All 3,479 protected lab, launcher, reference, dashboard, result and helper
  files retain their task-start hashes. All numbered lesson bodies are unchanged.
- The unmodified generic skill checker retains identical diagnostics on the
  before/after pages for all seven courses, including catalog markup, special
  profiles and source-listing parsing differences. It is not a passing gate;
  repository-native source and rendered-content parity pass independently.

## Browser and visual evidence

The final run passed 27 cases in an owned isolated headless Chrome
154.0.8037.58 process, using Playwright Test 1.57.0, at 1440×1000, 390×1000
and 320×1000. The complete pages were loaded for overview, navigation and guide
checks. Assertions cover three-paragraph overviews, course-specific scope,
absence of relocated command blocks, setup-link navigation, resource selection,
keyboard-operated menus and table of contents, local code scrollers, page-width
containment and 200% root-text reflow. Guide checks cover the exact show
commands, credential instructions, paired ports and footer/license controls.

All seven 320px overview screenshots, desktop Fundamentals and connection
views, the 390px Advanced overview and an enlarged-text view were inspected.
Text and links fit the shared layout; long commands scroll within their code
blocks. No product layout repair was required. Successful fixture cleanup
closed owned browser resources, and the final runner exited normally.

The initial two-worker run with failure tracing stopped making progress after
seven completed cases and had no remaining browser processes. Its incomplete
output is retained separately; the exact owned runner and workers were stopped
after an interrupt did not finish cleanup. The final run used one worker with
tracing disabled and passed all cases. This is a harness retry, not evidence of
a product repair or a proven diagnosis of the original stall.

Local evidence group `course-overviews-k24e0299` contains task-start sources,
`task.diff`, `preservation.json`, `generic-checker.json`,
`browser-retry-results.json`, `browser-artifacts.json`, the Playwright harness
and screenshots under `browser-retry-results/`. Initial captures remain under
`browser-results/`; no completed trace was produced by the stalled run.

## Artifact identities

| Artifact | SHA-256 |
| --- | --- |
| lab-guide.html | `a978a0c3e805491ea893761ad9cee7661e44fc0d4a32b644714013d1724aa037` |
| soperator/index.html | `004fb475f1d1ca1fd265713618018da8e0789d9b4f032789e510b62588cd64f4` |
| gpu-fundamentals/index.html | `fb595dc695ab8aa689570ee317d036fce3ee39938b86add727ed2ec21331a479` |
| gpu-optimizations/index.html | `200f2fbc5696ecda92739b2cd25a6c29ba30877f4bbd896ae239ef4df96f3d83` |
| llm-training/index.html | `ad37d5163934e4faea87e7234270b887aeed82ca566cdbf44bb15919c387d8b7` |
| llm-inference/index.html | `92f6cc07dc78ea00c63c6177f4bea033fd2a7e92bcf5bcccb6ce709155d3a763` |
| custom-cuda-kernels/index.html | `9b219cc9006e710c5f0360043513cef255aa33dbe82209fb704d88f4a76672cf` |
| advanced-gpu-communication/index.html | `8964efa93c5be1233f69ef66e6f9637b1875e38cf52c2b70105d7b73ea508912` |

## Evidence limits

The repository's local cxcli environment accepts both show command help
surfaces; this verifies syntax and availability there, not live Grafana or
Nsight access. No credential retrieval, cluster operation, dependency
installation, new runtime qualification, lab execution or external publication
was performed. Existing installed/runtime/live-target course qualifications
retain their separate scope and status. No source skill was changed.
