# Automated Warehouse Digital Twin for a Used-Cooking-Oil Biodiesel Plant

**Engineering project report — Phase 1: receiving, storage, transport and dispatch of used cooking oil**

| | |
|---|---|
| Platform | Ubuntu 24.04 · ROS 2 Jazzy · Gazebo Harmonic · Nav2 · Python 3.12 · SQLite |
| Repository | this repository (`ros2_ws/src/uco_*`, `docs/`, `scripts/`) |
| Status | implemented and verified in simulation (see Chapters 14–16 for measured results) |

---

## Contents
1. Introduction · 2. Business problem · 3. Problem definition · 4. Objectives · 5. System requirements ·
6. System architecture · 7. Warehouse design · 8. ROS 2 architecture · 9. Gazebo simulation ·
10. AGV design · 11. Warehouse management · 12. Safety · 13. Implementation · 14. Testing ·
15. Performance evaluation · 16. Results · 17. Limitations · 18. Future development · 19. Conclusion ·
Appendix A: how to reproduce

---

## 1. Introduction

Used cooking oil (UCO) collected from restaurants is a valuable feedstock for biodiesel. Before
any chemistry happens, a UCO plant has to *receive* hundreds of drums and IBC totes, establish
what is in each of them, store them safely and deliver them to processing in the right order.
This material-handling front end determines how much feedstock the plant can accept, how well
the oil quality is traced, and how many people have to work next to forklifts.

This project builds a **digital twin** of that front end: a physically simulated pilot warehouse in
Gazebo with an autonomous mobile robot (AGV/AMR) navigating with Nav2, a warehouse management
system (WMS) that tracks every container in SQLite, simulated receiving stations (unloading,
weighing, quality inspection), a dispatch interface to the processing plant, a safety supervisor,
a fault-injection framework, a web dashboard, and a metrics pipeline. The biodiesel process itself
is explicitly out of scope for this phase.

The work followed an engineering-phase approach (Phase 0 environment inspection to Phase 17
report). Every phase was built, run, tested and fixed before moving on; the development log
(`docs/development_log.md`) records each problem found and how it was resolved. Nothing in this
report is claimed as working unless it was executed; unverified items are stated as such.

## 2. Business problem

**The business concept.** A collection company visits restaurants, pumps or collects their used
frying oil in 200 L drums or 1000 L IBC totes, and brings it to a central facility. The oil is
pre-treated and converted into biodiesel (transesterification); by-products such as glycerol are
sold. Revenue depends on throughput and on oil quality: free fatty acids (FFA) and water content
drive pre-treatment cost and yield, and contaminated loads must not enter the process.

**Why automate the warehouse.**

- *Traceability*: every container must be attributable to a supplier, a weight and a quality result
  (payment of suppliers, sustainability certification, process control).
- *Quality segregation*: approved, marginal and rejected oil must never mix.
- *Safety*: heavy liquid containers (up to ≈ 1 t) and forklifts in a small building are the main
  accident risk; restricted zones (tank farm, process line) must be enforced.
- *Labour and hours*: deliveries arrive in bursts; an AGV can work continuously.
- *Planning*: the processing plant needs reliable, FIFO-ordered feedstock supply.

**Why a digital twin first.** The layout, the number of storage positions, the AGV fleet size and
the control logic can be evaluated and demonstrated to investors and authorities before any
equipment is bought, and the same software architecture (ROS 2) can later drive real hardware.

## 3. Problem definition

Design and simulate an automated warehouse that:

1. accepts deliveries of UCO containers and identifies, weighs and inspects each one;
2. allocates a storage position to every approved container (and a quarantine position to
   non-approved material) and moves it there with an AGV;
3. keeps an always-consistent inventory (container, quantity, quality, location, status, history);
4. retrieves containers on request and delivers them to a processing buffer, oldest first;
5. operates safely: emergency stop, obstacle handling, restricted zones, speed zones,
   sensor and communication watchdogs, battery management;
6. tolerates and reports faults, and continues operating when they clear;
7. is observable (Gazebo, RViz2, dashboard) and measurable (KPIs recorded per run).

## 4. Objectives

**Technical objectives**

| ID | Objective | Where verified |
|---|---|---|
| T1 | Runnable Gazebo Harmonic world of a 36 × 24 m pilot warehouse | Ch. 9, Fig. 2 |
| T2 | AGV with lidar, odometry, IMU, battery, e-stop; autonomous navigation with Nav2 | Ch. 10, 14 |
| T3 | WMS with SQLite persistence and the operations REGISTER … REQUEST_DISPATCH | Ch. 11, 14 |
| T4 | Automated task assignment and execution incl. retries and time-outs | Ch. 11, 14 |
| T5 | Simulated safety logic and nine injectable fault types | Ch. 12, 14 |
| T6 | Dashboard, RViz2 visualisation, metrics with graphs | Ch. 16 |
| T7 | One-command reproducible demonstration | Appendix A |

**Business objectives (demonstrated in simulation)**

| ID | Objective |
|---|---|
| B1 | Every received container is traceable from dock to processing plant |
| B2 | Non-approved oil is physically segregated (quarantine) automatically |
| B3 | Processing requests are served FIFO from approved stock |
| B4 | Faults do not lose containers or corrupt inventory |
| B5 | Throughput and AGV utilisation can be quantified to size the real facility |

## 5. System requirements

The complete list (36 functional, 8 non-functional requirements with IDs) is in
`PROJECT_REQUIREMENTS.md`. Summary:

| Group | Requirements |
|---|---|
| Receiving | delivery arrival, unique IDs `UCO-NNNN`, weighing, quality inspection, holding area |
| WMS | SQLite persistence, operations REGISTER_CONTAINER … REQUEST_DISPATCH, automatic allocation, state machine, FIFO dispatch, occupancy |
| Tasks / AGV | assignment to available AGV, lifecycle with retries, time-outs, named-location navigation, pick/deliver, low-battery behaviour |
| Safety / faults | e-stop, obstacle stop / replan, restricted & speed zones, sensor / heartbeat watchdogs, blocked path, alarm state, 9 fault types, severity-tagged logs |
| Monitoring | dashboard with controls, RViz2, KPIs as CSV/JSON + graphs |
| Non-functional | Jazzy + Harmonic, CPU-only operation, configuration in YAML, no machine-specific paths, standard messages where possible, ROS-free testable core logic, open source |

## 6. System architecture

### 6.1 Layers

```mermaid
flowchart TB
  subgraph B["Business"]
    REST[Restaurants] --> DOCK[Receiving dock]
    PLANT[Biodiesel plant]
  end
  subgraph O["Operations (ROS 2 nodes)"]
    RS[receiving_station] --> WMS[wms_node + SQLite]
    WS[weighing_station] --> RS
    IS[inspection_station] --> RS
    DM[dispatch_manager] --> WMS
    WMS --> TM[task_manager]
    TM --> AC[agv_controller]
    FI[fault_injector] -. faults .-> RS & WMS & AC & SM
  end
  subgraph M["Motion"]
    AC --> NAV[Nav2]
    NAV --> SM[safety_manager gate]
    BAT[battery_simulator] --> AC
  end
  subgraph S["Simulation (Gazebo Harmonic)"]
    SM --> GZ[AGV-01 + warehouse world + containers]
    GZ --> NAV
  end
  subgraph H["Monitoring"]
    DB[dashboard] 
    RV[RViz2]
    MR[metrics_recorder]
  end
  WMS --> DB & MR
  DM --> PLANT
```

### 6.2 Key architectural decisions

1. **One layout file generates the world, the navigation map, the keepout mask and the WMS slot
   table** (`warehouse_layout.yaml`). Digital-twin drift between simulation, navigation and the
   inventory is impossible by construction, and a test fails if generated files are stale.
2. **Warehouse logic is ROS-free Python** (`store.py`, `dispatch_core.py`, `safety_core.py`,
   `station_models.py`, `battery_model.py`, `metrics_core.py`); nodes are thin adapters. This makes
   the business rules unit-testable in milliseconds.
3. **Standard components first**: Nav2 for localisation, planning, control, recovery, collision
   monitoring and keepout zones; ros_gz for spawning and moving entities; standard messages for
   sensors, velocity, battery and e-stop. Custom interfaces only for warehouse-domain data.
4. **Faults are events** on `/warehouse/faults`; each node implements only the faults it owns.
5. **Ground truth is never used for navigation**, only for placing payloads and for metrics.

### 6.3 Data flow

```mermaid
flowchart LR
  ST[stations] -- register / measure / place --> DB[(SQLite: containers, slots, tasks, events)]
  TM[task_manager] -- assign / progress / result --> DB
  AGV[agv_controller] -- picked --> DB
  DB -- inventory, tasks, container events --> DASH[dashboard] & MET[metrics CSV/JSON] & RVIZ[RViz markers]
  AGV -- /agv/state --> DASH & MET & TM & SAFE[safety_manager]
  SAFE -- /safety/status --> AGV & DASH
```

## 7. Warehouse design

![Warehouse layout](figures/warehouse_layout.png)

*Figure 1 — Layout generated from the layout file. Arrows show the AGV access pose (position and
heading) for every position.*

| Area | Content | AGV rule |
|---|---|---|
| Receiving dock (west) | dock door, delivery truck | – |
| Receiving process line | conveyor with UNLOAD, WEIGH, INSPECT stations, inspection booth | restricted (keep-out) |
| Holding area | HOLD-01 … HOLD-05 | speed ≤ 0.4 m/s |
| Automated storage | aisles A and B, rows R01/R02, bays 01–08 → 32 positions `A-03-R02` | speed ≤ 0.6 m/s |
| Quarantine | Q-01 … Q-03 for marginal / rejected oil | – |
| Dispatch / processing buffer | DSP-01 … DSP-04 next to the transfer door to the plant | speed ≤ 0.5 m/s |
| Charging station | CHG-01 with barriers | speed ≤ 0.3 m/s |
| Bulk tank farm (bunded) | 3 vertical tanks — placeholder for later liquid transfer | restricted |
| Maintenance bay | painted area | restricted |
| Office / control room | walled | not accessible |

Each storage position holds one pallet (drum or IBC, ≤ 1.2 × 1.2 m). Aisles are 3.2–3.4 m wide;
the AGV stops 1.6 m from the slot centre, facing it, which leaves ≥ 0.45 m between the AGV
front and the container.

![Gazebo world](figures/gazebo_world_overview.png)

*Figure 2 — The generated Gazebo world (rendered from the running simulation).*

## 8. ROS 2 architecture

The full interface reference is `docs/ros_architecture.md`; the essentials:

**Packages (11).** `uco_interfaces` (messages/services/actions), `uco_common` (layout model,
container models, alerts, QoS), `uco_description` (AGV xacro), `uco_simulation` (world generator,
bridge, Gazebo launch), `uco_navigation` (Nav2 configuration, map, RViz), `uco_wms`
(SQLite WMS, metrics), `uco_stations` (receiving, weighing, inspection, dispatch), `uco_fleet`
(task manager, AGV controller, battery), `uco_safety` (safety supervisor, fault injector),
`uco_dashboard` (web UI), `uco_bringup` (launch, scenarios). The proposed packages
`warehouse_manager`, `inventory_manager` and `storage_manager` were merged into `uco_wms` because
they share one database; `simulation_interfaces` was renamed because that name already exists
upstream.

**Topics.** `/warehouse/inventory`, `/warehouse/tasks`, `/warehouse/container_state`,
`/warehouse/alerts`, `/warehouse/faults`, `/warehouse/markers`, `/agv/state`, `/agv/battery`,
`/agv/odom`, `/agv/scan`, `/agv/scan_rear`, `/agv/imu`, `/agv/cmd_vel`, `/agv/ground_truth`,
`/safety/status`, `/speed_limit` plus the standard Nav2/TF topics.

**Services.** `/warehouse/{register_container, assign_storage, create_task, cancel_task,
assign_agv, update_task, update_status, update_location, request_dispatch}`,
`/stations/{receive_delivery, weigh, inspect, request_processing}`, `/safety/emergency_stop`
(`std_srvs/SetBool`), `/faults/inject`, bridged `/world/uco_warehouse/{create, remove, set_pose}`.

**Actions.** `/agv_01/transport_container` (pick-up + delivery with phase feedback),
`/agv_01/navigate_to_location` (named locations, aliases such as `A03`), Nav2
`/navigate_to_pose`. Pick-up and delivery are phases of one action rather than two actions, so
cancellation and pre-emption stay atomic.

**Communication patterns.** State is published latched (late joiners such as the dashboard get it
immediately); events (alerts, container changes) use a deep reliable queue; every WMS mutation is
a service call that returns `success` + an operator-readable message; long-running robot work is
an action with feedback.

## 9. Gazebo simulation

| Aspect | Implementation |
|---|---|
| World | generated SDF: walls with shutters, receiving line, racks, tank farm with bund, charger, office, pallet store, columns, floor markings, truck, 7 lights; validated with `gz sdf -k` |
| Physics | DART, 2 ms step; friction on deck and floor; containers are dynamic bodies with realistic mass (33–980 kg) |
| Sensors | 2 GPU lidars (264°, 10 Hz), IMU (50 Hz), optional RGB-D camera (5 Hz) |
| Robot plugins | diff-drive (odometry + TF), joint-state publisher, odometry publisher (ground truth) |
| World plugins | Physics, UserCommands, SceneBroadcaster, Sensors (ogre2), IMU |
| Integration | `ros_gz_bridge` for topics **and** for the create / remove / set_pose services |
| Containers | spawned at run time from SDF generated per container type and fill level |
| Rendering without GPU | Xvfb + Mesa llvmpipe (EGL headless crashes ogre2 without a GPU) |

The occupancy map is **generated** from the world geometry rather than recorded with SLAM. One
finding (Ch. 13) was that a map with *solid-filled* obstacles biases AMCL towards walls, so the
generator outputs obstacle outlines only, like a SLAM map.

## 10. AGV design

![AGV model](figures/agv_model.png)

*Figure 3 — AGV-01 docked at the charger: green chassis, grey lift deck, two black corner lidars,
red e-stop and yellow beacon on the rear corners.*

**Mechanical assumptions.** Load-carrying AMR, 1.0 × 0.7 m, 140 kg, deck height 0.35 m, rated for
one pallet up to ≈ 1 t. Differential drive with wheels at mid-length (turns on the spot) and
front/rear castors. Payload transfer is a lift-deck operation (not simulated mechanically, see
Ch. 17).

**Sensors.** Two 264° safety lidars at diagonally opposite corners give 360° coverage — the layout
used by industrial AMRs. A single wide-angle scanner at the front would see its own chassis
(Gazebo cannot mask robot visuals from a lidar through URDF), and an edge ray grazing the chassis
was shown to create phantom obstacles before the field of view was reduced from 270° to 264°.
IMU, wheel odometry, battery state and an e-stop complete the sensor set.

**Navigation.** Nav2: AMCL (front lidar, likelihood field), NavFn A* global planner,
Regulated Pure Pursuit controller (no reversing, rotate-to-heading), behaviour server
(spin, back-up, wait) with the default replanning/recovery behaviour tree, velocity smoother,
collision monitor (stop and slowdown polygons from both lidars), keepout costmap filter for
restricted zones, and speed limits published by the safety manager.

**Drive control: why not `ros2_control`.** The AGV uses Gazebo's differential-drive system, which
publishes odometry and TF and accepts `cmd_vel`, the same interface a real AMR base or a
`diff_drive_controller` would expose. `ros2_control` with `gz_ros2_control` would add a controller
manager, hardware interface and controller configuration without changing any behaviour of a single
differential drive in simulation, and would cost CPU on a machine that is already the bottleneck.
It becomes appropriate once there is real hardware or an actuated lift deck to control. Because
Nav2 only sees `cmd_vel` and odometry, that switch would not affect the rest of the system.

**Control.** `agv_controller` executes transport tasks as a sequence *navigate to pick-up →
lift → navigate to drop-off → set down*, reports phases, pauses on safety stops and resumes
automatically, re-tries navigation once per leg, interrupts non-critical work on low battery,
and returns to the charger when idle.

```mermaid
stateDiagram-v2
  [*] --> IDLE
  IDLE --> NAVIGATING: TransportContainer / NavigateToLocation
  NAVIGATING --> PICKING: at pick-up
  PICKING --> NAVIGATING: container on deck (PICKED)
  NAVIGATING --> DROPPING: at drop-off
  DROPPING --> IDLE: delivered
  NAVIGATING --> PAUSED: safety stop (e-stop, sensor fault, heartbeat)
  PAUSED --> NAVIGATING: released (goal re-sent)
  NAVIGATING --> GOING_TO_CHARGE: battery < 25 % and not carrying
  IDLE --> GOING_TO_CHARGE: battery < 25 % or idle 45 s
  GOING_TO_CHARGE --> CHARGING: docked
  CHARGING --> IDLE: battery ≥ 80 % or new task
```

## 11. Warehouse management

**Data model.** SQLite tables `containers`, `slots`, `tasks`, `events` (full audit trail) and
`meta` (ID sequences, counters). The slot table is created from the layout; the database survives
restarts (tested) and can be reset for demonstrations.

**Inventory and material tracking.** A container moves through REGISTERED → WEIGHED →
APPROVED / QUARANTINED / REJECTED → STORAGE_ASSIGNED → IN_TRANSIT → STORED → RESERVED →
IN_TRANSIT → DISPATCHED (hand-over to the plant). Illegal transitions are rejected. The quality
result (PASS / MARGINAL / FAIL) is stored separately from the logistic status. Every change is
an event row (`history(container_id)` reconstructs a container's life).

**Storage allocation.** Approved containers go to the free, enabled, unreserved STORAGE position
with the smallest travel estimate (Manhattan distance between AGV access poses) from where the
container is; marginal and failed containers go to QUARANTINE. The position is *reserved* until the
container arrives, so two containers can never be sent to the same slot. A `sequential` policy is
available for comparison.

**Task assignment.** The dispatcher assigns pending tasks by priority then age to the nearest
available AGV whose heartbeat is fresh and whose battery is ≥ 30 %. A container already on an AGV
(retry after pick-up) can only be finished by that AGV. Failed tasks are retried (3 attempts
before pick-up; unlimited after pick-up, because the container must be delivered); interruptions
for charging do not count as failures; tasks running longer than 600 s are cancelled.

**Dispatch.** A processing request selects stored, approved containers in arrival order, reserves a
dispatch-buffer position for each and creates RETRIEVE tasks; requests larger than the 4-position
buffer are partially served and reported as such.

**Consistency.** `check_consistency()` verifies that slot occupancy, reservations, container
locations and statuses agree; it is asserted in the unit tests after complex sequences.

## 12. Safety

The safety layer is *simulated* — it demonstrates the logic a certified safety PLC / safety
scanner configuration would implement; it is not itself safety-rated.

| Function | Implementation |
|---|---|
| Emergency stop | `/safety/emergency_stop` (SetBool) or fault; the velocity gate outputs zero, the AGV controller cancels its Nav2 goal and waits, then re-sends it on release |
| Obstacle detection | Nav2 collision monitor: slowdown polygon (40 % speed) and stop polygon from both lidars; local planner avoids and global planner replans; safety manager reports OBSTACLE (debounced 1 s, 2 s hysteresis) |
| Blocked path | stop polygon occupied > 20 s → PATH_BLOCKED alarm; Nav2 recoveries and the task retry handle the rest |
| Restricted zones | keepout filter removes them from planning; entering one anyway (e.g. localisation error) → ALARM, 3 s stop, then 0.1 m/s crawl until outside |
| Speed zones | zone limit published as `nav2_msgs/SpeedLimit` and enforced again in the gate |
| Collision prevention | footprint-based costmaps, RPP collision checking, collision monitor, gate |
| Sensor failure | lidar / odometry watchdog (1 s) → motion blocked, ALARM until data returns |
| Localisation failure | scan-to-map match score (share of lidar endpoints within 0.25 m of a mapped obstacle *or a container known to the WMS* at the AMCL pose, 2 Hz): < 0.4 for 5 s → LOCALIZATION_DEGRADED warning, < 0.2 for 5 s → LOCALIZATION_LOST alarm, motion blocked until re-localised. Added after a test run in which the robot was physically displaced and AMCL stayed confidently wrong by 8.7 m |
| AGV time-out | heartbeat watchdog (3 s) in safety manager (stops motion) and task manager (stops assignments) |
| Task time-out | 600 s per task |
| Low battery | warning < 25 %, alarm and speed limit 0.3 m/s < 10 %; charging behaviour in the AGV controller |
| Alarm state | NORMAL < WARNING < ALARM < EMERGENCY_STOP aggregated from all conditions |

```mermaid
stateDiagram-v2
  [*] --> NORMAL
  NORMAL --> WARNING: obstacle, low battery, station down, storage full
  WARNING --> NORMAL: cleared
  NORMAL --> ALARM: sensor failure, heartbeat lost, restricted zone, blocked, battery critical
  WARNING --> ALARM
  ALARM --> NORMAL: all cleared
  NORMAL --> EMERGENCY_STOP: e-stop
  WARNING --> EMERGENCY_STOP
  ALARM --> EMERGENCY_STOP
  EMERGENCY_STOP --> NORMAL: released, nothing else active
```

**Fault injection** (`/faults/inject`, CLI `inject_fault`, dashboard): BLOCKED_PATH (physical pallet
spawned, e.g. 1 m in front of the moving AGV), SENSOR_FAILURE, LOW_BATTERY, NAVIGATION_FAILURE,
REGISTRATION_FAILURE, STORAGE_FULL, STATION_UNAVAILABLE, COMMUNICATION_FAILURE, EMERGENCY_STOP.
Faults can be time-limited. All nodes log with `[INFO] / [WARN] / [ERROR] / [ALARM]` prefixes and
publish the same text on `/warehouse/alerts`, e.g.

```
[ERROR] AGV-01 battery below threshold (18 %): task interrupted, going to charge
[WARN] UCO-0006 waiting in HOLD-01: No free storage position available for UCO-0006
[WARN] Obstacle detected in navigation corridor
[ALARM] Emergency stop activated
```

## 13. Implementation

**Process.** Eighteen phases (0–17), each with build, run, test, fix, verify and document steps
(`DEVELOPMENT_PLAN.md`, `docs/development_log.md`). Phases 8–10 (WMS) were done before Phase 7
because receiving needs the WMS.

**Environment.** The official ROS apt repositories were blocked in the development container
(HTTP 403), so ROS 2 Jazzy and Gazebo Harmonic were installed from RoboStack (conda-forge builds of
the same releases); `scripts/install_environment.sh` supports both paths. Two toolchain pins were
needed (pytest < 9 for `launch_testing`, setuptools < 80 for colcon symlink installs).

**Notable problems found by measurement and fixed** (details in the development log):

| Problem | Evidence | Fix |
|---|---|---|
| Odometry yaw error 10 % | spin: odom 186° vs ground truth 205° | sphere wheel collisions → 0° error |
| AGV stuck when undocking | stop polygon radius 0.81 m vs 0.775 m clearance | smaller stop polygon (0.766 m) |
| AMCL biased 0.1 m towards walls | sensor and map verified against ground truth; plateau in likelihood | outline-only generated map |
| BT navigator aborting goals | "timed out waiting for follow_path ack" | server timeout 20 → 200 ms |
| Phantom obstacles in costmaps | all from the −135° edge ray grazing the bumper | FOV ±132°, flush bumpers → 0 phantom points / 7202 scans |
| Safety alert flapping | collision monitor publishes on change only | hysteresis keyed to zone-clear time |
| Node crashes at start | `clients`, `timers` attributes shadowed rclpy `Node` properties | renamed |

**Code size.** 11 ROS packages: ≈ 5 900 lines of Python application code (largest: `uco_wms`
1 350, `uco_fleet` 1 180, `uco_safety` 810), ≈ 1 500 lines of tests, ≈ 1 800 lines of interfaces,
URDF/Xacro, YAML configuration, C++ (`clock_throttle`) and web front-end, ≈ 800 lines of scripts.
All business logic lives in plain Python modules with unit tests.

<!-- GENERATED:RESULTS:BEGIN -->

## 14. Testing

All results in this chapter are generated by `scripts/build_report.py` from the files in `project_report/results/` and the latest `colcon test` output.

### 14.1 Unit and ROS integration tests (`scripts/test.sh`)

| Package | Tests | Failures | Errors | Skipped |
|---|---|---|---|---|
| uco_common | 18 | 0 | 0 | 0 |
| uco_dashboard | 5 | 0 | 0 | 0 |
| uco_description | 4 | 0 | 0 | 0 |
| uco_fleet | 16 | 0 | 0 | 0 |
| uco_navigation | 4 | 0 | 0 | 0 |
| uco_safety | 16 | 0 | 0 | 0 |
| uco_simulation | 3 | 0 | 0 | 0 |
| uco_stations | 12 | 0 | 0 | 0 |
| uco_wms | 38 | 0 | 0 | 0 |
| **Total** | **116** | **0** | **0** | **0** |

The test inventory and what each test verifies is in `docs/testing.md`.

### 14.2 Simulation test: fault suite (`scripts/run_sim_tests.sh`) — **PASSED, 38/38 checks**

Simulated duration 838 s. Safety and fault-injection tests: registration failure, station unavailable, speed zone, emergency stop, lidar failure, obstacle in the path, low battery, navigation failure, storage full and communication loss. Each container must still end up stored.

| # | Step | Condition | Observed | Result |
|---|---|---|---|---|
| 0 | **fault** | type=REGISTRATION_FAILURE, value=1 | REGISTRATION_FAILURE injected | ok |
| 1 | **fault** | type=STATION_UNAVAILABLE, target=INSPECT, duration=30 | STATION_UNAVAILABLE injected at INSPECT | ok |
| 2 | **delivery** | supplier=Cafe Roma, containers=[['DRUM_200L', 180]] | D-001: 1 containers from Cafe Roma at the dock | ok |
| 3 | wait_for | alert_since_step=REGISTRATION_FAILED | alert_since_step=REGISTRATION_FAILED | PASS (0.1 s) |
| 4 | wait_for | received=1 | received=1 | PASS (5.2 s) |
| 5 | wait_for | alert_since_step=WAITING_AT_INSPECTION | alert_since_step=WAITING_AT_INSPECTION | PASS (11.7 s) |
| 6 | expect | holding===0 | holding=0 | PASS |
| 7 | wait_for | holding=1 | holding=1 | PASS (23.3 s) |
| 8 | wait_for | task_phase=PICKING | task_phase=PICKING | PASS (49.9 s) |
| 9 | expect | speed_limit===0.4 | speed_limit=0.4 | PASS |
| 10 | wait_for | task_phase=TO_DROPOFF, agv_speed_above=0.15 | task_phase=TO_DROPOFF, agv_speed_above=0.26729875802993774 | PASS (5.3 s) |
| 11 | **estop** | True | e-stop ACTIVE | ok |
| 13 | expect | safety_state=EMERGENCY_STOP, motion_allowed=False, agv_speed_below=0.05 | safety_state=EMERGENCY_STOP, motion_allowed=False, agv_speed_below=0.0 | PASS |
| 15 | expect | agv_speed_below=0.05, agv_state=PAUSED | agv_speed_below=0.0, agv_state=PAUSED | PASS |
| 16 | **estop** | False | e-stop released | ok |
| 17 | wait_for | tasks_completed=1 | tasks_completed=1 | PASS (37.7 s) |
| 18 | expect | alert_since_step=AGV_RESUMED, tasks_failed===0 | alert_since_step=AGV_RESUMED, tasks_failed=0 | PASS |
| 19 | **delivery** | supplier=Burger Hub, containers=[['DRUM_200L', 170]] | D-002: 1 containers from Burger Hub at the dock | ok |
| 20 | wait_for | task_phase=TO_DROPOFF, agv_speed_above=0.15 | task_phase=TO_DROPOFF, agv_speed_above=0.19519883394241333 | PASS (56.2 s) |
| 21 | **fault** | type=SENSOR_FAILURE, target=lidar, duration=8 | SENSOR_FAILURE injected at lidar | ok |
| 23 | expect | alert_since_step=SENSOR_FAULT:scan, motion_allowed=False, safety_state=ALARM | alert_since_step=SENSOR_FAULT:scan, motion_allowed=False, safety_state=ALARM | PASS |
| 24 | wait_for | motion_allowed=True | motion_allowed=True | PASS (4.6 s) |
| 25 | wait_for | tasks_completed=2 | tasks_completed=2 | PASS (29.8 s) |
| 26 | **delivery** | supplier=Noodle Bar, containers=[['DRUM_200L', 190]] | D-003: 1 containers from Noodle Bar at the dock | ok |
| 27 | wait_for | task_phase=TO_DROPOFF, agv_speed_above=0.2 | task_phase=TO_DROPOFF, agv_speed_above=0.2664032280445099 | PASS (55.1 s) |
| 28 | **fault** | type=BLOCKED_PATH, target=AHEAD, duration=20 | BLOCKED_PATH injected at AHEAD | ok |
| 29 | wait_for | alert_since_step=OBSTACLE | alert_since_step=OBSTACLE | PASS (1.1 s) |
| 30 | wait_for | tasks_completed=3 | tasks_completed=3 | PASS (56.3 s) |
| 31 | **delivery** | supplier=Taco Place, containers=[['DRUM_200L', 185]] | D-004: 1 containers from Taco Place at the dock | ok |
| 32 | wait_for | task_phase=TO_PICKUP, agv_speed_above=0.1 | task_phase=TO_PICKUP, agv_speed_above=0.3899995982646942 | PASS (24.6 s) |
| 33 | **fault** | type=LOW_BATTERY, value=18 | LOW_BATTERY injected | ok |
| 34 | wait_for | alert_since_step=BATTERY_LOW | alert_since_step=BATTERY_LOW | PASS (0.6 s) |
| 35 | wait_for | agv_state=['GOING_TO_CHARGE', 'CHARGING'] | agv_state=GOING_TO_CHARGE | PASS (1.0 s) |
| 36 | expect | tasks_active=>=1, tasks_failed===0 | tasks_active=1, tasks_failed=0 | PASS |
| 37 | wait_for | agv_state=CHARGING | agv_state=CHARGING | PASS (35.5 s) |
| 38 | wait_for | battery_above=80 | battery_above=80.08167266845703 | PASS (116.0 s) |
| 39 | wait_for | tasks_completed=4 | tasks_completed=4 | PASS (87.3 s) |
| 40 | **fault** | type=NAVIGATION_FAILURE, value=2 | NAVIGATION_FAILURE injected | ok |
| 41 | **delivery** | supplier=Sushi Corner, containers=[['DRUM_200L', 150]] | D-005: 1 containers from Sushi Corner at the dock | ok |
| 42 | wait_for | alert_since_step=TASK_RETRY | alert_since_step=TASK_RETRY | PASS (25.2 s) |
| 43 | wait_for | tasks_completed=5 | tasks_completed=5 | PASS (76.0 s) |
| 44 | expect | alert_since_step=NAVIGATION_FAILED, tasks_failed===0 | alert_since_step=NAVIGATION_FAILED, tasks_failed=0 | PASS |
| 45 | **fault** | type=STORAGE_FULL | STORAGE_FULL injected | ok |
| 46 | **delivery** | supplier=Pizza Express, containers=[['DRUM_200L', 160]] | D-006: 1 containers from Pizza Express at the dock | ok |
| 47 | wait_for | alert_since_step=STORAGE_UNAVAILABLE | alert_since_step=STORAGE_UNAVAILABLE | PASS (20.2 s) |
| 48 | expect | container_status={'UCO-0006': 'APPROVED'}, holding=1 | container_status={'UCO-0006': 'APPROVED'}, holding=1 | PASS |
| 49 | **fault** | type=STORAGE_FULL, active=False | STORAGE_FULL cleared | ok |
| 50 | wait_for | tasks_completed=6 | tasks_completed=6 | PASS (83.3 s) |
| 51 | **move_agv** | location=A05, wait=False |  | ok |
| 52 | wait_for | agv_speed_above=0.15 | agv_speed_above=0.2045592963695526 | PASS (2.4 s) |
| 53 | **fault** | type=COMMUNICATION_FAILURE, target=AGV-01, duration=8 | COMMUNICATION_FAILURE injected at AGV-01 | ok |
| 55 | expect | alert_since_step=HEARTBEAT_LOST, motion_allowed=False | alert_since_step=HEARTBEAT_LOST, motion_allowed=False | PASS |
| 56 | wait_for | motion_allowed=True | motion_allowed=True | PASS (1.6 s) |
| 57 | expect | alert_since_step=AGV_TIMEOUT | alert_since_step=AGV_TIMEOUT | PASS |
| 58 | wait_for | agv_at=A-05-R01 | agv_at=A-05-R01 | PASS (1.9 s) |
| 59 | expect | stored=6, tasks_completed=6, tasks_failed===0, tasks_active===0 | stored=6, tasks_completed=6, tasks_failed=0, tasks_active=0 | PASS |

### 14.3 Simulation test: demonstration scenario (`scripts/run_demo.sh`) — **PASSED, 9/9 checks**

| # | Step | Condition | Observed | Result |
|---|---|---|---|---|
| 2 | **delivery** | supplier=Restaurant Bella Napoli, containers=[['DRUM_200L', 190], ['DRUM_200L', 175], ['IBC_1000L', 900], ['DRUM_200L', 160], ['IBC_1000L', 850]] | D-001: 5 containers from Restaurant Bella Napoli at the dock | ok |
| 3 | wait_for | received=5 | received=5 | PASS (28.1 s) |
| 5 | wait_for | holding=>=3 | holding=3 | PASS (8.3 s) |
| 7 | wait_for | stored=1 | stored=1 | PASS (74.0 s) |
| 9 | wait_for | stored=5, tasks_active===0 | stored=5, tasks_active=0 | PASS (248.7 s) |
| 10 | expect | stored=5, tasks_completed=5, tasks_failed===0, holding===0 | stored=5, tasks_completed=5, tasks_failed=0, holding=0 | PASS |
| 13 | **processing_request** | count=2 | 2 containers scheduled for processing | ok |
| 14 | wait_for | tasks_completed=7 | tasks_completed=7 | PASS (145.9 s) |
| 16 | wait_for | dispatched=2 | dispatched=2 | PASS (20.8 s) |
| 17 | expect | stored=3, dispatched=2, tasks_completed=7, tasks_failed===0 | stored=3, dispatched=2, tasks_completed=7, tasks_failed=0 | PASS |
| 19 | **move_agv** | location=A03, wait=True, timeout=600 | AGV-01 at A-03-R01 | ok |
| 20 | expect | agv_at=A-03-R01 | agv_at=A-03-R01 | PASS |

## 15. Performance evaluation

| KPI | Demonstration run | Fault-suite run |
|---|---|---|
| Simulated duration | 627 s | 873 s |
| Containers received / in storage at end / dispatched | 5 / 3 / 2 | 6 / 6 / 0 |
| Transport tasks completed / failed / cancelled | 7 / 0 / 0 | 6 / 0 / 0 |
| Task completion rate | 100% | 100% |
| Recovered failed attempts (retries) | 0 | 1 |
| Average task time: total / waiting / execution | 172.73 / 104.02 / 68.71 s | 112.72 / 0.4 / 112.32 s |
| Average task time by type | STORE: 200.2 s, RETRIEVE: 104.03 s | STORE: 112.72 s |
| Throughput (tasks / stored / retrieved per hour) | 40.19 / 28.7 / 11.48 | 24.75 / 24.75 / 0.0 |
| Storage occupancy at end / maximum | 9.4% / 15.6% of 32 | 18.8% / 18.8% of 32 |
| Stored volume at end | 1848.9 L | 979.3 L |
| AGV travel distance | 187.4 m | 193.4 m |
| AGV utilisation (busy / uptime) | 85.7% | 63.6% |
| Battery start → end | 85.0 % → 79.3 % | 85.0 % → 70.6 % |
| Energy drawn from the battery | 248.5 Wh (battery time scale ×10.0; ≈ 24.9 Wh real-time equivalent) | 266.1 Wh (battery time scale ×10.0; ≈ 26.6 Wh real-time equivalent) |
| Localisation error vs ground truth (mean / p95 / max) | 0.071 / 0.13 / 0.176 m (624 samples) | 0.068 / 0.112 / 0.26 m (869 samples) |

Notes: times are simulation seconds (real-time factor ≈ 1.0 on the 4-core reference machine). Waiting time is the time from task creation to the AGV starting it. Throughput is limited by a single AGV and by the receiving line rate. Battery dynamics run 10× faster than real time in these scenarios so that charging behaviour appears within minutes; the energy figure is also given as its real-time equivalent.

**Alerts in the demonstration run:** AGV_ASSIGNED ×7, CONTAINER_DELIVERED ×7, CONTAINER_PICKED ×7, TASK_COMPLETED ×7, TASK_DONE ×7, TASK_STARTED ×7, CONTAINER_REGISTERED ×5, INSPECTED ×5, RECEIVED ×5, STORAGE_ASSIGNED ×5, TASK_CREATED ×5, UNLOADED ×5, WEIGHED ×5, DISPATCHED ×2, HANDED_OVER ×2, AGV_ONLINE ×1, CHARGING_STARTED ×1, CHARGING_STOPPED ×1, DELIVERY_ARRIVED ×1, DISPATCH_REQUEST ×1, MANUAL_MOVE ×1, PROCESSING_REQUEST ×1.

**Alerts in the fault-suite run:** AGV_ASSIGNED ×8, FAULT_INJECTED ×8, TASK_STARTED ×8, CONTAINER_DELIVERED ×6, CONTAINER_PICKED ×6, CONTAINER_REGISTERED ×6, DELIVERY_ARRIVED ×6, INSPECTED ×6, RECEIVED ×6, STORAGE_ASSIGNED ×6, TASK_COMPLETED ×6, TASK_CREATED ×6, TASK_DONE ×6, UNLOADED ×6, WEIGHED ×6, FAULT_CLEARED ×5, AGV_RESUMED ×3, BATTERY_LOW ×3, MOTION_BLOCKED ×3, MOTION_RESUMED ×3, NAVIGATION_FAILED ×3, WAITING_AT_INSPECTION ×3, AGV_ONLINE ×2, AGV_PAUSED ×2.

## 16. Results

### 16.1 Demonstration scenario

A restaurant delivery vehicle arrives with five containers of used cooking oil. They are registered, weighed, inspected, approved and stored by AGV-01. A processing request then retrieves the two oldest containers to the dispatch / processing buffer.

![Containers on the holding positions after unloading, weighing and inspection](results/demo/figures/demo_01_receiving.png)

*Containers on the holding positions after unloading, weighing and inspection*

![First container stored in aisle A](results/demo/figures/demo_02_first_stored.png)

*First container stored in aisle A*

![All five containers stored](results/demo/figures/demo_03_all_stored.png)

*All five containers stored*

![Retrieved containers in the dispatch / processing buffer](results/demo/figures/demo_04_dispatch_buffer.png)

*Retrieved containers in the dispatch / processing buffer*

![AGV trajectory during the demonstration (red: carrying a container)](results/demo/figures/agv_trajectory.png)

*AGV trajectory during the demonstration (red: carrying a container)*

![Occupancy and container flow during the demonstration](results/demo/figures/occupancy.png)

*Occupancy and container flow during the demonstration*

![Transport task durations in the demonstration](results/demo/figures/task_durations.png)

*Transport task durations in the demonstration*

![Battery, speed and AGV state during the demonstration](results/demo/figures/agv_battery_state.png)

*Battery, speed and AGV state during the demonstration*

![Localisation error and scan/map match during the demonstration](results/demo/figures/localization.png)

*Localisation error and scan/map match during the demonstration*

### 16.2 Fault suite

![Fault suite: battery forced to 18 %, charging, e-stop and sensor-failure pauses](results/fault_suite/figures/agv_battery_state.png)

*Fault suite: battery forced to 18 %, charging, e-stop and sensor-failure pauses*

![Fault suite: alerts and alarms over time](results/fault_suite/figures/alerts_timeline.png)

*Fault suite: alerts and alarms over time*

![Fault suite: AGV trajectory](results/fault_suite/figures/agv_trajectory.png)

*Fault suite: AGV trajectory*

![Fault suite: localisation error and scan/map match](results/fault_suite/figures/localization.png)

*Fault suite: localisation error and scan/map match*

### 16.3 Monitoring

![Dashboard](figures/dashboard.png)

*Web dashboard during operation: layout with live slot occupancy, AGV pose and trail, AGV / battery / safety state, KPIs, operator controls, task and inventory tables, alarm log.*

![RViz2](figures/rviz_navigation.png)

*RViz2: map, keep-out zones, global and local costmaps, AGV model, both lidars, odometry, global plan towards a storage position.*

<!-- GENERATED:RESULTS:END -->

## 17. Limitations

This is a simulation and must be read as such. In particular:

| Area | What is simulated | What is *not* modelled |
|---|---|---|
| Container transfer | containers are placed between conveyor stations, slots and the AGV deck with Gazebo's `set_pose` (a "lift-deck transfer" taking 3 s) | lift/roller mechanics, forks, manipulators, load sway; the AGV is assumed to have a transfer mechanism |
| Receiving line | conveyor movement between stations is a timed transfer | conveyor physics, manual unloading with pallet jacks |
| Weighing / inspection | load-cell noise and resolution; FFA / water measurement noise; acceptance rules | real lab procedures, sampling, lab turnaround time, temperature effects |
| Liquids | container mass reflects the oil volume | sloshing, leaks, spills, pumping into the tank farm, heating |
| Safety | supervisor logic, e-stop gate, collision monitor, zones, watchdogs | a certified safety system (PLd/SIL), safety-rated scanners with protective / warning fields, personnel detection, fire / ATEX aspects |
| Fleet | one AGV; dispatcher and data model are multi-AGV ready | multi-robot traffic management, deadlock handling at aisle entries |
| Localisation | AMCL on a map generated from the world (mean error 4–8 cm measured) | map-building with SLAM in a real building, dynamic environment changes |
| Battery | energy model, docking detection, 10× accelerated in demonstrations (reported) | battery chemistry, ageing, charger communication |
| Communication | heartbeat loss of the AGV state stream | Wi-Fi roaming, real network partitions affecting all topics |
| Sensors | ideal lidar geometry with 1 cm noise; IMU; optional RGB-D | reflections, dust, sunlight, transparent objects; the camera is not used by any algorithm |
| Compute | runs on 4 CPU cores with software rendering (RTF ≈ 1) | real-time guarantees |

Other known limitations:

- The world, map and slot table come from one layout file. That is a strength for a digital twin,
  but it means the map is perfect. Real deployments need SLAM and map maintenance.
- Navigation success depends on Nav2 tuning for this layout (aisle widths, docking clearances);
  narrower aisles would need re-tuning.
- `IN_TRANSIT` containers lost by a simulated crash are not reconciled automatically.
- The demonstration scenarios run at real-time factor ≈ 1 on the reference machine; slower machines
  run proportionally longer (all logic uses simulation time, so results do not change).

## 18. Future development

| Topic | Next step |
|---|---|
| Robotic arms | MoveIt 2 cell at the receiving line for drum handling and sampling (lid opening, sample extraction), integrated as another station node |
| Automated liquid transfer | decanting station: AGV delivers the container, a pump transfers oil into the bunded tank farm, level sensors update a tank inventory in the WMS |
| PLC integration | conveyor, scale, doors and charger controlled by a PLC via OPC UA / Modbus; ROS 2 nodes become OPC UA clients; the safety function moves into the PLC |
| Real sensors | weighbridge / load cells, RFID or barcode readers at the dock, inline FFA / water analysers |
| Industrial AGVs | replace the simulated AGV with a commercial AMR (VDA 5050 interface) or a ROS 2-native AMR; `TransportContainer` maps onto VDA 5050 orders |
| Multi-AGV fleet | Open-RMF for traffic management and multi-fleet coordination; the dispatcher already supports several AGVs |
| SCADA / MES | publish inventory and alarms to SCADA (OPC UA, MQTT); ERP integration for supplier payment and certification |
| IoT | container tags with fill-level sensors at restaurants → predictive collection routing |
| Predictive maintenance | AGV motor current, battery health, door cycles → condition monitoring from the recorded metrics |
| Biodiesel integration | dispatch buffer → pre-treatment (degumming, drying) → transesterification; feedstock scheduling based on FFA / water |
| Digital twin | run the same WMS / fleet software against the real plant and the simulation side by side (shadow mode), use simulation for what-if capacity studies |
| Real plant | site survey, safety risk assessment (ISO 3691-4 for driverless trucks), ATEX zoning near the tank farm, commissioning with a real map |

## 19. Conclusion

The project produced a complete, runnable digital twin of the receiving, storage, transport and
dispatch operations of a used-cooking-oil facility:

- a Gazebo Harmonic warehouse generated from one layout file, together with its navigation map,
  keepout zones and WMS slot table;
- an AGV with two corner lidars, IMU, odometry, battery and e-stop, navigating autonomously with
  Nav2 (measured localisation error 4–8 cm, 11/11 goals across all zones);
- a SQLite WMS that enforces a container state machine, allocates storage, manages transport
  tasks with retries and time-outs, serves FIFO processing requests and keeps a full audit trail;
- receiving and dispatch stations with realistic measurement models;
- a safety supervisor and nine injectable fault types, each verified in simulation;
- a dashboard, RViz2 configuration, recorded KPIs and generated graphs;
- automated tests at three levels: unit, ROS integration and full simulation scenarios.

The development process found and fixed real engineering problems: an odometry error from wheel
collision geometry, an AMCL bias from filled obstacles, phantom obstacles from a lidar edge ray,
an undocking deadlock, alert flapping and a CPU-starvation problem. Each was found by measurement
rather than assumption. The architecture (ROS 2, Nav2, standard interfaces, ROS-free business
logic) is a credible basis for the next phases: manipulation, liquid transfer, PLC/SCADA
integration and, eventually, a real plant.

---

## Appendix A — How to reproduce

```bash
scripts/install_environment.sh apt     # or: robostack
scripts/build.sh
scripts/test.sh                         # unit + integration tests
scripts/run_sim_tests.sh                # fault_suite scenario in Gazebo (headless)
scripts/run_demo.sh                     # demonstration scenario (add --headless without a display)
scripts/plot_results.py runtime/results/<run>
```

Results of the runs quoted in this report are stored in `project_report/results/`.
