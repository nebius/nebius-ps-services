# Catalog preparation alignment validation

## Scope — 2026-10-03

All eight courses and 110 practical lab guides were reviewed against the five
preparation groups. Each lab now refers once, from Before you start, to the
shared Lab Guide. Course introductions and current runbooks use the same route.
The guide provides a complete course/lab-number lookup and retains optional
variant selection. Its sidebar includes the lookup in authored heading order.

Removed duplicated installer commands and competing manual setup procedures.
Corrected current environment/version notes, CUDA rebuild guidance, runtime
activation wording and fabric/tool path ownership. Lab-specific hardware and
data prerequisites, qualification checks and intentional compilation experiments
remain in their owning labs. The shared preparation engine is unchanged.

## Source and preservation evidence

- 536 focused tests pass, including the exact 110-lab lookup, source/rendered
  referrals, validator rejection controls, runtime selection and native commands.
- All eight repository-native course validators pass. Complete HTML generation,
  freshness and six-course helper parity pass.
- All 685 learner Bash blocks parse; all 501 native lab command lines are
  unchanged from the task baseline.
- Scoped Ruff, Markdown and whitespace checks pass. Read-only code and security
  review found no concrete defect in the changed surfaces.
- All 2,872 historical result/archive files and 14 canonical runtime-engine
  Python files retain their baseline hashes. All six completed-run sections in
  VERSIONS.md remain byte-identical. Archives were regenerated and checked without
  changing their bytes.

The new native validator rejects a second Lab Guide link, misplaced preparation
referrals, setup script names and manual dependency installation in lab guides.
The renderer preserves direct shared-guide fragments. No dependency pins,
installation behavior, workload implementation or historical evidence changed.

## Browser and visual evidence

Owned, isolated headless Chrome 154.0.8037.93 with Playwright Test 1.57.0 checked
all nine pages at 1440×1000, 390×1000 and 320×1000. JavaScript was disabled.
Checks cover unique IDs, every TOC target, keyboard TOC controls and preparation
links, every rendered lab referral, page containment, keyboard local scrolling
and 200% text reflow. No automatic external requests or page errors occurred in
the successful cases. Recorded HTML identities match the final files below.

The final full run passed 26 cases and failed one 320px guide navigation
visibility assertion. The URL fragment changed correctly but the heading was
outside the viewport. Three unchanged replays of that exact case passed; nine
bounded probes with fresh, scroller-first and settled input sequences also
reached the correct heading. The cause is not established, and no product or
harness repair is claimed. Thus all 27 distinct page/viewport combinations have
successful coverage, with an unresolved intermittent navigation observation;
this is not a clean single-run 27-case result.

Separate settled screenshots cover every course and the guide, including the
mobile lookup's horizontally scrolled group links and 200% text reflow. Visual
inspection found readable preparation prose, reachable links and contained local
code/table scrolling. These targeted views supplement complete-page DOM checks;
they are not an accessibility certification. Earlier automated screenshots had
inconsistent framing, so the supplemental capture asserts visible positioning.

## Artifact identities

| HTML artifact | SHA-256 |
| --- | --- |
| `lab-guide.html` | `d770a9ef56018aa851f805ad661d67f74d5f18e68f665a1f2c045e0f142c52dd` |
| `soperator/index.html` | `e6c86ee693875f1fbf82f7d125f7d9f9d7d680f3b1054c50f74eaafe2ad32647` |
| `gpu-performance-tools/index.html` | `ada9be047b4ced5d1642aad60a213123448f6ace93321d7895967ed4d9c7fe6d` |
| `gpu-fundamentals/index.html` | `0fcab9d688020749cd3e8f530a99634fd7bcb419be395a40db81e92428d4445e` |
| `gpu-optimizations/index.html` | `d583d62b8c7ba844219aa24b24afcbb6c7b19dfcd1e46d7eb3cbee3b3762b79f` |
| `llm-training/index.html` | `362b6e9bbb2b6b25d8e28480c88518376e7b56d9ea2da86b7c449a8ebe389941` |
| `llm-inference/index.html` | `80e31fa0f7d9254b0a5bae442babe771b8cd8be3d89e2c4c53c4731a7e5c1dfa` |
| `custom-cuda-kernels/index.html` | `c726e0f632fed59574b0471e28d43570c75ccc7296e8c3836f9db8a246e3f4c1` |
| `advanced-gpu-communication/index.html` | `9d6054c89dde1011483cb2b76e65baa5bf1bd70e22fd7900a174a7087e652f10` |

## Evidence locations and limits

Local evidence group: `course-preparation-alignment-3o96xtfp`.

- Source: `tests-verified.log`, `final-quality-gates.json`, `preservation.json`,
  `native-command-preservation.json`, `versions-preservation.json`.
- Browser: `browser-results.json`, `browser-verified.json`,
  `navigation-replay-results.json`, `navigation-probe.json`.
- Per-case screenshots/traces: `browser-artifacts/catalog-<page>-<width>/`
  and `navigation-replay-artifacts/`; screenshots are `lookup.png`,
  `preparation-referral.png` and `text-reflow.png`; traces are `trace.zip`.
- Settled visual captures: `visual-results.json` and `visual/`.

Earlier failures are retained under `initial-browser-*` and
`interrupted-late-*`: the original harness selected the closed course switcher
instead of the TOC; its selector was corrected. `before-final-wording-browser-*`
records a clean 27-case run before the last prose corrections, and does not
replace the final-artifact evidence above. Owned browsers and contexts were
closed; the interrupted runner's remaining owned Node processes were terminated.

The generic full-course skill checker retains exactly its baseline diagnostics:
6 per ordinary practical/Soperator page, 8 for Advanced Labs, 7 for GPU
Performance Tools and 11 for the shared guide. It is **not a passing gate**.
`skill-checker.json` records the bounded markup comparison using private page
copies and an empty source allowlist. Native validators and source-parity tests
supply actual embedded-listing verification; the empty allowlist does not.

Installed-environment, runtime activation, GPU qualification, live-target and
deployment checks were not rerun for this documentation revision. Prior setup
and live evidence retain their original scope. No external publication occurred.
