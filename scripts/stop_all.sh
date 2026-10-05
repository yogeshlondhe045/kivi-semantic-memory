#!/usr/bin/env bash
# Stop every process started by the simulation launch files (Gazebo, bridge, Nav2, uco nodes).
# The [x] bracket trick keeps pkill from matching this script's own command line.
for pat in '[r]os2 launch' '[g]z sim' '[p]arameter_bridge' '[r]obot_state_publisher' '[n]av2_' \
           '[c]omponent_container' '[l]ib/uco_' '[l]ifecycle_manager' '[m]ap_server' '[a]mcl' \
           '[c]ontroller_server' '[p]lanner_server' '[b]ehavior_server' '[b]t_navigator' \
           '[v]elocity_smoother' '[c]ollision_monitor' '[r]viz2'; do
  pkill -INT -f "$pat" 2>/dev/null || true
done
sleep 2
for pat in '[g]z sim' '[r]os2 launch' '[l]ib/uco_' '[p]arameter_bridge'; do
  pkill -KILL -f "$pat" 2>/dev/null || true
done
exit 0
