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

## Phase 6 — Navigation
- Nav2 (Jazzy): map_server + AMCL, keepout filter (mask generated from restricted zones),
  NavFn A* planner, Regulated Pure Pursuit controller, behavior server, BT navigator, velocity
  smoother, collision monitor (stop + slowdown polygons from both lidars). Velocity chain ends in
  the `uco_safety` gate, which also serves as lidar gateway and speed-zone publisher (`/speed_limit`).
- RViz2 configuration with map, keepout, costmaps, robot model, TF, both scans, odometry, AMCL pose,
  global/local plan, goal, collision polygons.
- Problems found and fixed (each confirmed by measurement before and after):
  1. **Undocking stall** (first leg 165 s): collision-monitor stop polygon (corner radius 0.81 m) hit
     the charger unit (0.775 m) while turning on the spot → stop/slowdown flapping. Stop polygon
     shrunk to footprint +7/+5 cm (radius 0.766 m) → 0 stop events, leg time 61 s.
  2. **Alert flapping**: safety obstacle condition now has 1 s debounce / 2 s clear hysteresis.
  3. **AMCL bias ~0.1 m towards walls**: sensor geometry checked against ground truth (4 rays within
     1 cm), scan endpoints against the map (median 2.5 cm) → root cause was solid-filled obstacles
     in the generated map (flat likelihood plateau inside walls). Map now contains obstacle
     outlines only (as a SLAM map would). Mean AMCL error 0.08–0.11 → 0.04–0.08 m. A residual
     ~3–5 cm is AMCL's half-cell map indexing and is accepted.
  4. **Goal aborted** ("Timed out waiting for follow_path ack"): `default_server_timeout` 20 → 200 ms;
     controller 10 → 8 Hz (measured loop rate 7 Hz under load); progress allowance 30 s.
  5. **Phantom obstacles in the costmap** (cells in open floor, even inside the office): a
     ground-truth projection monitor showed all of them came from the first ray (−135°) of the
     front lidar, which runs parallel to the chassis face and grazed the 5 mm protruding bumper.
     FOV reduced to ±132° (front + rear still cover 360°), bumpers made flush → 0 phantom points
     in 7202 scans.
  6. RViz LaserScan displays set to Best Effort (sensor QoS).
- Verification: 11-leg tour across all zones (storage, dispatch, holding, quarantine, charger):
  11/11 SUCCEEDED, 0 aborts; final 5-leg tour after all fixes: 5/5, mean AMCL error
  0.035–0.071 m, final position error 0.06–0.15 m (AMCL error + 0.10 m goal tolerance).
  Logs: `project_report/results/phase6_navigation_tour*.txt`.

## Phases 8–10 — Inventory, storage allocation, task management
Order note: Phase 7 (receiving) registers containers *in* the WMS, so the WMS core was built and
tested first; receiving follows in Phase 7.
- `uco_wms/store.py`: SQLite `WarehouseStore` (containers, slots, tasks, events, counters) with the
  container state machine, nearest-slot allocation (Manhattan distance between access poses,
  reservation until stored), quarantine routing, task lifecycle (retries before pick-up, unlimited
  same-AGV retries once a container is on an AGV, cancellation rules), FIFO dispatch requests limited
  by dispatch-buffer capacity, hand-over to processing, event history and a consistency checker.
- `wms_node`: all operations as services, latched `/warehouse/inventory` and `/warehouse/tasks`,
  `/warehouse/container_state` events, `/warehouse/markers` for RViz, REGISTRATION_FAILURE and
  STORAGE_FULL fault handling.
- `uco_fleet`: `dispatch_core` (priority/FIFO, nearest eligible AGV, battery & heartbeat gating,
  time-outs), `task_manager` (assignment chain, feedback → WMS phases, result → COMPLETED / FAILED /
  requeue), `agv_controller` (TransportContainer + NavigateToLocation, pause on safety stop,
  low-battery interruption and charging, idle return, lift-deck transfer via `set_pose`, fault
  hooks), `battery_simulator` (energy model, docking detection), `move_agv` CLI.
- Bugs found by the tests and fixed:
  - `-p db_path:=:memory:` cannot be parsed by the ROS argument parser → alias `memory`.
  - `TaskManager.clients` shadowed rclpy's `Node.clients` property → node crashed on start.
  - Fleet modules lacked `if __name__ == '__main__'` → `python -m` started nothing.
  - Nodes printed `ExternalShutdownException` on SIGTERM → handled in every `main()`.
  - Low-battery interruption should not burn a retry attempt → `unassign` now also covers
    IN_PROGRESS tasks that have not picked up their container.
- Verification: 36 WMS tests (33 unit + 2 ROS service integration + 1 added), 10 fleet unit tests,
  5 fleet integration tests with real `wms_node` + `task_manager` + `agv_controller` against mock
  Nav2/Gazebo (end-to-end STORE, low-battery charging/requeue, injected navigation failure + retry,
  manual move with alias "A03"). Workspace total: 83 tests, 0 failures.
