std_msgs/Header header
ContainerState[] containers      # all containers not yet dispatched/rejected-and-gone
StorageSlot[] slots
uint32 storage_capacity          # number of STORAGE slots
uint32 storage_occupied
float64 storage_occupancy        # 0..1
float64 stored_volume_l
uint32 containers_received_total
uint32 containers_dispatched_total
