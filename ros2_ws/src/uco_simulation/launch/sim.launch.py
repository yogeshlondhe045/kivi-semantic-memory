"""Gazebo Harmonic warehouse + AGV-01 + ros_gz bridge + robot_state_publisher.

    ros2 launch uco_simulation sim.launch.py               # with Gazebo GUI
    ros2 launch uco_simulation sim.launch.py gui:=false    # server only (headless / CI)
"""
import math
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, OpaqueFunction
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import Command, LaunchConfiguration, PythonExpression
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue

from uco_common.layout import load_layout


def _spawn_defaults():
    layout = load_layout()
    dock = layout.location('CHG-01').access
    return layout, f'{dock.x:.3f}', f'{dock.y:.3f}', f'{dock.yaw:.4f}'


def generate_launch_description():
    sim_share = get_package_share_directory('uco_simulation')
    desc_share = get_package_share_directory('uco_description')
    layout, sx, sy, syaw = _spawn_defaults()
    world_default = os.path.join(sim_share, 'worlds', f'{layout.world_name}.sdf')

    gui = LaunchConfiguration('gui')
    camera = LaunchConfiguration('camera')
    use_sim_time = {'use_sim_time': True}

    robot_description = ParameterValue(
        Command(['xacro ', os.path.join(desc_share, 'urdf', 'agv.urdf.xacro'), ' camera:=', camera]),
        value_type=str)

    gz_args = PythonExpression([
        "'-r ' + ('' if '", gui, "' == 'true' else '-s ') + '", LaunchConfiguration('world'), "'"])

    gz = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(os.path.join(
            get_package_share_directory('ros_gz_sim'), 'launch', 'gz_sim.launch.py')),
        launch_arguments={'gz_args': gz_args, 'on_exit_shutdown': 'true'}.items())

    rsp = Node(package='robot_state_publisher', executable='robot_state_publisher', output='screen',
               parameters=[{'robot_description': robot_description}, use_sim_time])

    spawn = Node(package='ros_gz_sim', executable='create', output='screen',
                 arguments=['-world', layout.world_name, '-topic', 'robot_description', '-name', 'agv_01',
                            '-x', LaunchConfiguration('x'), '-y', LaunchConfiguration('y'), '-z', '0.02',
                            '-Y', LaunchConfiguration('yaw')])

    bridge = Node(package='ros_gz_bridge', executable='parameter_bridge', name='gz_bridge', output='screen',
                  parameters=[{'config_file': os.path.join(sim_share, 'config', 'bridge.yaml')}, use_sim_time])
    camera_bridge = Node(package='ros_gz_bridge', executable='parameter_bridge', name='gz_camera_bridge',
                         output='screen', condition=IfCondition(camera),
                         parameters=[{'config_file': os.path.join(sim_share, 'config', 'bridge_camera.yaml')},
                                     use_sim_time])

    return LaunchDescription([
        DeclareLaunchArgument('gui', default_value='true', description='start the Gazebo GUI'),
        DeclareLaunchArgument('camera', default_value='false', description='add the optional depth camera'),
        DeclareLaunchArgument('world', default_value=world_default),
        DeclareLaunchArgument('x', default_value=sx, description='spawn x (default: charger dock)'),
        DeclareLaunchArgument('y', default_value=sy),
        DeclareLaunchArgument('yaw', default_value=syaw),
        gz, rsp, spawn, bridge, camera_bridge,
    ])
