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

## Phase 7 — Receiving station
- `uco_stations`: `station_models` (supplier quality profiles, load-cell model with noise +
  0.5 kg resolution, lab measurement noise, acceptance rules PASS/MARGINAL/FAIL),
  `weighing_station`, `inspection_station`, `receiving_station` (conveyor-line pipeline with one
  container per station, Gazebo spawning via the bridged `create` service, registration retry,
  station-outage wait, storage-full retry), `dispatch_manager` (processing requests, hand-over
  after a dwell time, removal through the transfer door). `fault_injector` + `inject_fault` CLI
  added early because the stations consume its events.
- `uco_bringup/launch/warehouse.launch.py`: one launch file with switches per subsystem.
- Verification (Gazebo, headless): delivery of 3 drums + 2 IBCs → all registered (UCO-0001..5),
  weighed (e.g. UCO-0003 867.0 kg gross → 875 L), inspected (all PASS with the `good` profile),
  placed in HOLD-01..05, storage assigned nearest-first (A-01-R01, A-01-R02, A-02-R01, ...),
  STORE tasks T-0001..5 created. Image: `project_report/figures/receiving_holding.png`.
  12 station-model unit tests.
- Fixed: drum lid z-fighting (lid lifted 3 mm).

## Phase 11 — AGV task execution (Gazebo)
- Full stack (Gazebo + Nav2 + safety + WMS + stations + fleet): STORE T-0001 (890 kg IBC,
  HOLD-01 → A-01-R01) 110 s / 30.8 m; STORE T-0002 (drum HOLD-02 → A-01-R02); processing request
  → RETRIEVE T-0003 (A-01-R01 → DSP-01) 64 s / 20.8 m → hand-over to the processing plant after 20 s.
- Physical carry check: container pose sampled in the AGV frame during the whole RETRIEVE trip
  (79 samples incl. turns on the spot): offset (0.000, 0.000) m, relative yaw 0.0° → the container
  rides on the deck under friction without slipping. Final slot placement verified from Gazebo
  pose: UCO-0001 at exactly (13.0, 12.0) = A-01-R01. Trace: `project_report/results/phase11_container_carry_trace.txt`.
- Bug found: `FaultInjector.timers` shadowed rclpy `Node.timers` (crash at start) → renamed; all
  nodes grepped for other shadowed Node properties (none).
- Tuning: zone speed limits raised (receiving 0.3 → 0.4, storage 0.5 → 0.6, dispatch 0.4 → 0.5 m/s)
  after observing ~0.25 m/s average task speed.
- Workspace tests: 95, 0 failures.

## Phase 12 — Safety system
- `safety_core.SafetySupervisor` (pure Python): e-stop, lidar / odometry watchdogs with start-up
  grace period, heartbeat watchdog (armed on first AGV state), speed and restricted zones (stop
  then crawl), obstacle episodes with debounce/hysteresis → OBSTACLE warning / PATH_BLOCKED alarm,
  battery low / critical, external conditions (station down, storage full); aggregated state
  NORMAL < WARNING < ALARM < EMERGENCY_STOP. `safety_manager` adds the lidar gateway, the velocity
  gate, `/speed_limit`, `/safety/emergency_stop` and alerts.
- Bug found by the unit tests: the obstacle hysteresis measured "time since the last stop message",
  but the collision monitor publishes its state on change only, so a 20 s stop looked like it had
  cleared long ago → the clear timer now starts when the zone actually becomes free.
- A vacuous assertion (`... or True`) in a test was replaced with a real check.
- 10 safety-core unit tests.

## Phase 13 — Fault injection
- `fault_injector` service + `inject_fault` CLI, nine fault types, auto-clear by duration,
  BLOCKED_PATH spawns a physical pallet (location, `x,y` or `AHEAD` = 1.0 m in front of the
  moving AGV so its near face lies inside the collision-monitor stop zone). Faults are events on
  `/warehouse/faults` (transient local), consumed by the owning nodes only.
- `scenario_runner` + `config/scenarios.yaml`: scripted scenarios with `wait_for` / `expect`
  conditions, snapshots and a JSON report; `fault_suite` exercises every fault and safety function.
- Scenario-runner bug: `alert_since_step` reset its window at every step, including the `sleep`
  between a fault and its check, so it missed alerts raised during the sleep → the window now
  starts at the last *action* step.

## Phase 14 — Dashboard
- `dashboard_server`: std-lib HTTP server, `/api/layout`, `/api/state`, `/api/stream` (SSE, 1 Hz),
  POST endpoints for delivery, processing request, AGV move, e-stop and faults; vanilla JS canvas UI.
- 5 HTTP API tests (static files, layout, state without a running system, service-unavailable
  reporting, path traversal blocked). Screenshot with headless Chromium (Playwright).
- Zone labels overlapped in the first version → drawn last, clipped to their zone.

## Performance investigation (during Phases 13–15)
The second fault-suite run slowed to a real-time factor of 0.1–0.3. Findings and fixes, each
measured:
1. An orphaned `lifecycle_manager` from an earlier run spun at 100 % CPU (125 min of CPU time) →
   `stop_all.sh` now escalates SIGINT → SIGKILL and also kills test-spawned nodes.
2. Gazebo publishes `/clock` every physics step (≈500 Hz simulation time); each of ~15 Python nodes
   processed every message. New C++ `clock_throttle` (in `uco_simulation`) republishes `/clock`
   at 25 Hz of simulation time; Gazebo's full-rate clock is bridged to `/clock_gz`.
3. rclpy's `MultiThreadedExecutor` costs a lot per wake-up: the same idle `agv_controller` used
   8.3 % CPU single-threaded, 24.8 % with 2 threads, 42.7 % with 4. All Python nodes now use the
   single-threaded executor: blocking service waits moved to `async` callbacks (dispatch manager,
   fault injector) or to their own worker / HTTP threads (receiving station, dashboard); the AGV
   controller runs its action executions in worker threads awaited by coroutine callbacks
   (py-spy profile showed 63 % of its time in rclpy wait-set rebuilding).
4. Wake-up reduction: TF listeners on their own thread, ground truth 20 → 10 Hz, the AGV controller
   no longer subscribes to the 30 Hz odometry (statistics come from the 10 Hz ground truth),
   `/safety/status` published on change + 1 Hz.
Result: real-time factor ≈ 1.0 with the full stack; AGV now reaches its 0.8 m/s limit in open areas.

## Phases 15–16 — End-to-end runs, test hardening
Findings while running the scenarios as reference runs:
- **False OBSTACLE during lidar failure**: with stale scans the collision monitor reports STOP
  ("invalid source"); the safety manager counted it as an obstacle. Now only STOPs from the
  configured `StopZone` polygon count, and no obstacle is reported while a sensor fault is active
  (unit test added).
- **Weak scenario checks**: `alert` (since scenario start) matched an earlier, unrelated alert
  ("OK after 0.0 s"). All fault checks now use `alert_since_step` (alerts after the last action).
  The station-outage check matched the inspection station's own fault acknowledgement that raced
  with the delivery step → the receiving line now raises a distinct `WAITING_AT_INSPECTION` code.
- **Orphaned Gazebo server**: after `exit_after_scenario`, the `gz sim` server outlived the launch
  (the ros_gz_sim wrapper does not forward SIGINT) and kept publishing the same gz topics, which
  contaminated the next run (two robots on one partition). Run scripts now call `stop_all.sh` before
  and after (trap) every run and use a private `GZ_PARTITION`. The contaminated run was discarded.
- `run_sim_tests.sh` failed immediately: conda activation hooks are not `set -u` safe →
  `scripts/env.sh` disables nounset while sourcing third-party scripts.
- AGV state reported battery 100 % and pose (0, 0) before the first battery message / AMCL pose,
  which drew a false line in the trajectory plot → unknown values are now -1 / NaN, skipped by the
  metrics and plots; the dispatcher never assigns tasks to an unlocalised AGV or one with an unknown
  battery (unit test added).
- Test-count reconciliation: `colcon test-result` prints 114 because the three ament_cmake pytest
  suites are additionally counted as CTest entries; the real number of test cases is 111.
- **Kidnapped robot in a reference run**: in one fault-suite run the AGV could not reach the charger
  for 400 s ("failed to create plan"). Ground truth showed AMCL 8.7 m off. Cause: the obstacle
  test spawned a *static* pallet 1.0 m ahead of the moving AGV; with the ~0.3 s spawn latency the
  AGV had already closed the 10 cm margin, the pallet appeared overlapping the robot and the physics
  engine flung it. AMCL (confident, small covariance) never recovered, real walls were then marked
  at wrong positions (phantom "walls" in the global costmap) and planning failed.
  Fixes: (1) the obstacle is now a light dynamic pallet stack spawned 1.05 m + 0.3 s × speed
  ahead; (2) new **localisation monitor** in the safety manager: share of lidar endpoints within
  0.25 m of a mapped obstacle at the AMCL pose (2 Hz); < 0.4 for 5 s → LOCALIZATION_DEGRADED,
  < 0.2 for 5 s → LOCALIZATION_LOST (ALARM, motion blocked). In a normal fault-suite run the score
  never fell below 0.66, so the thresholds have margin. 4 unit tests.
- Localisation-error KPI: the first version compared the 2 Hz AGV state with the latest ground
  truth (up to 0.4 m artefact at 0.8 m/s); the recorder now interpolates ground truth at the AGV
  state's timestamp.
- Report generation: `scripts/build_report.py` fills Chapters 14–16 of the report from the recorded
  run files and the latest `colcon test` results.
- **False localisation warnings**: the first demonstration run raised LOCALIZATION_DEGRADED twice
  although the measured localisation error was < 0.18 m. The AGV was facing stored containers,
  which are not in the static map, so few lidar endpoints matched. The monitor now checks scans
  against the static map **plus the containers the WMS knows about** (occupied slot footprints from
  `/warehouse/inventory`), the digital twin's best knowledge of what the lidar should see
  (unit test added). Both reference scenarios were re-run on this final build.
