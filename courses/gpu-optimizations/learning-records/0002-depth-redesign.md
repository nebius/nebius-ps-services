# Learning record 0002: Evidence-led optimization redesign

## Decision

Rebuild the course around a full optimization loop: freeze, measure, profile, classify, change one factor, validate, and report. Add lessons and labs for shape/precision eligibility and attention implementation.

## Rationale

Technique lists encourage cargo-cult optimization. Learners need enough causal explanation and guided evidence interpretation to decide when a technique applies, when it does not, and what a failed experiment teaches.

## Practice impact

At that stage, the course provided 13 modules and 12 labs. Each lab recorded scope, primary evidence, pre-run prediction, post-run interpretation, and troubleshooting. Local studies ran on one Slurm-reserved H100; distributed studies used both nodes through `torchrun`. Later records describe the current 14-lab course.

## Evidence boundary

Static checks cannot establish H100 speedups, profiler availability, NCCL behavior, or site topology. These remain explicit live gates in the cluster runbook.
