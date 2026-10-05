#!/usr/bin/env bash
# Build the ROS 2 workspace.  Extra args are passed to colcon (e.g. --packages-select uco_wms).
set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/scripts/env.sh"
cd "${ROOT}/ros2_ws"
colcon build --symlink-install "$@"
