# Project Requirements — UCO Warehouse Digital Twin

Automated warehouse and material-handling simulation for a used-cooking-oil (UCO)
collection facility that feeds a biodiesel plant. **Phase 1 scope is the warehouse
only**: the chemical biodiesel process is explicitly out of scope.

Every requirement has an ID. `docs/testing.md` and the project report trace
tests and results back to these IDs.

## 1. Stakeholders and context

| Stakeholder | Interest |
|---|---|
| Business owner | Show investors/partners a credible automated intake and storage concept |
| Plant operations | Throughput, traceability of every container, safe AGV operation |
| Engineering | Modular ROS 2 software that can grow into a real deployment |
| Restaurants (suppliers) | Fast unloading at the dock, a record of what they delivered |

## 2. Functional requirements

### 2.1 Receiving
| ID | Requirement |
|---|---|
| FR-RCV-01 | The system shall simulate the arrival of a delivery containing N UCO containers (drums and IBCs) at the receiving dock. |
| FR-RCV-02 | Each incoming container shall be identified with a unique ID of the form `UCO-NNNN` (generated if the container has no tag). |
| FR-RCV-03 | Each container shall be weighed at the weighing station; the measured mass and derived volume (L) shall be recorded. |
| FR-RCV-04 | Each container shall be inspected (simulated quality test: free fatty acids, water, contamination) and marked `APPROVED`, `REJECTED` or `QUARANTINED`. |
| FR-RCV-05 | After processing, containers shall be placed in a temporary holding area until an AGV collects them. |

### 2.2 Warehouse management (WMS)
| ID | Requirement |
|---|---|
| FR-WMS-01 | The WMS shall persist container records in SQLite: ID, material, quantity, weight, status, quality status, arrival time, current location, destination, assigned AGV. |
| FR-WMS-02 | The WMS shall support the operations REGISTER_CONTAINER, ASSIGN_STORAGE, CREATE_TRANSPORT_TASK, ASSIGN_AGV, UPDATE_LOCATION, UPDATE_STATUS, COMPLETE_TASK, REQUEST_DISPATCH (and CANCEL_TASK). |
| FR-WMS-03 | Storage allocation shall choose a free, enabled storage position automatically and reserve it until the container is stored. |
| FR-WMS-04 | Storage positions shall be identified as `<aisle>-<bay>-<row>`, e.g. `A-03-R02`. |
| FR-WMS-05 | Inventory, container state and task lists shall be published continuously on ROS 2 topics. |
| FR-WMS-06 | Container status changes shall follow a defined state machine; illegal transitions shall be rejected. |
| FR-WMS-07 | A processing (dispatch) request shall select stored, approved containers (FIFO by arrival) and create retrieval tasks to the dispatch / processing buffer. |
| FR-WMS-08 | When a container is stored the slot shall become occupied; when retrieved it shall become free. |

### 2.3 Task management and AGV
| ID | Requirement |
|---|---|
| FR-TSK-01 | The task manager shall assign pending transport tasks to an available AGV (idle, healthy, battery above threshold). |
| FR-TSK-02 | Tasks shall follow the lifecycle PENDING → ASSIGNED → IN_PROGRESS → COMPLETED / FAILED / CANCELLED, with automatic retry of failed tasks up to a configurable limit. |
| FR-TSK-03 | A task exceeding its time limit shall be cancelled and marked failed (task timeout). |
| FR-AGV-01 | The AGV shall accept navigation goals by named warehouse location (e.g. "move AGV-01 to A-03-R01") and by pose. |
| FR-AGV-02 | The AGV shall navigate autonomously (localization, global planning, local control, obstacle avoidance, recovery) using Nav2. |
| FR-AGV-03 | The AGV shall execute a transport task: go to pickup, pick container, go to drop-off, deliver container, report completion. |
| FR-AGV-04 | The AGV shall publish odometry, lidar scan, IMU, battery state and a summarised AGV state. |
| FR-AGV-05 | When the battery falls below the low threshold the AGV shall abandon a non-critical task (not carrying a load), drive to the charger, charge, and then become available again; a task in which it is already carrying a load is completed first. |

### 2.4 Safety and faults
| ID | Requirement |
|---|---|
| FR-SAF-01 | An emergency stop shall immediately command zero velocity and pause all AGV motion until released. |
| FR-SAF-02 | Obstacles in the travel corridor shall cause slow-down then stop; the AGV shall wait / replan and resume when clear. |
| FR-SAF-03 | Restricted zones shall be excluded from path planning and entry shall raise an alarm. |
| FR-SAF-04 | Speed-reduction zones shall limit AGV speed. |
| FR-SAF-05 | Loss of lidar or odometry data (sensor failure) shall stop the AGV and raise an error. |
| FR-SAF-06 | Loss of AGV heartbeat (communication failure / AGV timeout) shall be detected and alarmed. |
| FR-SAF-07 | A path blocked longer than a configurable time shall be reported as BLOCKED and the task failed for re-planning / retry. |
| FR-SAF-08 | The warehouse shall have an aggregated alarm state (NORMAL / WARNING / ALARM / EMERGENCY_STOP). |
| FR-FLT-01 | A fault-injection interface shall simulate: blocked path, sensor failure, low battery, navigation failure, registration failure, storage full, station unavailable, communication failure, emergency stop. |
| FR-FLT-02 | All faults and alarms shall be logged with a severity prefix (`[INFO]`, `[WARN]`, `[ERROR]`, `[ALARM]`) and published on `/warehouse/alerts`. |

### 2.5 Monitoring, visualisation and analytics
| ID | Requirement |
|---|---|
| FR-MON-01 | A web dashboard shall show layout, AGV position/state/battery, active and completed tasks, inventory, storage occupancy, alarms, throughput and system status. |
| FR-MON-02 | The dashboard shall allow an operator to trigger a delivery, a processing request, an AGV move, an emergency stop and fault injection. |
| FR-MON-03 | RViz2 shall show robot model, TF, laser scan, odometry, map, planned path, robot pose and navigation goal. |
| FR-MET-01 | The system shall record: containers processed, average task time, AGV travel distance, AGV utilisation, occupancy, task completion rate, failed tasks, average waiting time, battery consumption, throughput — as CSV and JSON. |
| FR-MET-02 | A script shall turn recorded results into graphs. |

### 2.6 Simulation
| ID | Requirement |
|---|---|
| FR-SIM-01 | A Gazebo Sim world shall represent a small pilot warehouse: walls, doors, receiving dock and stations, holding area, storage lanes, dispatch / processing buffer, charging station, tank farm, barriers, floor markings, lighting, static obstacles. |
| FR-SIM-02 | The world, the navigation map and the WMS location table shall be generated from one layout file so they cannot disagree. |
| FR-SIM-03 | The complete demonstration scenario shall be reproducible with one launch command. |

## 3. Non-functional requirements

| ID | Requirement |
|---|---|
| NFR-01 | Target platform: Ubuntu 24.04, ROS 2 Jazzy, Gazebo Harmonic (officially paired LTS releases). |
| NFR-02 | Must run on a CPU-only machine (software OpenGL) — sensor and world complexity kept modest. |
| NFR-03 | Configuration (layout, thresholds, Nav2 parameters, scenarios) is in YAML, never hard-coded. |
| NFR-04 | No machine-specific absolute paths; everything resolves through ROS package shares or CLI arguments. |
| NFR-05 | Standard ROS messages wherever possible; custom interfaces only for warehouse-domain data. |
| NFR-06 | Warehouse logic (inventory, allocation, task state machine) is pure Python and unit-testable without ROS or Gazebo. |
| NFR-07 | Every claim of working behaviour is backed by a test or a recorded run; unverified items are listed as such. |
| NFR-08 | Open-source dependencies only (Apache-2.0 / BSD / MIT compatible). |

## 4. Out of scope (phase 1)

- Biodiesel chemistry (transesterification, methanol/catalyst handling, glycerol separation).
- Liquid transfer (pumping from containers into tanks) — the tank farm is a visual placeholder.
- Robotic manipulation — container transfer between station/AGV/slot is a simulated lift-deck
  transfer (no manipulator physics). MoveIt 2 is therefore not used.
- Multi-AGV traffic management — data model and task manager are fleet-ready, the demo uses one AGV.
- Real PLC / SCADA / hardware integration.
