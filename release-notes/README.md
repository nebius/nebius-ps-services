# Release fragments

These fragments are prepared for the later enrolled automation deployment.
Before enrollment, `$publish-release` owns ordinary changelog and tag publication.

After enrollment, package changes include an immutable JSON fragment in the original PR:

```json
{"project":"vpngw","bump":"patch","summary":"Describe the user-visible correction."}
```

Use `vpngw` or `nebius-cxcli`. Choose `patch` for compatible fixes, `minor`
for compatible features, and `major` for breaking changes. Use a unique filename
such as `pr-123-vpngw.json`; never edit or delete an already merged fragment.
No secrets, cloud identifiers, private endpoints or customer information belong
in these files. The controller validates the closed schema in
`github-ai-automation/src/github_ai_automation/contracts.py`.

The largest bump in the affected project's fragments since its last verified
release determines the next version. Release notes aggregate those fragments.
Only package-affecting changes trigger a release; documentation and tests alone
do not. The registry is `github-ai-automation/config/release-projects.json`.
Actions and laptop requests use the same coordinator, tag reservation and
original artifact manifest. No release-preparation PR or local tag push is used.
