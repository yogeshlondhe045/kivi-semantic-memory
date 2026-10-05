cmake_minimum_required(VERSION 3.10)
project(uco_navigation)

find_package(ament_cmake REQUIRED)
install(DIRECTORY config launch maps rviz DESTINATION share/${PROJECT_NAME})

if(BUILD_TESTING)
  find_package(ament_cmake_pytest REQUIRED)
  ament_add_pytest_test(test_nav_config test/test_nav_config.py TIMEOUT 60)
endif()

ament_package()
