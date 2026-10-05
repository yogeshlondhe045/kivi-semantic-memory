"""Complete UCO warehouse digital twin.

    ros2 launch uco_bringup warehouse.launch.py                 # everything, Gazebo GUI + RViz
    ros2 launch uco_bringup warehouse.launch.py gui:=false rviz:=false     # headless
    ros2 launch uco_bringup warehouse.launch.py scenario:=demo  # also run the demonstration scenario

Switches: sim, nav, wms, stations, fleet, safety, dashboard, metrics, rviz, gui, camera
"""
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (DeclareLaunchArgument, EmitEvent, GroupAction, IncludeLaunchDescription,
                            RegisterEventHandler, TimerAction)
from launch.conditions import IfCondition, LaunchConfigurationNotEquals
from launch.event_handlers import OnProcessExit
from launch.events import Shutdown
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

from uco_common.layout import default_layout_path


def share(pkg, *parts):
    return os.path.join(get_package_share_directory(pkg), *parts)


def generate_launch_description():
    L = LaunchConfiguration
    layout = {'layout_file': L('layout_file'), 'use_sim_time': True}

    def node(pkg, exe, cfg=None, name=None, cond='', extra=None, **kw):
        params = ([cfg] if cfg else []) + [layout] + ([extra] if extra else [])
        return Node(package=pkg, executable=exe, name=name or exe, output='screen', parameters=params,
                    condition=IfCondition(L(cond)) if cond else None, **kw)

    sim = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(share('uco_simulation', 'launch', 'sim.launch.py')),
        launch_arguments={'gui': L('gui'), 'camera': L('camera')}.items(), condition=IfCondition(L('sim')))
    nav = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(share('uco_navigation', 'launch', 'navigation.launch.py')),
        launch_arguments={'rviz': L('rviz')}.items(), condition=IfCondition(L('nav')))

    safety_cfg = share('uco_safety', 'config', 'safety.yaml')
    wms_cfg = share('uco_wms', 'config', 'wms.yaml')
    st_cfg = share('uco_stations', 'config', 'stations.yaml')
    fleet_cfg = share('uco_fleet', 'config', 'fleet.yaml')
    results_dir = {'results_dir': L('results_dir'), 'run_name': L('run_name')}

    core = [
        node('uco_safety', 'safety_manager', safety_cfg, cond='safety'),
        node('uco_safety', 'fault_injector', cond='safety'),
        node('uco_wms', 'wms_node', wms_cfg, cond='wms'),
        node('uco_wms', 'metrics_recorder', extra=results_dir, cond='metrics'),
        node('uco_stations', 'weighing_station', st_cfg, cond='stations'),
        node('uco_stations', 'inspection_station', st_cfg, cond='stations'),
        node('uco_stations', 'receiving_station', st_cfg, cond='stations',
             extra={'quality_profile': L('quality_profile')}),
        node('uco_stations', 'dispatch_manager', st_cfg, cond='stations'),
        node('uco_fleet', 'battery_simulator', fleet_cfg, cond='fleet'),
        node('uco_fleet', 'agv_controller', fleet_cfg, cond='fleet'),
        node('uco_fleet', 'task_manager', fleet_cfg, cond='fleet'),
        node('uco_dashboard', 'dashboard_server', extra={'port': L('dashboard_port')}, cond='dashboard'),
    ]
    scenario = Node(package='uco_bringup', executable='scenario_runner', name='scenario_runner', output='screen',
                    parameters=[layout, {'scenario_file': L('scenario_file'), 'scenario': L('scenario')}],
                    condition=LaunchConfigurationNotEquals('scenario', ''))
    shutdown_after_scenario = RegisterEventHandler(OnProcessExit(
        target_action=scenario, on_exit=[EmitEvent(event=Shutdown(reason='scenario finished'))]),
        condition=IfCondition(L('exit_after_scenario')))

    args = [
        DeclareLaunchArgument('layout_file', default_value=default_layout_path()),
        DeclareLaunchArgument('gui', default_value='true', description='Gazebo GUI'),
        DeclareLaunchArgument('rviz', default_value='true'),
        DeclareLaunchArgument('camera', default_value='false', description='AGV depth camera'),
        DeclareLaunchArgument('quality_profile', default_value='good', description='good | mixed | poor'),
        DeclareLaunchArgument('dashboard_port', default_value='8080'),
        DeclareLaunchArgument('scenario', default_value='', description='scenario name to run (e.g. demo)'),
        DeclareLaunchArgument('scenario_file', default_value=share('uco_bringup', 'config', 'scenarios.yaml')),
        DeclareLaunchArgument('exit_after_scenario', default_value='false'),
        DeclareLaunchArgument('results_dir', default_value=os.path.join(os.getcwd(), 'runtime', 'results')),
        DeclareLaunchArgument('run_name', default_value=''),
    ] + [DeclareLaunchArgument(s, default_value='true') for s in
         ('sim', 'nav', 'wms', 'stations', 'fleet', 'safety', 'dashboard', 'metrics')]
    # application nodes start once Gazebo and Nav2 are up
    return LaunchDescription(args + [sim, TimerAction(period=12.0, actions=[nav]),
                                     TimerAction(period=8.0, actions=core),
                                     TimerAction(period=45.0, actions=[scenario]), shutdown_after_scenario])
