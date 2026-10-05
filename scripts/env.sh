# Source this file:  source scripts/env.sh
# Activates ROS 2 Jazzy (apt install or RoboStack env) and this workspace's overlay, and
# provides a virtual display + software OpenGL when no display is available.
_UCO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export UCO_ROOT="${_UCO_ROOT}"

if [[ -z "${ROS_DISTRO:-}" ]]; then
  if [[ -f /opt/ros/jazzy/setup.bash ]]; then
    # shellcheck disable=SC1091
    source /opt/ros/jazzy/setup.bash
  elif [[ -f "${UCO_ROOT}/env/ros_env.sh" ]]; then
    # shellcheck disable=SC1091
    source "${UCO_ROOT}/env/ros_env.sh"
  else
    echo "ROS 2 Jazzy not found - run scripts/install_environment.sh first" >&2
    return 1
  fi
fi

if [[ -f "${UCO_ROOT}/ros2_ws/install/setup.bash" ]]; then
  # shellcheck disable=SC1091
  source "${UCO_ROOT}/ros2_ws/install/setup.bash"
fi

# Headless machines: start a virtual X server once and use Mesa software rendering
# (EGL headless rendering crashes ogre2 without a GPU - see docs/environment.md).
if [[ -z "${DISPLAY:-}" ]]; then
  export DISPLAY=:99
  if ! pgrep -f "Xvfb :99" >/dev/null 2>&1; then
    (Xvfb :99 -screen 0 1600x1000x24 >/dev/null 2>&1 &)
    sleep 1
  fi
  export LIBGL_ALWAYS_SOFTWARE=1
fi
# Keep this project's DDS traffic separate from other ROS systems on the network.
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-42}"
