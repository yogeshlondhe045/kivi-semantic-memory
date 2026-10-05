uint8 INFO=0
uint8 WARN=1
uint8 ERROR=2
uint8 ALARM=3

builtin_interfaces/Time stamp
uint8 severity
string source       # node name / AGV id
string code         # machine-readable code, e.g. BATTERY_LOW
string message      # human readable
