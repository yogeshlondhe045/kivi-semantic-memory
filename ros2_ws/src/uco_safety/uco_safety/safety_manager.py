"""safety_manager: warehouse safety supervisor for AGV-01.

Responsibilities
  * sensor gateway   /agv/scan_raw -> /agv/scan, /agv/scan_rear_raw -> /agv/scan_rear
                     (drops data while a SENSOR_FAILURE fault is injected)
  * velocity gate    /cmd_vel_safe (collision monitor output) -> /agv/cmd_vel
                     (zero while e-stop / sensor fault / heartbeat loss; clamps to the speed limit)
  * speed zones      publishes nav2_msgs/SpeedLimit on /speed_limit from the layout zones
  * watchdogs        lidar / odometry timeouts, AGV heartbeat, path blocked, restricted zone, battery
  * e-stop           std_srvs/SetBool on /safety/emergency_stop
  * status           uco_interfaces/SafetyStatus on /safety/status, alerts on /warehouse/alerts
"""
from __future__ import annotations

import rclpy
from geometry_msgs.msg import Twist
from nav2_msgs.msg import CollisionMonitorState, SpeedLimit
from rclpy.duration import Duration
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from rclpy.time import Time
from sensor_msgs.msg import BatteryState, LaserScan
from nav_msgs.msg import Odometry
from std_srvs.srv import SetBool
from tf2_ros import Buffer, TransformListener

from uco_common.alerts import AlertPublisher
from uco_common.layout import load_layout
from uco_common.qos import FAULTS, LATCHED
from uco_interfaces.msg import AgvState, Fault, SafetyStatus

from .safety_core import ALARM, EMERGENCY_STOP, NORMAL, WARNING, SafetyConfig, SafetySupervisor


class SafetyManager(Node):
    def __init__(self):
        super().__init__('safety_manager')
        self.declare_parameter('layout_file', '')
        self.declare_parameter('scan_timeout_s', 1.0)
        self.declare_parameter('odom_timeout_s', 1.0)
        self.declare_parameter('heartbeat_timeout_s', 3.0)
        self.declare_parameter('blocked_timeout_s', 20.0)
        self.declare_parameter('battery_low_pct', 25.0)
        self.declare_parameter('battery_critical_pct', 10.0)
        self.declare_parameter('rate_hz', 10.0)
        p = lambda n: self.get_parameter(n).value  # noqa: E731
        self.layout = load_layout(p('layout_file') or None)
        cfg = SafetyConfig(scan_timeout_s=p('scan_timeout_s'), odom_timeout_s=p('odom_timeout_s'),
                           heartbeat_timeout_s=p('heartbeat_timeout_s'), blocked_timeout_s=p('blocked_timeout_s'),
                           battery_low_pct=p('battery_low_pct'), battery_critical_pct=p('battery_critical_pct'))
        self.sup = SafetySupervisor(self.layout, cfg)
        self.alerts = AlertPublisher(self, 'safety_manager')
        self.failed_sensors = set()
        self.decision = None
        self.active_codes = {}

        # sensor gateway
        self.scan_pub = self.create_publisher(LaserScan, '/agv/scan', qos_profile_sensor_data)
        self.scan_rear_pub = self.create_publisher(LaserScan, '/agv/scan_rear', qos_profile_sensor_data)
        self.create_subscription(LaserScan, '/agv/scan_raw',
                                 lambda m: self._relay(m, 'scan', self.scan_pub), qos_profile_sensor_data)
        self.create_subscription(LaserScan, '/agv/scan_rear_raw',
                                 lambda m: self._relay(m, 'scan_rear', self.scan_rear_pub), qos_profile_sensor_data)
        self.create_subscription(Odometry, '/agv/odom', self._on_odom, qos_profile_sensor_data)

        # velocity gate
        self.cmd_pub = self.create_publisher(Twist, '/agv/cmd_vel', 10)
        self.create_subscription(Twist, '/cmd_vel_safe', self._on_cmd, 10)

        self.speed_pub = self.create_publisher(SpeedLimit, '/speed_limit', LATCHED)
        self.status_pub = self.create_publisher(SafetyStatus, '/safety/status', LATCHED)
        self.create_subscription(AgvState, '/agv/state', self._on_agv_state, 10)
        self.create_subscription(BatteryState, '/agv/battery', lambda m: self.sup.set_battery(m.percentage * 100.0), 10)
        self.create_subscription(CollisionMonitorState, '/collision_monitor_state', self._on_cm_state, 10)
        self.create_subscription(Fault, '/warehouse/faults', self._on_fault, FAULTS)
        self.create_service(SetBool, '/safety/emergency_stop', self._on_estop)

        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self)
        self.last_speed_limit = None
        self.create_timer(1.0 / p('rate_hz'), self._tick)
        self.get_logger().info('[INFO] Safety manager running (layout: %s)' % self.layout.name)

    # ------------------------------------------------------------------ helpers
    def _now(self) -> float:
        return self.get_clock().now().nanoseconds * 1e-9

    def _relay(self, msg: LaserScan, name: str, pub) -> None:
        if name in self.failed_sensors:
            return
        self.sup.sensor_seen(name, self._now())
        pub.publish(msg)

    def _on_odom(self, _msg) -> None:
        if 'odom' not in self.failed_sensors:
            self.sup.sensor_seen('odom', self._now())

    def _on_cmd(self, msg: Twist) -> None:
        d = self.decision
        if d is None or not d.motion_allowed:
            self.cmd_pub.publish(Twist())
            return
        out = Twist()
        lim = d.speed_limit
        out.linear.x = max(-lim, min(lim, msg.linear.x)) if lim > 0 else msg.linear.x
        out.angular.z = msg.angular.z
        self.cmd_pub.publish(out)

    def _on_agv_state(self, msg: AgvState) -> None:
        self.sup.heartbeat(self._now(), navigating=msg.state in (AgvState.STATE_NAVIGATING,
                                                                 AgvState.STATE_GOING_TO_CHARGE))

    def _on_cm_state(self, msg: CollisionMonitorState) -> None:
        self.sup.set_obstacle_stop(msg.action_type == CollisionMonitorState.STOP, self._now())

    def _on_estop(self, req, res):
        self.sup.set_estop(req.data, 'operator request')
        if req.data:
            self.alerts.alarm('ESTOP', 'Emergency stop activated')
        else:
            self.alerts.info('ESTOP_RELEASED', 'Emergency stop released')
        self._tick()
        res.success = True
        res.message = 'e-stop ' + ('ACTIVE' if req.data else 'released')
        return res

    def _on_fault(self, f: Fault) -> None:
        if f.fault_type == Fault.SENSOR_FAILURE:
            targets = {'scan', 'scan_rear'} if f.target in ('', 'lidar', 'all') else {f.target}
            if f.active:
                self.failed_sensors |= targets
            else:
                self.failed_sensors -= targets
        elif f.fault_type == Fault.EMERGENCY_STOP:
            self.sup.set_estop(f.active, 'fault injection')
            if f.active:
                self.alerts.alarm('ESTOP', 'Emergency stop activated')
            else:
                self.alerts.info('ESTOP_RELEASED', 'Emergency stop released')
        elif f.fault_type == Fault.STATION_UNAVAILABLE:
            code = f'STATION_DOWN:{f.target or "INSPECT"}'
            if f.active:
                self.sup.raise_condition(code, WARNING, f'Station {f.target or "INSPECT"} unavailable')
            else:
                self.sup.clear_condition(code)
        elif f.fault_type == Fault.STORAGE_FULL:
            if f.active:
                self.sup.raise_condition('STORAGE_FULL', WARNING, 'Storage area full')
            else:
                self.sup.clear_condition('STORAGE_FULL')

    def _update_pose(self) -> None:
        try:
            t = self.tf_buffer.lookup_transform('map', 'base_footprint', Time(), timeout=Duration(seconds=0.0))
            self.sup.set_pose(t.transform.translation.x, t.transform.translation.y)
        except Exception:  # noqa: BLE001 - map frame not yet available (AMCL starting)
            pass

    # ------------------------------------------------------------------ main loop
    def _tick(self) -> None:
        self._update_pose()
        d = self.sup.evaluate(self._now())
        prev_allowed = self.decision.motion_allowed if self.decision else True
        self.decision = d
        if not d.motion_allowed:
            self.cmd_pub.publish(Twist())   # hold the robot even if Nav2 stops publishing
        self._report_transitions(d)
        if prev_allowed != d.motion_allowed:
            (self.alerts.warn if not d.motion_allowed else self.alerts.info)(
                'MOTION_' + ('BLOCKED' if not d.motion_allowed else 'RESUMED'),
                'AGV motion ' + ('blocked by safety system' if not d.motion_allowed else 'resumed - safe'))
        if d.speed_limit != self.last_speed_limit:
            sl = SpeedLimit()
            sl.header.stamp = self.get_clock().now().to_msg()
            sl.percentage = False
            sl.speed_limit = float(d.speed_limit) if d.motion_allowed else 0.01
            self.speed_pub.publish(sl)
            self.last_speed_limit = d.speed_limit
        st = SafetyStatus()
        st.header.stamp = self.get_clock().now().to_msg()
        st.state = d.state
        st.estop_active = self.sup.estop
        st.motion_allowed = d.motion_allowed
        st.speed_limit = float(d.speed_limit)
        st.zone = d.zone
        st.active_conditions = [f'{c.code}' for c in d.conditions]
        self.status_pub.publish(st)

    def _report_transitions(self, d) -> None:
        current = {c.code: c for c in d.conditions}
        for code, c in current.items():
            if code not in self.active_codes and code not in ('ESTOP',):
                if c.level == ALARM:
                    self.alerts.alarm(code, c.message)
                elif c.level == WARNING:
                    self.alerts.warn(code, c.message)
                elif c.level == EMERGENCY_STOP:
                    self.alerts.alarm(code, c.message)
        for code in set(self.active_codes) - set(current):
            if code != 'ESTOP':
                self.alerts.info(code + '_CLEARED', f'{code} cleared')
        self.active_codes = current


def main(args=None):
    rclpy.init(args=args)
    node = SafetyManager()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
