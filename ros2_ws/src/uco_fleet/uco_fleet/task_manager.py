"""task_manager: fleet dispatcher.

Every period: read PENDING tasks (/warehouse/tasks) and AGV states (/agv/state), choose assignments
(dispatch_core), call /warehouse/assign_agv, send a TransportContainer goal to the AGV, mirror
progress into the WMS (/warehouse/update_task) and turn action results into COMPLETED / FAILED
(retry) / PENDING (requeue without penalty). Also enforces task time-outs and AGV heartbeat.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.action import ActionClient
from rclpy.node import Node

from uco_common.alerts import AlertPublisher
from uco_common.layout import load_layout
from uco_common.qos import LATCHED
from uco_interfaces.action import TransportContainer
from uco_interfaces.msg import AgvState, TaskList
from uco_interfaces.srv import AssignAgv, UpdateTask

from .dispatch_core import AgvInfo, DispatchPolicy, TaskInfo, agv_online, choose_assignments, timed_out_tasks

# failure codes that are not the task's fault: requeue without counting an attempt
_REQUEUE_CODES = ('LOW_BATTERY',)


@dataclass
class ActiveGoal:
    task_id: str
    agv_id: str
    handle: Optional[object] = None
    phase: str = ''
    timed_out: bool = False


class TaskManager(Node):
    def __init__(self):
        super().__init__('task_manager')
        self.declare_parameter('layout_file', '')
        self.declare_parameter('agv_ids', ['AGV-01'])
        self.declare_parameter('agv_action_namespaces', ['/agv_01'])
        self.declare_parameter('min_task_battery_pct', 30.0)
        self.declare_parameter('heartbeat_timeout_s', 3.0)
        self.declare_parameter('max_task_duration_s', 600.0)
        self.declare_parameter('period_s', 1.0)
        p = lambda n: self.get_parameter(n).value  # noqa: E731
        self.layout = load_layout(p('layout_file') or None)
        self.policy = DispatchPolicy(p('min_task_battery_pct'), p('heartbeat_timeout_s'), p('max_task_duration_s'))
        self.alerts = AlertPublisher(self, 'task_manager')
        self.agvs: Dict[str, AgvInfo] = {}
        self.action_clients: Dict[str, ActionClient] = {}
        for agv_id, ns in zip(p('agv_ids'), p('agv_action_namespaces')):
            self.agvs[agv_id] = AgvInfo(agv_id=agv_id)
            self.action_clients[agv_id] = ActionClient(self, TransportContainer, f'{ns.rstrip("/")}/transport_container')
        self.online: Dict[str, bool] = {a: False for a in self.agvs}
        self.tasks: Dict[str, TaskInfo] = {}
        self.active: Dict[str, ActiveGoal] = {}         # task id -> goal
        self.in_flight = set()                           # task ids with an assignment call outstanding
        self.assign_cli = self.create_client(AssignAgv, '/warehouse/assign_agv')
        self.update_cli = self.create_client(UpdateTask, '/warehouse/update_task')
        self.create_subscription(TaskList, '/warehouse/tasks', self._on_tasks, LATCHED)
        self.create_subscription(AgvState, '/agv/state', self._on_agv, 10)
        self.create_timer(p('period_s'), self._tick)
        self.started = self._now()
        self.get_logger().info(f'[INFO] Task manager running for {list(self.agvs)}')

    def _now(self) -> float:
        return self.get_clock().now().nanoseconds * 1e-9

    # ------------------------------------------------------------------ inputs
    def _on_tasks(self, msg: TaskList) -> None:
        self.tasks = {t.id: TaskInfo(id=t.id, status=t.status, source=t.source, destination=t.destination,
                                     container_id=t.container_id, priority=t.priority, created_time=t.created_time,
                                     started_time=t.started_time, assigned_agv=t.assigned_agv) for t in msg.tasks}

    def _on_agv(self, m: AgvState) -> None:
        a = self.agvs.get(m.agv_id)
        if a is None:
            return
        a.available, a.battery_pct, a.state = m.available, m.battery_percent, m.state
        a.x, a.y, a.carrying = m.pose.x, m.pose.y, m.carrying_container
        # keep our own view of the current task while a goal is outstanding
        if not any(g.agv_id == m.agv_id for g in self.active.values()):
            a.current_task = m.current_task
        a.last_seen = self._now()

    # ------------------------------------------------------------------ main loop
    def _tick(self) -> None:
        now = self._now()
        for agv_id, a in self.agvs.items():
            online = agv_online(a, now, self.policy)
            if online != self.online[agv_id]:
                self.online[agv_id] = online
                if online:
                    self.alerts.info('AGV_ONLINE', f'{agv_id} online')
                elif now - self.started > self.policy.heartbeat_timeout_s:
                    self.alerts.alarm('AGV_TIMEOUT', f'{agv_id} heartbeat lost: no state for '
                                      f'{now - a.last_seen:.1f} s; no new tasks will be assigned')
        for tid in timed_out_tasks(list(self.tasks.values()), now, self.policy):
            g = self.active.get(tid)
            if g and g.handle and not g.timed_out:
                g.timed_out = True
                self.alerts.error('TASK_TIMEOUT', f'Task {tid} exceeded {self.policy.max_task_duration_s:.0f} s; '
                                  'cancelling')
                g.handle.cancel_goal_async()
        candidates = [t for t in self.tasks.values() if t.id not in self.active and t.id not in self.in_flight]
        busy = {g.agv_id for g in self.active.values()}
        agvs = {k: v for k, v in self.agvs.items() if k not in busy}
        for tid, agv_id in choose_assignments(candidates, agvs, self._locate, now, self.policy):
            self._assign(tid, agv_id)

    def _locate(self, location_id: str):
        loc = self.layout.locations.get(location_id)
        if loc is None:
            return None
        p = loc.access or loc.slot
        return (p.x, p.y)

    # ------------------------------------------------------------------ assignment chain
    def _assign(self, tid: str, agv_id: str) -> None:
        if not self.assign_cli.service_is_ready():
            return
        self.in_flight.add(tid)
        req = AssignAgv.Request()
        req.task_id, req.agv_id = tid, agv_id
        self.assign_cli.call_async(req).add_done_callback(lambda f: self._on_assigned(f, tid, agv_id))

    def _on_assigned(self, fut, tid: str, agv_id: str) -> None:
        r = fut.result()
        if r is None or not r.success:
            self.in_flight.discard(tid)
            self.get_logger().warning(f'[WARN] assign {tid} -> {agv_id} rejected: {r.message if r else "no reply"}')
            return
        t = self.tasks[tid]
        client = self.action_clients[agv_id]
        if not client.server_is_ready():
            self.in_flight.discard(tid)
            self._update(tid, 'PENDING', message=f'{agv_id} action server not ready')
            return
        goal = TransportContainer.Goal()
        goal.task_id, goal.container_id = tid, t.container_id
        goal.source_location, goal.destination_location = t.source, t.destination
        self.active[tid] = ActiveGoal(task_id=tid, agv_id=agv_id)
        self.agvs[agv_id].current_task = tid
        self.alerts.info('AGV_ASSIGNED', f'Task {tid} assigned to {agv_id} ({t.container_id}: {t.source} -> '
                         f'{t.destination})')
        client.send_goal_async(goal, feedback_callback=lambda fb: self._on_feedback(tid, fb)) \
            .add_done_callback(lambda f: self._on_goal_response(f, tid, agv_id))

    def _on_goal_response(self, fut, tid: str, agv_id: str) -> None:
        self.in_flight.discard(tid)
        handle = fut.result()
        if handle is None or not handle.accepted:
            self.active.pop(tid, None)
            self.agvs[agv_id].current_task = ''
            self._update(tid, 'PENDING', message=f'{agv_id} rejected the task')
            return
        self.active[tid].handle = handle
        self._update(tid, 'IN_PROGRESS', phase='TO_PICKUP')
        handle.get_result_async().add_done_callback(lambda f: self._on_result(f, tid, agv_id))

    def _on_feedback(self, tid: str, fb) -> None:
        g = self.active.get(tid)
        phase = fb.feedback.phase
        if g and phase and phase != g.phase:
            g.phase = phase
            self._update(tid, 'IN_PROGRESS', phase=phase)

    def _on_result(self, fut, tid: str, agv_id: str) -> None:
        g = self.active.pop(tid, None)
        self.agvs[agv_id].current_task = ''
        wrapped = fut.result()
        res = wrapped.result if wrapped else None
        if res is not None and res.success:
            self._update(tid, 'COMPLETED')
            self.alerts.info('TASK_DONE', f'{tid} done by {agv_id} in {res.duration_s:.0f} s, {res.distance_m:.1f} m')
            return
        code = res.failure_code if res else 'NO_RESULT'
        msg = res.message if res else 'no result'
        if g and g.timed_out:
            code, msg = 'TASK_TIMEOUT', 'task exceeded maximum duration'
        carrying = self.agvs[agv_id].carrying == (self.tasks.get(tid).container_id if tid in self.tasks else '')
        if code in _REQUEUE_CODES and not carrying:
            self._update(tid, 'PENDING', message=code)
            self.alerts.warn('TASK_REQUEUED', f'{tid} returned to queue ({code})')
        else:
            self._update(tid, 'FAILED', message=f'{code}: {msg}')

    def _update(self, tid: str, status: str, phase: str = '', message: str = '') -> None:
        if not self.update_cli.service_is_ready():
            self.get_logger().error(f'[ERROR] update_task unavailable; {tid} -> {status} lost')
            return
        req = UpdateTask.Request()
        req.task_id, req.status, req.phase, req.message = tid, status, phase, message
        self.update_cli.call_async(req).add_done_callback(
            lambda f: (f.result() and not f.result().success) and self.get_logger().warning(
                f'[WARN] WMS refused {tid} -> {status}: {f.result().message}'))


def main(args=None):
    rclpy.init(args=args)
    node = TaskManager()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
