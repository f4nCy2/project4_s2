cmake_minimum_required(VERSION 3.0.2)
project(gazebo_simulation)

find_package(catkin REQUIRED COMPONENTS
  gazebo_ros
  gazebo_plugins
  robot_state_publisher
  geometry_msgs
  nav_msgs
  sensor_msgs
  std_msgs
  tf
  cv_bridge
)

catkin_package(
  CATKIN_DEPENDS
    gazebo_ros
    gazebo_plugins
    robot_state_publisher
    geometry_msgs
    nav_msgs
    sensor_msgs
    std_msgs
    tf
    cv_bridge
)

install(DIRECTORY launch worlds robot_description
  DESTINATION ${CATKIN_PACKAGE_SHARE_DESTINATION}
)

install(PROGRAMS
  scripts/gazebo_bridge.py
  scripts/run_simulation.sh
  DESTINATION ${CATKIN_PACKAGE_SHARE_DESTINATION}/scripts
)
