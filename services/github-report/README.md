# github-report

`github-report` is a Python CLI that ranks contributors across GitHub
repositories owned by a GitHub organization or personal account.

It works with any GitHub owner that your token can access. `--owner` is
required for every report or listing command.

It is optimized for executive reporting:

- scans only each repository's default branch
- ranks by commits or total code modifications (`additions + deletions`)
- filters the scope to all accessible repos, a comma-separated repo list, a
  text file, or a comma-separated exclusion list
- shows GitHub profile names when available, with login fallback
- emits plain text, Markdown, CSV, or Word-friendly HTML

The CLI reads `GITHUB_TOKEN` first and falls back to `GH_TOKEN`.

When `--format markdown` is printed to an interactive terminal, the CLI renders
a readable table.
Redirected stdout and `--output` still preserve raw Markdown.
Interactive report runs also show a transient spinner on stderr while GitHub
activity is collected.
Report summaries label the window as `last N days`, `custom window`, or `full
reachable history`.
When `--format` is omitted, `--output report.csv`, `--output report.txt`, and
`--output report.md` infer the format from the file extension.
If you pass `--format` explicitly, that takes precedence over the output file
extension.
If the output file already exists, the CLI overwrites it.
Use `--output report.html` when you want to paste the report into Microsoft
Word or Google Docs with table formatting preserved.

## Defaults

- owner: none, must be provided with `--owner`
- report window: relative days, defaulting to `--days 30`
- output format: `markdown`
- ranking: `modifications`
- top rows: `10`
- bots: excluded when GitHub classifies the account type as `Bot`
- repositories: all accessible repos under the selected owner
- branch scope: repository default branch only

When you omit `--since` and `--all-time`, the CLI uses a relative-days window.
If you also omit `--days`, it behaves as if `--days 30` was provided.

`--until` anchors that relative window. For example, `github-report top-users
--until 2026-03-01` means the default `--days 30` window ending on
`2026-03-01T23:59:59.999999Z`.

Use `--all-time` when you want the entire reachable history of the default
branch instead of the default `--days 30` window.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## End-User Setup

No git clone is required.

Keep the installer in the service root, not under `src/`. It is an
application/distribution artifact, not a Python package module.

Recommended for end users: download the bundled install archive from the
matching GitHub Release and run it from the extracted folder:

```bash
curl -fsSLO \
  https://github.com/nebius/nebius-ps-services/releases/download/github-report-v0.1.0/github-report-install-v0.1.0.tar.gz
tar -xzf github-report-install-v0.1.0.tar.gz
bash ./install-github-report.sh
```

The release also includes these assets:

- `github-report-install-<version>.tar.gz`: recommended bundle for end users
- `install-github-report.sh`: standalone installer
- `github_report-<version>-py3-none-any.whl`: wheel
- `INSTALL.txt`: plain-language install guide
- `github-report-<version>-SHA256SUMS.txt`: checksums

When the bundle already contains `install-github-report.sh` and the matching
wheel, the installer uses that local wheel directly. If no bundled wheel is
present, it falls back to downloading the latest published wheel from GitHub
Releases. In both cases it installs into a dedicated virtual environment and
creates a `~/.local/bin/github-report` launcher. If you do not already have a
GitHub account or token, the installer also shows the signup link, the token
creation link, the minimum token permissions, and copy-paste commands for
setting `GITHUB_TOKEN`.

## Usage

Show the top 10 contributors across all accessible repos under an organization:

```bash
github-report top-users --owner nebius
```

The explicit equivalent is:

```bash
github-report top-users --owner nebius --days 30
```

Run the same report against another organization:

```bash
github-report --owner lm-academy top-users
```

Run the same report against a personal account:

```bash
github-report top-users --owner dashabalashova
```

Show the top 50 contributors since a specific date. This already ranks by
modifications by default:

```bash
github-report top-users --owner nebius --top 50 --since 2026-01-01
```

Use a relative lookback window when you want the last `N` days instead of a
fixed start date:

```bash
github-report top-users --owner nebius --top 50 --days 60
```

`--days` cannot be combined with `--since`. `--all-time` bypasses both.

You can combine `--days` with `--until` to anchor that relative window at a
specific end time:

```bash
github-report top-users --owner lm-academy --top 50 --days 60 --until 2026-03-01
```

You can also anchor the default `--days 30` window without passing `--days`
explicitly:

```bash
github-report top-users --owner nebius --top 50 --until 2026-03-01
```

Switch back to commit-first ranking when you need it:

```bash
github-report top-users --owner nebius --top 50 --since 2026-01-01 --sort-by commits
```

Show contributor rows per repository instead of aggregating across the selected
repos:

```bash
github-report top-users --owner dashabalashova --per-repo --top 50 --since 2026-01-01
```

Limit the report to a small repo set:

```bash
github-report top-users --owner nebius --repos pysdk,gosdk,api --since 2026-02-01
```

Exclude specific repositories from an owner-wide report:

```bash
github-report top-users --owner nebius --exclude csa-soperator-deployments,api --since 2026-02-01
```

Exclusions are applied after `--repos` and `--repos-file`, so you can start
with a small include list and still subtract a few repositories.

Load repo filters from a text file:

```bash
github-report top-users --owner lm-academy --repos-file repos.txt --since 2026-02-01
```

Limit the report to one repo under an organization or personal account:

```bash
github-report top-users --owner lm-academy --repos github-actions-course --all-time

github-report top-users --owner dashabalashova --repos boltz-benchmark --all-time
```

Write CSV output to a file:

```bash
github-report top-users --owner nebius --per-repo --format csv --output report.csv --all-time
```

Write Markdown output without passing `--format` explicitly:

```bash
github-report top-users --owner nebius --output report.md
```

Write plain text output for easier copy/paste into Slack or editors:

```bash
github-report top-users --owner nebius --output report.txt
```

Write HTML output for easier copy/paste into Word:

```bash
github-report top-users --owner nebius --output report.html
```

List accessible repositories before building a filter file:

```bash
github-report list-repos --owner nebius
```

List repositories for a personal account:

```bash
github-report list-repos --owner dashabalashova
```

## Repo Filter File

`--repos-file` accepts one repo per line. Short names are resolved against the
selected owner.

```text
# comments are allowed
pysdk
gosdk
nebius/api
```

## Output Shapes

`top-users` returns aggregated contributors by default with:

- `user_name` rendered from GitHub profile name when available
- `num_modifications`
- `num_commits`
- `repos`

`top-users --per-repo` returns one row per `user_name` and `repo_name` with:

- `user_name` rendered from GitHub profile name when available
- `repo_name`
- `num_modifications`
- `num_commits`

## Development

```bash
make install
make fmt
make lint
make test-unit
make build
```

Optional local automation:

```bash
.venv/bin/pip install pre-commit
.venv/bin/pre-commit install
```

## CI

The monorepo CI workflow for this service is
`.github/workflows/github-report-ci.yml` with:

- pull requests: `lint`, fast `unit-tests`, `build`
- release tags or manual runs: `integration-tests`, `coverage`, `packaging`

Release tags should follow `github-report-vMAJOR.MINOR.PATCH`.

## Release Flow

This service publishes GitHub Releases from the monorepo workflow
`.github/workflows/github-report-release.yml`.

Use the standard three-step flow:

1. Prepare the changelog on your branch:

   ```bash
   ./publish-release.sh --prep X.Y.Z
   ```

2. Merge that changelog PR to `main`.

3. From a clean, synced `main`, create and push the release tag:

   ```bash
   ./publish-release.sh --publish X.Y.Z
   ```

`--prep` updates [CHANGELOG.md](CHANGELOG.md) into a
`github-report-vX.Y.Z` section and commits it. `--publish` only creates and
pushes the tag; it refuses to proceed if the changelog section is missing, the
worktree is dirty, or `HEAD` is not at `origin/main` unless you explicitly
override that with `--allow-non-main`.
