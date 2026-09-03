# Foundations-first redesign

## Decision

Teach the logical H100 hierarchy and Hopper SM resource scopes before CUDA
launch and performance concepts. Separate measurement and scheduling from data
movement and computation, then introduce the two-node path and capstone. Keep
all architecture diagrams schematic, SKU-aware, and explicit about ownership
and data-path boundaries.

## Rationale

Learners need to know where HBM, L2, GPCs, TPCs, SMs, SMSPs, schedulers,
registers, shared memory, and execution pipelines belong before terms such as
occupancy, coalescing, Tensor Core eligibility, and collective cost can be used
precisely. Dedicated overlap, precision-routing, and topology visuals make the
later evidence requirements concrete.

## Evidence

The portable page now contains 15 ordered lessons and 12 accessible SVG
diagrams. The contents, syllabus, course map, glossary, source-coverage map,
official references, and validator use the same four-part progression. The
validator passes, and browser inspection found no oversized arrow markers,
box-text overflow, page overflow, or figure-container escape at desktop and
390-pixel viewport widths.

## Boundary

The diagrams teach logical relationships rather than a physical floorplan.
Shipping H100 unit counts, installed topology, CUDA behavior, numerical
acceptance, NCCL behavior, and performance remain live cluster gates.
