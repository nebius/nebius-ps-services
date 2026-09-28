# Syllabus

Read these six lessons in order. The practical outcome is the ability to explain
and adapt job commands; all checks are reading questions with worked answers.
There are no executable labs. Estimated guided hours: 2.

| Lesson | Competency and reading check |
| --- | --- |
| 1. Slurm turns resource requests into running work | Identify the component that schedules a waiting job and the worker that executes it. |
| 2. Soperator maintains Slurm inside Kubernetes | Distinguish Kubernetes Pod readiness, NodeSet membership and Slurm eligibility. |
| 3. Describe the work before requesting resources | Calculate task, CPU and GPU counts and distinguish host memory from GPU memory. |
| 4. Choose batch or interactive execution | Explain allocation versus execution and locate a batch job's output files. |
| 5. Count Slurm tasks and PyTorch workers separately | Transfer a two-node process layout to four nodes without multiplying launchers incorrectly. |
| 6. Follow a job from queue to completion | Diagnose contradictory batch/step status using exit codes and application logs. |

Before moving on, explain why an accepted batch job may not yet be running,
why a login shell does not acquire a GPU merely through allocation, and why a
successful exit alone does not establish application correctness. These are
useful readiness checks before the GPU courses' practical work.
