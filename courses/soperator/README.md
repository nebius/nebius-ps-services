# Soperator: A Nebius Slurm cluster running on Kubernetes

[Read the course](index.html) or [the Markdown lessons](COURSE.md).

Six lessons introduce Slurm, Soperator's Kubernetes components and the commands
used to submit and investigate jobs. Worked examples and reading checks explain
resource requests and the roles of login, controller and worker Pods.

Basic Linux shell knowledge is enough. No cluster, labs or setup are required.
The HTML includes all teaching and can be saved for offline reading.

## Readiness checkpoints

Before moving on, explain why an accepted batch job may not yet be running,
why a login shell does not acquire a GPU merely through allocation, and why a
successful exit alone does not establish application correctness. These are
useful readiness checks before the GPU courses' practical work.

## Maintain this course

**For course maintainers.** Edit the Markdown and `reference/course.json`, then
run the following from the enclosing `courses` directory:

```bash
python3 tools/build_course_html.py
python3 tools/validate_all_courses.py
python3 -m pytest tests/test_text_course.py tests/test_course_catalog.py tests/test_sync_labs.py -q
```

Building every course refreshes the shared navigation. The text-only profile
has its own validator and does not require GPU runtime assets. Keep command
examples public-safe and distinguish documented syntax from live verification.
Review publication evidence in [PUBLICATION-REVIEW.md](PUBLICATION-REVIEW.md).
