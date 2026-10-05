string TYPE_STORE="STORE"            # holding -> storage
string TYPE_RETRIEVE="RETRIEVE"      # storage -> dispatch buffer
string TYPE_QUARANTINE="QUARANTINE"  # holding -> quarantine
string TYPE_MOVE="MOVE"              # generic container move

string STATUS_PENDING="PENDING"
string STATUS_ASSIGNED="ASSIGNED"
string STATUS_IN_PROGRESS="IN_PROGRESS"
string STATUS_COMPLETED="COMPLETED"
string STATUS_FAILED="FAILED"
string STATUS_CANCELLED="CANCELLED"

string id                 # e.g. T-0001
string task_type
string container_id
string source
string destination
string status
string phase              # TO_PICKUP | PICKING | TO_DROPOFF | DROPPING | ""
string assigned_agv
int32 priority            # higher first
int32 attempts
float64 created_time
float64 assigned_time
float64 started_time
float64 completed_time
string failure_reason
