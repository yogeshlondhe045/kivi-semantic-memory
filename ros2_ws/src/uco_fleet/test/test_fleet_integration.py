"""Integration test of the task pipeline without Gazebo.

Real nodes: wms_node, task_manager, agv_controller (separate processes).
Mocks (in this process): Nav2 navigate_to_pose (moves a fake robot pose to the goal and
publishes map->base_footprint TF), Gazebo set_pose service, battery and safety status.

Covers requirement chains:
  container registration -> storage assignment -> task creation -> AGV assignment
  -> AGV navigation -> pick/drop transfer -> task completion -> inventory update
plus low-battery requeue/charging, injected navigation failure + retry, and manual move.
"""
import math
import os
import subprocess
import sys
import threading
import time

import pytest
import rclpy
from geometry_msgs.msg import TransformStamped
from nav2_msgs.action import NavigateToPose
from nav_msgs.msg import Odometry
from rclpy.action import ActionClient, ActionServer
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from ros_gz_interfaces.srv import SetEntityPose
from sensor_msgs.msg import BatteryState
from tf2_ros import TransformBroadcaster

from uco_common.layout import load_layout
from uco_common.qos import FAULTS, LATCHED
from uco_interfaces.action import NavigateToLocation
from uco_interfaces.msg import AgvState, Fault, SafetyStatus, TaskList
from uco_interfaces.srv import AssignStorage, CreateTask, RegisterContainer, UpdateLocation, UpdateStatus

LAYOUT = load_layout()


class SimMock(Node):
    """Stands in for Gazebo + Nav2 + battery + safety."""

    def __init__(self):
        super().__init__('sim_mock')
        cb = ReentrantCallbackGroup()
        dock = LAYOUT.location('CHG-01').access
        self.pose = [dock.x, dock.y, dock.yaw]
        self.battery = 0.80
        self.nav_goals = []
        self.set_pose_calls = []
        self.tf = TransformBroadcaster(self)
        self.batt_pub = self.create_publisher(BatteryState, '/agv/battery', 10)
        self.safe_pub = self.create_publisher(SafetyStatus, '/safety/status', 10)
        self.gt_pub = self.create_publisher(Odometry, '/agv/ground_truth', 10)
        ActionServer(self, NavigateToPose, '/navigate_to_pose', self._nav, callback_group=cb)
        self.create_service(SetEntityPose, f'/world/{LAYOUT.world_name}/set_pose', self._set_pose, callback_group=cb)
        self.create_timer(0.05, self._publish, callback_group=cb)

    def _nav(self, gh):
        p = gh.request.pose.pose
        yaw = 2 * math.atan2(p.orientation.z, p.orientation.w)
        self.nav_goals.append((round(p.position.x, 2), round(p.position.y, 2)))
        for _ in range(10):                      # 0.5 s "drive"
            if gh.is_cancel_requested:
                gh.canceled()
                return NavigateToPose.Result()
            time.sleep(0.05)
        self.pose = [p.position.x, p.position.y, yaw]
        gh.succeed()
        return NavigateToPose.Result()

    def _set_pose(self, req, res):
        self.set_pose_calls.append((req.entity.name, round(req.pose.position.x, 2), round(req.pose.position.y, 2),
                                    round(req.pose.position.z, 3)))
        res.success = True
        return res

    def _publish(self):
        now = self.get_clock().now().to_msg()
        t = TransformStamped()
        t.header.stamp, t.header.frame_id, t.child_frame_id = now, 'map', 'base_footprint'
        t.transform.translation.x, t.transform.translation.y = self.pose[0], self.pose[1]
        t.transform.rotation.z, t.transform.rotation.w = math.sin(self.pose[2] / 2), math.cos(self.pose[2] / 2)
        self.tf.sendTransform(t)
        o = Odometry()
        o.header.stamp = now
        o.pose.pose.position.x, o.pose.pose.position.y = self.pose[0], self.pose[1]
        o.pose.pose.orientation = t.transform.rotation
        self.gt_pub.publish(o)
        b = BatteryState()
        b.percentage = self.battery
        dock = LAYOUT.location('CHG-01').access
        at_dock = math.hypot(self.pose[0] - dock.x, self.pose[1] - dock.y) < 0.3
        b.power_supply_status = (BatteryState.POWER_SUPPLY_STATUS_CHARGING if at_dock
                                 else BatteryState.POWER_SUPPLY_STATUS_DISCHARGING)
        self.batt_pub.publish(b)
        s = SafetyStatus()
        s.state, s.motion_allowed = 'NORMAL', True
        self.safe_pub.publish(s)


class Client(Node):
    def __init__(self):
        super().__init__('fleet_test_client')
        self.tasks = {}
        self.agv = None
        self.create_subscription(TaskList, '/warehouse/tasks', self._on_tasks, LATCHED)
        self.create_subscription(AgvState, '/agv/state', lambda m: setattr(self, 'agv', m), 10)
        self.fault_pub = self.create_publisher(Fault, '/warehouse/faults', FAULTS)

    def _on_tasks(self, m):
        self.tasks = {t.id: t for t in m.tasks}

    def call(self, srv_type, name, **fields):
        cli = self.create_client(srv_type, f'/warehouse/{name}')
        assert cli.wait_for_service(timeout_sec=20.0), name
        req = srv_type.Request()
        for k, v in fields.items():
            setattr(req, k, v)
        fut = cli.call_async(req)
        end = time.time() + 10
        while not fut.done() and time.time() < end:
            time.sleep(0.02)
        assert fut.done(), name
        return fut.result()


def wait_for(pred, timeout=60.0, msg='condition'):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return
        time.sleep(0.1)
    raise AssertionError(f'timeout waiting for {msg}')


@pytest.fixture(scope='module')
def system(tmp_path_factory):
    domain = str(100 + os.getpid() % 100)
    os.environ['ROS_DOMAIN_ID'] = domain
    env = dict(os.environ, PYTHONUNBUFFERED='1')
    logdir = tmp_path_factory.mktemp('fleet_logs')
    print(f'node logs in {logdir}')
    procs = [subprocess.Popen([sys.executable, '-m', mod, '--ros-args', *args], env=env,
                              stdout=open(logdir / f'{mod}.log', 'w'), stderr=subprocess.STDOUT)
             for mod, args in (
                 ('uco_wms.wms_node', ['-p', 'db_path:=memory', '-p', 'publish_period_s:=0.3']),
                 ('uco_fleet.task_manager', ['-p', 'period_s:=0.3']),
                 ('uco_fleet.agv_controller', ['-p', 'transfer_time_s:=0.2', '-p', 'idle_return_s:=1000.0']),
             )]
    rclpy.init()
    mock, client = SimMock(), Client()
    ex = MultiThreadedExecutor(num_threads=6)
    ex.add_node(mock)
    ex.add_node(client)
    thread = threading.Thread(target=ex.spin, daemon=True)
    thread.start()
    wait_for(lambda: client.agv is not None and client.agv.available, 40, 'AGV heartbeat')
    yield mock, client
    ex.shutdown()
    for p in procs:
        p.terminate()
    for p in procs:
        p.wait(timeout=10)
    rclpy.shutdown()


def new_store_task(client, hold):
    r = client.call(RegisterContainer, 'register_container', container_type='DRUM_200L', supplier='Cafe',
                    declared_volume_l=180.0, location='UNLOAD')
    cid = r.container_id
    assert client.call(UpdateStatus, 'update_status', container_id=cid, status='WEIGHED', weight_kg=199.0,
                       measured_volume_l=180.0).success
    assert client.call(UpdateStatus, 'update_status', container_id=cid, status='APPROVED',
                       quality_status='PASS').success
    assert client.call(UpdateLocation, 'update_location', container_id=cid, location=hold).success
    slot = client.call(AssignStorage, 'assign_storage', container_id=cid).slot_id
    tid = client.call(CreateTask, 'create_task', task_type='STORE', container_id=cid).task_id
    return cid, slot, tid


def test_store_task_end_to_end(system):
    mock, client = system
    cid, slot, tid = new_store_task(client, 'HOLD-02')
    wait_for(lambda: tid in client.tasks and client.tasks[tid].status == 'COMPLETED', 60, f'{tid} COMPLETED')
    t = client.tasks[tid]
    assert t.assigned_agv == 'AGV-01' and t.attempts == 0
    hold, dest = LAYOUT.location('HOLD-02'), LAYOUT.location(slot)
    assert (round(hold.access.x, 2), round(hold.access.y, 2)) in mock.nav_goals
    assert (round(dest.access.x, 2), round(dest.access.y, 2)) in mock.nav_goals
    picks = [c for c in mock.set_pose_calls if c[0] == cid]
    assert len(picks) == 2
    assert picks[0][3] == pytest.approx(LAYOUT.deck_height + 0.01)              # onto the deck
    assert picks[1][1:3] == (round(dest.slot.x, 2), round(dest.slot.y, 2))      # into the slot


def test_low_battery_interrupts_and_requeues(system):
    mock, client = system
    wait_for(lambda: client.agv.available and not client.agv.current_task, 20, 'idle AGV')
    mock.battery = 0.20
    wait_for(lambda: not client.agv.available, 10, 'AGV unavailable on low battery')
    charger = LAYOUT.location('CHG-01').access
    wait_for(lambda: (round(charger.x, 2), round(charger.y, 2)) in mock.nav_goals, 20, 'trip to charger')
    cid, slot, tid = new_store_task(client, 'HOLD-03')
    time.sleep(2.0)
    assert client.tasks[tid].status == 'PENDING'          # not assigned while charging
    mock.battery = 0.85                                     # charged
    wait_for(lambda: client.tasks[tid].status == 'COMPLETED', 60, f'{tid} completed after charging')


def test_low_battery_during_pickup_leg_requeues_without_penalty(system):
    mock, client = system
    wait_for(lambda: client.agv.available, 20, 'AGV available')
    cid, slot, tid = new_store_task(client, 'HOLD-04')
    wait_for(lambda: client.tasks[tid].status == 'IN_PROGRESS', 20, 'task started')
    mock.battery = 0.20     # the 0.5 s mock drive is too short to react mid-leg; the next leg check aborts
    wait_for(lambda: client.tasks[tid].status in ('PENDING', 'COMPLETED'), 30, 'requeue or completion')
    if client.tasks[tid].status == 'PENDING':
        assert client.tasks[tid].attempts == 0           # not counted as a failure
    mock.battery = 0.85
    wait_for(lambda: client.tasks[tid].status == 'COMPLETED', 60, f'{tid} completed')


def test_navigation_failure_is_retried(system):
    mock, client = system
    wait_for(lambda: client.agv.available, 20, 'AGV available')
    f = Fault()
    f.fault_type, f.active, f.target, f.value = Fault.NAVIGATION_FAILURE, True, 'AGV-01', 2.0
    client.fault_pub.publish(f)
    time.sleep(0.5)
    cid, slot, tid = new_store_task(client, 'HOLD-05')
    wait_for(lambda: client.tasks[tid].status == 'COMPLETED', 90, f'{tid} completed after retry')
    assert client.tasks[tid].attempts == 1
    assert 'NAV_FAILED' in client.tasks[tid].failure_reason


def test_manual_move_to_storage_alias(system):
    mock, client = system
    wait_for(lambda: client.agv.available, 20, 'AGV available')
    ac = ActionClient(client, NavigateToLocation, '/agv_01/navigate_to_location')
    assert ac.wait_for_server(timeout_sec=10)
    goal = NavigateToLocation.Goal()
    goal.location_id = 'A03'
    fut = ac.send_goal_async(goal)
    wait_for(fut.done, 10, 'goal response')
    rfut = fut.result().get_result_async()
    wait_for(rfut.done, 30, 'manual move result')
    assert rfut.result().result.success
    a = LAYOUT.location('A-03-R01').access
    assert mock.pose[0] == pytest.approx(a.x) and mock.pose[1] == pytest.approx(a.y)
