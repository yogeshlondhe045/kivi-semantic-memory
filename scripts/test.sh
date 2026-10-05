#!/usr/bin/env bash
# Run unit + integration tests of every package (no Gazebo needed) and print a summary.
set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/scripts/env.sh"
cd "${ROOT}/ros2_ws"
colcon test --event-handlers console_direct+ "$@" || true
colcon test-result --verbose --all
