"""KPI computation (pure Python, unit-tested)."""
from __future__ import annotations

from statistics import mean
from typing import Dict, List, Optional


def _avg(values: List[Optional[float]]) -> Optional[float]:
    vals = [v for v in values if v is not None]
    return round(mean(vals), 2) if vals else None


def compute_kpis(tasks: List[dict], sim_duration_s: float, agv=None, inventory=None, max_occupancy: float = 0.0,
                 consumed_wh: float = 0.0, battery_first: Optional[float] = None,
                 battery_last: Optional[float] = None, battery_time_scale: float = 1.0,
                 alert_counts: Optional[Dict[str, int]] = None) -> dict:
    """Return the KPI dictionary written to metrics.json.

    tasks: finished task records with keys type, status, attempts, waiting_s, execution_s, total_s.
    """
    done = [t for t in tasks if t['status'] == 'COMPLETED']
    failed = [t for t in tasks if t['status'] == 'FAILED']
    cancelled = [t for t in tasks if t['status'] == 'CANCELLED']
    finished = len(done) + len(failed)
    hours = sim_duration_s / 3600.0 if sim_duration_s > 0 else 0.0
    stored_tasks = [t for t in done if t['type'] in ('STORE', 'QUARANTINE')]
    retrieved_tasks = [t for t in done if t['type'] == 'RETRIEVE']
    by_type = {}
    for t in done:
        by_type.setdefault(t['type'], []).append(t['total_s'])
    k = {
        'sim_duration_s': round(sim_duration_s, 1),
        'containers': {
            'received': inventory.containers_received_total if inventory else 0,
            'stored_now': inventory.storage_occupied if inventory else 0,
            'dispatched': inventory.containers_dispatched_total if inventory else 0,
            'stored_volume_l': round(inventory.stored_volume_l, 1) if inventory else 0.0,
        },
        'tasks': {
            'completed': len(done),
            'failed': len(failed),
            'cancelled': len(cancelled),
            'completion_rate': round(len(done) / finished, 3) if finished else None,
            'retried_attempts': sum(t['attempts'] for t in done + failed),
            'avg_total_time_s': _avg([t['total_s'] for t in done]),
            'avg_waiting_time_s': _avg([t['waiting_s'] for t in done]),
            'avg_execution_time_s': _avg([t['execution_s'] for t in done]),
            'avg_total_time_by_type_s': {ty: _avg(v) for ty, v in by_type.items()},
        },
        'throughput': {
            'tasks_per_hour': round(len(done) / hours, 2) if hours else None,
            'containers_stored_per_hour': round(len(stored_tasks) / hours, 2) if hours else None,
            'containers_retrieved_per_hour': round(len(retrieved_tasks) / hours, 2) if hours else None,
        },
        'warehouse': {
            'occupancy_now': round(inventory.storage_occupancy, 3) if inventory else 0.0,
            'occupancy_max': round(max_occupancy, 3),
            'storage_capacity': inventory.storage_capacity if inventory else 0,
        },
        'agv': {
            'distance_m': round(agv.distance_travelled_m, 1) if agv else 0.0,
            'busy_time_s': round(agv.busy_time_s, 1) if agv else 0.0,
            'uptime_s': round(agv.uptime_s, 1) if agv else 0.0,
            'utilization': round(agv.busy_time_s / agv.uptime_s, 3) if agv and agv.uptime_s > 0 else None,
            'battery_start_pct': round(battery_first, 1) if battery_first is not None else None,
            'battery_end_pct': round(battery_last, 1) if battery_last is not None else None,
            'energy_consumed_wh': round(consumed_wh, 1),
            'battery_time_scale': battery_time_scale,
            'energy_consumed_wh_real_time_equivalent': round(consumed_wh / battery_time_scale, 1)
            if battery_time_scale else None,
        },
        'alerts': dict(sorted((alert_counts or {}).items())),
    }
    return k
