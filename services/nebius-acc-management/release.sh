#!/usr/bin/env bash
set -euo pipefail

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
  ${cyan}./release.sh${reset} ${yellow}--prep${reset} vX.Y.Z
    # update changelog, commit, push branch
  ${cyan}./release.sh${reset} ${yellow}--publish${reset} vX.Y.Z
    # main only, clean, up-to-date; tag/build/release
  ${cyan}./release.sh${reset} ${yellow}--verify${reset} vX.Y.Z
    # verify existing release asset

${bold}Options:${reset}
  ${yellow}--force-retag${reset}
    # allow deleting/recreating tag (publish only)
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
    if [[ "${ALLOW_RETAG:-0}" != "1" ]]; then
      die "tag exists; use --force-retag"
    fi
    confirm "Retag ${tag}? This deletes existing tag/release." || exit 1
    get_token
    retag_cleanup "$tag"
  fi
}

verify_release_asset() {
  local tag="$1"
  ensure_gh
  local tmp_dir
  tmp_dir="$(mktemp -d)"
  gh release download "$tag" \
    --pattern '*.whl' \
    --dir "$tmp_dir" \
    --clobber >/dev/null
  local wheel
  wheel="$(find "$tmp_dir" -maxdepth 1 -type f -name '*.whl' -print -quit)"
  [[ -n "$wheel" ]] || die "no wheel downloaded"
  python -m zipfile -t "$wheel" >/dev/null 2>&1 || die "wheel corrupt"
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
  rm -rf dist
  python -m build --wheel
  local wheel expected
  wheel="$(ls dist/*.whl | head -n 1)"
  [[ -n "$wheel" ]] || die "wheel not found"
  expected="${tag#v}"
  python - "$wheel" "$expected" <<'PY'
import sys
import zipfile
from email import message_from_bytes

wheel, expected = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(wheel) as zf:
    meta = [n for n in zf.namelist() if n.endswith("METADATA")][0]
    msg = message_from_bytes(zf.read(meta))
    version = msg.get("Version", "")
    if version != expected:
        raise SystemExit(f"Wheel version {version} != {expected}")
PY
  gh release create "$tag" "$wheel" \
    --title "$tag" \
    --notes "Release $tag"
  verify_release_asset "$tag"
}

MODE=""
FORCE_RETAG=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --prep|--publish|--verify) MODE="${1#--}"; shift ;;
    --force-retag) FORCE_RETAG=1; shift ;;
    --help|-h) usage; exit 0 ;;
    *) break ;;
  esac
done

TAG="${TAG:-${1-}}"
if [[ -z "$MODE" || -z "$TAG" ]]; then
  usage
  exit 1
fi
if [[ ! "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  die "Tag must be vMAJOR.MINOR.PATCH"
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
