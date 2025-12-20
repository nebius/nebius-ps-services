#!/usr/bin/env bash
set -euo pipefail

usage() {
  local b=$'\033[1m'
  local g=$'\033[32m'
  local c=$'\033[36m'
  local r=$'\033[0m'
  cat <<EOF
${b}Usage:${r}
  ${g}./release.sh${r} ${c}vX.Y.Z${r}            # full flow: commit/tag/build/release
  ${g}./release.sh${r} ${c}--verify vX.Y.Z${r}   # verify an existing release asset only
EOF
}

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

verify_release_asset() {
  local tag="$1"
  ensure_gh
  echo "==> Downloading wheel from GitHub release ${tag} to verify integrity..."
  local tmp_dir
  tmp_dir="$(mktemp -d)"
  gh release download "${tag}" \
    --pattern 'nebius_vpngw-*.whl' \
    --dir "${tmp_dir}" \
    --clobber >/dev/null

  local downloaded
  downloaded="$(find "${tmp_dir}" -maxdepth 1 -type f -name 'nebius_vpngw-*.whl' -print -quit)"
  if [[ -z "${downloaded}" ]]; then
    echo "ERROR: Could not find downloaded wheel in ${tmp_dir}; aborting integrity check." >&2
    rm -rf "${tmp_dir}"
    exit 1
  fi

  echo "==> Verifying downloaded wheel zip structure..."
  if ! python -m zipfile -t "${downloaded}" >/dev/null 2>&1; then
    echo "ERROR: Downloaded wheel ${downloaded} appears to be corrupt." >&2
    rm -rf "${tmp_dir}"
    exit 1
  fi

  rm -rf "${tmp_dir}"
  echo "==> Downloaded wheel integrity check passed."
}

VERIFY_ONLY=0
if [[ "${1-}" == "--verify" ]]; then
  VERIFY_ONLY=1
  shift
fi
if [[ "${1-}" == "--help" || "${1-}" == "-h" ]]; then
  usage
  exit 0
fi

# Tag can come from env (TAG) or positional arg
TAG="${TAG:-${1-}}"
ALLOW_RETAG="${ALLOW_RETAG:-1}"
RETAGGED=0

if [[ -z "${TAG}" ]]; then
  usage
  exit 1
fi

if [[ ! "${TAG}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "Tag must be in form vMAJOR.MINOR.PATCH (e.g., v0.4.0)"
  exit 1
fi

if [[ "${VERIFY_ONLY}" -eq 1 ]]; then
  verify_release_asset "${TAG}"
  exit 0
fi

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

TAG_EXISTS=0
if git rev-parse --verify --quiet "${TAG}" >/dev/null 2>&1; then
  TAG_EXISTS=1
  TAG_COMMIT="$(git rev-parse "${TAG}")"
  HEAD_COMMIT="$(git rev-parse HEAD)"
  if [[ "${TAG_COMMIT}" != "${HEAD_COMMIT}" ]]; then
    if [[ "${ALLOW_RETAG}" == "1" ]]; then
      echo "Head commit (${HEAD_COMMIT}) differs from tag ${TAG} (${TAG_COMMIT}); retagging because ALLOW_RETAG=1."
      # Try to delete existing GitHub release for this tag (ignore errors if it does not exist)
      if gh release view "${TAG}" >/dev/null 2>&1; then
        echo "Deleting existing GitHub release ${TAG} (ALLOW_RETAG=1)..."
        gh release delete "${TAG}" -y || true
      fi
      echo "Deleting existing tag ${TAG} locally and remotely..."
      git tag -d "${TAG}" >/dev/null 2>&1 || true
      git push origin :refs/tags/"${TAG}" >/dev/null 2>&1 || true
      TAG_EXISTS=0
      RETAGGED=1
    else
      echo "Head commit (${HEAD_COMMIT}) does not match existing tag ${TAG} (${TAG_COMMIT})."
      echo "Set ALLOW_RETAG=1 to retag the current HEAD, or checkout the tagged commit."
      exit 1
    fi
  fi
  if [[ "${TAG_EXISTS}" -eq 1 ]]; then
    echo "Tag ${TAG} already exists on current HEAD."
  elif [[ "${RETAGGED}" -eq 1 ]]; then
    echo "Tag ${TAG} was retagged to current HEAD."
  fi
fi

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

# Ensure we keep the content that followed Unreleased, normalized to a single blank line
post = text[match.end():]
post = post.lstrip("\n")

new_text = (
    text[: match.start()]
    + "## [Unreleased]\n\n"
    + release_header
    + "\n\n"
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
if [[ "${TAG_EXISTS}" -eq 1 ]]; then
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

  mapfile -t wheels < <(find dist -maxdepth 1 -type f -name "nebius_vpngw-*.whl" | sort)
  if [[ "${#wheels[@]}" -eq 0 ]]; then
    echo "Wheel not found in dist/. Aborting."
    exit 1
  fi
  if [[ "${#wheels[@]}" -gt 1 ]]; then
    echo "Multiple wheels found, using the first one:"
    printf '  %s\n' "${wheels[@]}"
  fi
  WHEEL_PATH="${wheels[0]}"

  EXPECTED_VERSION="${TAG#v}"
  WHEEL_VERSION="$(python - "$WHEEL_PATH" <<'PY'
import sys
import zipfile
from email import message_from_bytes

w = sys.argv[1]
with zipfile.ZipFile(w) as zf:
    meta_names = [n for n in zf.namelist() if n.endswith("METADATA")]
    if not meta_names:
        print("")
        sys.exit(0)
    meta = message_from_bytes(zf.read(meta_names[0]))
    print(meta.get("Version", ""))
PY
)"

  if [[ -z "${WHEEL_VERSION}" ]]; then
    echo "Could not read wheel version from ${WHEEL_PATH}; aborting."
    exit 1
  fi

  if [[ "${WHEEL_VERSION}" != "${EXPECTED_VERSION}" ]]; then
    echo "Wheel version (${WHEEL_VERSION}) does not match tag (${EXPECTED_VERSION}). Aborting to avoid publishing a mismatched artifact."
    exit 1
  fi

  echo "==> Creating GitHub release ${TAG} with asset ${WHEEL_PATH}..."
  gh release create "${TAG}" "${WHEEL_PATH}" --title "${TAG}" --notes "Release ${TAG}"

  verify_release_asset "${TAG}"
else
  echo "==> Skipping GitHub release creation; already exists."
fi

if [[ "${RELEASE_EXISTS}" -eq 0 ]]; then
  echo "==> Done. Published ${TAG} with asset: ${WHEEL_PATH}"
else
  echo "==> Done. Release ${TAG} already existed; no new asset published."
fi
