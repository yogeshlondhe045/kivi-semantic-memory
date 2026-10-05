string STATE_NORMAL="NORMAL"
string STATE_WARNING="WARNING"
string STATE_ALARM="ALARM"
string STATE_EMERGENCY_STOP="EMERGENCY_STOP"

std_msgs/Header header
string state
bool estop_active
bool motion_allowed          # false -> cmd gate outputs zero
float32 speed_limit          # m/s currently applied (0 = none)
string zone                  # zone the AGV is in
string[] active_conditions   # e.g. OBSTACLE, SENSOR_FAULT:scan, HEARTBEAT_LOST
