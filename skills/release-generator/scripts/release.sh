#!/usr/bin/env bash
set -euo pipefail

TAG_PREFIX="${TAG_PREFIX:-}"
WHEEL_PATTERN="${WHEEL_PATTERN:-*.whl}"

if [[ -n "${TAG_PREFIX}" && ! "${TAG_PREFIX}" =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "ERROR: TAG_PREFIX must use letters, digits, dot, underscore, or hyphen." >&2
  exit 1
fi

usage() {
  local b=$'\033[1m'
  local g=$'\033[32m'
  local c=$'\033[36m'
  local r=$'\033[0m'
  local tag_example="vX.Y.Z"
  local tag_format="vMAJOR.MINOR.PATCH"
  if [[ -n "${TAG_PREFIX}" ]]; then
    tag_example="${TAG_PREFIX}-vX.Y.Z"
    tag_format="${TAG_PREFIX}-vMAJOR.MINOR.PATCH"
  else
    tag_example="vX.Y.Z (or <prefix>-vX.Y.Z)"
    tag_format="vMAJOR.MINOR.PATCH (or <prefix>-vMAJOR.MINOR.PATCH)"
  fi
  cat <<EOF
${b}Usage:${r}
  ${g}./release.sh${r} ${c}--prep ${tag_example}${r}     # prepare changelog commit and push branch
  ${g}./release.sh${r} ${c}--publish ${tag_example}${r}  # main only, clean, up-to-date; tag/build/release
  ${g}./release.sh${r} ${c}--verify ${tag_example}${r}   # verify an existing release asset only

${b}Options:${r}
  ${c}--force-retag${r}                          # allow deleting/recreating existing tag (publish only)

${b}Tag format:${r}
  ${c}${tag_format}${r}       # use a prefix in multi-project repos

${b}Environment:${r}
  ${c}TAG_PREFIX${r}            # optional prefix to require <prefix>-vX.Y.Z tags
  ${c}WHEEL_PATTERN${r}         # wheel glob pattern (default: *.whl)
EOF
}

die() {
  echo "ERROR: $*" >&2
  exit 1
}

extract_version_from_tag() {
  local tag="$1"
  if [[ "${tag}" =~ ^v([0-9]+\.[0-9]+\.[0-9]+)$ ]]; then
    echo "${BASH_REMATCH[1]}"
    return 0
  fi
  if [[ "${tag}" =~ ^.+-v([0-9]+\.[0-9]+\.[0-9]+)$ ]]; then
    echo "${BASH_REMATCH[1]}"
    return 0
  fi
  return 1
}

confirm() {
  local prompt="$1"
  local ans
  read -r -p "${prompt} [y/N] " ans
  [[ "${ans}" == "y" || "${ans}" == "Y" ]]
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
    --pattern "${WHEEL_PATTERN}" \
    --dir "${tmp_dir}" \
    --clobber >/dev/null

  local downloaded
  downloaded="$(find "${tmp_dir}" -maxdepth 1 -type f -name "${WHEEL_PATTERN}" -print -quit)"
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

TOKEN=""
TAG_EXISTS_ON_HEAD=0

get_token() {
  if [[ -n "${GH_TOKEN:-}" ]]; then
    TOKEN="${GH_TOKEN}"
  elif [[ -n "${GITHUB_TOKEN:-}" ]]; then
    TOKEN="${GITHUB_TOKEN}"
  else
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
  fi
  export GH_TOKEN="${TOKEN}"
}

update_changelog() {
  local tag="$1"
  local changelog="CHANGELOG.md"
  local release_date
  release_date="$(date +%Y-%m-%d)"
  python - "$tag" "$release_date" "$changelog" <<'PY'
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

header_re = re.compile(r"^## \[.+?\].*$", re.MULTILINE)
headers = list(header_re.finditer(text))
if not headers:
    print("No CHANGELOG headers found", file=sys.stderr)
    sys.exit(1)

preamble = text[: headers[0].start()]
sections = []
for i, h in enumerate(headers):
    start = h.start()
    end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
    header = h.group(0)
    content = text[h.end() : end]
    sections.append((header, content))

def normalize_content(content: str) -> str:
    stripped = content.strip("\n")
    if not stripped.strip():
        return "\n"
    return "\n\n" + stripped + "\n"

def merge_content(new_part: str, existing: str) -> str:
    parts = []
    if new_part.strip():
        parts.append(new_part.strip("\n"))
    if existing.strip():
        parts.append(existing.strip("\n"))
    if not parts:
        return "\n"
    return "\n\n" + "\n\n".join(parts) + "\n"

unreleased_idx = None
tag_idx = None
for idx, (header, _) in enumerate(sections):
    if header.strip() == "## [Unreleased]":
        unreleased_idx = idx
    if header.startswith(f"## [{tag}] -"):
        tag_idx = idx

if unreleased_idx is None:
    print("Unable to locate Unreleased heading", file=sys.stderr)
    sys.exit(1)

unreleased_header, unreleased_content = sections[unreleased_idx]
unreleased_payload = unreleased_content.strip("\n")

if tag_idx is None:
    # Insert new release header after Unreleased
    new_sections = []
    for idx, (header, content) in enumerate(sections):
        if idx == unreleased_idx:
            new_sections.append((header, "\n\n"))
            new_sections.append((release_header, normalize_content(unreleased_payload)))
        else:
            new_sections.append((header, content))
    sections = new_sections
else:
    # Move Unreleased payload into existing tag section
    if unreleased_payload.strip():
        tag_header, tag_content = sections[tag_idx]
        sections[tag_idx] = (tag_header, merge_content(unreleased_payload, tag_content))
    # Always clear Unreleased content
    sections[unreleased_idx] = (unreleased_header, "\n\n")

new_text = preamble + "".join(f"{h}{c}" for h, c in sections)
path.write_text(new_text)
PY
}

prep_release() {
  local tag="$1"
  echo "==> Updating CHANGELOG.md..."
  update_changelog "${tag}"

  git add -A
  if git diff --cached --quiet; then
    echo "No staged changes. Skipping commit."
  else
    git commit -m "Prepare release ${tag}"
  fi

  echo "==> Pushing current branch..."
  git push

  echo "==> Done. Open a PR when ready."
}

require_main_clean() {
  local branch
  branch="$(git rev-parse --abbrev-ref HEAD)"
  if [[ "${branch}" != "main" ]]; then
    echo "ERROR: must be on main (currently: ${branch})"
    echo "Stash or commit changes, then run:"
    echo "  git stash -a"
    echo "  git switch main"
    exit 1
  fi

  if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "ERROR: working tree is not clean."
    git status --porcelain
    echo "Stash or commit changes, then retry:"
    echo "  git stash -a"
    exit 1
  fi

  git fetch origin
  local local_commit remote_commit
  local_commit="$(git rev-parse HEAD)"
  remote_commit="$(git rev-parse origin/main)"
  if [[ "${local_commit}" != "${remote_commit}" ]]; then
    echo "ERROR: local main is not at origin/main"
    echo "local : ${local_commit}"
    echo "origin: ${remote_commit}"
    exit 1
  fi
}

retag_cleanup() {
  local tag="$1"
  if gh release view "${tag}" >/dev/null 2>&1; then
    echo "Deleting existing GitHub release ${tag}..."
    gh release delete "${tag}" -y || true
  fi
  echo "Deleting existing tag ${tag} locally and remotely..."
  git tag -d "${tag}" >/dev/null 2>&1 || true
  git push origin :refs/tags/"${tag}" >/dev/null 2>&1 || true
}

ensure_tag_available() {
  local tag="$1"
  TAG_EXISTS_ON_HEAD=0
  if git rev-parse --verify --quiet "${tag}" >/dev/null 2>&1; then
    local tag_commit head_commit
    tag_commit="$(git rev-parse "${tag}")"
    head_commit="$(git rev-parse HEAD)"
    get_token
    local release_exists=0
    if gh release view "${tag}" >/dev/null 2>&1; then
      release_exists=1
    fi

    if [[ "${tag_commit}" == "${head_commit}" && "${release_exists}" -eq 0 ]]; then
      echo "==> Tag ${tag} already exists on current HEAD and no GitHub release was found. Reusing tag."
      TAG_EXISTS_ON_HEAD=1
      return 0
    fi

    if [[ "${ALLOW_RETAG}" != "1" ]]; then
      if [[ "${release_exists}" -eq 0 ]]; then
        echo "WARNING: tag ${tag} exists at ${tag_commit} (current HEAD is ${head_commit}), but no GitHub release was found."
        echo "This typically means the tag was created during a previous attempt."
        if ! confirm "Retag ${tag} to current HEAD?"; then
          exit 1
        fi
        retag_cleanup "${tag}"
        return 0
      fi
      if [[ "${tag_commit}" == "${head_commit}" ]]; then
        echo "ERROR: tag ${tag} already exists on current HEAD and a GitHub release was found."
        echo "Use --verify to check the release asset, or --force-retag to recreate it."
      else
        echo "ERROR: tag ${tag} exists at ${tag_commit} (current HEAD is ${head_commit})."
        echo "Refusing to retag by default. Use --force-retag to overwrite."
      fi
      exit 1
    fi

    echo "WARNING: tag ${tag} already exists."
    echo "Tag commit : ${tag_commit}"
    echo "HEAD commit: ${head_commit}"
    echo "This will delete the existing tag and any GitHub release named ${tag}."
    if ! confirm "Proceed with retagging ${tag}?"; then
      exit 1
    fi

    get_token
    retag_cleanup "${tag}"
  fi
}

publish_release() {
  local tag="$1"
  require_main_clean
  ensure_tag_available "${tag}"

  echo "About to release from:"
  git --no-pager log -1 --decorate --oneline
  echo
  git --no-pager show -s --format="Commit: %H%nAuthor: %an%nDate:   %ad%n%n%s" HEAD
  echo

  if ! confirm "Proceed to create tag/release?"; then
    exit 1
  fi

  if [[ "${TAG_EXISTS_ON_HEAD}" -eq 1 ]]; then
    echo "==> Tag ${tag} already exists on current HEAD. Pushing tag to origin if needed..."
    git push origin "refs/tags/${tag}"
  else
    echo "==> Creating tag ${tag} and pushing..."
    git tag -a "${tag}" -m "Release ${tag}"
    git push origin "refs/tags/${tag}"
  fi

  echo "==> Building wheel from tagged state..."
  RELEASE_EXISTS=0
  if gh release view "${tag}" >/dev/null 2>&1; then
    RELEASE_EXISTS=1
    echo "Release ${tag} already exists; skipping build and release creation."
  fi

  if [[ "${RELEASE_EXISTS}" -eq 0 ]]; then
    rm -rf dist
    python -m build --wheel

    mapfile -t wheels < <(find dist -maxdepth 1 -type f -name "${WHEEL_PATTERN}" | sort)
    if [[ "${#wheels[@]}" -eq 0 ]]; then
      echo "Wheel not found in dist/. Aborting."
      exit 1
    fi
    if [[ "${#wheels[@]}" -gt 1 ]]; then
      echo "Multiple wheels found, using the first one:"
      printf '  %s\n' "${wheels[@]}"
    fi
    WHEEL_PATH="${wheels[0]}"

    EXPECTED_VERSION="$(extract_version_from_tag "${tag}")" || {
      echo "Could not parse version from tag ${tag}; aborting."
      exit 1
    }
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

    echo "==> Creating GitHub release ${tag} with asset ${WHEEL_PATH}..."
    gh release create "${tag}" "${WHEEL_PATH}" --title "${tag}" --notes "Release ${tag}"

    verify_release_asset "${tag}"
  else
    echo "==> Skipping GitHub release creation; already exists."
  fi

  if [[ "${RELEASE_EXISTS}" -eq 0 ]]; then
    echo "==> Done. Published ${tag} with asset: ${WHEEL_PATH}"
  else
    echo "==> Done. Release ${tag} already existed; no new asset published."
  fi
}

MODE=""
FORCE_RETAG=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prep)
      MODE="prep"
      shift
      ;;
    --publish)
      MODE="publish"
      shift
      ;;
    --verify)
      MODE="verify"
      shift
      ;;
    --force-retag)
      FORCE_RETAG=1
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    --)
      shift
      break
      ;;
    -*)
      echo "Unknown flag: $1"
      usage
      exit 1
      ;;
    *)
      break
      ;;
  esac
done

if [[ -z "${MODE}" ]]; then
  usage
  exit 1
fi

if [[ "${FORCE_RETAG}" -eq 1 && "${MODE}" != "publish" ]]; then
  die "--force-retag is only valid with --publish"
fi

TAG="${TAG:-${1-}}"
if [[ -z "${TAG}" ]]; then
  usage
  exit 1
fi
if [[ -n "${2-}" ]]; then
  die "Unexpected extra arguments: ${*:2}"
fi

if [[ -n "${TAG_PREFIX}" ]]; then
  if [[ ! "${TAG}" =~ ^${TAG_PREFIX}-v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    die "Tag must be in form ${TAG_PREFIX}-vMAJOR.MINOR.PATCH"
  fi
else
  if [[ ! "${TAG}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ && ! "${TAG}" =~ ^.+-v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    die "Tag must be vMAJOR.MINOR.PATCH or <prefix>-vMAJOR.MINOR.PATCH"
  fi
fi

ALLOW_RETAG="${ALLOW_RETAG:-0}"
if [[ "${FORCE_RETAG}" -eq 1 ]]; then
  ALLOW_RETAG=1
fi

case "${MODE}" in
  prep)
    prep_release "${TAG}"
    ;;
  publish)
    publish_release "${TAG}"
    ;;
  verify)
    verify_release_asset "${TAG}"
    ;;
  *)
    usage
    exit 1
    ;;
esac
