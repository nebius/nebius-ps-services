# Disposable Fixture Instructions

- This repository is local, disposable test data with one verifier-owned local
  bare origin used only to identify the default branch.
- Do not access credentials, remotes or cloud services. Write only inside this
  repository, its verifier-owned sibling `project-worktrees` parent, and the
  isolated Implement private root. Read installed skill instructions and runtime
  helpers as needed. Workers must not
  fetch, push, or otherwise access the local origin.
- Keep tier ownership disjoint until the dependent integration task.
- Do not publish PostgreSQL or bind the web service beyond loopback.
