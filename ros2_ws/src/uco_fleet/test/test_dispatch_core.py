import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from uco_fleet.dispatch_core import AgvInfo, DispatchPolicy, TaskInfo, choose_assignments, timed_out_tasks  # noqa

POS = {'HOLD-01': (2.0, 16.0), 'HOLD-05': (10.0, 16.0), 'A-01-R01': (13.0, 10.4)}
locate = POS.get
P = DispatchPolicy(min_task_battery_pct=30, heartbeat_timeout_s=3, max_task_duration_s=100)


def agv(i, x=0.0, y=0.0, **kw):
    a = AgvInfo(agv_id=i, available=True, battery_pct=80, x=x, y=y, last_seen=100.0)
    for k, v in kw.items():
        setattr(a, k, v)
    return a


def task(i, src='HOLD-01', prio=0, created=0.0, status='PENDING'):
    return TaskInfo(id=i, status=status, source=src, destination='A-01-R01', container_id='C' + i,
                    priority=prio, created_time=created)


def test_nearest_agv_gets_task():
    agvs = {'AGV-01': agv('AGV-01', 2, 1), 'AGV-02': agv('AGV-02', 10, 15)}
    assert choose_assignments([task('T1', 'HOLD-05')], agvs, locate, 101, P) == [('T1', 'AGV-02')]


def test_priority_then_fifo():
    agvs = {'AGV-01': agv('AGV-01')}
    tasks = [task('T1', created=1), task('T2', prio=5, created=2), task('T3', created=0)]
    assert choose_assignments(tasks, agvs, locate, 101, P) == [('T2', 'AGV-01')]
    del tasks[1]
    assert choose_assignments(tasks, agvs, locate, 101, P) == [('T3', 'AGV-01')]


def test_one_task_per_agv_and_busy_agv_skipped():
    agvs = {'AGV-01': agv('AGV-01'), 'AGV-02': agv('AGV-02', current_task='T9')}
    out = choose_assignments([task('T1'), task('T2')], agvs, locate, 101, P)
    assert out == [('T1', 'AGV-01')]


def test_low_battery_offline_unavailable_not_eligible():
    agvs = {'A': agv('A', battery_pct=25), 'B': agv('B', last_seen=90.0), 'C': agv('C', available=False)}
    assert choose_assignments([task('T1')], agvs, locate, 101, P) == []


def test_picked_container_stays_with_its_agv():
    agvs = {'AGV-01': agv('AGV-01', 50, 50), 'AGV-02': agv('AGV-02', 2, 16)}
    t = task('T1', src='AGV-01')
    assert choose_assignments([t], agvs, locate, 101, P) == [('T1', 'AGV-01')]
    agvs['AGV-01'].current_task = 'T7'
    assert choose_assignments([t], agvs, locate, 101, P) == []      # waits for its AGV


def test_non_pending_ignored_and_timeouts():
    t1 = task('T1', status='IN_PROGRESS')
    t1.started_time = 10
    t2 = task('T2', status='IN_PROGRESS')
    t2.started_time = 50
    assert choose_assignments([t1], {'A': agv('A')}, locate, 101, P) == []
    assert timed_out_tasks([t1, t2], 120, P) == ['T1']


def test_unlocalised_or_unknown_battery_not_eligible():
    agvs = {'A': agv('A', x=float('nan'), y=float('nan')), 'B': agv('B', battery_pct=-1.0)}
    assert choose_assignments([task('T1')], agvs, locate, 101, P) == []
