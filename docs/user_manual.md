# User Manual

## 1. Install

```bash
scripts/install_environment.sh apt          # Ubuntu 24.04 + packages.ros.org (recommended)
# or
scripts/install_environment.sh robostack    # conda-forge / RoboStack build (what the reference container used)
scripts/check_environment.sh                 # prints detected versions
```

## 2. Build

```bash
scripts/build.sh                 # colcon build --symlink-install of ros2_ws
source scripts/env.sh            # ROS 2 + workspace overlay (+ Xvfb/software GL if no display)
```

`scripts/env.sh` sets `ROS_DOMAIN_ID=42` unless you already set one.

## 3. Run the digital twin

| What | Command |
|---|---|
| Everything (Gazebo GUI, RViz2, dashboard) | `ros2 launch uco_bringup warehouse.launch.py` |
| Headless (no GUI windows) | `ros2 launch uco_bringup warehouse.launch.py gui:=false rviz:=false` |
| Demonstration scenario, then exit | `scripts/run_demo.sh` (or add `scenario:=demo exit_after_scenario:=true`) |
| Safety / fault test suite | `scripts/run_sim_tests.sh` |
| Only Gazebo + AGV | `ros2 launch uco_simulation sim.launch.py` |
| Only Nav2 (+RViz) | `ros2 launch uco_navigation navigation.launch.py rviz:=true` |

Launch switches: `sim nav wms stations fleet safety dashboard metrics rviz gui camera` (`true`/`false`),
`quality_profile:=good|mixed|poor`, `dashboard_port:=8080`, `results_dir:=...`, `run_name:=...`.

The application nodes start 8 s after Gazebo and Nav2 starts after 12 s, so give the
system ~40 s before sending commands (the dashboard shows `SYSTEM OK` when everything is up).

## 4. Operate

### Dashboard
Open <http://localhost:8080>. It shows the layout with live slot occupancy, the AGV pose, trail,
state and battery, the safety state, KPIs, task and inventory tables and the alarm log. The
operator panel can trigger a delivery, a processing request, an AGV move, the e-stop and any fault.

### Command line

```bash
# a restaurant delivery with two drums and one IBC
ros2 service call /stations/receive_delivery uco_interfaces/srv/ReceiveDelivery \
  "{supplier: 'Restaurant A', container_types: [DRUM_200L, DRUM_200L, IBC_1000L], declared_volumes_l: [180, 170, 900]}"

# processing request: retrieve the two oldest approved containers to the dispatch buffer
ros2 service call /stations/request_processing uco_interfaces/srv/RequestDispatch "{count: 2}"

# move AGV-01 to storage position A03 (aliases: A03 = A-03-R01, A03R2, charger, home, dispatch)
ros2 run uco_fleet move_agv A03

# emergency stop / release
ros2 service call /safety/emergency_stop std_srvs/srv/SetBool "{data: true}"
ros2 service call /safety/emergency_stop std_srvs/srv/SetBool "{data: false}"

# faults (see table below); --duration auto-clears, --clear clears
ros2 run uco_safety inject_fault LOW_BATTERY --value 18
ros2 run uco_safety inject_fault BLOCKED_PATH --target AHEAD --duration 20
ros2 run uco_safety inject_fault SENSOR_FAILURE --target lidar --duration 8
ros2 run uco_safety inject_fault STORAGE_FULL            # ... later: --clear

# WMS operations directly
ros2 service call /warehouse/register_container uco_interfaces/srv/RegisterContainer "{container_type: DRUM_200L, declared_volume_l: 180}"
ros2 topic echo /warehouse/inventory --once
ros2 topic echo /warehouse/tasks --once
ros2 topic echo /warehouse/alerts
```

### Faults

| Fault | Target | Value | Effect |
|---|---|---|---|
| `BLOCKED_PATH` | location id, `x,y` or `AHEAD` | – | pallet obstacle spawned in Gazebo |
| `SENSOR_FAILURE` | `scan`, `scan_rear`, `lidar` (both) | – | lidar data stops → AGV stopped, ALARM |
| `LOW_BATTERY` | – | % (default 18) | battery set → task interrupted, AGV charges |
| `NAVIGATION_FAILURE` | `AGV-01` | number of attempts | next navigation attempts fail → task retry |
| `REGISTRATION_FAILURE` | – | count | next registrations rejected → receiving retries |
| `STORAGE_FULL` | – | – | no storage positions offered while active |
| `STATION_UNAVAILABLE` | `INSPECT` or `WEIGH` | – | station refuses work, line waits |
| `COMMUNICATION_FAILURE` | `AGV-01` | – | AGV heartbeat suppressed for `--duration` s |
| `EMERGENCY_STOP` | – | – | e-stop latched while active |

## 5. Visualise

- **Gazebo**: the warehouse, containers moving along the receiving line, AGV transports.
- **RViz2** (`rviz:=true`): map, keepout zones, costmaps, robot model, TF, both lidars,
  odometry, AMCL pose, global/local plan, goal, collision-monitor zones, WMS container markers.
  Use *2D Goal Pose* to send manual Nav2 goals.

## 6. Results

Every launch with `metrics:=true` writes to `runtime/results/<run_name>/`:
`tasks.csv`, `agv_trace.csv`, `occupancy.csv`, `alerts.csv`, `metrics.json`
(+ `scenario_report.json` and `figures/` when a scenario ran). Generate graphs with

```bash
scripts/plot_results.py runtime/results/<run_name>
```

## 7. Test

```bash
scripts/test.sh            # unit + ROS integration tests of all packages (no Gazebo)
scripts/run_sim_tests.sh   # full simulation: fault_suite scenario, exits non-zero on failure
```

## 8. Stop

`Ctrl+C` in the launch terminal, or `scripts/stop_all.sh` to kill every simulation process.

## 9. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Gazebo crashes in `Ogre2RenderEngine::CreateRenderSystem` | no GPU and no display: use `source scripts/env.sh` (starts Xvfb + software GL) |
| AGV does not move, dashboard shows `motion_allowed: false` | e-stop latched or sensor fault: check `/safety/status` conditions |
| Tasks stay `PENDING` | AGV below 30 % battery, charging, or offline; see AGV state |
| `colcon build --symlink-install` fails with `--editable` | setuptools ≥ 80; install `setuptools<80` |
| `launch_testing` pytest plugin error | pytest ≥ 9; install `pytest<9` |
