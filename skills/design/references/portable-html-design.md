# Portable HTML Design

Read this reference when designing a standalone, self-contained HTML document,
report or presentation. Choose one layout profile, then apply the shared
portability contract and theme. Hosted pages and application interfaces do not
need this reference unless the requested output includes a portable artifact.

During planning, specify the artifact and its acceptance checks; do not claim
those checks ran. Design's existing implementation authority and host-mode
limits still apply. A request to design an artifact alone does not authorize
creating it.

## Portability Contract

Require a deliverable that:

- Consists of one `.html` file and opens directly through `file://`, without
  installation or a local server.
- Contains all required CSS, JavaScript, graphics and data. It makes no
  automatic network requests, including telemetry, remote embeds, prefetches or
  font loads. Ordinary external reference links may require internet when the
  reader follows them; the document must remain useful offline.
- Works when copied, renamed or moved to another computer with an intended
  browser. Use document-relative fragments, not absolute paths or links tied
  to the delivered filename.
- Keeps essential explanations, conclusions and evidence readable when
  JavaScript is unavailable.

Default to embedded `<style>`, small inline scripts, inline SVG and a local
system-font stack. Avoid CDNs, remote fonts, neighboring files, separate JSON,
runtime imports and fetch-based initialization. Local file origins are commonly
opaque, so even neighboring files can encounter browser security restrictions.
[MDN: same-origin policy](https://developer.mozilla.org/en-US/docs/Web/Security/Defenses/Same-origin_policy#file_origins).

Keep state in memory or the URL fragment by default. Preserve section,
subsection and skip-link fragments when adding navigation state. Do not require
`localStorage` for correctness: its behavior for `file:` URLs is undefined and
browser-dependent. A copied file must work without state from its authoring
browser. [MDN: localStorage](https://developer.mozilla.org/en-US/docs/Web/API/Window/localStorage#description).

Authoring may use templates or build tools. The delivered file must not require
them. A library needs a concrete required capability that native HTML/CSS/SVG
and small JavaScript functions cannot reasonably serve; bundle all required
library code and assets into the deliverable.

## Content Before Interaction

Build a complete semantic document first: meaningful headings and sections,
lists, tables with appropriate headers and captions, figures and real links.
Place essential content in the HTML itself, not only in JavaScript objects,
canvas output, tooltips or a selected-state detail panel. JavaScript may
organize, filter or reveal that content after successful initialization.
[MDN: progressive enhancement](https://developer.mozilla.org/en-US/docs/Glossary/Progressive_Enhancement).

- Give each section a descriptive title and a clear purpose. Lead with the main
  message or conclusion, then provide supporting detail.
- Use cards for distinct concepts, tables for comparisons and diagrams for
  relationships. Do not wrap every paragraph in a card.
- Keep sources, assumptions, limitations and uncertainty visible. Optional
  detail must supplement an already complete essential explanation.
- Let the material determine page length and section count. Split or reorganize
  content before reducing its text size to fit a layout.

For reports, show the reporting period, generation date, units, data sources
and definitions. Label the artifact as a snapshot; do not imply live data.
Distinguish zero, missing and unavailable values, identify any estimates and
document relevant rounding, denominators and time zones.

## Shared Visual Theme

Use warm neutral page space, white surfaces, dark slate text, restrained accents,
thin borders and generous spacing. These are configurable defaults, not fixed
domain meanings. Teal must not silently mean both an infrastructure category
and success. Explain category/status meanings with text or a legend.

| Role / suggested variable | Default | Usage |
| --- | --- | --- |
| Page / `--page-background` | `#F4F3EF` | Warm neutral canvas |
| Surface / `--surface` | `#FFFFFF` | Cards, tables and diagram panels |
| Primary text / `--text-primary` | `#233344` | Headings and body |
| Secondary text / `--text-secondary` | `#626E79` | Supporting text and metadata |
| Accent / `--accent` | `#4458D5` | Links, selected states and focus |
| Accent surface / `--accent-surface` | `#EEF0FD` | Selected or emphasized regions |
| Secondary category / `--category-secondary`, `--category-secondary-surface` | `#14786D` / `#EDF7F3` | Teal emphasis |
| Tertiary category / `--category-tertiary`, `--category-tertiary-surface` | `#A55029` / `#FBF0E9` | Rust emphasis |
| Caution / `--caution`, `--caution-surface` | `#916816` / `#FBF5E5` | Qualified or cautionary emphasis |
| Decoration / `--divider` | `#DDE2E5` | Subtle nonessential separation |

Use semantic variable names rather than project-specific component names. Check
actual foreground/background combinations and interactive states; a palette
alone does not establish contrast compliance.

| Property | Documents and reports | Presentations |
| --- | --- | --- |
| Body text | Usually 16–18px | Usually 20–28px for projected content |
| Supporting text | Usually 13–14px | Readable at the intended viewing distance |
| Body line height | Approximately 1.5–1.7 | Approximately 1.3–1.5 |
| Prose width | Approximately 60–75 characters | Shorter text blocks |
| Font | Local system sans-serif stack | Same family |
| Monospace | Code, identifiers, compact metadata | Use sparingly |
| Spacing scale | 4, 8, 12, 16, 24, 32, 48, 64px | Same scale with more breathing room |
| Corner radius | Approximately 8–16px | Same family |

Treat these sizes as design defaults, not accessibility thresholds. Use relative
units where appropriate and preserve browser zoom and enlarged text. Compact
diagram labels are not a default body-text size.

WCAG normal text requires at least 4.5:1 contrast; qualifying large text requires
3:1. Essential graphical and control cues generally require 3:1 against adjacent
colors. The pale divider is decorative: use a stronger cue when a boundary,
connection or focus indicator carries meaning. [Text contrast](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html),
[non-text contrast](https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast.html).

## Three Layout Profiles

Choose the profile from the reader's task; state it in the design handoff.

| Profile | Structure | Useful interactions | Print approach |
| --- | --- | --- | --- |
| General document | Introduction → organized sections → examples → references | Contents links, disclosures, optional search | Continuous reading layout |
| Report | Summary → findings → evidence → implications → methodology | Filters, sorting, chart details, reset | Complete findings and evidence with scope stated |
| Presentation | Narrative sequence with one main message per view | Previous/next, component exploration, optional presentation mode | Deliberate page breaks and suitable orientation |

Reports should remain easy to scan and search; presentations should support a
speaker's narrative. Choose viewport, print dimensions and orientation for the
actual audience. Do not impose five tabs, a side inspector, fixed diagram
dimensions, an A3 page or a fixed section count on every artifact. Preserve a
readable continuous fallback for presentations without JavaScript.

## Purposeful Optional Interaction

Add a control only when it answers a reader question. Prefer native elements and
leave a useful reading experience when enhancement fails or is disabled.

| Interaction | Required behavior |
| --- | --- |
| Selectable diagram component | Click and keyboard activation with a readable text equivalent |
| Selection highlighting | Color plus an outline, label or other visible non-color cue |
| Contextual detail panel | Stack below the visual on narrow screens; retain all essential details in HTML and print |
| Section navigation | Ordinary anchors for documents; tabs only when one-panel-at-a-time reading helps |
| Previous/next | Derive counts, boundaries and default selections from actual sections |
| Fragment navigation | Support bookmarks and browser history without breaking section, subsection or skip links |
| Presentation mode | Optional entry and obvious exit; preserve normal reading and keyboard access |
| Print action | Use the complete print layout, including content outside the selected view |

Use `<details>`/`<summary>` for optional disclosures where appropriate; their
native behavior does not require JavaScript. Keep essential conclusions outside
closed disclosures. [MDN: details](https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/details).

Custom tabs need roles, selected state, focus management and the expected arrow,
Tab and activation-key behavior. ARIA attributes alone do not implement those
interactions. [W3C: tabs pattern](https://www.w3.org/WAI/ARIA/apg/patterns/tabs/).

Do not copy a global `P` shortcut. Character-only shortcuts must be disableable,
remappable to include a non-character key, or active only while the relevant
component has focus. Avoid intercepting typing in editable controls or ordinary
browser navigation. [W3C: character key shortcuts](https://www.w3.org/WAI/WCAG22/Understanding/character-key-shortcuts.html).

Search, sorting, filters, comparison switches and calculators are optional.
Filters must show active scope, offer reset and keep displayed totals consistent
with the included data. Define empty-result and missing-data behavior. Preserve
the baseline content and explain any calculated result's inputs and units.

## Responsive, Accessible And Printable Behavior

Stack supporting panels and reflow text on narrow screens. Contain horizontal
scrolling within genuinely two-dimensional diagrams or tables, rather than
making the whole document overflow. Test reflow at 320 CSS pixels wide and
enlarged text; limited two-dimensional exceptions do not exempt surrounding
headings, explanations or controls. [W3C: reflow](https://www.w3.org/WAI/WCAG22/Understanding/reflow.html).

For diagrams:

- Prefer inline SVG for lightweight, sharp geometry and selectable elements.
- Label arrows and distinguish relationship types. Preserve direction and
  meaning when rearranging elements for a narrow viewport.
- Provide a nearby explanation or structured text equivalent describing
  essential relationships, not just a short accessible name.
- Offer zoom or a readable alternate view when fitting the full diagram would
  make labels too small. Make any diagram controls keyboard-accessible.

Complex visuals require descriptions sufficient to communicate their important
data and relationships. [W3C: complex images](https://www.w3.org/WAI/tutorials/images/complex/).

Give controls understandable names, logical focus order, visible focus and
usable targets. WCAG 2.2 AA uses a 24×24 CSS-pixel target criterion with stated
exceptions; larger targets are a sensible touch default. Honor reduced-motion
preferences and avoid autoplay or motion needed to understand the content.
[W3C: target size](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html),
[MDN: reduced motion](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/At-rules/@media/prefers-reduced-motion).

Create a dedicated embedded `@media print` layout and use `@page` where helpful.
Remove interface controls, print all sections and evidence, reveal collapsed
content, and check page breaks, clipping and label readability. For reports,
print the complete snapshot and state that printed scope rather than silently
printing only a filtered subset. Verify disclosure expansion in the intended
browsers; essential print content must not depend on a script opening it.
Use an always-readable print representation where a disclosure technique fails.
Browser print must work as well as an optional in-page print button.
[MDN: printing](https://developer.mozilla.org/en-US/docs/Web/CSS/Guides/Media_queries/Printing).

## Data Safety And File Size

Optimize images before embedding them. Base64 typically increases binary size
by about one third; large embedded media can make copying and opening cumbersome.
Choose media appropriate to the delivery channel rather than imposing a universal
file-size limit. [MDN: Base64](https://developer.mozilla.org/en-US/docs/Glossary/Base64#encoded_size_increase).

For dynamic text, use `textContent` and explicit element creation instead of
inserting untrusted HTML. Treat report values and fragment state as data, never
executable code. Apply context-appropriate escaping when authoring HTML and
serializing embedded data; a safe DOM assignment does not protect an unsafe
inline script or HTML serialization boundary. Do not allow imported values to
create executable URLs or markup. [OWASP: DOM XSS prevention](https://cheatsheetseries.owasp.org/cheatsheets/DOM_based_XSS_Prevention_Cheat_Sheet.html).

Everything embedded travels with the file, including hidden rows, comments,
metadata and data outside the active filter. Include only information intended
for the recipients. Hiding content is not access control.

## Acceptance Checklist

Record the planned target browsers and viewing/print conditions. For an
implemented artifact, report each check's actual result and any unavailable
evidence; do not call it fully validated while required checks are unobserved.

1. **Direct file:** cold-open the delivered file through `file://` with networking
   disabled. Repeat from a renamed copy in an isolated directory without source
   files; test another intended environment when available and report limits.
   A successful localhost preview alone is insufficient.
2. **Dependencies:** inspect source and browser request evidence for required
   external scripts, styles, fonts, images, data or neighboring files. Check for
   attempted document-initiated network requests, including blocked ones; offline
   rendering alone does not prove there were no attempts. Followed citations are
   separate, deliberate navigation.
3. **Content:** cold-open with JavaScript disabled. Confirm essential messages,
   findings, evidence and explanations remain readable, including in print.
4. **Interactions:** exercise every visible control, default selection, navigation
   boundary/count, deep link and browser back/forward behavior. Check reset and
   empty states for controls that have them.
5. **Keyboard:** verify logical focus, visible focus, expected activation keys,
   no traps and safe shortcuts. Include diagram and presentation controls.
6. **Layouts:** inspect narrow and ordinary desktop viewports, the intended
   presentation viewport when applicable, and enlarged text. Check surrounding
   text reflow and containment of genuinely two-dimensional scrolling.
7. **Visuals:** inspect readable labels, arrow direction/meaning, contrast,
   selection/focus cues and absence of clipping or overlap.
8. **Print:** inspect the resulting pages, including complete sections, evidence
   and collapsed details, pagination, scope labels and no-JavaScript essential
   content. Screen screenshots or print CSS inspection are insufficient.
9. **Browsers:** test the intended browsers and report their actual names and
   versions, with tested environments and explicit gaps.
10. **Data:** for reports and data-bearing artifacts, independently verify
    calculations, units, dates, sources, definitions, filters, reset and
    missing-data handling. Mark this check not applicable only with a reason.

Static skill checks validate these instructions and evaluation definitions, not
the future artifact's browser behavior. Preserve that distinction in handoffs
and completion reports.
