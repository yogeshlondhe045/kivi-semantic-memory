"""Safety supervisor state logic (FR-SAF-01..08)."""
import os
import sys

import pytest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'uco_common'))
from uco_common.layout import load_layout  # noqa: E402
from uco_safety.safety_core import (ALARM, EMERGENCY_STOP, NORMAL, WARNING, SafetyConfig,  # noqa: E402
                                    SafetySupervisor)

LAYOUT = load_layout(os.path.join(HERE, '..', '..', 'uco_common', 'config', 'warehouse_layout.yaml'))


def healthy(t=100.0, cfg=None):
    s = SafetySupervisor(LAYOUT, cfg or SafetyConfig())
    s.evaluate(0.0)                         # start-up
    for name in ('scan', 'scan_rear', 'odom'):
        s.sensor_seen(name, t)
    s.set_pose(18.0, 9.8)                   # storage aisle A
    return s


def test_normal_operation():
    d = healthy().evaluate(100.2)
    assert (d.state, d.motion_allowed, d.speed_limit, d.zone) == (NORMAL, True, pytest.approx(0.6), 'STORAGE')


def test_estop_blocks_motion_and_has_top_priority():
    s = healthy()
    s.set_battery(5.0)                      # an ALARM condition at the same time
    s.set_estop(True, 'operator')
    d = s.evaluate(100.2)
    assert d.state == EMERGENCY_STOP and not d.motion_allowed and d.speed_limit == 0.0
    s.set_estop(False)
    s.set_battery(80.0)
    assert s.evaluate(100.3).state == NORMAL


def test_sensor_failure_detected_after_timeout_and_recovers():
    s = healthy()
    assert s.evaluate(100.9).motion_allowed
    d = s.evaluate(101.5)                   # scan, scan_rear, odom silent > 1 s
    assert d.state == ALARM and not d.motion_allowed and 'SENSOR_FAULT:scan' in d.codes
    for name in ('scan', 'scan_rear', 'odom'):
        s.sensor_seen(name, 101.6)
    assert s.evaluate(101.7).motion_allowed


def test_startup_grace_period():
    s = SafetySupervisor(LAYOUT)
    assert s.evaluate(0.0).motion_allowed        # no sensors yet, but still starting up
    assert not s.evaluate(11.0).motion_allowed   # never received -> failure


def test_heartbeat_only_after_first_message():
    s = healthy()
    assert 'HEARTBEAT_LOST' not in s.evaluate(150.0).codes     # never armed: no heartbeat expected yet
    s2 = healthy()
    s2.heartbeat(100.0)
    for name in ('scan', 'scan_rear', 'odom'):
        s2.sensor_seen(name, 104.0)
    d = s2.evaluate(104.1)
    assert 'HEARTBEAT_LOST' in d.codes and not d.motion_allowed and d.state == ALARM


def test_speed_zones():
    s = healthy()
    s.set_pose(5.0, 15.0)
    assert s.evaluate(100.1).speed_limit == pytest.approx(0.4)          # receiving
    s.set_pose(27.0, 14.5)
    assert s.evaluate(100.1).speed_limit == pytest.approx(LAYOUT.default_speed_limit)


def test_restricted_zone_stop_then_crawl():
    s = healthy()
    s.set_pose(32.0, 4.0)                   # tank farm
    d = s.evaluate(100.1)
    assert d.state == ALARM and 'RESTRICTED_ZONE' in d.codes and not d.motion_allowed
    d = s.evaluate(100.1 + 3.5)             # after restricted_stop_s
    for name in ('scan', 'scan_rear', 'odom'):
        s.sensor_seen(name, 103.6)
    d = s.evaluate(103.7)
    assert d.motion_allowed and d.speed_limit == pytest.approx(0.1) and d.state == ALARM
    s.set_pose(27.0, 14.5)
    assert s.evaluate(103.8).state == NORMAL


def test_obstacle_debounce_warning_then_blocked_alarm():
    cfg = SafetyConfig(blocked_timeout_s=20.0)
    s = healthy(cfg=cfg)

    def at(t):
        for name in ('scan', 'scan_rear', 'odom'):
            s.sensor_seen(name, t)
        return s.evaluate(t)
    s.set_obstacle_stop(True, 100.0)
    assert 'OBSTACLE' not in at(100.5).codes            # debounce 1 s
    assert at(101.2).state == WARNING
    s.set_obstacle_stop(False, 101.5)                   # brief gap: still the same episode
    s.set_obstacle_stop(True, 102.0)
    assert 'OBSTACLE' in at(102.1).codes
    d = at(121.0)
    assert 'PATH_BLOCKED' in d.codes and d.state == ALARM and d.motion_allowed
    s.set_obstacle_stop(False, 121.5)
    assert at(122.0).codes == ['PATH_BLOCKED']          # hysteresis: not cleared yet
    assert at(124.0).state == NORMAL                    # clear after 2 s


def test_battery_conditions():
    s = healthy()
    s.set_battery(20.0)
    d = s.evaluate(100.1)
    assert d.state == WARNING and 'BATTERY_LOW' in d.codes and d.motion_allowed
    s.set_battery(8.0)
    d = s.evaluate(100.2)
    assert d.state == ALARM and d.speed_limit == pytest.approx(0.3)


def test_external_conditions():
    s = healthy()
    s.raise_condition('STATION_DOWN:INSPECT', WARNING, 'Inspection unavailable')
    assert s.evaluate(100.1).state == WARNING
    s.clear_condition('STATION_DOWN:INSPECT')
    assert s.evaluate(100.2).state == NORMAL


def test_no_obstacle_report_during_sensor_fault():
    s = healthy()
    s.set_obstacle_stop(True, 100.0)
    for name in ('scan_rear', 'odom'):
        s.sensor_seen(name, 102.0)
    d = s.evaluate(102.0)                       # front scan silent for 2 s -> sensor fault
    assert 'SENSOR_FAULT:scan' in d.codes and 'OBSTACLE' not in d.codes
