# Simulated fault event, published by fault_injector on /warehouse/faults.
string BLOCKED_PATH="BLOCKED_PATH"
string SENSOR_FAILURE="SENSOR_FAILURE"
string LOW_BATTERY="LOW_BATTERY"
string NAVIGATION_FAILURE="NAVIGATION_FAILURE"
string REGISTRATION_FAILURE="REGISTRATION_FAILURE"
string STORAGE_FULL="STORAGE_FULL"
string STATION_UNAVAILABLE="STATION_UNAVAILABLE"
string COMMUNICATION_FAILURE="COMMUNICATION_FAILURE"
string EMERGENCY_STOP="EMERGENCY_STOP"

builtin_interfaces/Time stamp
string fault_type
bool active
string target          # e.g. AGV-01, scan, odom, INSPECT, location id
float64 value          # fault-specific numeric parameter (battery %, x, ...)
float64 duration_s     # 0 = until cleared
