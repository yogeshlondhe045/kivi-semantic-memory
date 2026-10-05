"""battery_simulator: AGV battery model publishing sensor_msgs/BatteryState on /agv/battery.

Charging happens only when the AGV is physically docked: ground-truth pose within
`dock_tolerance_m` of the charger dock pose, heading within 20 deg, and (almost) stationary.
Fault LOW_BATTERY sets the state of charge to Fault.value (default 18 %).
"""
from __future__ import annotations

import math

import rclpy
from rclpy.executors import ExternalShutdownException
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import BatteryState
from std_msgs.msg import Float64

from uco_common.alerts import AlertPublisher
from uco_common.layout import CHARGER, load_layout, quaternion_to_yaw
from uco_common.qos import FAULTS
from uco_interfaces.msg import AgvState, Fault

from .battery_model import BatteryModel, BatteryParams


class BatterySimulator(Node):
    def __init__(self):
        super().__init__('battery_simulator')
        defaults = BatteryParams()
        for name, val in (('capacity_wh', defaults.capacity_wh), ('idle_power_w', defaults.idle_power_w),
                          ('motion_power_w_per_mps', defaults.motion_power_w_per_mps),
                          ('turn_power_w_per_radps', defaults.turn_power_w_per_radps),
                          ('payload_factor', defaults.payload_factor), ('charge_power_w', defaults.charge_power_w),
                          ('time_scale', 1.0), ('initial_percent', 85.0), ('dock_tolerance_m', 0.30),
                          ('rate_hz', 2.0)):
            self.declare_parameter(name, val)
        self.declare_parameter('layout_file', '')
        self.declare_parameter('agv_id', 'AGV-01')
        p = lambda n: self.get_parameter(n).value  # noqa: E731
        params = BatteryParams(capacity_wh=p('capacity_wh'), idle_power_w=p('idle_power_w'),
                               motion_power_w_per_mps=p('motion_power_w_per_mps'),
                               turn_power_w_per_radps=p('turn_power_w_per_radps'),
                               payload_factor=p('payload_factor'), charge_power_w=p('charge_power_w'),
                               time_scale=p('time_scale'))
        self.model = BatteryModel(params, p('initial_percent'))
        layout = load_layout(p('layout_file') or None)
        self.docks = [loc.access for loc in layout.locations_of_kind(CHARGER)]
        self.dock_tol = p('dock_tolerance_m')
        self.agv_id = p('agv_id')
        self.alerts = AlertPublisher(self, self.agv_id)
        self.v = 0.0
        self.w = 0.0
        self.pose = None
        self.carrying = False
        self.charging = False
        self.last_t = None
        self.pub = self.create_publisher(BatteryState, '/agv/battery', 10)
        # cumulative energy drawn from the battery (for metrics)
        self.energy_pub = self.create_publisher(Float64, '/agv/battery/consumed_wh', 10)
        self.create_subscription(Odometry, '/agv/odom', self._on_odom, qos_profile_sensor_data)
        self.create_subscription(Odometry, '/agv/ground_truth', self._on_gt, 10)
        self.create_subscription(AgvState, '/agv/state', lambda m: setattr(self, 'carrying', bool(m.carrying_container)), 10)
        self.create_subscription(Fault, '/warehouse/faults', self._on_fault, FAULTS)
        self.create_timer(1.0 / p('rate_hz'), self._tick)
        self.get_logger().info(f'[INFO] Battery simulator: {params.capacity_wh:.0f} Wh, start {p("initial_percent"):.0f} %, '
                               f'time scale x{params.time_scale}')

    def _on_odom(self, m: Odometry) -> None:
        self.v = m.twist.twist.linear.x
        self.w = m.twist.twist.angular.z

    def _on_gt(self, m: Odometry) -> None:
        q = m.pose.pose.orientation
        self.pose = (m.pose.pose.position.x, m.pose.pose.position.y, quaternion_to_yaw(q.x, q.y, q.z, q.w))

    def _on_fault(self, f: Fault) -> None:
        if f.fault_type == Fault.LOW_BATTERY and f.active:
            pct = f.value if f.value > 0 else 18.0
            self.model.set_percent(pct)
            self.alerts.warn('BATTERY_FAULT_INJECTED', f'{self.agv_id} battery forced to {pct:.0f} % (simulated fault)')

    def _docked(self) -> bool:
        if self.pose is None or abs(self.v) > 0.03 or abs(self.w) > 0.05:
            return False
        x, y, yaw = self.pose
        for d in self.docks:
            dyaw = abs(math.atan2(math.sin(yaw - d.yaw), math.cos(yaw - d.yaw)))
            if math.hypot(x - d.x, y - d.y) <= self.dock_tol and dyaw < math.radians(20):
                return True
        return False

    def _tick(self) -> None:
        now = self.get_clock().now().nanoseconds * 1e-9
        dt = 0.0 if self.last_t is None else max(0.0, now - self.last_t)
        self.last_t = now
        charging = self._docked()
        if charging != self.charging:
            self.charging = charging
            self.alerts.info('CHARGING_STARTED' if charging else 'CHARGING_STOPPED',
                             f'{self.agv_id} {"docked, charging" if charging else "left charger"} '
                             f'at {self.model.percent:.0f} %')
        power = self.model.step(dt, self.v, self.w, self.carrying, charging)
        msg = BatteryState()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = 'base_link'
        msg.voltage = float(self.model.voltage)
        msg.current = float(power / max(1.0, self.model.voltage))
        msg.charge = float(self.model.energy_wh / self.model.p.nominal_voltage)          # Ah
        msg.capacity = float(self.model.p.capacity_wh / self.model.p.nominal_voltage)
        msg.design_capacity = msg.capacity
        msg.percentage = float(self.model.percent / 100.0)
        msg.power_supply_status = (BatteryState.POWER_SUPPLY_STATUS_CHARGING if charging and self.model.percent < 99.9
                                   else BatteryState.POWER_SUPPLY_STATUS_FULL if charging
                                   else BatteryState.POWER_SUPPLY_STATUS_DISCHARGING)
        msg.power_supply_technology = BatteryState.POWER_SUPPLY_TECHNOLOGY_LION
        msg.present = True
        msg.location = self.agv_id
        self.pub.publish(msg)
        self.energy_pub.publish(Float64(data=float(self.model.consumed_wh)))


def main(args=None):
    rclpy.init(args=args)
    node = BatterySimulator()
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
