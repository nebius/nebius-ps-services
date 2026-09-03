# Learning record 0002: Depth redesign

> Superseded structure: learning record 0005 replaced this 12-module design
> with the current 15-module, four-part foundations-first sequence.

## Decision

Replace the brief overview with a 12-module evidence-first course and add explicit lessons for asynchronous execution, warp scheduling, operator-to-kernel mapping, distributed execution, and falsifiable diagnosis.

## Rationale

The earlier artifact had runnable examples but too little explanatory prose and too few guided exercises for a learner to build a transferable mental model. The redesign couples each concept to a worked example, retrieval prompt, answer key, review cue, and guided lab interpretation.

## Practice impact

Two additional local-GPU labs cover streams and profiler mapping. The two-node collective lab is launched by Slurm and `torchrun`. All lab cards declare scope, prediction, primary evidence, interpretation, and troubleshooting.

## Evidence boundary

Static validation can prove artifact structure and source parity. It cannot prove H100 performance, NCCL behavior, or site topology; those remain live cluster gates.
