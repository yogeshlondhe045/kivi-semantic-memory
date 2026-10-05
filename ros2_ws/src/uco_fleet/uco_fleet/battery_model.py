"""Battery state-of-charge model for the AGV (pure Python).

Energy balance per step:  dE = (P_charge if docked) - (P_idle + k_motion*|v| + k_turn*|w|) * payload_factor
The `time_scale` multiplier accelerates both charge and discharge so that battery behaviour is
visible within a ~20 minute demonstration; it is reported in every result file.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BatteryParams:
    capacity_wh: float = 1200.0          # 48 V x 25 Ah
    nominal_voltage: float = 48.0
    idle_power_w: float = 60.0           # computers, sensors, lights
    motion_power_w_per_mps: float = 220.0
    turn_power_w_per_radps: float = 80.0
    payload_factor: float = 1.35         # extra drive power when carrying a container
    charge_power_w: float = 2400.0       # 2C fast charging
    time_scale: float = 1.0


class BatteryModel:
    def __init__(self, params: BatteryParams, initial_pct: float = 100.0):
        self.p = params
        self.energy_wh = params.capacity_wh * max(0.0, min(100.0, initial_pct)) / 100.0
        self.consumed_wh = 0.0
        self.charged_wh = 0.0
        self.last_power_w = 0.0

    @property
    def percent(self) -> float:
        return 100.0 * self.energy_wh / self.p.capacity_wh

    @property
    def voltage(self) -> float:
        # simple linear OCV curve of a 13s Li-ion pack: 42.9 V (empty) .. 54.6 V (full)
        return 42.9 + (54.6 - 42.9) * self.percent / 100.0

    def set_percent(self, pct: float) -> None:
        self.energy_wh = self.p.capacity_wh * max(0.0, min(100.0, pct)) / 100.0

    def step(self, dt: float, linear_speed: float, angular_speed: float, carrying: bool, charging: bool) -> float:
        """Advance dt seconds; returns battery power in W (negative = discharging)."""
        dt *= self.p.time_scale
        drive = self.p.motion_power_w_per_mps * abs(linear_speed) + self.p.turn_power_w_per_radps * abs(angular_speed)
        if carrying:
            drive *= self.p.payload_factor
        load = self.p.idle_power_w + drive
        net = (self.p.charge_power_w if charging else 0.0) - load
        if charging and self.energy_wh >= self.p.capacity_wh:
            net = 0.0
        delta = net * dt / 3600.0
        if delta < 0:
            self.consumed_wh += -delta
        else:
            self.charged_wh += delta
        self.energy_wh = max(0.0, min(self.p.capacity_wh, self.energy_wh + delta))
        self.last_power_w = net
        return net
