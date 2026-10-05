"""Safety supervisor logic (pure Python, time is passed in explicitly so it is unit-testable).

The supervisor combines independent *conditions* into one warehouse safety state:

    EMERGENCY_STOP  > ALARM > WARNING > NORMAL

and decides whether AGV motion is allowed and which speed limit applies.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

NORMAL = 'NORMAL'
WARNING = 'WARNING'
ALARM = 'ALARM'
EMERGENCY_STOP = 'EMERGENCY_STOP'
_RANK = {NORMAL: 0, WARNING: 1, ALARM: 2, EMERGENCY_STOP: 3}


@dataclass
class SafetyConfig:
    scan_timeout_s: float = 1.0
    odom_timeout_s: float = 1.0
    heartbeat_timeout_s: float = 3.0
    blocked_timeout_s: float = 20.0
    restricted_stop_s: float = 3.0       # full stop after entering a restricted zone ...
    restricted_crawl_speed: float = 0.1  # ... then crawl so Nav2 can leave it
    battery_low_pct: float = 25.0
    battery_critical_pct: float = 10.0
    critical_battery_speed: float = 0.3
    obstacle_debounce_s: float = 1.0     # stop zone must be occupied this long to count
    obstacle_clear_s: float = 2.0        # ... and free this long to clear (hysteresis)


@dataclass
class Condition:
    code: str
    level: str
    message: str
    blocks_motion: bool = False


@dataclass
class SafetyDecision:
    state: str
    motion_allowed: bool
    speed_limit: float
    zone: str
    conditions: List[Condition] = field(default_factory=list)

    @property
    def codes(self) -> List[str]:
        return [c.code for c in self.conditions]


class SafetySupervisor:
    def __init__(self, layout, config: Optional[SafetyConfig] = None):
        self.layout = layout
        self.cfg = config or SafetyConfig()
        self.estop = False
        self.estop_reason = ''
        self.pose: Optional[Tuple[float, float]] = None
        self.last_seen: Dict[str, float] = {}     # sensor name -> last message time
        self.monitored_sensors = {'scan': self.cfg.scan_timeout_s, 'scan_rear': self.cfg.scan_timeout_s,
                                  'odom': self.cfg.odom_timeout_s}
        self.heartbeat_armed = False
        self.last_heartbeat = 0.0
        self.navigating = False
        self.obstacle_stop = False
        self.obstacle_since: Optional[float] = None   # start of the current stop episode
        self.obstacle_last: float = -1e9              # last time the stop zone became free
        self.battery_pct: Optional[float] = None
        self.restricted_since: Optional[float] = None
        self.restricted_zone: str = ''
        self.extra: Dict[str, Condition] = {}     # externally raised conditions (e.g. station down)
        self.started = None

    # ------------------------------------------------------------- inputs
    def set_estop(self, active: bool, reason: str = '') -> None:
        self.estop = active
        self.estop_reason = reason if active else ''

    def sensor_seen(self, name: str, now: float) -> None:
        self.last_seen[name] = now

    def heartbeat(self, now: float, navigating: bool = False) -> None:
        self.heartbeat_armed = True
        self.last_heartbeat = now
        self.navigating = navigating

    def set_pose(self, x: float, y: float) -> None:
        self.pose = (x, y)

    def set_obstacle_stop(self, active: bool, now: float) -> None:
        """Collision-monitor stop zone state (published on change only).

        An episode starts when the stop zone becomes occupied and ends only after it has been
        free for obstacle_clear_s, so short gaps do not split one blockage into several.
        """
        if active and self.obstacle_since is None:
            self.obstacle_since = now
        if not active and self.obstacle_stop:
            self.obstacle_last = now          # moment the zone became free
        self.obstacle_stop = active

    def _obstacle_episode(self, now: float) -> Optional[float]:
        """Duration of the current obstacle episode, or None if there is none (after hysteresis)."""
        if self.obstacle_since is None:
            return None
        if not self.obstacle_stop and now - self.obstacle_last > self.cfg.obstacle_clear_s:
            self.obstacle_since = None
            return None
        return now - self.obstacle_since

    def set_battery(self, pct: float) -> None:
        self.battery_pct = pct

    def raise_condition(self, code: str, level: str, message: str, blocks_motion: bool = False) -> None:
        self.extra[code] = Condition(code, level, message, blocks_motion)

    def clear_condition(self, code: str) -> None:
        self.extra.pop(code, None)

    # ------------------------------------------------------------- evaluation
    def evaluate(self, now: float) -> SafetyDecision:
        if self.started is None:
            self.started = now
        conds: List[Condition] = []
        zone_id = ''
        speed = self.layout.default_speed_limit

        if self.estop:
            conds.append(Condition('ESTOP', EMERGENCY_STOP,
                                   f'Emergency stop activated{": " + self.estop_reason if self.estop_reason else ""}',
                                   True))

        for name, timeout in self.monitored_sensors.items():
            last = self.last_seen.get(name)
            # grace period after start-up before a never-seen sensor counts as failed
            if last is None and now - self.started < 10.0:
                continue
            if last is None or now - last > timeout:
                conds.append(Condition(f'SENSOR_FAULT:{name}', ALARM,
                                       f'Sensor failure: no {name} data for '
                                       f'{(now - last) if last is not None else now - self.started:.1f} s', True))

        if self.heartbeat_armed and now - self.last_heartbeat > self.cfg.heartbeat_timeout_s:
            conds.append(Condition('HEARTBEAT_LOST', ALARM,
                                   f'AGV heartbeat lost for {now - self.last_heartbeat:.1f} s', True))

        if self.pose is not None:
            x, y = self.pose
            zones = self.layout.zones_at(x, y)
            zone_id = ','.join(z.id for z in zones)
            speed = self.layout.speed_limit_at(x, y)
            rz = self.layout.restricted_zone_at(x, y)
            if rz is not None:
                if self.restricted_since is None or self.restricted_zone != rz.id:
                    self.restricted_since, self.restricted_zone = now, rz.id
                stopped = now - self.restricted_since < self.cfg.restricted_stop_s
                conds.append(Condition('RESTRICTED_ZONE', ALARM, f'AGV inside restricted zone {rz.name}', stopped))
                speed = min(speed, self.cfg.restricted_crawl_speed)
            else:
                self.restricted_since, self.restricted_zone = None, ''

        waited = self._obstacle_episode(now)
        sensor_fault = any(c.code.startswith('SENSOR_FAULT') for c in conds)
        if waited is not None and waited >= self.cfg.obstacle_debounce_s and not sensor_fault:
            if waited > self.cfg.blocked_timeout_s:
                conds.append(Condition('PATH_BLOCKED', ALARM, f'Navigation path blocked for {waited:.0f} s'))
            else:
                conds.append(Condition('OBSTACLE', WARNING, 'Obstacle detected in navigation corridor'))

        if self.battery_pct is not None:
            if self.battery_pct < self.cfg.battery_critical_pct:
                conds.append(Condition('BATTERY_CRITICAL', ALARM, f'Battery critical ({self.battery_pct:.0f} %)'))
                speed = min(speed, self.cfg.critical_battery_speed)
            elif self.battery_pct < self.cfg.battery_low_pct:
                conds.append(Condition('BATTERY_LOW', WARNING, f'Battery low ({self.battery_pct:.0f} %)'))

        conds.extend(self.extra.values())
        state = NORMAL
        for c in conds:
            if _RANK[c.level] > _RANK[state]:
                state = c.level
        motion = not any(c.blocks_motion for c in conds)
        return SafetyDecision(state=state, motion_allowed=motion, speed_limit=speed if motion else 0.0,
                              zone=zone_id, conditions=conds)
