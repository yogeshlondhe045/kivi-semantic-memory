"""Consistency checks between Nav2 parameters, the AGV model and the layout."""
import os

import yaml

PKG = os.path.join(os.path.dirname(__file__), '..')


def load():
    with open(os.path.join(PKG, 'config', 'nav2_params.yaml')) as f:
        return yaml.safe_load(f)


def test_topics_match_agv_bridge():
    p = load()
    assert p['amcl']['ros__parameters']['scan_topic'] == '/agv/scan'
    assert p['controller_server']['ros__parameters']['odom_topic'] == '/agv/odom'
    assert p['collision_monitor']['ros__parameters']['cmd_vel_out_topic'] == '/cmd_vel_safe'
    assert p['controller_server']['ros__parameters']['speed_limit_topic'] == '/speed_limit'


def test_footprints_identical():
    p = load()
    local = p['local_costmap']['local_costmap']['ros__parameters']['footprint']
    glob = p['global_costmap']['global_costmap']['ros__parameters']['footprint']
    assert local == glob


def test_velocity_limits_consistent():
    p = load()
    smoother = p['velocity_smoother']['ros__parameters']['max_velocity'][0]
    rpp = p['controller_server']['ros__parameters']['FollowPath']['desired_linear_vel']
    assert rpp <= smoother <= 1.0   # 1.0 = diff-drive plugin limit in agv.urdf.xacro


def test_generated_maps_present():
    for name in ('warehouse.yaml', 'warehouse.pgm', 'keepout_mask.yaml', 'keepout_mask.pgm'):
        assert os.path.exists(os.path.join(PKG, 'maps', name)), name
