# Development Environment (Phase 0)

Captured with `scripts/check_environment.sh` on 2026-10-05.

| Item | Detected |
|---|---|
| OS | Ubuntu 24.04.4 LTS (Noble), kernel 6.18, x86_64 |
| CPU / RAM | 4 cores / 15 GiB |
| GPU | none (no NVIDIA driver) |
| Display | none — virtual display via `Xvfb` |
| OpenGL | Mesa llvmpipe (LLVM 20, software rendering), OpenGL 4.5 |
| Python | 3.12 (ROS env); system has 3.10–3.13 |
| C++ | conda-forge gcc 15.3 (ROS env); system g++ 13.3 |
| CMake | 4.x (ROS env); system 3.28 |
| Git | 2.43 |
| ROS 2 | **Jazzy** (RoboStack build) |
| Gazebo | **Gazebo Sim 8.10 (Harmonic)** |
| Nav2 | installed (navigation2, nav2_bringup, collision monitor, RPP, NavFn, Smac) |
| ros_gz | installed (bridge, sim, interfaces incl. Spawn/Delete/SetEntityPose services) |
| RViz2 | installed |
| colcon / rosdep | installed |
| slam_toolbox | installed (not required — map is generated from the layout) |

## How it was installed

Nothing ROS-related was preinstalled. The official apt repositories
(`packages.ros.org`, `packages.osrfoundation.org`) are **blocked by the network policy
of the development container (HTTP 403)**. `conda.anaconda.org` is reachable, so
ROS 2 Jazzy + Gazebo Harmonic were installed from **RoboStack** (conda-forge builds of
the same upstream releases):

```bash
scripts/install_environment.sh robostack   # what was used here
source env/ros_env.sh
```

On a normal Ubuntu 24.04 machine use the official binaries instead:

```bash
scripts/install_environment.sh apt
source /opt/ros/jazzy/setup.bash
```

Both paths give ROS 2 Jazzy + Gazebo Harmonic, the officially paired LTS combination for
Ubuntu 24.04. Gazebo Classic is not used.

## Smoke tests run in Phase 0

| Test | Result |
|---|---|
| `ros2 topic pub` / `ros2 topic echo` round trip | PASS |
| `gz sim -s -r --iterations 500 empty.sdf` (server only) | PASS |
| GPU lidar with `--headless-rendering` (EGL, no GPU) | **FAIL** — segfault in `Ogre2RenderEngine::CreateRenderSystem` |
| GPU lidar under `Xvfb :99` + `LIBGL_ALWAYS_SOFTWARE=1` (GLX, llvmpipe) | PASS — box at 2 m detected at 1.51–1.56 m from the 0.5 m half-width face |

Consequence: all simulation runs in this environment use `Xvfb` + software OpenGL.
`scripts/sim_env.sh` sets this up automatically when no `DISPLAY` is present.
On a desktop with a GPU nothing special is needed.
