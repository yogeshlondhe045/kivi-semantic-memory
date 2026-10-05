# A physical position that can hold one container.
string id                 # e.g. A-03-R02
string kind               # STORAGE | HOLDING | QUARANTINE | DISPATCH
string zone
float64 x
float64 y
float64 yaw
string container_id       # empty if free
bool reserved             # reserved for an incoming transport
bool enabled              # false if blocked (maintenance / fault)
