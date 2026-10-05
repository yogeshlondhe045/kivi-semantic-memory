# UCO Warehouse Digital Twin

Automated warehouse simulation for a **used-cooking-oil (UCO) collection facility** that feeds a
biodiesel plant. The simulation covers:

- receiving: unloading, weighing and quality inspection of restaurant deliveries
- automated storage, with AGV transport between receiving, storage and dispatch
- inventory tracking in a SQLite warehouse management system
- safety supervision and fault injection
- monitoring through a web dashboard and RViz2
- dispatch to the processing plant, plus recorded KPIs

Built with **ROS 2 Jazzy**, **Gazebo Harmonic** and **Nav2**. The biodiesel chemistry is out of scope.

![Gazebo world](project_report/figures/gazebo_world_overview.png)

## What it does

1. A restaurant delivery arrives. Each container (200 L drum or 1000 L IBC) gets an ID (`UCO-0001`)
   and is then unloaded, weighed (load-cell model) and inspected (FFA, water, contamination).
2. Approved oil is assigned the nearest free storage position (`A-03-R02`). Marginal or rejected oil
   goes to quarantine.
3. The task manager assigns the transport to **AGV-01**. The AGV navigates with Nav2, lifts the
   container onto its deck, drives it to the slot and sets it down. The inventory updates.
4. A processing request retrieves the oldest approved containers to the dispatch buffer, where they
   are handed over to the plant.
5. Safety and faults throughout:
   - e-stop, obstacle stop/replan, speed zones and keep-out zones
   - lidar and heartbeat watchdogs, low-battery charging
   - 9 injectable faults
6. Everything is visible in Gazebo, RViz2 and the dashboard (<http://localhost:8080>), and is
   recorded as CSV/JSON with generated graphs.

## Quick start

```bash
# 1. ROS 2 Jazzy + Gazebo Harmonic + Nav2 (Ubuntu 24.04)
scripts/install_environment.sh apt        # or: scripts/install_environment.sh robostack
# 2. build
scripts/build.sh
# 3. run the full demonstration (Gazebo GUI + RViz2 + dashboard), then plots
scripts/run_demo.sh                        # add --headless on a machine without a display
```

Interactive use:

```bash
source scripts/env.sh
ros2 launch uco_bringup warehouse.launch.py          # open http://localhost:8080
ros2 service call /stations/receive_delivery uco_interfaces/srv/ReceiveDelivery \
  "{supplier: 'Restaurant A', container_types: [DRUM_200L, IBC_1000L], declared_volumes_l: [180, 900]}"
ros2 service call /stations/request_processing uco_interfaces/srv/RequestDispatch "{count: 1}"
ros2 run uco_fleet move_agv A03                       # "move AGV-01 to storage position A03"
ros2 run uco_safety inject_fault LOW_BATTERY --value 18
ros2 service call /safety/emergency_stop std_srvs/srv/SetBool "{data: true}"
```

Tests:

```bash
scripts/test.sh            # 111 unit + ROS integration tests (no Gazebo)
scripts/run_sim_tests.sh   # fault_suite scenario in Gazebo: every safety / fault behaviour
```

See [docs/user_manual.md](docs/user_manual.md) for all commands, launch switches and troubleshooting.

## Architecture at a glance

| Package | Responsibility |
|---|---|
| `uco_interfaces` | messages / services / actions for containers, inventory, tasks, AGV state, alerts, safety, faults |
| `uco_common` | **layout file (single source of truth)**, layout model, container models, alerts, QoS |
| `uco_description` | AGV xacro: differential drive, 2 corner lidars (360°), IMU, optional RGB-D, e-stop |
| `uco_simulation` | world / map / keepout generator, Gazebo world, ros_gz bridge, `/clock` throttle |
| `uco_navigation` | Nav2 (AMCL, NavFn, Regulated Pure Pursuit, collision monitor, keepout filter), RViz2 |
| `uco_wms` | SQLite WMS: inventory, storage allocation, tasks, dispatch; metrics recorder |
| `uco_stations` | receiving line (unload / weigh / inspect / hold), dispatch & hand-over |
| `uco_fleet` | task dispatcher, AGV task execution, battery simulator, `move_agv` |
| `uco_safety` | safety supervisor (e-stop gate, zones, watchdogs), fault injector |
| `uco_dashboard` | web dashboard with operator controls |
| `uco_bringup` | one-command launch, scenario runner, demo and fault-suite scenarios |

## Verification status

| Item | Evidence |
|---|---|
| World, map and WMS generated from one layout file | generator `--check` test, `gz sdf -k` |
| AGV odometry / localisation | odometry matches ground truth; AMCL mean error 4–8 cm |
| Autonomous navigation | 11/11 goals across all zones; 0 phantom lidar points in 7202 scans |
| Receiving → storage → retrieval → hand-over in Gazebo | scenario runs, container carried without slip |
| Safety and 9 fault types | `fault_suite`: all checks passed (see report) |
| Unit + integration tests | 111 pass |
| Real-time factor (4 CPU cores, no GPU) | ≈ 1.0 with the full stack |

Details, measured KPIs, graphs and limitations: **[project_report/report.md](project_report/report.md)**.

## Documentation

| Document | Content |
|---|---|
| [PROJECT_REQUIREMENTS.md](PROJECT_REQUIREMENTS.md) | functional / non-functional requirements with IDs |
| [SYSTEM_ARCHITECTURE.md](SYSTEM_ARCHITECTURE.md) | layers, packages, nodes, interfaces, data model, state machines, layout |
| [DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md) | phases 0–17 and how each was verified |
| [docs/development_log.md](docs/development_log.md) | what was built, tested, found and fixed in each phase |
| [docs/ros_architecture.md](docs/ros_architecture.md) | node graph, topics, services, actions, QoS, frames |
| [docs/simulation_architecture.md](docs/simulation_architecture.md) | world generation, Gazebo, AGV model, performance |
| [docs/warehouse_workflow.md](docs/warehouse_workflow.md) | inbound / outbound sequences, lifecycles, battery management |
| [docs/testing.md](docs/testing.md) | test inventory and traceability |
| [docs/user_manual.md](docs/user_manual.md) | install, run, operate, faults, troubleshooting |
| [docs/environment.md](docs/environment.md) | detected development environment |
| [project_report/report.md](project_report/report.md) | engineering report (19 chapters) with measured results |

## Repository layout

```
├── PROJECT_REQUIREMENTS.md  SYSTEM_ARCHITECTURE.md  DEVELOPMENT_PLAN.md
├── docs/                 design, workflow, testing, user manual, development log
├── env/                  RoboStack environment definition
├── ros2_ws/src/uco_*     11 ROS 2 packages
├── scripts/              install, build, test, run, plot, stop helpers
└── project_report/       report.md, figures/, results/ (recorded runs)
```

Generated artefacts (world SDF, map, keepout mask) are committed; regenerate them with
`ros2_ws/src/uco_simulation/scripts/generate_world.py` after editing
`ros2_ws/src/uco_common/config/warehouse_layout.yaml`.

## License

Apache-2.0
