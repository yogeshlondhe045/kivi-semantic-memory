# Digital record of one used-cooking-oil container.
string STATUS_ARRIVED="ARRIVED"
string STATUS_REGISTERED="REGISTERED"
string STATUS_WEIGHED="WEIGHED"
string STATUS_APPROVED="APPROVED"
string STATUS_QUARANTINED="QUARANTINED"
string STATUS_REJECTED="REJECTED"
string STATUS_STORAGE_ASSIGNED="STORAGE_ASSIGNED"
string STATUS_IN_TRANSIT="IN_TRANSIT"
string STATUS_STORED="STORED"
string STATUS_RESERVED="RESERVED"
string STATUS_DISPATCHED="DISPATCHED"

string id                    # e.g. UCO-0001
string container_type        # DRUM_200L | IBC_1000L
string material              # "Used Cooking Oil"
string supplier
float64 declared_volume_l    # from delivery note
float64 measured_volume_l    # from weighing station
float64 weight_kg            # gross mass measured at the weighing station
string status
string quality_status        # PENDING | PASS | MARGINAL | FAIL
string location              # location id, e.g. HOLD-02, A-03-R02, AGV-01
string destination           # location id or empty
string assigned_agv
float64 arrival_time         # seconds (ROS time)
float64 updated_time
