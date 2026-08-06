# Disposable Fixture Instructions

- This repository is local, disposable test data with one verifier-owned local
  bare origin used only to identify the default branch.
- Do not access credentials, remotes, cloud services, or paths outside this
  repository and the isolated Task Implementer private root. Workers must not
  fetch, push, or otherwise access the local origin.
- Keep tier ownership disjoint until the dependent integration task.
- Do not publish PostgreSQL or bind the web service beyond loopback.
