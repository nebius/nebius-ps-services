#!/usr/bin/env bash
# Explicit one-time installation, run by the learner only after owner prerequisites.
set -euo pipefail
umask 077
: "${COURSE_TOOLS:?complete shared README setup}"
: "${BRIDGE_BASE_PYTHON:?owner-prepared Python 3.12 training stack}"
: "${UCX_PREFIX:?isolated CUDA-aware UCX 1.22.0 installation}"
: "${DYNAMO_UCX_PREFIX:?CUDA-aware UCX development prefix matching the Dynamo runtime}"
: "${COURSE_ETCD:?owner-prepared shared etcd executable}"
command -v uv >/dev/null
command -v meson >/dev/null
[[ -x $BRIDGE_BASE_PYTHON && -x $COURSE_ETCD && -d $UCX_PREFIX/lib ]] || exit 2
[[ -f $DYNAMO_UCX_PREFIX/include/ucp/api/ucp.h && -f $DYNAMO_UCX_PREFIX/lib/libucp.so ]] || exit 2
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
vendor_root="$COURSE_TOOLS/vendor-candidates"
[[ ! -e $vendor_root ]] || { printf 'ERROR: destination exists; inspect the prior installation before retrying.\n' >&2; exit 2; }
mkdir -m 700 "$vendor_root"
uv venv --python 3.12 "$vendor_root/dynamo"
uv pip install --python "$vendor_root/dynamo/bin/python" --prerelease=allow 'ai-dynamo[vllm]==1.4.2'
"$vendor_root/dynamo/bin/python" "$script_dir/../tools/install_dynamo_native.py" \
  --python "$vendor_root/dynamo/bin/python" --ucx-prefix "$DYNAMO_UCX_PREFIX" \
  --prefix "$vendor_root/dynamo-native"
uv pip check --python "$vendor_root/dynamo/bin/python"
uv venv --python 3.12 "$vendor_root/aiperf"
uv pip install --python "$vendor_root/aiperf/bin/python" 'aiperf==0.12.0'
uv pip check --python "$vendor_root/aiperf/bin/python"
git clone --branch v0.6.0 --recurse-submodules https://github.com/NVIDIA-NeMo/Megatron-Bridge.git "$vendor_root/bridge-src"
[[ $(git -C "$vendor_root/bridge-src" rev-parse HEAD) == 51885cf132b2814188b6855c25a8588254274c2a ]]
uv venv --python "$BRIDGE_BASE_PYTHON" --system-site-packages "$vendor_root/bridge"
(
  cd "$vendor_root/bridge-src"
  UV_PROJECT_ENVIRONMENT="$vendor_root/bridge" uv sync --locked --only-group build
  UV_PROJECT_ENVIRONMENT="$vendor_root/bridge" uv sync --locked --no-default-groups --extra te
)
"$vendor_root/bridge/bin/python" -c 'import torch, transformer_engine.pytorch, megatron.core, megatron.bridge; print(torch.__version__, torch.version.cuda)'
"$vendor_root/bridge/bin/python" -m torch.distributed.run --help >/dev/null
git clone --branch v1.4.1 --recurse-submodules https://github.com/ai-dynamo/nixl.git "$vendor_root/nixl-src"
[[ $(git -C "$vendor_root/nixl-src" rev-parse HEAD) == 778edd1d1a50936b12c264879e12ef465e629002 ]]
meson setup "$vendor_root/nixl-src/build-course" "$vendor_root/nixl-src" \
  --prefix="$vendor_root/nixl" --libdir=lib --buildtype=release \
  -Denable_plugins=UCX -Ducx_path="$UCX_PREFIX" -Dnixl_cuda_arch_list=90 \
  -Dbuild_tests=false -Dbuild_examples=false
meson compile -C "$vendor_root/nixl-src/build-course"
meson install -C "$vendor_root/nixl-src/build-course"
meson setup "$vendor_root/nixl-src/benchmark/nixlbench/build-course" "$vendor_root/nixl-src/benchmark/nixlbench" \
  --prefix="$vendor_root/nixlbench" --libdir=lib --buildtype=release -Dnixl_path="$vendor_root/nixl"
meson compile -C "$vendor_root/nixl-src/benchmark/nixlbench/build-course"
meson install -C "$vendor_root/nixl-src/benchmark/nixlbench/build-course"
"$BRIDGE_BASE_PYTHON" - "$vendor_root/nixlbench/include/nixlbench/config.h" <<'PY'
import pathlib, re, sys
text = pathlib.Path(sys.argv[1]).read_text()
for feature in ('HAVE_ETCD', 'HAVE_CUDA'):
    if not re.search(r'^#define\s+' + feature + r'\s+1\s*$', text, re.M):
        raise SystemExit('NIXLBench missing required feature: ' + feature)
PY
for runtime in bridge dynamo aiperf; do
  uv pip freeze --python "$vendor_root/$runtime/bin/python" > "$vendor_root/$runtime-packages.txt"
done
printf 'Installed candidates. Complete both-worker readiness before performance experiments.\n'
