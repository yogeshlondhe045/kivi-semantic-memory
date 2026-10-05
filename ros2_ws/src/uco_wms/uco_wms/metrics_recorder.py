"""metrics_recorder: collects simulation KPIs and writes them as CSV + JSON.

Output directory: <results_dir>/<run_name>/
  tasks.csv        one row per finished task (timestamps, waiting / execution / total time, attempts)
  agv_trace.csv    1 Hz AGV state: pose, speed, state, battery, distance, busy time, carried container
  occupancy.csv    storage occupancy and container counters over time
  alerts.csv       every alert (severity, source, code, message)
  metrics.json     KPI summary (updated every `summary_period_s` and at shutdown)

All times are simulation seconds.
"""
from __future__ import annotations

import bisect
import collections
import csv
import json
import os
import time
from typing import Dict

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from sensor_msgs.msg import BatteryState
from nav_msgs.msg import Odometry
from std_msgs.msg import Float32, Float64

from uco_common.qos import EVENTS, LATCHED
from uco_interfaces.msg import AgvState, Alert, Inventory, SafetyStatus, TaskList

from .metrics_core import compute_kpis


class MetricsRecorder(Node):
    def __init__(self):
        super().__init__('metrics_recorder')
        self.declare_parameter('results_dir', os.path.join(os.getcwd(), 'runtime', 'results'))
        self.declare_parameter('run_name', '')
        self.declare_parameter('summary_period_s', 10.0)
        self.declare_parameter('battery_time_scale', 10.0)
        self.declare_parameter('layout_file', '')
        run = self.get_parameter('run_name').value or time.strftime('run_%Y%m%d_%H%M%S')
        self.out = os.path.join(self.get_parameter('results_dir').value, run)
        os.makedirs(self.out, exist_ok=True)
        self.files = {}
        self.writers = {}
        self._open('tasks', ['task_id', 'type', 'container_id', 'source', 'destination', 'status', 'agv', 'attempts',
                             'created_s', 'assigned_s', 'started_s', 'completed_s', 'waiting_s', 'execution_s',
                             'total_s', 'failure_reason'])
        self._open('agv_trace', ['t_s', 'x', 'y', 'theta', 'state', 'speed', 'battery_pct', 'charging', 'distance_m',
                                 'busy_s', 'uptime_s', 'task', 'carrying', 'safety_state', 'gt_x', 'gt_y',
                                 'loc_error_m', 'loc_score'])
        self._open('occupancy', ['t_s', 'storage_occupied', 'storage_capacity', 'occupancy', 'holding', 'quarantine',
                                 'dispatch_buffer', 'received_total', 'dispatched_total', 'stored_volume_l'])
        self._open('alerts', ['t_s', 'severity', 'source', 'code', 'message'])
        self.finished: Dict[str, dict] = {}
        self.agv = None
        self.inv = None
        self.tasklist = None
        self.safety = None
        self.consumed_wh = 0.0
        self.battery_first = None
        self.battery_last = None
        self.start = None
        self.max_occupancy = 0.0
        self.alert_counts: Dict[str, int] = {}
        self.create_subscription(TaskList, '/warehouse/tasks', self._on_tasks, LATCHED)
        self.create_subscription(AgvState, '/agv/state', lambda m: setattr(self, 'agv', m), 10)
        self.create_subscription(Inventory, '/warehouse/inventory', self._on_inventory, LATCHED)
        self.create_subscription(SafetyStatus, '/safety/status', lambda m: setattr(self, 'safety', m), LATCHED)
        self.create_subscription(Alert, '/warehouse/alerts', self._on_alert, EVENTS)
        self.create_subscription(BatteryState, '/agv/battery', self._on_battery, 10)
        self.create_subscription(Float64, '/agv/battery/consumed_wh', lambda m: setattr(self, 'consumed_wh', m.data), 10)
        self.gt = None
        self.gt_hist = collections.deque(maxlen=100)     # (stamp, x, y): 10 s of ground truth at 10 Hz
        self.loc_score = None
        self.loc_errors = []
        self.create_subscription(Odometry, '/agv/ground_truth', self._on_gt, 10)
        self.create_subscription(Float32, '/safety/localization_score',
                                 lambda m: setattr(self, 'loc_score', m.data), 10)
        self.create_timer(1.0, self._sample)
        self.create_timer(self.get_parameter('summary_period_s').value, self.write_summary)
        self.get_logger().info(f'[INFO] Recording metrics to {self.out}')

    def _open(self, name, header):
        f = open(os.path.join(self.out, f'{name}.csv'), 'w', newline='', encoding='utf-8')
        w = csv.writer(f)
        w.writerow(header)
        self.files[name], self.writers[name] = f, w

    def _now(self) -> float:
        return self.get_clock().now().nanoseconds * 1e-9

    def _on_battery(self, m: BatteryState) -> None:
        pct = m.percentage * 100.0
        if self.battery_first is None:
            self.battery_first = pct
        self.battery_last = pct

    def _on_alert(self, a: Alert) -> None:
        t = a.stamp.sec + a.stamp.nanosec * 1e-9
        self.writers['alerts'].writerow([f'{t:.1f}', ['INFO', 'WARN', 'ERROR', 'ALARM'][min(a.severity, 3)], a.source,
                                         a.code, a.message])
        self.alert_counts[a.code] = self.alert_counts.get(a.code, 0) + 1
        self.files['alerts'].flush()

    def _on_tasks(self, tl: TaskList) -> None:
        self.tasklist = tl
        for t in tl.tasks:
            if t.status in ('COMPLETED', 'FAILED', 'CANCELLED') and t.id not in self.finished:
                rec = {'task_id': t.id, 'type': t.task_type, 'container_id': t.container_id, 'source': t.source,
                       'destination': t.destination, 'status': t.status, 'agv': t.assigned_agv,
                       'attempts': t.attempts, 'created_s': t.created_time, 'assigned_s': t.assigned_time,
                       'started_s': t.started_time, 'completed_s': t.completed_time,
                       'waiting_s': (t.started_time - t.created_time) if t.started_time else None,
                       'execution_s': (t.completed_time - t.started_time) if t.started_time else None,
                       'total_s': t.completed_time - t.created_time, 'failure_reason': t.failure_reason}
                self.finished[t.id] = rec
                self.writers['tasks'].writerow([('' if rec[k] is None else (f'{rec[k]:.2f}' if isinstance(rec[k], float)
                                                                            else rec[k])) for k in rec])
                self.files['tasks'].flush()

    def _on_inventory(self, inv: Inventory) -> None:
        self.inv = inv
        self.max_occupancy = max(self.max_occupancy, inv.storage_occupancy)

    def _sample(self) -> None:
        now = self._now()
        if self.start is None:
            self.start = now
        a, inv = self.agv, self.inv
        if a is not None and a.battery_percent >= 0 and a.pose.x == a.pose.x:   # skip until localised (NaN)
            self.writers['agv_trace'].writerow([
                f'{now:.1f}', f'{a.pose.x:.3f}', f'{a.pose.y:.3f}', f'{a.pose.theta:.3f}', a.state,
                f'{a.linear_speed:.3f}', f'{a.battery_percent:.2f}', int(a.charging), f'{a.distance_travelled_m:.2f}',
                f'{a.busy_time_s:.1f}', f'{a.uptime_s:.1f}', a.current_task, a.carrying_container,
                self.safety.state if self.safety else ''] + self._loc_columns(a))
        if inv is not None:
            kinds: Dict[str, int] = {}
            for s in inv.slots:
                if s.container_id:
                    kinds[s.kind] = kinds.get(s.kind, 0) + 1
            self.writers['occupancy'].writerow([
                f'{now:.1f}', inv.storage_occupied, inv.storage_capacity, f'{inv.storage_occupancy:.4f}',
                kinds.get('HOLDING', 0), kinds.get('QUARANTINE', 0), kinds.get('DISPATCH', 0),
                inv.containers_received_total, inv.containers_dispatched_total, f'{inv.stored_volume_l:.1f}'])
        for f in self.files.values():
            f.flush()

    def _on_gt(self, m: Odometry) -> None:
        self.gt = m.pose.pose
        t = m.header.stamp.sec + m.header.stamp.nanosec * 1e-9
        self.gt_hist.append((t, m.pose.pose.position.x, m.pose.pose.position.y))

    def _gt_at(self, t: float):
        """Ground-truth position interpolated at time t (the AGV state's own timestamp)."""
        h = self.gt_hist
        if len(h) < 2 or t < h[0][0] or t > h[-1][0] + 0.2:
            return None
        i = bisect.bisect_left([s[0] for s in h], t)
        if i == 0:
            return h[0][1:]
        if i >= len(h):
            return h[-1][1:]
        (t0, x0, y0), (t1, x1, y1) = h[i - 1], h[i]
        w = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
        return x0 + w * (x1 - x0), y0 + w * (y1 - y0)

    def _loc_columns(self, a):
        score = '' if self.loc_score is None else f'{self.loc_score:.3f}'
        # compare the AGV's AMCL pose with ground truth *at the same time*: the AGV state is published
        # at 2 Hz, so comparing with the latest ground truth would add up to 0.4 m at 0.8 m/s
        gt = self._gt_at(a.header.stamp.sec + a.header.stamp.nanosec * 1e-9)
        if gt is None:
            return ['', '', '', score]
        err = ((a.pose.x - gt[0]) ** 2 + (a.pose.y - gt[1]) ** 2) ** 0.5
        self.loc_errors.append(err)
        return [f'{gt[0]:.3f}', f'{gt[1]:.3f}', f'{err:.3f}', score]

    def write_summary(self) -> None:
        now = self._now()
        kpis = compute_kpis(
            tasks=list(self.finished.values()),
            sim_duration_s=(now - self.start) if self.start else 0.0,
            agv=self.agv, inventory=self.inv, max_occupancy=self.max_occupancy,
            consumed_wh=self.consumed_wh, battery_first=self.battery_first, battery_last=self.battery_last,
            battery_time_scale=self.get_parameter('battery_time_scale').value, alert_counts=self.alert_counts,
            localization_errors=self.loc_errors)
        with open(os.path.join(self.out, 'metrics.json'), 'w', encoding='utf-8') as f:
            json.dump(kpis, f, indent=2)

    def close(self) -> None:
        self.write_summary()
        for f in self.files.values():
            f.close()


def main(args=None):
    rclpy.init(args=args)
    node = MetricsRecorder()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.close()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
