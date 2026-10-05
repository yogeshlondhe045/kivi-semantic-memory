"""fault_injector: simulated fault framework.

Service /faults/inject (uco_interfaces/InjectFault) publishes a uco_interfaces/Fault event on
/warehouse/faults; every node reacts only to the faults it owns:

  BLOCKED_PATH          fault_injector spawns / removes a pallet obstacle in Gazebo at `target`
                        (location id -> its AGV access pose, "x,y", or AHEAD = a pallet dropped
                        1.0 m in front of the AGV: its near face lies inside the collision
                        monitor's stop zone, clear of the AGV and of a carried drum)
  SENSOR_FAILURE        safety_manager stops relaying lidar data (target scan | scan_rear | lidar)
  LOW_BATTERY           battery_simulator sets state of charge to `value` % (default 18)
  NAVIGATION_FAILURE    agv_controller fails the next `value` navigation attempts
  REGISTRATION_FAILURE  wms_node rejects the next `value` registrations
  STORAGE_FULL          wms_node offers no storage positions while active
  STATION_UNAVAILABLE   weighing / inspection station refuses requests (target WEIGH | INSPECT)
  COMMUNICATION_FAILURE agv_controller suppresses its heartbeat for `duration_s`
  EMERGENCY_STOP        safety_manager latches the e-stop while active

A fault with duration_s > 0 is cleared automatically. CLI: `ros2 run uco_safety inject_fault -h`.
"""
from __future__ import annotations

import argparse
import math
import sys

import rclpy
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import ExternalShutdownException
from nav_msgs.msg import Odometry
from rclpy.node import Node
from ros_gz_interfaces.msg import Entity
from ros_gz_interfaces.srv import DeleteEntity, SpawnEntity

from uco_common.alerts import AlertPublisher
from uco_common.layout import load_layout, quaternion_to_yaw
from uco_common.qos import FAULTS
from uco_interfaces.msg import Fault
from uco_interfaces.srv import InjectFault

FAULT_TYPES = (Fault.BLOCKED_PATH, Fault.SENSOR_FAILURE, Fault.LOW_BATTERY, Fault.NAVIGATION_FAILURE,
               Fault.REGISTRATION_FAILURE, Fault.STORAGE_FULL, Fault.STATION_UNAVAILABLE,
               Fault.COMMUNICATION_FAILURE, Fault.EMERGENCY_STOP)
# one-shot faults: the event itself is the fault, nothing to clear later
ONE_SHOT = (Fault.LOW_BATTERY, Fault.NAVIGATION_FAILURE, Fault.REGISTRATION_FAILURE)

OBSTACLE_SDF = ('<?xml version="1.0"?><sdf version="1.9"><model name="{name}"><static>true</static><link name="l">'
                '<collision name="c"><pose>0 0 0.5 0 0 0</pose><geometry><box><size>1.0 0.8 1.0</size></box>'
                '</geometry></collision><visual name="v"><pose>0 0 0.5 0 0 0</pose><geometry><box>'
                '<size>1.0 0.8 1.0</size></box></geometry><material><ambient>0.9 0.35 0.1 1</ambient>'
                '<diffuse>0.9 0.35 0.1 1</diffuse></material></visual></link></model></sdf>')


class FaultInjector(Node):
    def __init__(self):
        super().__init__('fault_injector')
        self.declare_parameter('layout_file', '')
        self.layout = load_layout(self.get_parameter('layout_file').value or None)
        self.alerts = AlertPublisher(self, 'fault_injector')
        cb = ReentrantCallbackGroup()
        self.pub = self.create_publisher(Fault, '/warehouse/faults', FAULTS)
        self.spawn_cli = self.create_client(SpawnEntity, f'/world/{self.layout.world_name}/create', callback_group=cb)
        self.remove_cli = self.create_client(DeleteEntity, f'/world/{self.layout.world_name}/remove', callback_group=cb)
        self.create_service(InjectFault, '/faults/inject', self._on_inject, callback_group=cb)
        self.agv_pose = None
        self.create_subscription(Odometry, '/agv/ground_truth', self._on_gt, 10, callback_group=cb)
        self.active = {}           # (type, target) -> Fault
        self.obstacles = {}        # target -> model name
        self.fault_timers = {}

    def _now(self):
        return self.get_clock().now()

    def _on_gt(self, m: Odometry) -> None:
        q = m.pose.pose.orientation
        self.agv_pose = (m.pose.pose.position.x, m.pose.pose.position.y, quaternion_to_yaw(q.x, q.y, q.z, q.w))

    def _xy(self, target: str):
        if target.upper() == 'AHEAD':
            if self.agv_pose is None:
                return None
            x, y, yaw = self.agv_pose
            return x + 1.0 * math.cos(yaw), y + 1.0 * math.sin(yaw)
        loc = self.layout.resolve(target)
        if loc:
            p = self.layout.locations[loc].access or self.layout.locations[loc].slot
            return p.x, p.y
        try:
            x, y = (float(v) for v in target.split(','))
            return x, y
        except ValueError:
            return None

    async def _blocked_path(self, target: str, active: bool) -> str:
        if active:
            xy = self._xy(target)
            if xy is None:
                return f'BLOCKED_PATH needs a location id, "x,y" or AHEAD as target, got "{target}"'
            name = f'fault_obstacle_{len(self.obstacles) + 1}'
            req = SpawnEntity.Request()
            req.entity_factory.name = name
            req.entity_factory.sdf = OBSTACLE_SDF.format(name=name)
            req.entity_factory.pose.position.x, req.entity_factory.pose.position.y = xy
            if not self.spawn_cli.service_is_ready():
                return 'could not spawn obstacle (simulation not running?)'
            r = await self.spawn_cli.call_async(req)
            if r is None or not r.success:
                return 'could not spawn obstacle'
            self.obstacles[target] = name
            return ''
        name = self.obstacles.pop(target, None)
        if name is None:
            return f'no obstacle at {target}'
        req = DeleteEntity.Request()
        req.entity.name, req.entity.type = name, Entity.MODEL
        if not self.remove_cli.service_is_ready():
            return 'could not remove obstacle (simulation not running?)'
        r = await self.remove_cli.call_async(req)
        if r is None or not r.success:
            return 'could not remove obstacle'
        return ''

    async def _on_inject(self, req, res):
        ft = req.fault_type.upper()
        if ft not in FAULT_TYPES:
            res.success, res.message = False, f'unknown fault type {req.fault_type}; one of {", ".join(FAULT_TYPES)}'
            return res
        if ft == Fault.BLOCKED_PATH:
            err = await self._blocked_path(req.target, req.active)
            if err:
                res.success, res.message = False, err
                return res
        self.publish(ft, req.active, req.target, req.value, req.duration_s)
        key = (ft, req.target)
        if req.active and ft not in ONE_SHOT:
            self.active[key] = True
            if req.duration_s > 0:
                if key in self.fault_timers:
                    self.fault_timers[key].cancel()
                self.fault_timers[key] = self.create_timer(req.duration_s, lambda k=key: self._schedule_clear(k))
        else:
            self.active.pop(key, None)
        res.success = True
        res.message = f'{ft} {"injected" if req.active else "cleared"}' + (f' at {req.target}' if req.target else '')
        return res

    def _schedule_clear(self, key):
        timer = self.fault_timers.pop(key, None)
        if timer is not None:
            timer.cancel()
        self.executor.create_task(self._auto_clear(key))

    async def _auto_clear(self, key):
        ft, target = key
        if ft == Fault.BLOCKED_PATH:
            await self._blocked_path(target, False)
        self.publish(ft, False, target, 0.0, 0.0)
        self.active.pop(key, None)

    def publish(self, ft, active, target, value, duration):
        f = Fault()
        f.stamp = self._now().to_msg()
        f.fault_type, f.active, f.target, f.value, f.duration_s = ft, active, target, float(value), float(duration)
        self.pub.publish(f)
        text = f'{ft}{" at " + target if target else ""}' + (f' (value {value:g})' if value else '') + \
               (f' for {duration:.0f} s' if active and duration > 0 else '')
        if not active:
            self.alerts.info('FAULT_CLEARED', f'Fault cleared: {text}')
        elif ft == Fault.EMERGENCY_STOP:
            self.alerts.alarm('FAULT_INJECTED', f'Fault injected: {text}')
        else:
            self.alerts.warn('FAULT_INJECTED', f'Fault injected: {text}')


def main(args=None):
    rclpy.init(args=args)
    node = FaultInjector()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


def cli(argv=None):
    """ros2 run uco_safety inject_fault TYPE [--target T] [--value V] [--duration S] [--clear]"""
    argv = rclpy.utilities.remove_ros_args(sys.argv if argv is None else argv)[1:]
    ap = argparse.ArgumentParser(description='Inject or clear a simulated fault')
    ap.add_argument('fault_type', choices=[t for t in FAULT_TYPES] + [t.lower() for t in FAULT_TYPES])
    ap.add_argument('--target', default='')
    ap.add_argument('--value', type=float, default=0.0)
    ap.add_argument('--duration', type=float, default=0.0, help='auto-clear after seconds (0 = until --clear)')
    ap.add_argument('--clear', action='store_true')
    a = ap.parse_args(argv)
    rclpy.init()
    node = rclpy.create_node('inject_fault_cli')
    c = node.create_client(InjectFault, '/faults/inject')
    if not c.wait_for_service(timeout_sec=10.0):
        print('fault_injector not running', file=sys.stderr)
        return 2
    req = InjectFault.Request()
    req.fault_type, req.active, req.target = a.fault_type.upper(), not a.clear, a.target
    req.value, req.duration_s = a.value, a.duration
    fut = c.call_async(req)
    rclpy.spin_until_future_complete(node, fut, timeout_sec=15.0)
    r = fut.result()
    print(r.message if r else 'no response')
    rclpy.shutdown()
    return 0 if r and r.success else 1


if __name__ == '__main__':
    main()
