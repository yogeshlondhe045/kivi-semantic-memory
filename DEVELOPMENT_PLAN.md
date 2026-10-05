# Development Plan

Each phase follows: explain → create files → build/run/test → fix → verify → document.
A phase is only marked **Done** after its verification step has actually been executed in
the development environment. The *Verification* column states what was run.

| Phase | Scope | Deliverables | Verification | Status |
|---|---|---|---|---|
| 0 | Environment inspection | `scripts/check_environment.sh`, `scripts/install_environment.sh`, `env/robostack-jazzy.yml`, `docs/environment.md` | Script run; ROS pub/sub smoke test; headless Gazebo + GPU lidar smoke test | Done |
| 1 | Requirements & architecture | `PROJECT_REQUIREMENTS.md`, `SYSTEM_ARCHITECTURE.md`, this plan, README | Review | Done |
| 2 | Repository & workspace | Package skeletons, `uco_interfaces`, `uco_common` (layout model) | `colcon build`, interface generation, layout unit tests (18 pass) | Done |
| 3 | Gazebo warehouse world | Layout YAML, world generator, generated SDF, container models, map + keepout mask | Generator tests (3 pass); world runs in `gz sim -s` without errors; `gz sdf -k` valid; rendered images | Done |
| 4 | AGV model | Xacro, diff-drive, casters, joint states | `xacro` + `check_urdf`; spawns and drives; odom vs ground truth < 3 mm, 0° yaw error | Done |
| 5 | Sensors & bridge | Lidar, IMU, odometry, ground truth, optional depth camera, bridge YAML | All topics live with correct frames; RTF 0.97–1.0 | Done |
| 6 | Navigation | Nav2 params, AMCL, keepout filter, collision monitor, RViz config | 11/11 goals across all zones; 0 phantom lidar points; AMCL error 4–7 cm | Done |
| 7 | Receiving station | Receiving/weighing/inspection nodes, container spawning | Delivery spawns containers, pipeline reaches holding area | Planned |
| 8 | Inventory system | SQLite store, container state machine | Unit tests (inventory, state machine, persistence) | Done |
| 9 | Storage allocation | Allocation policy, reservation | Unit tests (nearest/sequential, full, quarantine, 32-slot fill) | Done |
| 10 | Task manager | Task lifecycle, AGV assignment, retries, timeouts, `wms_node` services | Unit + ROS integration tests (83 total pass) | Done |
| 11 | AGV task execution | `agv_controller`, `battery_simulator`, pick/drop transfer | Integration test with mock Nav2; full run in Gazebo | Planned |
| 12 | Safety system | `safety_manager`, zones, e-stop gate, watchdogs | Unit tests + simulation checks | Planned |
| 13 | Fault injection | `fault_injector`, fault handlers in each node | Simulation fault tests | Planned |
| 14 | Dashboard | Web UI | HTTP endpoint tests + screenshot | Planned |
| 15 | End-to-end demo | Scenario runner, one-command launch | Full scenario run, results captured | Planned |
| 16 | Testing & metrics | Test suite, metrics recorder, plotting | `colcon test`, sim tests, generated graphs | Planned |
| 17 | Project report | `project_report/report.md` with real results | Generated from recorded runs | Planned |

Phases 8–10 were implemented before Phase 7 because receiving registers containers in the WMS.

Status is updated at the end of every phase; the per-phase log of what was run, what failed
and how it was fixed is kept in `docs/development_log.md`.
