cmake_minimum_required(VERSION 3.0.2)
project(ugv_mission_pkg)

find_package(catkin REQUIRED COMPONENTS
  message_generation
  std_msgs
  sensor_msgs   # Add this for CompressedImage
)

add_message_files(
  FILES
  GPS.msg
  MissionWaypoint.msg
)

add_service_files(
  FILES
  mission_commands.srv
  mission_states.srv
  mission_file_transfer.srv
  segmentation_file_transfer.srv
)

generate_messages(
  DEPENDENCIES
  std_msgs
  sensor_msgs    # Add this dependency
)

catkin_package(
  CATKIN_DEPENDS
    message_runtime
    std_msgs
    sensor_msgs  # Add this dependency
)