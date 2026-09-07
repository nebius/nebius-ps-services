# Practical Work That Teaches

## Purpose And Appropriate Scope

Immediately after the exact lab/exercise title, explain the problem, the skill
being developed, the baseline and changed condition, and why the result is
useful. State what the supplied implementation actually supports. A lab is not
just a source listing, prediction prompt or unexplained command.

Use a realistic small task before a capstone. Keep independent variables few
enough that learners can explain causality. A modeled exercise, microbenchmark,
end-to-end run and live service experiment are different evidence; name which
one this activity supplies.

## Seven Sections

### Before you start

List prerequisite lessons, tools, input data, target resources and expected
cost/duration. Define core versus optional work and unsupported environments.
Explain safe setup, permissions and how to detect a missing prerequisite.
Use synthetic/public inputs. Non-code cases identify the scenario and worksheet.

### Concepts and code path

Explain the technology, method and important unfamiliar terms first. Then
describe the architecture: inputs, major components/functions, transformations,
outputs and correctness check. Include a diagram for a nontrivial workflow.
Describe decision points and reuse patterns, not every source line. For
non-code work explain the reasoning/process path instead.

### Run the experiment

Give an ordered, reproducible baseline procedure and one deliberate variation.
Commands must match real parser options, working directory, filenames and
entry points. Explain generated files and where to find them. Separate supplied
runnable steps from optional student extensions; do not imply a flag or
capability exists when students must implement it.

### Check your results

Show a small synthetic example of output or a reference solution and label it
as illustrative unless actually measured. Explain each relevant field, unit,
correctness criterion and allowable variation. Failure to reproduce an
illustrative speedup is not automatically a failed lab.

### Investigate the behavior

Ask specific questions with observations that could answer them. Compare one
factor at a time, identify confounders and state what the experiment cannot
prove. Distinguish correctness, performance, quality and usability. Have the
learner connect evidence back to the mechanism, not merely collect screenshots.

### If something goes wrong

Provide likely symptoms, probable causes, discriminating checks and safe
responses. Include invalid inputs, missing resources and environment mismatch.
Never recommend bypassing policy, broad permission changes or disabling safety
checks to obtain a passing result.

### Takeaways and next step

Explain the transferable lesson and limits of the result. Give an answer or
rubric plus a new input/constraint to attempt independently. Link to the next
competency rather than declaring mastery from one run.

## Executable Lab Profile

For code-based courses also require:

- Consistent lab ID/title/lesson association in every artifact. Detect missing,
  orphaned and duplicated guides or source associations.
- Complete canonical source, including support modules and launchers where
  needed. Keep courses independently runnable.
- Argument validation, helpful failure messages and side-effect-free help.
  Verify actual supported flags, output paths, defaults and optional dependencies.
- Deterministic correctness references where possible. Record justified
  numerical tolerances by dtype/recipe rather than a universal threshold.
- Explicit candidate/installed/qualified version records. Pin versions or
  container digests only at the evidence level actually established.
- Separate dependency installation from runtime activation and target smoke
  tests. Document restart/service/allocation needs and cost.
- Narrow launchers, timeouts and resources. Never provision a cluster or change
  drivers, network fabric, sharing modes or service exposure just for a lab.
- Private-by-default runtime output; apply permissions before submission or
  launch when the scheduler/service creates logs first. Publish only sanitized,
  allowlisted result fields, not raw traces, topology or environment dumps.
- Correlatable run identifiers shared by cooperating components, clear output
  ownership and cleanup of only task-owned paths.

## Measurement Profile

Use this branch only for performance/experimental subjects.

Establish the workload and baseline; make one change; check correctness; repeat
measurement; interpret distributions and uncertainty. Define timing boundaries,
warm-up, synchronization, units and input/shape/seed control. Use suitable
target-native timers, not host submission time for asynchronous execution.

Choose repeat counts and statistical treatment appropriate to the experiment.
Keep microbenchmarks separate from independent end-to-end trials. Publish no
speedup, scalability or production-fit claim without corresponding target
evidence. Record what was measured, what remains a hypothesis and which
topology/resources limit generalization.

A user's two small nodes, local laptop or simulated service is not evidence
for production-scale behavior. Offer conditional advanced exercises with
explicit prerequisites instead of pretending unavailable systems were tested.
