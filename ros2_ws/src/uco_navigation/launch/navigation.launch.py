"""Nav2 for AGV-01: map server + AMCL, keepout filter, planner, controller, behaviors,
BT navigator, velocity smoother and collision monitor.

    ros2 launch uco_navigation navigation.launch.py [rviz:=true]
"""
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

from uco_common.layout import load_layout


def generate_launch_description():
    share = get_package_share_directory('uco_navigation')
    params = LaunchConfiguration('params_file')
    use_sim_time = {'use_sim_time': True}
    dock = load_layout().location('CHG-01').access

    def nav_node(package, executable, name=None, remaps=(), extra=None):
        return Node(package=package, executable=executable, name=name or executable, output='screen',
                    parameters=[params, use_sim_time] + ([extra] if extra else []),
                    remappings=list(remaps), arguments=['--ros-args', '--log-level', LaunchConfiguration('log_level')])

    localization = [
        nav_node('nav2_map_server', 'map_server', extra={'yaml_filename': LaunchConfiguration('map')}),
        nav_node('nav2_amcl', 'amcl', extra={
            'initial_pose.x': LaunchConfiguration('initial_x'),
            'initial_pose.y': LaunchConfiguration('initial_y'),
            'initial_pose.yaw': LaunchConfiguration('initial_yaw')}),
        Node(package='nav2_lifecycle_manager', executable='lifecycle_manager', name='lifecycle_manager_localization',
             output='screen', parameters=[use_sim_time, {'autostart': True, 'node_names': ['map_server', 'amcl']}]),
    ]
    filters = [
        nav_node('nav2_map_server', 'map_server', name='filter_mask_server',
                 extra={'yaml_filename': LaunchConfiguration('keepout_mask')}),
        nav_node('nav2_map_server', 'costmap_filter_info_server'),
        Node(package='nav2_lifecycle_manager', executable='lifecycle_manager', name='lifecycle_manager_filters',
             output='screen', parameters=[use_sim_time, {
                 'autostart': True, 'node_names': ['filter_mask_server', 'costmap_filter_info_server']}]),
    ]
    nav_cmd = [('cmd_vel', 'cmd_vel_nav')]
    navigation = [
        nav_node('nav2_controller', 'controller_server', remaps=nav_cmd),
        nav_node('nav2_planner', 'planner_server'),
        nav_node('nav2_behaviors', 'behavior_server', remaps=nav_cmd),
        nav_node('nav2_bt_navigator', 'bt_navigator'),
        nav_node('nav2_velocity_smoother', 'velocity_smoother',
                 remaps=[('cmd_vel', 'cmd_vel_nav'), ('cmd_vel_smoothed', 'cmd_vel_smoothed')]),
        nav_node('nav2_collision_monitor', 'collision_monitor'),
        Node(package='nav2_lifecycle_manager', executable='lifecycle_manager', name='lifecycle_manager_navigation',
             output='screen', parameters=[use_sim_time, {'autostart': True, 'node_names': [
                 'controller_server', 'planner_server', 'behavior_server', 'bt_navigator',
                 'velocity_smoother', 'collision_monitor']}]),
    ]
    rviz = Node(package='rviz2', executable='rviz2', name='rviz2', output='log',
                arguments=['-d', os.path.join(share, 'rviz', 'warehouse.rviz')],
                parameters=[use_sim_time], condition=IfCondition(LaunchConfiguration('rviz')))

    return LaunchDescription([
        DeclareLaunchArgument('params_file', default_value=os.path.join(share, 'config', 'nav2_params.yaml')),
        DeclareLaunchArgument('map', default_value=os.path.join(share, 'maps', 'warehouse.yaml')),
        DeclareLaunchArgument('keepout_mask', default_value=os.path.join(share, 'maps', 'keepout_mask.yaml')),
        DeclareLaunchArgument('initial_x', default_value=str(round(dock.x, 3))),
        DeclareLaunchArgument('initial_y', default_value=str(round(dock.y, 3))),
        DeclareLaunchArgument('initial_yaw', default_value=str(round(dock.yaw, 4))),
        DeclareLaunchArgument('rviz', default_value='false'),
        DeclareLaunchArgument('log_level', default_value='info'),
        *localization, *filters, *navigation, rviz,
    ])
