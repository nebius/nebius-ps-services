#!/usr/bin/env bash
set -euo pipefail

TAG_PREFIX="nebius-acc"
WHEEL_PATTERN="nebius_acc-*.whl"

usage() {
  local bold reset cyan yellow
  if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
    bold=$'\033[1m'
    cyan=$'\033[36m'
    yellow=$'\033[33m'
    reset=$'\033[0m'
  else
    bold=""
    cyan=""
    yellow=""
    reset=""
  fi

  cat <<EOF
${bold}Usage:${reset}
  ${cyan}./release.sh${reset} ${yellow}--prep${reset} ${TAG_PREFIX}-vX.Y.Z
    # update changelog, commit, push branch
  ${cyan}./release.sh${reset} ${yellow}--publish${reset} ${TAG_PREFIX}-vX.Y.Z
    # main only, clean, up-to-date; tag/build/release
  ${cyan}./release.sh${reset} ${yellow}--verify${reset} ${TAG_PREFIX}-vX.Y.Z
    # verify existing release asset

${bold}Options:${reset}
  ${yellow}--force-retag${reset}
    # allow deleting/recreating tag (publish only)

${bold}Tag format:${reset}
  ${yellow}${TAG_PREFIX}-vMAJOR.MINOR.PATCH${reset}
    # required to avoid tag collisions in this multi-project repo
EOF
}

die() { echo "ERROR: $*" >&2; exit 1; }

confirm() {
  local ans
  read -r -p "$1 [y/N] " ans
  [[ "$ans" == "y" || "$ans" == "Y" ]]
}

ensure_gh() {
  command -v gh >/dev/null 2>&1 && return
  echo "GitHub CLI (gh) not found."
  exit 1
}

get_token() {
  if [[ -n "${GH_TOKEN:-}" ]]; then
    export GH_TOKEN
    return
  fi
  if [[ -n "${GITHUB_TOKEN:-}" ]]; then
    export GH_TOKEN="${GITHUB_TOKEN}"
    return
  fi
  ensure_gh
  if ! gh auth status -h github.com >/dev/null 2>&1; then
    gh auth login \
      --web \
      --hostname github.com \
      --git-protocol https \
      --skip-ssh-key
  fi
  GH_TOKEN="$(gh auth token 2>/dev/null)"
  export GH_TOKEN
}

update_changelog() {
  local tag="$1" changelog="CHANGELOG.md" date_str
  date_str="$(date +%Y-%m-%d)"
  python - "$tag" "$date_str" "$changelog" <<'PY'
import pathlib
import re
import sys

tag, date_str, path = sys.argv[1], sys.argv[2], pathlib.Path(sys.argv[3])
text = path.read_text()

marker = "## [Unreleased]"
if marker not in text:
    raise SystemExit("Unreleased section not found")

release_header = f"## [{tag}] - {date_str}"
if re.search(rf"^## \[{re.escape(tag)}\] -", text, re.MULTILINE):
    raise SystemExit(0)

match = re.search(r"^## \[Unreleased\]\s*$", text, re.MULTILINE)
if not match:
    raise SystemExit("Unable to locate Unreleased heading")

post = text[match.end():].lstrip("\n")
new_text = (
    text[: match.start()]
    + "## [Unreleased]\n\n"
    + release_header
    + "\n\n"
    + post
)
path.write_text(new_text)
PY
}

prep_release() {
  local tag="$1"
  update_changelog "$tag"
  git add -A
  if ! git diff --cached --quiet; then
    git commit -m "Prepare release ${tag}"
  fi
  git push
  echo "Done. Open a PR."
}

require_main_clean() {
  if [[ "$(git rev-parse --abbrev-ref HEAD)" != "main" ]]; then
    die "must be on main"
  fi
  if ! git diff --quiet || ! git diff --cached --quiet; then
    die "working tree not clean"
  fi
  git fetch origin
  if [[ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]]; then
    die "local main not at origin/main"
  fi
}

retag_cleanup() {
  local tag="$1"
  if gh release view "$tag" >/dev/null 2>&1; then
    gh release delete "$tag" -y || true
  fi
  git tag -d "$tag" >/dev/null 2>&1 || true
  git push origin :refs/tags/"$tag" >/dev/null 2>&1 || true
}

ensure_tag_available() {
  local tag="$1"
  if git rev-parse --verify --quiet "$tag" >/dev/null 2>&1; then
    local tag_commit head_commit
    tag_commit="$(git rev-parse "$tag")"
    head_commit="$(git rev-parse HEAD)"

    if [[ "${ALLOW_RETAG:-0}" != "1" ]]; then
      if [[ "${tag_commit}" == "${head_commit}" ]]; then
        die "tag ${tag} already exists on current HEAD; use --verify or --force-retag"
      fi
      die "tag ${tag} exists at ${tag_commit} (current HEAD is ${head_commit}); use --force-retag to overwrite"
    fi

    echo "WARNING: tag ${tag} already exists."
    echo "Tag commit : ${tag_commit}"
    echo "HEAD commit: ${head_commit}"
    echo "This will delete the existing tag and any GitHub release named ${tag}."
    confirm "Proceed with retagging ${tag}?" || exit 1
    get_token
    retag_cleanup "$tag"
  fi
}

verify_release_asset() {
  local tag="$1"
  ensure_gh
  echo "==> Downloading wheel from GitHub release ${tag} to verify integrity..."
  local tmp_dir
  tmp_dir="$(mktemp -d)"
  gh release download "$tag" \
    --pattern "${WHEEL_PATTERN}" \
    --dir "$tmp_dir" \
    --clobber >/dev/null
  local wheel
  wheel="$(find "$tmp_dir" -maxdepth 1 -type f -name "${WHEEL_PATTERN}" -print -quit)"
  if [[ -z "$wheel" ]]; then
    rm -rf "$tmp_dir"
    die "no wheel downloaded"
  fi
  echo "==> Verifying downloaded wheel zip structure..."
  if ! python -m zipfile -t "$wheel" >/dev/null 2>&1; then
    rm -rf "$tmp_dir"
    die "wheel corrupt"
  fi
  rm -rf "$tmp_dir"
  echo "Release asset OK."
}

publish_release() {
  local tag="$1"
  require_main_clean
  ensure_tag_available "$tag"
  confirm "Proceed to create tag/release?" || exit 1
  git tag -a "$tag" -m "Release ${tag}"
  git push origin "$tag"
  get_token
  local release_exists=0
  if gh release view "$tag" >/dev/null 2>&1; then
    release_exists=1
    echo "Release ${tag} already exists; skipping build and release creation."
  fi

  if [[ "$release_exists" -eq 0 ]]; then
    rm -rf dist
    python -m build --wheel
    local wheel expected wheel_version
    mapfile -t wheels < <(find dist -maxdepth 1 -type f -name "${WHEEL_PATTERN}" | sort)
    if [[ "${#wheels[@]}" -eq 0 ]]; then
      die "wheel not found in dist/"
    fi
    if [[ "${#wheels[@]}" -gt 1 ]]; then
      echo "Multiple wheels found, using the first one:"
      printf '  %s\n' "${wheels[@]}"
    fi
    wheel="${wheels[0]}"
    expected="${tag#${TAG_PREFIX}-v}"
    wheel_version="$(python - "$wheel" <<'PY'
import sys
import zipfile
from email import message_from_bytes

w = sys.argv[1]
with zipfile.ZipFile(w) as zf:
    meta = [n for n in zf.namelist() if n.endswith("METADATA")][0]
    msg = message_from_bytes(zf.read(meta))
    print(msg.get("Version", ""))
PY
)"
    if [[ -z "${wheel_version}" ]]; then
      die "could not read wheel version from ${wheel}"
    fi
    if [[ "${wheel_version}" != "${expected}" ]]; then
      die "wheel version ${wheel_version} != ${expected}"
    fi
    gh release create "$tag" "$wheel" \
      --title "$tag" \
      --notes "Release $tag"
    verify_release_asset "$tag"
    echo "Done. Published ${tag} with asset: ${wheel}"
  else
    echo "Done. Release ${tag} already existed; no new asset published."
  fi
}

MODE=""
FORCE_RETAG=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --prep|--publish|--verify)
      MODE="${1#--}"
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
      die "unknown flag: $1"
      ;;
    *)
      break
      ;;
  esac
done

TAG="${TAG:-${1-}}"
if [[ -z "$MODE" || -z "$TAG" ]]; then
  usage
  exit 1
fi
if [[ -n "${2-}" ]]; then
  die "unexpected extra arguments: ${*:2}"
fi
if [[ ! "$TAG" =~ ^${TAG_PREFIX}-v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  die "Tag must be ${TAG_PREFIX}-vMAJOR.MINOR.PATCH"
fi
if [[ "$FORCE_RETAG" -eq 1 && "$MODE" != "publish" ]]; then
  die "--force-retag only valid with --publish"
fi
if [[ "$FORCE_RETAG" -eq 1 ]]; then
  export ALLOW_RETAG=1
fi

case "$MODE" in
  prep) prep_release "$TAG" ;;
  publish) publish_release "$TAG" ;;
  verify) verify_release_asset "$TAG" ;;
  *) usage; exit 1 ;;
esac
