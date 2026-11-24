# Unattended document-package extraction

Load only for `$design --extract-once <source-folder> [--project-root <target-folder>]`.
This is an explicit documentation-write request, not implementation authority.
Host Plan Mode returns the proposed extraction plan without creating artifacts.
Reject a simultaneous implementation request before writes. Help stops before
all intake, timing, source reads and private-state operations.

## Scope and owners

Capture invocation cwd once; it is the default target, not the Git root or source
folder. Resolve both directories and all applicable instructions. The target must
exist; an empty plain folder is supported without Git initialization. Git targets
retain the committed-HEAD prerequisite and registered workflow guards. Missing,
corrupt, inaccessible or unborn Git is an operational finding, never filesystem
fallback. Do not enter an already active Implement/SDLC workflow as a standalone
publisher; return the handoff to its owner.

`design` interprets, researches and prepares supplementary documentation.
`research` supplies scoped evidence; specialists retain their existing decisions.
`maintain-project-specs` alone renders and publishes the canonical pair. Read its
SKILL and relevant lifecycle/template references. Reuse
`../../worktree/references/document-delivery.md` and `phase-planning.md` for source
coverage and ordering, without creating SDLC state or starting a coordinator.

## Unattended decisions

Ask no design-clarification questions during extraction. Research material
ambiguities comprehensively, compare applicable alternatives and resolve them
when evidence and supplied intent suffice. Keep each original claim, selected
correction and rationale traceable. Preserve accepted hard constraints and
product choices. External evidence can validate feasibility; it cannot invent
stakeholder intent. Continue independent work and aggregate unresolved decisions
at the end. Missing access, unsafe targets, actual permission requirements and
host safety limits remain operational blockers; this mode does not bypass them.

Keep research bounded by evidence, not a fixed item quota: expand for a named
remaining gap, reuse findings and stop when further available retrieval cannot
resolve it. A source list or mechanical validation never proves semantic coverage.

## Process within the seven design phases

1. **Requirements:** inventory the complete package before selecting a slice.
   Identify normative versus informative sources, original identifiers, source
   authority, user outcomes, acceptance criteria and conflicting drafts.
2. **Existing system:** inspect target specs, implementation and callers. Record
   current behavior separately from intended changes. Never overwrite human
   envelopes or classify malformed/legacy specs as missing.
3. **Research:** assess every extracted requirement, design decision and contract.
   Group related evidence questions; keep explicit coverage for each item.
   Inspect original primary sources, relevant versions and applicability. Product
   intent can use local evidence; vendor claims require current vendor evidence.
4. **Specialists:** apply the existing provider, AI behavior, AI stack and app
   stack routes when applicable. Reuse settled choices. No implicit provisioning,
   external writes, new credentials or live experiments follow from research.
5. **Solution:** reconcile contradictions, author missing supported interfaces and
   failure behavior, and record unresolved constraints and affected dependents.
   Review cross-layer integration, security, data ownership and operations.
6. **Review:** apply system-design-rules, check source references and all item
   mappings, assess alternatives and give each finding an actionable disposition.
7. **Plan and publication:** preserve supplied phases/exits or derive a reviewed
   dependency roadmap. Schedule all accepted obligations; detail upcoming vertical
   slices and true prerequisites. Publish documentation, validate and report; stop.

## Mechanical helpers

These are private supporting actions, not additional public skill flags. Run
from the resolved design skill directory with Python 3.11+. They require sibling
`maintain-project-specs` and its shared runtime; use the scoped owner installer
when installing the feature. Do not silently install missing dependencies during
an extraction.

```text
python3 -B scripts/design_intake.py snapshot --source-folder SOURCE --project-root TARGET
python3 -B scripts/design_intake.py validate-review --snapshot SNAPSHOT --review REVIEW [--requirements-candidate REQ --design-candidate DES]
python3 -B scripts/design_intake.py progress --state PRIVATE_STATE --stage research --layer API --reviewed 12 --total 40 --findings 2
python3 -B scripts/design_intake.py publish-assets --package PACKAGE --project-root TARGET --revision REVIEW_DIGEST --snapshot SNAPSHOT --review REVIEW
```

Start progress before intake. Keep review candidates, source copies and progress
state in task-owned private scratch; checkpoint only concise decisions and paths
in the advertised task-state file. Do not store raw source contents, prompts,
private URLs or secrets in task state. `snapshot` creates protected temporary
copies and records relative paths and hashes, including unsupported/unreadable
files and justified exclusions. Read copied bytes for the review. It checks for
source changes during capture. If capture cannot stabilize, report that boundary
and do not claim a coherent baseline. Subsequent original-folder edits do not
silently replace the snapshot.

Markdown/text, structured contracts and HTML can be read directly. Use matching
installed document/PDF skills or existing readers for binary formats. Record
conversion limitations and original page/section locators. Do not invent missing
OCR, omit unreadable requirements or automatically install converters. Snapshot
limits follow the existing source-bundle owner: 8 MiB per file, 64 MiB per package.
Supplementary asset ingestion enforces the aggregate limit while reading and
stops at the first over-limit file, before target publication or further reads.
Exclusions and unreadable files require explicit review; size bounds are not
permission to claim complete extraction. Review source privacy before copying
any normative asset into the target. No reading authority implies public release.

### Review ledger contract

The agent writes `design/review-v1` JSON with:

- `snapshot_sha256`: the inventory identity.
- `source_reviews`: every discovered relative path mapped to `disposition`
  (`reviewed`, `context`, `excluded`, `unreadable`) and a meaningful `reason`.
- `evidence`: records with `id`, `kind` (`local`, `primary`, `secondary`),
  `reference`, supported `claim`, `checked_at` and relevant `version` scope.
- `items`: records with stable `id`, `kind` (`requirement`, `design`, `contract`,
  `acceptance`), `layer`, `summary`, `rationale`, `validation`, `sources` (each
  has `path`, exact `locator`, and original ID when present), `evidence` IDs,
  canonical `requirements`/`features`, and `status` (`supported`,
  `review-required`, `deferred`, `rejected`). Preserve IDs across reviewed revisions.
- `findings`: stable `GAP-*` records with `kind` (`decision`, `evidence`, `coverage`,
  `planning`), `status` (`resolved`, `review-required`),
  affected `items`, original `sources`, `description`, `research_summary` and
  concrete `next_action`. Link precise item locators, and include original
  section/page/contract locators in `locators` records (`path`, `locator`) for
  sources not covered by linked items. For an unreadable file, name the whole
  file and explain why a more precise location is unavailable; never invent one.
- `roadmap`: `mode` (`supplied`, `derived`), `rationale`, `phases` (each has `id`,
  `depends_on`, `exit_criteria`) and `slices` (each has `id`, `phase`, `depends_on`,
  `criteria`). Criteria reference item IDs. It can be null only with a reported
  unresolved finding with `kind: planning`. These are design slices, not frozen
  execution tasks.

A supported item needs applicable evidence. An unresolved item needs a finding
and cannot yield a ready dependent design. Every unavailable source is accounted
for. Validate this ledger with both canonical candidates before publication, then use the spec owner's canonical pair validator
and check REQ/FEAT mappings/readiness against the ledger before publication.
Mechanical success verifies recorded coverage; the root still reviews whether
all prose obligations and research claims were interpreted correctly. Mapped
features collectively cover an item's requirements; each feature independently
retains its readiness constraint.

## Outputs and publication

Keep `docs/requirements.md` and `docs/design.md` concise, with stable canonical
records and links to detailed artifacts. Place authorized normative snapshots,
derived catalogs and contracts under `docs/contracts/design-extractions/REVISION/`.
Place the review ledger, evidence summary, roadmap and final reports under
`docs/reports/design-extractions/REVISION/`. Preserve original source identifiers;
derived catalogs label synthetic identities and map them back to the snapshot.

Prepare an asset package with `contracts/` and `reports/` subdirectories. The
validated review digest is REVISION. `publish-assets` uses immutable files and
exact-byte replay; it does not publish canonical specs or claim atomic whole-
package publication. Publish assets first, then the canonical pair referencing
those exact assets through `project_specs.py publish`. Recheck target identity,
predecessor hashes and source coverage. If pair publication fails, report the
unreferenced retained assets and retry only against verified identities.

Use the existing source-bundle-v2 manifest only when all its invariants hold,
including no unresolved normative conflicts. Keep incomplete inventory/review
reports separate; never erase conflicts or manufacture an admissible manifest.
A reviewed extraction can finish with gaps. Supported features may be ready;
affected features remain draft/blocked and delivery remains not-started unless
independent pre-existing implementation evidence establishes otherwise. Research
is not implementation or runtime verification.

Rerun against the same frozen inputs and applicable evidence without renumbering
or rewriting unchanged canonical content. Changed source or research decisions
create a new reviewed revision; explain the affected requirements/designs. Resume
an interruption using the recorded snapshot and review only after checking their
hashes, completed artifacts, target identity and current canonical predecessors.
Never overwrite unknown edits or automatically migrate legacy specs.

## Report and progress

Use the common progress contract in `design-workflow.md`, including measured
wall time and stage transitions. Count items as reviewed only after an evidence
assessment/disposition, not after listing their titles. Display the current layer,
completed/total review count and open findings; coverage is distinct from readiness.

Finish with a concise layer table: sources, requirements/features, contract
outputs, supported/blocked scope and document links. Include source revision,
publication outcome, implementation phase order/first ready slices, resolved
findings, research limitations and total elapsed wall time.

Use **Unresolved Design Decisions and Evidence Gaps — Review Required** for the
consolidated remaining findings. Each includes GAP ID, original relative file,
source digest, section/page/contract locator, affected REQ/FEAT IDs, research and
alternatives attempted, implementation impact and precise review/evidence needed.
Report operational blockers separately. Do not ask a series of questions at the
end or start implementation. Stop after delivering this report.
