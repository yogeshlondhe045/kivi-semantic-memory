# Development Log

Chronological record of what was built, what was run, what failed and how it was fixed.

## Phase 0 — Environment inspection
- Detected Ubuntu 24.04.4, no ROS, no Gazebo, no GPU, no display.
- `packages.ros.org` and `packages.osrfoundation.org` → HTTP 403 (egress policy). Chose RoboStack
  (conda-forge) for ROS 2 Jazzy + Gazebo Harmonic; bootstrapped `micromamba` from conda-forge
  because `micro.mamba.pm` was also blocked.
- EGL headless rendering crashed the ogre2 engine (no DRM device). Installed `xvfb` and Mesa
  from the Ubuntu archive; GPU lidar then worked with llvmpipe under Xvfb.

## Phase 1 — Requirements and architecture
- Wrote `PROJECT_REQUIREMENTS.md`, `SYSTEM_ARCHITECTURE.md`, `DEVELOPMENT_PLAN.md`.
- Decision: generate world, map, keepout mask and WMS slot table from one layout YAML.
- Decision: rename the proposed `simulation_interfaces` package to `uco_interfaces` to avoid
  clashing with the upstream ROS `simulation_interfaces` package.

## Phase 2 — Repository and ROS 2 workspace
- Created `uco_interfaces` (9 msgs, 13 srvs, 2 actions) and `uco_common` (layout model,
  container SDF factory, alert helper, QoS profiles) plus `scripts/env.sh`, `build.sh`, `test.sh`.
- Issues found and fixed:
  - pytest 9 (pulled by conda) breaks the Jazzy `launch_testing` pytest plugin
    (`PluginValidationError: ... {'path'}`) → pinned `pytest>=8,<9`.
  - setuptools 84 breaks `colcon build --symlink-install` for ament_python packages
    (`option --editable not recognized`) → pinned `setuptools<80` (apt Jazzy ships 68).
  - colcon ran unittest (0 tests) once `tests_require` was removed → declared pytest via
    `extras_require={'test': ['pytest']}`.
- Verification: `scripts/build.sh` OK; `scripts/test.sh` → 18 tests, 0 failures
  (layout parsing, id format, alias resolution, access poses clear of obstacles and restricted
  zones, slot spacing, speed/restricted zone queries, container mass/SDF).

## Phase 3 — Gazebo warehouse world
- `uco_simulation/scripts/generate_world.py` generates from the layout YAML: the SDF world
  (walls with door shutters, receiving line, inspection booth, racks, tank farm with bund,
  charger, pallet store, columns, floor markings, delivery truck, lighting), the Nav2
  occupancy map and the keepout filter mask. `--check` detects stale generated files.
- `snapshot.py` spawns a temporary camera through the world's `create` service and saves a PNG.
- Issue found: door shutters were generated at 60 % wall thickness and rasterised as a 2-pixel
  line that looked like a gap. Fixed by using full wall thickness.
- Verification: world runs 2000 iterations in `gz sim -s` with no errors or warnings;
  `gz sdf -k` → Valid; 3 generator tests pass (up-to-date check, SDF validity, map free at every
  access pose / occupied at walls, doors, tanks); rendered images in `project_report/figures/`.

## Phase 4 — AGV model
- `uco_description/urdf/agv.urdf.xacro`: 1.0 × 0.7 m differential-drive AMR, mid drive wheels,
  front/rear frictionless casters, lift deck at 0.35 m, e-stop button and beacon visuals,
  diff-drive / joint-state / ground-truth odometry Gazebo systems.
- Design decision: Gazebo's URDF→SDF converter does not pass visual `visibility_flags`, so a
  single wide-angle lidar would see its own chassis. Used the industrial layout instead: two
  270° scanners just outside diagonally opposite chassis corners (360° coverage, no self-hits).
- Issue found by measurement: in a commanded 180° spin, ground truth turned 205° while wheel
  odometry reported 186° (~10 % yaw error) because cylinder wheel collisions touch the floor at
  their inner rims. Fixed with sphere wheel collisions → spin 187.7° vs 187.7°, arc Δyaw 134.8°
  vs 134.8°, displacement error < 3 mm.
- Tooling issue: `pkill -f "ros2 launch"` killed the calling shell (pattern matched its own command
  line) → `scripts/stop_all.sh` with the `[x]` bracket pattern trick.

## Phase 5 — Sensors and bridge
- Topics verified live (headless): `/agv/scan_raw` (frame `lidar_front_link`),
  `/agv/scan_rear_raw` (`lidar_rear_link`), `/agv/imu` (`imu_link`), `/agv/odom` (`odom`),
  `/agv/ground_truth` (`world`), `/joint_states`, `/tf`, `/clock`; optional camera
  `/agv/camera/image` (rgb8) and `/agv/camera/depth_image` (32FC1).
- Docked at the charger the front scan minimum is 0.22 m at −48°, which is the charger unit
  (robot front 0.23 m from it), i.e. no self-detection.
- Real-time factor on 4 CPU cores with software rendering: 0.97 (2 lidars), 1.00 (2 lidars + RGB-D).
- TF tree: odom → base_footprint → base_link → {wheels, casters, deck, lidars, imu, e-stop, beacon, camera}.
- `colcon test`: 27 tests, 0 failures (adds URDF expansion / frames / deck height / check_urdf).
