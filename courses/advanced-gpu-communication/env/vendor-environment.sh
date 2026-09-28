#!/usr/bin/env bash
# Source from the advanced course after explicit installation.
: "${COURSE_TOOLS:?source profiler environment}"
: "${UCX_PREFIX:?prepared standalone UCX prefix}"
: "${COURSE_ETCD:?prepared shared etcd executable}"
export COURSE_BASE_PYTHON="$HOME/courses/.venvs/advanced-gpu-communication/bin/python"
export COURSE_BASE_TORCHRUN="$HOME/courses/.venvs/advanced-gpu-communication/bin/torchrun"
export COURSE_PYTHON="$COURSE_BASE_PYTHON"
export COURSE_TORCHRUN="$COURSE_BASE_TORCHRUN"
export COURSE_BRIDGE_PYTHON="$COURSE_TOOLS/vendor-candidates/bridge/bin/python"
export COURSE_BRIDGE_TORCHRUN="$PWD/env/bridge-torchrun"
export COURSE_DYNAMO_PYTHON="$COURSE_TOOLS/vendor-candidates/dynamo/bin/python"
export COURSE_AIPERF="$COURSE_TOOLS/vendor-candidates/aiperf/bin/aiperf"
export COURSE_NIXLBENCH="$PWD/env/nixlbench"
