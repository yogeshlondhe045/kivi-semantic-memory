import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from uco_fleet.battery_model import BatteryModel, BatteryParams  # noqa: E402


def test_idle_drain():
    b = BatteryModel(BatteryParams(capacity_wh=1200, idle_power_w=60), 100)
    for _ in range(3600):
        b.step(1.0, 0, 0, False, False)
    assert b.percent == pytest.approx(95.0)          # 60 Wh of 1200 Wh
    assert b.consumed_wh == pytest.approx(60.0)


def test_motion_and_payload_increase_consumption():
    def drain(carrying):
        b = BatteryModel(BatteryParams(), 100)
        for _ in range(600):
            b.step(1.0, 0.7, 0.0, carrying, False)
        return b.consumed_wh
    empty, loaded = drain(False), drain(True)
    assert empty == pytest.approx((60 + 220 * 0.7) * 600 / 3600)
    assert loaded > empty


def test_charging_and_clamp():
    b = BatteryModel(BatteryParams(), 10)
    for _ in range(3600):
        b.step(1.0, 0, 0, False, True)
    assert b.percent == pytest.approx(100.0)
    assert b.charged_wh > 0


def test_time_scale_and_voltage_and_set():
    b = BatteryModel(BatteryParams(time_scale=10), 50)
    b.step(360.0, 0, 0, False, False)              # 3600 s effective at 60 W
    assert b.percent == pytest.approx(45.0)
    b.set_percent(0)
    assert b.voltage == pytest.approx(42.9)
    b.set_percent(150)
    assert b.percent == 100 and b.voltage == pytest.approx(54.6)
