# Soperator: A Nebius Slurm cluster running on Kubernetes

A short text-only introduction to Slurm, Soperator's Kubernetes components and
the job commands used throughout the GPU courses. There are six lessons,
worked command examples and reading checks with answers; no labs or setup tasks.
The resource lessons distinguish host capacity, worker container limits and
per-job Slurm requests.

Open [the complete course](index.html), or read [the canonical lessons](COURSE.md).
Basic Linux shell knowledge is sufficient. No cluster or prior GPU course is
needed to read it. Each lesson starts with its learning objective.
The course overview briefly covers the learning goals, entry knowledge and
example-use limits; detailed readiness checkpoints appear below.

The HTML contains the full text, glossary and public references, with no
external assets. Save it for offline reading. The course switcher works when
sibling courses are present; the course itself remains readable independently.

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

## Readiness checkpoints

Before moving on, explain why an accepted batch job may not yet be running,
why a login shell does not acquire a GPU merely through allocation, and why a
successful exit alone does not establish application correctness. These are
useful readiness checks before the GPU courses' practical work.
