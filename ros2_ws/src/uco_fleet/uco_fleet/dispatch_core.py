"""Fleet dispatching policy (pure Python).

Matches PENDING transport tasks to available AGVs:
  * tasks in priority order (higher first), then creation time (FIFO);
  * a task whose container is already on an AGV (retry after pick-up) can only go to that AGV;
  * otherwise the nearest eligible AGV (straight-line distance to the pick-up access pose);
  * an AGV is eligible if it reports available, its heartbeat is fresh, it has no task and its
    battery is above the task threshold.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple


@dataclass
class AgvInfo:
    agv_id: str
    available: bool = False
    battery_pct: float = 100.0
    x: float = 0.0
    y: float = 0.0
    state: str = 'IDLE'
    current_task: str = ''
    carrying: str = ''
    last_seen: float = -1e9


@dataclass
class TaskInfo:
    id: str
    status: str
    source: str
    destination: str
    container_id: str
    priority: int = 0
    created_time: float = 0.0
    started_time: float = 0.0
    assigned_agv: str = ''


@dataclass
class DispatchPolicy:
    min_task_battery_pct: float = 30.0
    heartbeat_timeout_s: float = 3.0
    max_task_duration_s: float = 600.0


def agv_online(agv: AgvInfo, now: float, policy: DispatchPolicy) -> bool:
    return now - agv.last_seen <= policy.heartbeat_timeout_s


def eligible(agv: AgvInfo, now: float, policy: DispatchPolicy) -> bool:
    """Available, idle, online, localised (finite pose) and charged enough (unknown battery = -1)."""
    return (agv.available and not agv.current_task and agv_online(agv, now, policy)
            and math.isfinite(agv.x) and math.isfinite(agv.y)
            and agv.battery_pct >= policy.min_task_battery_pct)


def choose_assignments(tasks: Sequence[TaskInfo], agvs: Dict[str, AgvInfo], locate, now: float,
                       policy: DispatchPolicy) -> List[Tuple[str, str]]:
    """Return [(task_id, agv_id)]. `locate(location_id) -> (x, y) | None` gives pick-up positions."""
    free = {a.agv_id: a for a in agvs.values() if eligible(a, now, policy)}
    out = []
    pending = sorted((t for t in tasks if t.status == 'PENDING'), key=lambda t: (-t.priority, t.created_time, t.id))
    for t in pending:
        if not free:
            break
        if t.source in agvs:                 # container already on that AGV
            if t.source in free:
                out.append((t.id, t.source))
                del free[t.source]
            continue
        src = locate(t.source)
        if src is None:
            continue

        def dist(a: AgvInfo) -> Tuple[float, str]:
            return (math.hypot(a.x - src[0], a.y - src[1]), a.agv_id)
        best = min(free.values(), key=dist)
        out.append((t.id, best.agv_id))
        del free[best.agv_id]
    return out


def timed_out_tasks(tasks: Sequence[TaskInfo], now: float, policy: DispatchPolicy) -> List[str]:
    return [t.id for t in tasks
            if t.status == 'IN_PROGRESS' and t.started_time > 0 and now - t.started_time > policy.max_task_duration_s]
