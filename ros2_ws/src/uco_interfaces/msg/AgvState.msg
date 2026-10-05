string STATE_IDLE="IDLE"
string STATE_NAVIGATING="NAVIGATING"
string STATE_PICKING="PICKING"
string STATE_DROPPING="DROPPING"
string STATE_CHARGING="CHARGING"
string STATE_GOING_TO_CHARGE="GOING_TO_CHARGE"
string STATE_PAUSED="PAUSED"           # e-stop / sensor fault
string STATE_ERROR="ERROR"

std_msgs/Header header
string agv_id
string state
string current_task
string carrying_container
string current_location      # nearest named location or ""
string goal_location
geometry_msgs/Pose2D pose    # map frame (AMCL)
float32 linear_speed
float32 battery_percent
bool charging
bool available               # accepts new tasks
float64 distance_travelled_m
float64 busy_time_s
float64 uptime_s
