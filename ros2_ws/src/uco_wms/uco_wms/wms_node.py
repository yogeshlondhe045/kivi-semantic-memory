"""wms_node: ROS 2 interface of the warehouse management system.

Owns the SQLite database (WarehouseStore) and exposes the WMS operations as services.

Services   /warehouse/register_container, assign_storage, create_task, cancel_task, assign_agv,
           update_task, update_status, update_location, request_dispatch
Topics     /warehouse/inventory (latched, periodic), /warehouse/tasks (latched, on change + periodic),
           /warehouse/container_state (event per change), /warehouse/markers (RViz), /warehouse/alerts
Faults     REGISTRATION_FAILURE, STORAGE_FULL (from /warehouse/faults)
"""
from __future__ import annotations

import os

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from visualization_msgs.msg import Marker, MarkerArray

from uco_common.alerts import AlertPublisher
from uco_common.layout import load_layout, yaw_to_quaternion
from uco_common.qos import EVENTS, FAULTS, LATCHED
from uco_interfaces.msg import ContainerState, Fault, Inventory, StorageSlot, Task, TaskList
from uco_interfaces.srv import (AssignAgv, AssignStorage, CancelTask, CreateTask, RegisterContainer,
                                RequestDispatch, UpdateLocation, UpdateStatus, UpdateTask)

from . import store as S

_STATUS_COLOR = {
    S.REGISTERED: (0.7, 0.7, 0.7), S.WEIGHED: (0.7, 0.7, 0.7), S.APPROVED: (0.95, 0.75, 0.1),
    S.QUARANTINED: (0.95, 0.5, 0.1), S.REJECTED: (0.85, 0.15, 0.1), S.STORAGE_ASSIGNED: (0.95, 0.85, 0.3),
    S.IN_TRANSIT: (0.2, 0.8, 0.9), S.STORED: (0.2, 0.5, 0.95), S.RESERVED: (0.6, 0.4, 0.9),
    S.DISPATCHED: (0.2, 0.8, 0.3),
}


class WmsNode(Node):
    def __init__(self):
        super().__init__('wms_node')
        self.declare_parameter('layout_file', '')
        self.declare_parameter('db_path', '')
        self.declare_parameter('reset_on_start', True)
        self.declare_parameter('allocation_policy', 'nearest')
        self.declare_parameter('max_task_attempts', 3)
        self.declare_parameter('publish_period_s', 1.0)
        p = lambda n: self.get_parameter(n).value  # noqa: E731
        self.layout = load_layout(p('layout_file') or None)
        db_path = p('db_path')
        if db_path in ('memory', ':memory:'):   # ':memory:' cannot be passed as a CLI value
            db_path = ':memory:'
        db_path = db_path or os.path.join(
            os.environ.get('ROS_HOME', os.path.expanduser('~/.ros')), 'uco_warehouse', 'wms.db')
        if db_path != ':memory:':
            os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.store = S.WarehouseStore(self.layout, db_path, clock=self._now,
                                      allocation_policy=p('allocation_policy'),
                                      max_task_attempts=p('max_task_attempts'))
        if p('reset_on_start'):
            self.store.reset()
        self.alerts = AlertPublisher(self, 'WMS')

        self.inv_pub = self.create_publisher(Inventory, '/warehouse/inventory', LATCHED)
        self.task_pub = self.create_publisher(TaskList, '/warehouse/tasks', LATCHED)
        self.cs_pub = self.create_publisher(ContainerState, '/warehouse/container_state', EVENTS)
        self.marker_pub = self.create_publisher(MarkerArray, '/warehouse/markers', LATCHED)
        self.create_subscription(Fault, '/warehouse/faults', self._on_fault, FAULTS)

        srv = [
            (RegisterContainer, 'register_container', self._register),
            (AssignStorage, 'assign_storage', self._assign_storage),
            (CreateTask, 'create_task', self._create_task),
            (CancelTask, 'cancel_task', self._cancel_task),
            (AssignAgv, 'assign_agv', self._assign_agv),
            (UpdateTask, 'update_task', self._update_task),
            (UpdateStatus, 'update_status', self._update_status),
            (UpdateLocation, 'update_location', self._update_location),
            (RequestDispatch, 'request_dispatch', self._request_dispatch),
        ]
        for srv_type, name, cb in srv:
            self.create_service(srv_type, f'/warehouse/{name}', cb)
        self.create_timer(p('publish_period_s'), self.publish_all)
        self.publish_all()
        self.alerts.info('WMS_READY', f'WMS ready: {len(self.store.slots("STORAGE"))} storage positions, '
                         f'database {db_path}')

    # ------------------------------------------------------------------ helpers
    def _now(self) -> float:
        return self.get_clock().now().nanoseconds * 1e-9

    def _guard(self, res, fn, *args, **kwargs):
        """Run a store operation; turn WmsError into success=False + message."""
        try:
            out = fn(*args, **kwargs)
            res.success = True
            return out
        except S.WmsError as e:
            res.success = False
            res.message = str(e)
            sev = self.alerts.warn if isinstance(e, S.StorageFullError) else self.alerts.error
            sev(type(e).__name__.upper(), str(e), dedup=True)
            return None

    def _container_msg(self, c: S.Container) -> ContainerState:
        m = ContainerState()
        m.id, m.container_type, m.material, m.supplier = c.id, c.container_type, c.material, c.supplier or ''
        m.declared_volume_l = float(c.declared_volume_l or 0)
        m.measured_volume_l = float(c.measured_volume_l or 0)
        m.weight_kg = float(c.weight_kg or 0)
        m.status, m.quality_status = c.status, c.quality_status
        m.location, m.destination, m.assigned_agv = c.location or '', c.destination or '', c.assigned_agv or ''
        m.arrival_time, m.updated_time = float(c.arrival_time or 0), float(c.updated_time or 0)
        return m

    def _publish_container(self, cid: str) -> None:
        try:
            self.cs_pub.publish(self._container_msg(self.store.get_container(cid)))
        except S.WmsError:
            pass

    def _changed(self, *container_ids: str) -> None:
        for cid in container_ids:
            if cid:
                self._publish_container(cid)
        self.publish_all()

    @staticmethod
    def _task_msg(t: S.Task) -> Task:
        m = Task()
        m.id, m.task_type, m.container_id = t.id, t.task_type, t.container_id
        m.source, m.destination, m.status, m.phase = t.source or '', t.destination or '', t.status, t.phase or ''
        m.assigned_agv, m.priority, m.attempts = t.assigned_agv or '', int(t.priority), int(t.attempts)
        m.created_time, m.assigned_time = float(t.created_time or 0), float(t.assigned_time or 0)
        m.started_time, m.completed_time = float(t.started_time or 0), float(t.completed_time or 0)
        m.failure_reason = t.failure_reason or ''
        return m

    def publish_all(self) -> None:
        stamp = self.get_clock().now().to_msg()
        inv = Inventory()
        inv.header.stamp = stamp
        inv.header.frame_id = 'map'
        containers = self.store.containers()
        inv.containers = [self._container_msg(c) for c in containers]
        for s in self.store.slots():
            sm = StorageSlot()
            sm.id, sm.kind, sm.zone = s.id, s.kind, s.zone or ''
            sm.x, sm.y, sm.yaw = float(s.x), float(s.y), float(s.yaw)
            sm.container_id, sm.reserved, sm.enabled = s.container_id or '', bool(s.reserved_for), s.enabled
            inv.slots.append(sm)
        summ = self.store.summary()
        inv.storage_capacity = summ['storage_capacity']
        inv.storage_occupied = summ['storage_occupied']
        inv.storage_occupancy = float(summ['storage_occupancy'])
        inv.stored_volume_l = float(summ['stored_volume_l'])
        inv.containers_received_total = summ['received_total']
        inv.containers_dispatched_total = summ['dispatched_total']
        self.inv_pub.publish(inv)
        tl = TaskList()
        tl.header.stamp = stamp
        tl.tasks = [self._task_msg(t) for t in self.store.tasks()]
        tl.completed_total = summ['tasks_completed']
        tl.failed_total = summ['tasks_failed']
        tl.cancelled_total = summ['tasks_cancelled']
        self.task_pub.publish(tl)
        self._publish_markers(containers, stamp)

    def _publish_markers(self, containers, stamp) -> None:
        ma = MarkerArray()
        clear = Marker()
        clear.action = Marker.DELETEALL
        ma.markers.append(clear)
        for i, c in enumerate(containers):
            loc = self.layout.locations.get(c.location)
            if loc is None:
                continue    # on an AGV / at a station shown in Gazebo
            col = _STATUS_COLOR.get(c.status, (1.0, 1.0, 1.0))
            box = Marker()
            box.header.frame_id, box.header.stamp = 'map', stamp
            box.ns, box.id, box.type, box.action = 'containers', i, Marker.CUBE, Marker.ADD
            box.pose.position.x, box.pose.position.y, box.pose.position.z = loc.slot.x, loc.slot.y, 0.55
            box.pose.orientation.w = 1.0
            box.scale.x = box.scale.y = 0.9
            box.scale.z = 1.1
            box.color.r, box.color.g, box.color.b, box.color.a = col[0], col[1], col[2], 0.8
            ma.markers.append(box)
            txt = Marker()
            txt.header = box.header
            txt.ns, txt.id, txt.type, txt.action = 'labels', i, Marker.TEXT_VIEW_FACING, Marker.ADD
            txt.pose.position.x, txt.pose.position.y, txt.pose.position.z = loc.slot.x, loc.slot.y, 1.5
            txt.pose.orientation.w = 1.0
            txt.scale.z = 0.35
            txt.color.r = txt.color.g = txt.color.b = txt.color.a = 1.0
            txt.text = f'{c.id}\n{c.status}'
            ma.markers.append(txt)
        self.marker_pub.publish(ma)

    # ------------------------------------------------------------------ services
    def _register(self, req, res):
        cid = self._guard(res, self.store.register_container, req.container_type, req.supplier,
                          req.declared_volume_l, req.location or 'UNLOAD', req.container_id)
        if cid:
            res.container_id = cid
            res.message = f'registered {cid}'
            self.alerts.info('CONTAINER_REGISTERED', f'{cid} registered ({req.container_type}, '
                             f'{req.declared_volume_l:.0f} L declared, supplier {req.supplier or "-"})')
            self._changed(cid)
        return res

    def _assign_storage(self, req, res):
        slot = self._guard(res, self.store.assign_storage, req.container_id, req.kind)
        if slot:
            res.slot_id = slot
            res.message = f'{req.container_id} -> {slot}'
            self.alerts.info('STORAGE_ASSIGNED', f'Storage location {slot} assigned to {req.container_id}')
            self._changed(req.container_id)
        return res

    def _create_task(self, req, res):
        tid = self._guard(res, self.store.create_task, req.task_type, req.container_id, req.destination,
                          req.priority)
        if tid:
            t = self.store.get_task(tid)
            res.task_id = tid
            res.message = f'{tid}: {t.task_type} {t.container_id} {t.source} -> {t.destination}'
            self.alerts.info('TASK_CREATED', f'Task {res.message}')
            self._changed(req.container_id)
        return res

    def _cancel_task(self, req, res):
        self._guard(res, self.store.cancel_task, req.task_id, req.reason)
        if res.success:
            res.message = f'{req.task_id} cancelled'
            self.alerts.warn('TASK_CANCELLED', f'Task {req.task_id} cancelled: {req.reason or "no reason"}')
            self._changed(self.store.get_task(req.task_id).container_id)
        return res

    def _assign_agv(self, req, res):
        self._guard(res, self.store.assign_agv, req.task_id, req.agv_id)
        if res.success:
            res.message = f'{req.task_id} -> {req.agv_id}'
            self._changed(self.store.get_task(req.task_id).container_id)
        return res

    def _update_task(self, req, res):
        """status: IN_PROGRESS (+phase) | PICKED | COMPLETED | FAILED | PENDING (unassign)."""
        st = req.status
        if st == S.IN_PROGRESS:
            self._guard(res, self.store.start_task, req.task_id, req.phase)
        elif st == 'PICKED':
            self._guard(res, self.store.mark_picked, req.task_id)
        elif st == S.COMPLETED:
            self._guard(res, self.store.complete_task, req.task_id)
            if res.success:
                t = self.store.get_task(req.task_id)
                self.alerts.info('TASK_COMPLETED', f'Task {t.id} completed: {t.container_id} at {t.destination}')
        elif st == S.FAILED:
            outcome = self._guard(res, self.store.fail_task, req.task_id, req.message or 'unspecified')
            if res.success:
                t = self.store.get_task(req.task_id)
                if outcome == S.FAILED:
                    self.alerts.error('TASK_FAILED', f'Task {t.id} failed permanently after {t.attempts} '
                                      f'attempts: {req.message}')
                else:
                    self.alerts.warn('TASK_RETRY', f'Task {t.id} attempt {t.attempts} failed ({req.message}); '
                                     'requeued')
                res.message = outcome
        elif st == S.PENDING:
            self._guard(res, self.store.unassign, req.task_id, req.message)
        else:
            res.success, res.message = False, f'unsupported task status {st}'
        if res.success:
            try:
                self._changed(self.store.get_task(req.task_id).container_id)
            except S.WmsError:
                pass
        return res

    def _update_status(self, req, res):
        st = req.status
        if st == S.WEIGHED:
            self._guard(res, self.store.record_weight, req.container_id, req.weight_kg, req.measured_volume_l)
        elif st in (S.APPROVED, S.QUARANTINED, S.REJECTED) and req.quality_status:
            self._guard(res, self.store.record_inspection, req.container_id, req.quality_status)
        else:
            self._guard(res, self.store.update_status, req.container_id, st, req.quality_status,
                        req.weight_kg, req.measured_volume_l, req.note)
        if res.success:
            res.message = f'{req.container_id} -> {self.store.get_container(req.container_id).status}'
            self._changed(req.container_id)
        return res

    def _update_location(self, req, res):
        loc = req.location
        if loc == S.PROCESSING_LOCATION:
            self._guard(res, self.store.hand_over, req.container_id)
            if res.success:
                self.alerts.info('HANDED_OVER', f'{req.container_id} handed over to the processing plant')
        elif loc in self.layout.locations and self.layout.locations[loc].is_slot:
            self._guard(res, self.store.place_in_slot, req.container_id, loc)
        else:
            self._guard(res, self.store.update_location, req.container_id, loc)
        if res.success:
            res.message = f'{req.container_id} at {loc}'
            self._changed(req.container_id)
        return res

    def _request_dispatch(self, req, res):
        out = self._guard(res, self.store.request_dispatch, int(req.count), list(req.container_ids))
        if out is not None:
            chosen, tasks, msg = out
            res.container_ids, res.task_ids, res.message = chosen, tasks, msg
            res.success = bool(chosen)
            (self.alerts.info if chosen else self.alerts.warn)('DISPATCH_REQUEST', f'Processing request: {msg}')
            self._changed(*chosen)
        return res

    # ------------------------------------------------------------------ faults
    def _on_fault(self, f: Fault) -> None:
        if f.fault_type == Fault.REGISTRATION_FAILURE:
            self.store.fail_next_registrations = max(1, int(f.value)) if f.active else 0
        elif f.fault_type == Fault.STORAGE_FULL:
            self.store.storage_full_fault = f.active
            if f.active:
                self.alerts.warn('STORAGE_FULL', 'Storage full: no storage positions available (simulated)')
            else:
                self.alerts.info('STORAGE_AVAILABLE', 'Storage positions available again')
            self.publish_all()


def main(args=None):
    rclpy.init(args=args)
    node = WmsNode()
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
