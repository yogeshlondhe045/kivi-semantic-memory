from types import SimpleNamespace

import pytest

from uco_wms.metrics_core import compute_kpis


def task(ty, status, total, wait=5.0, attempts=0):
    return {'type': ty, 'status': status, 'attempts': attempts, 'waiting_s': wait,
            'execution_s': total - wait, 'total_s': total}


def test_kpis():
    tasks = [task('STORE', 'COMPLETED', 100), task('STORE', 'COMPLETED', 140, attempts=1),
             task('RETRIEVE', 'COMPLETED', 60), task('STORE', 'FAILED', 300, attempts=3)]
    agv = SimpleNamespace(distance_travelled_m=250.0, busy_time_s=900.0, uptime_s=1800.0)
    inv = SimpleNamespace(containers_received_total=4, storage_occupied=2, containers_dispatched_total=1,
                          stored_volume_l=1000.0, storage_occupancy=2 / 32, storage_capacity=32)
    k = compute_kpis(tasks, 3600.0, agv, inv, max_occupancy=3 / 32, consumed_wh=150.0, battery_first=85,
                     battery_last=70, battery_time_scale=10.0, alert_counts={'TASK_RETRY': 1})
    assert k['tasks']['completed'] == 3 and k['tasks']['failed'] == 1
    assert k['tasks']['completion_rate'] == pytest.approx(0.75)
    assert k['tasks']['avg_total_time_s'] == pytest.approx(100.0)
    assert k['tasks']['avg_total_time_by_type_s'] == {'STORE': 120.0, 'RETRIEVE': 60.0}
    assert k['throughput']['tasks_per_hour'] == pytest.approx(3.0)
    assert k['throughput']['containers_stored_per_hour'] == pytest.approx(2.0)
    assert k['agv']['utilization'] == pytest.approx(0.5)
    assert k['agv']['energy_consumed_wh_real_time_equivalent'] == pytest.approx(15.0)
    assert k['warehouse']['occupancy_max'] == pytest.approx(0.094, abs=1e-3)


def test_empty_run():
    k = compute_kpis([], 0.0)
    assert k['tasks']['completion_rate'] is None and k['throughput']['tasks_per_hour'] is None
