#!/usr/bin/env bash

# Usage: ./release.sh vX.Y.Z
# Tags the current HEAD, builds the wheel, and creates a GitHub release with the wheel asset.
# Requires: git, python, gh, build deps (setuptools, setuptools-scm, build).

set -euo pipefail

TAG="${1-}"

if [[ -z "${TAG}" ]]; then
  echo "Usage: $0 vX.Y.Z"
  exit 1
fi

if [[ ! "${TAG}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "Tag must be in form vMAJOR.MINOR.PATCH (e.g., v0.4.0)"
  exit 1
fi

ensure_gh() {
  if command -v gh >/dev/null 2>&1; then
    return
  fi
  echo "GitHub CLI (gh) not found. Attempting to install..."
  if command -v brew >/dev/null 2>&1; then
    brew install gh
  elif command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update
    sudo apt-get install -y gh
  else
    echo "Could not install gh automatically. Install manually: https://github.com/cli/cli#installation"
    exit 1
  fi
}

TOKEN=""

get_token() {
  if [[ -n "${GH_TOKEN:-}" ]]; then
    TOKEN="${GH_TOKEN}"
    return
  fi
  if [[ -n "${GITHUB_TOKEN:-}" ]]; then
    TOKEN="${GITHUB_TOKEN}"
    return
  fi

  ensure_gh

  if ! gh auth status -h github.com >/dev/null 2>&1; then
    echo "Logging into GitHub via browser..."
    gh auth login \
      --web \
      --hostname github.com \
      --git-protocol https \
      --skip-ssh-key
  fi

  TOKEN="$(gh auth token 2>/dev/null)"
}

get_token
export GH_TOKEN="${TOKEN}"

echo "==> Committing any staged changes (if any)..."
echo "==> Updating CHANGELOG.md..."
CHANGELOG="CHANGELOG.md"
RELEASE_DATE="$(date +%Y-%m-%d)"
python - "$TAG" "$RELEASE_DATE" "$CHANGELOG" <<'PY'
import sys
import pathlib
import re

tag, date_str, changelog_path = sys.argv[1], sys.argv[2], sys.argv[3]
path = pathlib.Path(changelog_path)
text = path.read_text()

marker = "## [Unreleased]"
if marker not in text:
    print("Unreleased section not found in CHANGELOG.md", file=sys.stderr)
    sys.exit(1)

release_header = f"## [{tag}] - {date_str}"

# If this release already exists in the changelog, do nothing (idempotent)
if release_header in text:
    sys.exit(0)

# Replace the first Unreleased heading with a new Unreleased + release header
pattern = re.compile(r"^## \[Unreleased\]\s*$", re.MULTILINE)
match = pattern.search(text)
if not match:
    print("Unable to locate Unreleased heading", file=sys.stderr)
    sys.exit(1)

# Ensure we keep the content that followed Unreleased
post = text[match.end():]
if post.startswith("\n"):
    post = post[1:]

new_text = (
    text[: match.start()]
    + "## [Unreleased]\n\n"
    + release_header
    + "\n"
    + post
)

path.write_text(new_text)
PY

git add -A
if git diff --cached --quiet; then
  echo "No staged changes. Skipping commit."
else
  git commit -m "Release ${TAG} commit"
fi

echo "==> Pushing current branch..."
git push

echo "==> Creating tag ${TAG} and pushing..."
if git rev-parse "${TAG}" >/dev/null 2>&1; then
  echo "Tag ${TAG} already exists; skipping tag creation."
else
  git tag -a "${TAG}" -m "Release ${TAG}"
  git push origin "${TAG}"
fi

echo "==> Building wheel from tagged state..."
RELEASE_EXISTS=0
if gh release view "${TAG}" >/dev/null 2>&1; then
  RELEASE_EXISTS=1
  echo "Release ${TAG} already exists; skipping build and release creation."
fi

if [[ "${RELEASE_EXISTS}" -eq 0 ]]; then
  rm -rf dist
  python -m build --wheel

  WHEEL_PATH="$(ls dist/nebius_vpngw-*.whl | head -n 1 || true)"
  if [[ -z "${WHEEL_PATH}" ]]; then
    echo "Wheel not found in dist/. Aborting."
    exit 1
  fi

  echo "==> Creating GitHub release ${TAG} with asset ${WHEEL_PATH}..."
  gh release create "${TAG}" "${WHEEL_PATH}" --title "${TAG}" --notes "Release ${TAG}"
else
  echo "==> Skipping GitHub release creation; already exists."
fi

if [[ "${RELEASE_EXISTS}" -eq 0 ]]; then
  echo "==> Done. Published ${TAG} with asset: ${WHEEL_PATH}"
else
  echo "==> Done. Release ${TAG} already existed; no new asset published."
fi
