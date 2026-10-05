cmake_minimum_required(VERSION 3.10)
project(uco_simulation)

find_package(ament_cmake REQUIRED)

install(DIRECTORY worlds launch config DESTINATION share/${PROJECT_NAME})
install(PROGRAMS scripts/generate_world.py scripts/snapshot.py DESTINATION lib/${PROJECT_NAME})

if(BUILD_TESTING)
  find_package(ament_cmake_pytest REQUIRED)
  ament_add_pytest_test(test_world_generation test/test_world_generation.py TIMEOUT 120)
endif()

ament_package()
