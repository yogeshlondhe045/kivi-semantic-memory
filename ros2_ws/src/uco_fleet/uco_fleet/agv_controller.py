"""agv_controller: task execution for one AGV (AGV-01).

Action servers
  /<ns>/transport_container   uco_interfaces/TransportContainer  pick-up + delivery of one container
  /<ns>/navigate_to_location  uco_interfaces/NavigateToLocation  "move AGV-01 to A-03-R01"
Uses
  Nav2 /navigate_to_pose, Gazebo /world/<w>/set_pose (lift-deck transfer), /warehouse/update_task (PICKED)
Publishes
  /agv/state (uco_interfaces/AgvState, 2 Hz heartbeat), alerts
Behaviour
  * e-stop / safety motion block -> cancel the Nav2 goal, wait, re-send when released (pause/resume)
  * battery below low threshold  -> abort a task that has not picked up yet (LOW_BATTERY), drive to the
                                    charger, charge to resume threshold, become available again; a task
                                    already carrying a container is completed first
  * idle for idle_return_s       -> return to the charger (opportunity charging)
  * faults: NAVIGATION_FAILURE (next N navigation attempts fail), COMMUNICATION_FAILURE (heartbeat
    suppressed for duration_s)
"""
from __future__ import annotations

import math
import threading
import time
from typing import Optional

import rclpy
from rclpy.executors import ExternalShutdownException
from action_msgs.msg import GoalStatus
from geometry_msgs.msg import PoseStamped
from nav2_msgs.action import NavigateToPose
from nav_msgs.msg import Odometry
from rclpy.action import ActionClient, ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.duration import Duration
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from rclpy.time import Time
from ros_gz_interfaces.srv import SetEntityPose
from sensor_msgs.msg import BatteryState
from tf2_ros import Buffer, TransformListener

from uco_common.alerts import AlertPublisher
from uco_common.layout import CHARGER, Pose2D, load_layout, quaternion_to_yaw, yaw_to_quaternion
from uco_common.qos import FAULTS
from uco_interfaces.action import NavigateToLocation, TransportContainer
from uco_interfaces.msg import AgvState, Fault, SafetyStatus
from uco_interfaces.srv import UpdateTask

OK, NAV_FAILED, CANCELLED, LOW_BATTERY = 'OK', 'NAV_FAILED', 'CANCELLED', 'LOW_BATTERY'


class AgvController(Node):
    def __init__(self):
        super().__init__('agv_controller')
        params = {
            'agv_id': 'AGV-01', 'model_name': 'agv_01', 'action_ns': '/agv_01', 'layout_file': '',
            'nav_action': '/navigate_to_pose', 'low_battery_pct': 25.0, 'resume_battery_pct': 80.0,
            'nav_retries': 1, 'transfer_time_s': 3.0, 'idle_return_s': 45.0, 'arrival_tolerance_m': 0.35,
            'state_rate_hz': 2.0, 'estop_wait_timeout_s': 600.0,
        }
        for k, v in params.items():
            self.declare_parameter(k, v)
        p = lambda n: self.get_parameter(n).value  # noqa: E731
        self.agv_id = p('agv_id')
        self.model_name = p('model_name')
        self.layout = load_layout(p('layout_file') or None)
        self.charger = self.layout.locations_of_kind(CHARGER)[0]
        self.low_pct = p('low_battery_pct')
        self.resume_pct = p('resume_battery_pct')
        self.nav_retries = int(p('nav_retries'))
        self.transfer_time = p('transfer_time_s')
        self.idle_return_s = p('idle_return_s')
        self.arrival_tol = p('arrival_tolerance_m')
        self.estop_timeout = p('estop_wait_timeout_s')
        self.alerts = AlertPublisher(self, self.agv_id)
        cb = ReentrantCallbackGroup()

        # ---- state
        self.mode = AgvState.STATE_IDLE
        self.current_task = ''
        self.carrying = ''
        self.goal_location = ''
        self.battery_pct = 100.0
        self.charging = False
        self.needs_charge = False          # low-battery episode: unavailable until resume_pct
        self.motion_allowed = True
        self.estop = False
        self.pose: Optional[Pose2D] = None          # AMCL (map frame)
        self.gt: Optional[Pose2D] = None            # ground truth (payload placement only)
        self.speed = 0.0
        self.distance = 0.0
        self.busy_time = 0.0
        self.start_time = self._now()
        self.last_odom_xy = None
        self.last_activity = self._now()
        self.fail_next_nav = 0
        self.comm_blackout_until = 0.0
        self.comm_blackout = False
        self.motion_lock = threading.Lock()       # one motion owner at a time
        self.preempt_auto = threading.Event()     # a task wants the robot: stop autonomous trips
        self.auto_thread: Optional[threading.Thread] = None
        self.nav_feedback_dist = 0.0

        # ---- interfaces
        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self)
        self.state_pub = self.create_publisher(AgvState, '/agv/state', 10)
        self.create_subscription(BatteryState, '/agv/battery', self._on_battery, 10, callback_group=cb)
        self.create_subscription(Odometry, '/agv/odom', self._on_odom, qos_profile_sensor_data, callback_group=cb)
        self.create_subscription(Odometry, '/agv/ground_truth', self._on_gt, 10, callback_group=cb)
        self.create_subscription(SafetyStatus, '/safety/status', self._on_safety, 10, callback_group=cb)
        self.create_subscription(Fault, '/warehouse/faults', self._on_fault, FAULTS, callback_group=cb)
        self.nav_client = ActionClient(self, NavigateToPose, p('nav_action'), callback_group=cb)
        self.set_pose_cli = self.create_client(SetEntityPose, f'/world/{self.layout.world_name}/set_pose',
                                               callback_group=cb)
        self.update_task_cli = self.create_client(UpdateTask, '/warehouse/update_task', callback_group=cb)
        ns = p('action_ns').rstrip('/')
        self.transport_server = ActionServer(
            self, TransportContainer, f'{ns}/transport_container', execute_callback=self._exec_transport,
            goal_callback=self._accept_goal, cancel_callback=lambda _: CancelResponse.ACCEPT, callback_group=cb)
        self.navloc_server = ActionServer(
            self, NavigateToLocation, f'{ns}/navigate_to_location', execute_callback=self._exec_navigate_location,
            goal_callback=self._accept_goal, cancel_callback=lambda _: CancelResponse.ACCEPT, callback_group=cb)
        self.create_timer(1.0 / p('state_rate_hz'), self._publish_state, callback_group=cb)
        self.create_timer(1.0, self._supervise, callback_group=cb)
        self.alerts.info('AGV_READY', f'{self.agv_id} controller ready')

    # ================================================================== callbacks
    def _now(self) -> float:
        return self.get_clock().now().nanoseconds * 1e-9

    def _on_battery(self, m: BatteryState) -> None:
        self.battery_pct = m.percentage * 100.0
        self.charging = m.power_supply_status in (BatteryState.POWER_SUPPLY_STATUS_CHARGING,
                                                  BatteryState.POWER_SUPPLY_STATUS_FULL)

    def _on_odom(self, m: Odometry) -> None:
        self.speed = m.twist.twist.linear.x
        xy = (m.pose.pose.position.x, m.pose.pose.position.y)
        if self.last_odom_xy is not None:
            d = math.hypot(xy[0] - self.last_odom_xy[0], xy[1] - self.last_odom_xy[1])
            if d < 1.0:       # ignore odometry resets
                self.distance += d
        self.last_odom_xy = xy

    def _on_gt(self, m: Odometry) -> None:
        q = m.pose.pose.orientation
        self.gt = Pose2D(m.pose.pose.position.x, m.pose.pose.position.y, quaternion_to_yaw(q.x, q.y, q.z, q.w))

    def _on_safety(self, m: SafetyStatus) -> None:
        self.motion_allowed = m.motion_allowed
        self.estop = m.estop_active

    def _on_fault(self, f: Fault) -> None:
        if f.target and f.target not in (self.agv_id, self.model_name, 'AGV'):
            return
        if f.fault_type == Fault.NAVIGATION_FAILURE:
            self.fail_next_nav = (max(1, int(f.value)) if f.value > 0 else self.nav_retries + 1) if f.active else 0
            if f.active:
                self.alerts.warn('NAV_FAULT_INJECTED', f'{self.agv_id}: next {self.fail_next_nav} navigation '
                                 'attempt(s) will fail (simulated)')
        elif f.fault_type == Fault.COMMUNICATION_FAILURE:
            if f.active:
                dur = f.duration_s if f.duration_s > 0 else 10.0
                self.comm_blackout_until = self._now() + dur
                self.get_logger().error(f'[ERROR] {self.agv_id} communication link lost (simulated, {dur:.0f} s)')
            else:
                self.comm_blackout_until = 0.0

    def _update_pose(self) -> None:
        try:
            t = self.tf_buffer.lookup_transform('map', 'base_footprint', Time(), timeout=Duration(seconds=0.0))
            q = t.transform.rotation
            self.pose = Pose2D(t.transform.translation.x, t.transform.translation.y,
                               quaternion_to_yaw(q.x, q.y, q.z, q.w))
        except Exception:  # noqa: BLE001 - AMCL not ready yet
            pass

    def _available(self) -> bool:
        return (not self.current_task and not self.needs_charge and self.mode not in (
            AgvState.STATE_ERROR,) and self.motion_allowed)

    def _publish_state(self) -> None:
        self._update_pose()
        now = self._now()
        blackout = now < self.comm_blackout_until
        if blackout != self.comm_blackout:
            self.comm_blackout = blackout
            if not blackout:
                self.alerts.info('COMM_RESTORED', f'{self.agv_id} communication restored')
        if blackout:
            return                      # heartbeat lost: nothing leaves the AGV
        if self.mode not in (AgvState.STATE_IDLE, AgvState.STATE_CHARGING):
            self.busy_time += 1.0 / self.get_parameter('state_rate_hz').value
        m = AgvState()
        m.header.stamp = self.get_clock().now().to_msg()
        m.header.frame_id = 'map'
        m.agv_id = self.agv_id
        m.state = self.mode
        m.current_task = self.current_task
        m.carrying_container = self.carrying
        m.goal_location = self.goal_location
        if self.pose is not None:
            m.pose.x, m.pose.y, m.pose.theta = self.pose.x, self.pose.y, self.pose.yaw
            m.current_location = self.layout.nearest_location(self.pose.x, self.pose.y) or ''
        m.linear_speed = float(self.speed)
        m.battery_percent = float(self.battery_pct)
        m.charging = self.charging
        m.available = self._available()
        m.distance_travelled_m = float(self.distance)
        m.busy_time_s = float(self.busy_time)
        m.uptime_s = float(now - self.start_time)
        self.state_pub.publish(m)

    # ================================================================== helpers
    def _wait_future(self, fut, timeout: float) -> bool:
        ev = threading.Event()
        fut.add_done_callback(lambda _: ev.set())
        return ev.wait(timeout)

    def _sleep(self, seconds: float) -> None:
        """Sleep in simulation time."""
        end = self._now() + seconds
        while self._now() < end and rclpy.ok():
            time.sleep(0.05)

    def _set_entity_pose(self, name: str, x: float, y: float, z: float, yaw: float) -> bool:
        if not self.set_pose_cli.wait_for_service(timeout_sec=5.0):
            self.alerts.error('SIM_SERVICE_MISSING', 'Gazebo set_pose service unavailable')
            return False
        req = SetEntityPose.Request()
        req.entity.name = name
        req.entity.type = req.entity.MODEL
        req.pose.position.x, req.pose.position.y, req.pose.position.z = x, y, z
        qx, qy, qz, qw = yaw_to_quaternion(yaw)
        req.pose.orientation.x, req.pose.orientation.y, req.pose.orientation.z, req.pose.orientation.w = qx, qy, qz, qw
        fut = self.set_pose_cli.call_async(req)
        if not self._wait_future(fut, 10.0) or fut.result() is None:
            return False
        return bool(fut.result().success)

    def _report_picked(self, task_id: str) -> None:
        if not task_id or not self.update_task_cli.wait_for_service(timeout_sec=5.0):
            return
        req = UpdateTask.Request()
        req.task_id, req.status = task_id, 'PICKED'
        fut = self.update_task_cli.call_async(req)
        if self._wait_future(fut, 10.0) and fut.result() is not None and not fut.result().success:
            self.alerts.error('WMS_UPDATE_FAILED', f'WMS rejected PICKED for {task_id}: {fut.result().message}')

    def _make_goal(self, pose: Pose2D) -> NavigateToPose.Goal:
        g = NavigateToPose.Goal()
        ps = PoseStamped()
        ps.header.frame_id = 'map'
        ps.header.stamp = self.get_clock().now().to_msg()
        ps.pose.position.x, ps.pose.position.y = pose.x, pose.y
        qx, qy, qz, qw = yaw_to_quaternion(pose.yaw)
        ps.pose.orientation.x, ps.pose.orientation.y, ps.pose.orientation.z, ps.pose.orientation.w = qx, qy, qz, qw
        g.pose = ps
        return g

    def _navigate(self, location_id: str, cancel_requested, abort_on_low_battery: bool,
                  feedback=None) -> str:
        """Drive to a location's access pose. Returns OK | NAV_FAILED | CANCELLED | LOW_BATTERY."""
        loc = self.layout.location(location_id)
        target = loc.access or loc.slot
        self.goal_location = loc.id
        attempt = 0
        if not self.nav_client.wait_for_server(timeout_sec=30.0):
            self.alerts.error('NAV2_UNAVAILABLE', 'Nav2 navigate_to_pose server not available')
            return NAV_FAILED
        while attempt <= self.nav_retries:
            if cancel_requested():
                return CANCELLED
            if not self.motion_allowed:
                outcome = self._wait_motion_allowed(cancel_requested)
                if outcome != OK:
                    return outcome
            if abort_on_low_battery and self.battery_pct < self.low_pct:
                return LOW_BATTERY
            if self.fail_next_nav > 0:
                self.fail_next_nav -= 1
                attempt += 1
                self.alerts.error('NAVIGATION_FAILED', f'{self.agv_id} navigation to {loc.id} failed '
                                  f'(simulated fault, attempt {attempt})')
                self._sleep(2.0)
                continue
            result = self._run_nav_goal(target, cancel_requested, abort_on_low_battery, feedback)
            if result == 'PAUSED':
                continue                         # safety stop: re-send without counting an attempt
            if result in (CANCELLED, LOW_BATTERY):
                return result
            if result == OK and self._arrived(target):
                return OK
            attempt += 1
            self.alerts.warn('NAVIGATION_RETRY', f'{self.agv_id} could not reach {loc.id} '
                             f'(attempt {attempt}/{self.nav_retries + 1})')
        self.alerts.error('NAVIGATION_FAILED', f'{self.agv_id} navigation to {loc.id} failed')
        return NAV_FAILED

    def _arrived(self, target: Pose2D) -> bool:
        self._update_pose()
        if self.pose is None:
            return False
        return math.hypot(self.pose.x - target.x, self.pose.y - target.y) <= self.arrival_tol

    def _wait_motion_allowed(self, cancel_requested) -> str:
        prev_mode = self.mode
        self.mode = AgvState.STATE_PAUSED
        start = self._now()
        while not self.motion_allowed:
            if cancel_requested():
                self.mode = prev_mode
                return CANCELLED
            if self._now() - start > self.estop_timeout:
                self.mode = prev_mode
                return NAV_FAILED
            time.sleep(0.2)
        self.alerts.info('AGV_RESUMED', f'{self.agv_id} resuming after safety stop')
        self.mode = prev_mode
        return OK

    def _run_nav_goal(self, target: Pose2D, cancel_requested, abort_on_low_battery, feedback) -> str:
        send = self.nav_client.send_goal_async(
            self._make_goal(target), feedback_callback=lambda fb: setattr(
                self, 'nav_feedback_dist', fb.feedback.distance_remaining))
        if not self._wait_future(send, 20.0) or send.result() is None or not send.result().accepted:
            return NAV_FAILED
        handle = send.result()
        res_fut = handle.get_result_async()
        reason = None
        while not res_fut.done():
            if cancel_requested():
                reason = CANCELLED
            elif not self.motion_allowed:
                reason = 'PAUSED'
                self.alerts.warn('AGV_PAUSED', f'{self.agv_id} paused by safety system')
            elif abort_on_low_battery and self.battery_pct < self.low_pct:
                reason = LOW_BATTERY
            if reason:
                cfut = handle.cancel_goal_async()
                self._wait_future(cfut, 10.0)
                self._wait_future(res_fut, 10.0)
                if reason == 'PAUSED':
                    out = self._wait_motion_allowed(cancel_requested)
                    return 'PAUSED' if out == OK else out
                return reason
            if feedback is not None:
                feedback(self.nav_feedback_dist)
            time.sleep(0.2)
        status = res_fut.result().status if res_fut.result() is not None else GoalStatus.STATUS_ABORTED
        return OK if status == GoalStatus.STATUS_SUCCEEDED else NAV_FAILED

    # ================================================================== goals
    def _accept_goal(self, goal_request) -> GoalResponse:
        if self.current_task or self.needs_charge or not self.motion_allowed:
            self.get_logger().warning(f'[WARN] {self.agv_id} rejected goal (task={self.current_task or "-"}, '
                                      f'needs_charge={self.needs_charge}, motion_allowed={self.motion_allowed})')
            return GoalResponse.REJECT
        # reserve immediately so that a second goal is rejected
        self.current_task = getattr(goal_request, 'task_id', '') or 'MANUAL'
        return GoalResponse.ACCEPT

    def _acquire_motion(self) -> None:
        self.preempt_auto.set()
        self.motion_lock.acquire()
        self.preempt_auto.clear()

    def _exec_transport(self, goal_handle):
        g = goal_handle.request
        res = TransportContainer.Result()
        t0, d0 = self._now(), self.distance
        self._acquire_motion()
        try:
            self.current_task = g.task_id
            self.last_activity = self._now()
            fb = TransportContainer.Feedback()

            def publish(phase, dist=0.0):
                fb.phase, fb.distance_remaining = phase, float(dist)
                goal_handle.publish_feedback(fb)

            cancelled = lambda: goal_handle.is_cancel_requested  # noqa: E731
            self.alerts.info('TASK_STARTED', f'{self.agv_id} executing {g.task_id}: {g.container_id} '
                             f'{g.source_location} -> {g.destination_location}')

            if self.carrying != g.container_id:
                if self.carrying:
                    return self._finish(goal_handle, res, False, 'PICK_FAILED',
                                        f'already carrying {self.carrying}', t0, d0)
                self.mode = AgvState.STATE_NAVIGATING
                publish('TO_PICKUP')
                out = self._navigate(g.source_location, cancelled, True, lambda d: publish('TO_PICKUP', d))
                if out != OK:
                    return self._finish(goal_handle, res, False, out, f'to pick-up: {out}', t0, d0)
                self.mode = AgvState.STATE_PICKING
                publish('PICKING')
                if not self._pick(g.container_id):
                    return self._finish(goal_handle, res, False, 'PICK_FAILED',
                                        f'could not transfer {g.container_id} onto the deck', t0, d0)
                self._report_picked(g.task_id)
            # carrying a container: delivery is critical, never aborted for battery
            self.mode = AgvState.STATE_NAVIGATING
            publish('TO_DROPOFF')
            out = self._navigate(g.destination_location, cancelled, False, lambda d: publish('TO_DROPOFF', d))
            if out != OK:
                return self._finish(goal_handle, res, False, out, f'to drop-off: {out}', t0, d0)
            self.mode = AgvState.STATE_DROPPING
            publish('DROPPING')
            if not self._drop(g.container_id, g.destination_location):
                return self._finish(goal_handle, res, False, 'DROP_FAILED', 'transfer to slot failed', t0, d0)
            return self._finish(goal_handle, res, True, '', f'{g.container_id} delivered to '
                                f'{g.destination_location}', t0, d0)
        finally:
            self.current_task = ''
            self.goal_location = ''
            if self.mode != AgvState.STATE_ERROR:
                self.mode = AgvState.STATE_IDLE
            self.last_activity = self._now()
            self.motion_lock.release()

    def _finish(self, goal_handle, res, success: bool, code: str, message: str, t0: float, d0: float):
        res.success, res.failure_code, res.message = success, code, message
        res.duration_s = float(self._now() - t0)
        res.distance_m = float(self.distance - d0)
        if success:
            goal_handle.succeed()
        elif code == CANCELLED or goal_handle.is_cancel_requested:
            res.failure_code = CANCELLED
            goal_handle.canceled()
        else:
            goal_handle.abort()
            if code == LOW_BATTERY:
                self.alerts.error('BATTERY_LOW', f'{self.agv_id} battery below threshold '
                                  f'({self.battery_pct:.0f} %): task interrupted, going to charge')
            else:
                self.alerts.warn('TASK_ABORTED', f'{self.agv_id}: {message}')
        return res

    def _pick(self, container_id: str) -> bool:
        """Simulated lift-deck transfer: the container entity is moved from its slot onto the deck."""
        self._sleep(self.transfer_time / 2)
        if self.gt is None:
            return False
        ok = self._set_entity_pose(container_id, self.gt.x, self.gt.y, self.layout.deck_height + 0.01, self.gt.yaw)
        if ok:
            self.carrying = container_id
            self._sleep(self.transfer_time / 2)
            self.alerts.info('CONTAINER_PICKED', f'{self.agv_id} picked {container_id}')
        return ok

    def _drop(self, container_id: str, location_id: str) -> bool:
        slot = self.layout.location(location_id).slot
        self._sleep(self.transfer_time / 2)
        ok = self._set_entity_pose(container_id, slot.x, slot.y, 0.01, slot.yaw)
        if ok:
            self.carrying = ''
            self._sleep(self.transfer_time / 2)
            self.alerts.info('CONTAINER_DELIVERED', f'{self.agv_id} delivered {container_id} to {location_id}')
        return ok

    def _exec_navigate_location(self, goal_handle):
        res = NavigateToLocation.Result()
        loc_id = self.layout.resolve(goal_handle.request.location_id)
        t0 = self._now()
        if loc_id is None or self.layout.locations[loc_id].access is None:
            self.current_task = ''
            res.success, res.message = False, f'unknown or inaccessible location "{goal_handle.request.location_id}"'
            goal_handle.abort()
            return res
        self._acquire_motion()
        try:
            self.current_task = 'MANUAL'
            self.mode = AgvState.STATE_NAVIGATING
            self.alerts.info('MANUAL_MOVE', f'{self.agv_id} moving to {loc_id} (operator request)')
            fb = NavigateToLocation.Feedback()

            def publish(d):
                fb.state, fb.distance_remaining = self.mode, float(d)
                goal_handle.publish_feedback(fb)
            out = self._navigate(loc_id, lambda: goal_handle.is_cancel_requested, False, publish)
            res.travel_time_s = float(self._now() - t0)
            res.success = out == OK
            res.message = f'{self.agv_id} at {loc_id}' if res.success else f'navigation to {loc_id}: {out}'
            if res.success:
                goal_handle.succeed()
            elif out == CANCELLED:
                goal_handle.canceled()
            else:
                goal_handle.abort()
            return res
        finally:
            self.current_task = ''
            self.goal_location = ''
            self.mode = AgvState.STATE_IDLE
            self.last_activity = self._now()
            self.motion_lock.release()

    # ================================================================== autonomous behaviour
    def _supervise(self) -> None:
        """1 Hz: low-battery charging, idle return to charger, charge completion."""
        if self.needs_charge and self.battery_pct >= self.resume_pct:
            self.needs_charge = False
            self.alerts.info('CHARGED', f'{self.agv_id} charged to {self.battery_pct:.0f} %, available for tasks')
        if self.charging and self.mode == AgvState.STATE_IDLE:
            self.mode = AgvState.STATE_CHARGING
        elif not self.charging and self.mode == AgvState.STATE_CHARGING:
            self.mode = AgvState.STATE_IDLE
        if self.current_task or (self.auto_thread and self.auto_thread.is_alive()):
            return
        if self.battery_pct < self.low_pct and not self.charging:
            if not self.needs_charge:
                self.needs_charge = True
                self.alerts.error('BATTERY_LOW', f'{self.agv_id} battery below threshold ({self.battery_pct:.0f} %)')
            self._start_auto_trip('low battery')
        elif (not self.charging and self.mode == AgvState.STATE_IDLE
              and self._now() - self.last_activity > self.idle_return_s and not self._at_charger()):
            self._start_auto_trip('idle')

    def _at_charger(self) -> bool:
        return self.pose is not None and math.hypot(self.pose.x - self.charger.access.x,
                                                    self.pose.y - self.charger.access.y) < 0.5

    def _start_auto_trip(self, reason: str) -> None:
        self.auto_thread = threading.Thread(target=self._go_charge, args=(reason,), daemon=True)
        self.auto_thread.start()

    def _go_charge(self, reason: str) -> None:
        if not self.motion_lock.acquire(blocking=False):
            return
        try:
            self.mode = AgvState.STATE_GOING_TO_CHARGE
            self.alerts.info('GOING_TO_CHARGE', f'{self.agv_id} returning to charger ({reason})')
            # an idle return is pre-empted by new tasks; a low-battery trip is not
            cancel = (lambda: self.preempt_auto.is_set()) if reason == 'idle' else (lambda: False)
            out = self._navigate(self.charger.id, cancel, False)
            if out != OK and out != CANCELLED:
                self.alerts.error('CHARGER_UNREACHABLE', f'{self.agv_id} could not reach the charger: {out}')
        finally:
            self.mode = AgvState.STATE_IDLE
            self.goal_location = ''
            self.last_activity = self._now()
            self.motion_lock.release()


def main(args=None):
    rclpy.init(args=args)
    node = AgvController()
    executor = MultiThreadedExecutor(num_threads=6)
    executor.add_node(node)
    try:
        executor.spin()
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
