# Tests

Tests live next to the code they test, so `colcon test` finds them:

| Level | Location | Run with |
|---|---|---|
| Unit | `ros2_ws/src/<package>/test/test_*.py` | `scripts/test.sh` |
| ROS integration | `uco_wms/test/test_wms_node_integration.py`, `uco_fleet/test/test_fleet_integration.py`, `uco_dashboard/test/test_dashboard_http.py` | `scripts/test.sh` |
| Simulation (Gazebo) | scenarios in `ros2_ws/src/uco_bringup/config/scenarios.yaml` | `scripts/run_sim_tests.sh`, `scripts/run_demo.sh` |

Inventory, traceability and results: [`../docs/testing.md`](../docs/testing.md).
