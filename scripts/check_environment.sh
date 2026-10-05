#!/usr/bin/env bash
# Phase 0 - environment inspection.
# Prints the versions of every tool this project depends on. Safe to run any time;
# it never installs or modifies anything.
# Usage: scripts/check_environment.sh            (uses whatever ROS env is already sourced)
#        ROS_SETUP=/opt/ros/jazzy/setup.bash scripts/check_environment.sh
set -u
if [[ -n "${ROS_SETUP:-}" && -f "${ROS_SETUP}" ]]; then
  # shellcheck disable=SC1090
  source "${ROS_SETUP}"
fi

row() { printf '%-22s %s\n' "$1" "$2"; }
ver() { command -v "$1" >/dev/null 2>&1 && { shift; "$@" 2>&1 | head -1; } || echo "NOT FOUND"; }

echo "== Operating system =="
row "OS" "$(. /etc/os-release && echo "$PRETTY_NAME")"
row "Kernel" "$(uname -r)"
row "CPU cores" "$(nproc)"
row "Memory" "$(free -h | awk '/Mem:/ {print $2}')"

echo; echo "== Toolchain =="
row "python3" "$(ver python3 python3 --version)"
row "g++" "$(ver g++ g++ --version)"
row "cmake" "$(ver cmake cmake --version)"
row "git" "$(ver git git --version)"
row "sqlite3 (python)" "$(python3 -c 'import sqlite3; print(sqlite3.sqlite_version)' 2>/dev/null || echo 'NOT FOUND')"

echo; echo "== ROS 2 / Gazebo =="
row "ROS_DISTRO" "${ROS_DISTRO:-NOT SOURCED}"
row "ros2" "$(command -v ros2 || echo 'NOT FOUND')"
row "colcon" "$(ver colcon colcon version-check 2>/dev/null | grep -m1 colcon-core || echo present)"
row "rosdep" "$(ver rosdep rosdep --version)"
row "rviz2" "$(command -v rviz2 || echo 'NOT FOUND')"
row "gz sim" "$(ver gz gz sim --version)"
if command -v ros2 >/dev/null 2>&1; then
  for p in nav2_bringup nav2_amcl nav2_collision_monitor nav2_regulated_pure_pursuit_controller \
           ros_gz_sim ros_gz_bridge robot_state_publisher xacro rviz2 slam_toolbox; do
    if ros2 pkg prefix "$p" >/dev/null 2>&1; then row "  $p" "found"; else row "  $p" "MISSING"; fi
  done
fi

echo; echo "== Display / GPU =="
row "DISPLAY" "${DISPLAY:-<unset>}"
row "nvidia-smi" "$(command -v nvidia-smi >/dev/null && nvidia-smi --query-gpu=name --format=csv,noheader || echo 'no NVIDIA GPU')"
row "Xvfb" "$(command -v Xvfb || echo 'NOT FOUND')"
if command -v glxinfo >/dev/null 2>&1 && [[ -n "${DISPLAY:-}" ]]; then
  row "OpenGL renderer" "$(glxinfo -B 2>/dev/null | awk -F': ' '/OpenGL renderer/ {print $2}')"
fi
