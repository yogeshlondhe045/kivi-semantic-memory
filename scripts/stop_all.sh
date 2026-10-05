#!/usr/bin/env bash
# Stop every process started by the simulation launch files (Gazebo, bridge, Nav2, uco nodes).
# SIGINT first (clean shutdown, metrics are flushed), then SIGKILL for anything left.
# The [x] bracket trick keeps pkill from matching this script's own command line.
PATTERNS=('[r]os2 launch' '[g]z sim' '[p]arameter_bridge' '[r]obot_state_publisher' '[c]lock_throttle'
          '[l]ib/uco_' '[l]ifecycle_manager' '[m]ap_server' '[a]mcl' '[c]ontroller_server' '[p]lanner_server'
          '[b]ehavior_server' '[b]t_navigator' '[v]elocity_smoother' '[c]ollision_monitor'
          '[c]ostmap_filter_info_server' '[r]viz2' '[s]napshot.py'
          '[p]ython -m uco_')
for pat in "${PATTERNS[@]}"; do pkill -INT -f "$pat" 2>/dev/null || true; done
sleep 4
for pat in "${PATTERNS[@]}"; do pkill -KILL -f "$pat" 2>/dev/null || true; done
exit 0
