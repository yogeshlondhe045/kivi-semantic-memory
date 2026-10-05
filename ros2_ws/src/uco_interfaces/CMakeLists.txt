cmake_minimum_required(VERSION 3.10)
project(uco_interfaces)

find_package(ament_cmake REQUIRED)
find_package(rosidl_default_generators REQUIRED)
find_package(std_msgs REQUIRED)
find_package(geometry_msgs REQUIRED)
find_package(builtin_interfaces REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "msg/ContainerState.msg"
  "msg/StorageSlot.msg"
  "msg/Inventory.msg"
  "msg/Task.msg"
  "msg/TaskList.msg"
  "msg/AgvState.msg"
  "msg/Alert.msg"
  "msg/SafetyStatus.msg"
  "msg/Fault.msg"
  "srv/RegisterContainer.srv"
  "srv/AssignStorage.srv"
  "srv/CreateTask.srv"
  "srv/CancelTask.srv"
  "srv/AssignAgv.srv"
  "srv/UpdateTask.srv"
  "srv/UpdateStatus.srv"
  "srv/UpdateLocation.srv"
  "srv/RequestDispatch.srv"
  "srv/InjectFault.srv"
  "srv/Weigh.srv"
  "srv/Inspect.srv"
  "srv/ReceiveDelivery.srv"
  "action/NavigateToLocation.action"
  "action/TransportContainer.action"
  DEPENDENCIES std_msgs geometry_msgs builtin_interfaces
)

ament_export_dependencies(rosidl_default_runtime)
ament_package()
