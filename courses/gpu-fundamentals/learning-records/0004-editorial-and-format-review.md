# Editorial and format review

> Superseded structure: learning record 0005 replaced the three-part grouping
> below with the current four-part foundations-first sequence. The editorial
> and accessibility principles remain active.

## Decision

Use one canonical course title across the HTML document, learner README, and
page heading. Keep lesson titles in sentence case, make contents entries match
their targets exactly, and group the lessons into three visible syllabus parts.
Use the shared responsive course style, accessible navigation, and current
official documentation links.

## Rationale

Consistent labels reduce orientation cost, while explicit parts make the
learning progression visible. A skip link, clear keyboard focus, contained
overflow, and single-column narrow layout improve access without adding a
runtime dependency to the portable page.

## Evidence

The course validator checks title, contents, anchor, part, reference structure,
official-host allowlists, and embedded-source consistency. Live link
reachability was reviewed separately. A fresh browser review covered desktop
and narrow viewport layouts, including diagrams and overflow behavior.

## Boundary

This review does not prove H100, NCCL, profiler, numerical, or performance
behavior. Those claims remain gated by the cluster smoke-test runbook.
