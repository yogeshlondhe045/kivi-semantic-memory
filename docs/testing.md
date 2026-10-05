# Testing

Three levels, all automated and all actually executed in the development environment
(Ubuntu 24.04, ROS 2 Jazzy, Gazebo Harmonic, 4 CPU cores, software rendering).

| Level | Command | Needs Gazebo | Count | Result |
|---|---|---|---|---|
| Unit (pure Python / generators / configs) | `scripts/test.sh` | no | 103 | all pass |
| ROS integration (real nodes, mock Nav2 / Gazebo services) | `scripts/test.sh` | no | 12 | all pass |
| Simulation scenarios (full stack in Gazebo) | `scripts/run_sim_tests.sh`, `scripts/run_demo.sh` | yes | 2 scenarios, 38 + 14 checks | see §4 |

`scripts/test.sh` = `colcon test` + `colcon test-result`: **115 test cases, 0 failures, 0 skipped**
(latest run). `colcon test-result` prints 114 because the pytest suites of the three ament_cmake
packages are also counted once each as a CTest entry (colcon prints 118).

## 1. Unit tests

| Package | File | Tests | What is verified |
|---|---|---|---|
| uco_common | `test_layout.py` | 18 | layout parsing, 32/5/3/4/1 positions, `A-03-R02` id format, aliases (`A03`, `charger`), access poses inside the building, outside restricted zones and ≥ 0.61 m from every obstacle, slot spacing, speed / restricted zone queries, nearest location, container mass and SDF |
| uco_simulation | `test_world_generation.py` | 3 | generated files up to date (`--check`), `gz sdf -k` valid, map free at every access pose and occupied at walls / doors / tanks, keepout covers restricted zones |
| uco_description | `test_urdf.py` | 4 | xacro expands with / without camera, required frames and sensors, deck height = layout, `check_urdf` |
| uco_navigation | `test_nav_config.py` | 4 | Nav2 topics match the bridge / gate, identical footprints, velocity limits consistent with the drive plugin, generated maps present |
| uco_wms | `test_inventory.py` | 9 | registration, sequential ids, duplicates, validation, quality outcomes, illegal transitions rejected, weights, event history, simulated registration failure, persistence across restart |
| uco_wms | `test_storage_allocation.py` | 9 | nearest-slot allocation with reservation, no double booking, id format, quarantine routing, storage full and recovery, storage-full fault, occupancy after store / dispatch, filling all 32 positions, sequential policy |
| uco_wms | `test_tasks.py` | 16 | full STORE lifecycle with timings, validation, no completion before pick-up, assignment rules, retries before pick-up then FAILED with reservation released, unlimited same-AGV retries after pick-up, cancel rules, requeue on AGV offline, priority ordering, FIFO dispatch + hand-over, buffer limit, explicit selection validation, cancel RETRIEVE, low-battery unassign without penalty |
| uco_wms | `test_metrics_core.py` | 2 | KPI computation (completion rate, averages per type, throughput, utilisation, energy) and empty run |
| uco_fleet | `test_dispatch_core.py` | 7 | nearest AGV, priority then FIFO, one task per AGV, low battery / offline / unavailable / unlocalised not eligible, picked container stays with its AGV, time-outs |
| uco_fleet | `test_battery_model.py` | 4 | idle drain, motion + payload consumption, charging and clamping, time scale, voltage |
| uco_stations | `test_station_models.py` | 12 | acceptance-rule boundaries (8 cases), load-cell quantisation and accuracy, profile pass rates, truth volume range, seed reproducibility |
| uco_safety | `test_localization_monitor.py` | 4 | obstacle-mask dilation, match score high at the true pose and low when shifted / rotated, too few points, degraded / lost persistence |
| uco_safety | `test_safety_core.py` | 11 | normal state, e-stop priority, sensor timeout and recovery, start-up grace, heartbeat armed on first message, speed zones, restricted zone stop then crawl, obstacle debounce / hysteresis / PATH_BLOCKED, no obstacle report during sensor fault, battery levels, external conditions |

## 2. ROS integration tests

| File | Tests | Setup | Requirement chain |
|---|---|---|---|
| `uco_wms/test/test_wms_node_integration.py` | 2 | real `wms_node` (in-memory DB) | REGISTER → WEIGHED → APPROVED → holding → ASSIGN_STORAGE → CREATE_TASK → ASSIGN_AGV → IN_PROGRESS / PICKED / COMPLETED → inventory topic shows STORED + occupancy → REQUEST_DISPATCH; errors returned as `success=false` |
| `uco_fleet/test/test_fleet_integration.py` | 5 | real `wms_node`, `task_manager`, `agv_controller`; mock Nav2 action server, Gazebo `set_pose`, battery, safety | (1) task creation → AGV assignment → navigation → pick / drop transfer → completion → inventory; (2) low battery → AGV unavailable, drives to charger, tasks wait, resume after charging; (3) low battery during a task → requeued without counting an attempt; (4) injected navigation failure → retry → completed with `attempts = 1`; (5) "move AGV-01 to A03" |
| `uco_dashboard/test/test_dashboard_http.py` | 5 | real `dashboard_server` | page and assets served, layout API, state API without a running system, commands report unavailable services, path traversal blocked |

## 3. Simulation tests (Gazebo + Nav2 + all nodes)

`scripts/run_sim_tests.sh` launches the complete system headless and runs the `fault_suite`
scenario (`uco_bringup/config/scenarios.yaml`). Every check is a `wait_for` (condition must become
true before a time-out) or an `expect` (condition true now). Alert checks only accept alerts raised
after the fault was injected.

| Section | Injected | Expected (checked) behaviour |
|---|---|---|
| Registration failure | REGISTRATION_FAILURE ×1 | `REGISTRATION_FAILED` alert, retry succeeds, container registered |
| Station unavailable | STATION_UNAVAILABLE INSPECT 30 s | container waits at inspection (holding stays empty), proceeds after the outage |
| Speed zone | – | safety speed limit 0.4 m/s while the AGV is at HOLD-01 |
| Emergency stop | e-stop during transport, 11 s | EMERGENCY_STOP, motion blocked, speed 0 within 3 s, AGV state PAUSED, `AGV_RESUMED` after release, task completed, no failures |
| Lidar failure | SENSOR_FAILURE lidar 8 s during transport | `SENSOR_FAULT:scan`, ALARM, motion blocked, motion allowed again after recovery, task completed |
| Obstacle | pallet dropped 1 m in front of the moving AGV, 20 s | `OBSTACLE` warning, task completed |
| Low battery | battery → 18 % on the way to a pick-up | `BATTERY_LOW`, AGV goes to charge, task back in the queue (not failed), CHARGING, battery > 80 %, task completed |
| Navigation failure | next 2 navigation attempts fail | `TASK_RETRY`, `NAVIGATION_FAILED`, task completed, 0 failed tasks |
| Storage full | STORAGE_FULL | `STORAGE_UNAVAILABLE` warning, container APPROVED in holding, stored after the fault clears |
| Communication loss | AGV heartbeat suppressed 8 s while moving | `HEARTBEAT_LOST`, motion blocked, `AGV_TIMEOUT` alarm, recovery, AGV reaches its goal |
| Final state | – | 6 stored, 6 tasks completed, 0 failed, 0 active |

`scripts/run_demo.sh` runs the `demo` scenario (Chapter 16 of the report).

## 4. Recorded results

See `project_report/results/` for the JSON reports, CSV data and graphs of the runs quoted in the
report, and the section "Testing" of `project_report/report.md` for the results table.

## 5. Problems the tests found

| Test | Defect found |
|---|---|
| `test_wms_node_integration` | `db_path:=:memory:` cannot be passed on the ROS command line |
| `test_fleet_integration` | node attribute `clients` shadowed rclpy's `Node.clients` (task manager crashed); modules not runnable with `python -m` |
| `test_safety_core` | obstacle hysteresis broken because the collision monitor publishes on change only |
| `fault_suite` (Gazebo) | scenario-runner alert window; safety manager reported OBSTACLE for a stale-sensor STOP; CPU starvation (RTF 0.1) from the 500 Hz clock and multi-threaded executors |
| `run_sim_tests.sh` | the environment script failed under `set -u` (conda activation hooks) |
| Phase 6 measurements | odometry yaw error, AMCL wall bias, phantom obstacles, undocking stall (see development log) |

## 6. Not covered by automated tests

- Gazebo GUI and RViz2 *visual* appearance: checked with screenshots, not automatically.
- Restricted-zone entry in simulation: Nav2's keepout filter prevents it, so the restricted-zone
  supervisor reaction is covered by unit tests only.
- Multi-AGV operation: the dispatcher logic is unit-tested with several AGVs; the simulation has one.
- Long-duration runs (hours): not executed; the longest recorded run is the fault suite (~15 min simulated).
