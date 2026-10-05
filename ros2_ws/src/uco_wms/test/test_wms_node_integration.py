"""Integration test: wms_node services over real ROS 2 communication (no Gazebo).

Covers: container registration -> storage assignment -> task creation -> AGV assignment ->
task completion -> inventory update -> dispatch request (requirements FR-WMS-*, FR-TSK-*).
"""
import os
import subprocess
import sys
import time

import pytest
import rclpy
from rclpy.node import Node

from uco_common.qos import LATCHED
from uco_interfaces.msg import Inventory, TaskList
from uco_interfaces.srv import (AssignAgv, AssignStorage, CreateTask, RegisterContainer, RequestDispatch,
                                UpdateLocation, UpdateStatus, UpdateTask)


@pytest.fixture(scope='module')
def wms_process():
    env = dict(os.environ, ROS_DOMAIN_ID=str(60 + os.getpid() % 30))
    os.environ['ROS_DOMAIN_ID'] = env['ROS_DOMAIN_ID']
    proc = subprocess.Popen([sys.executable, '-m', 'uco_wms.wms_node', '--ros-args', '-p', 'db_path:=memory',
                             '-p', 'publish_period_s:=0.2'], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    yield proc
    proc.terminate()
    proc.wait(timeout=10)


@pytest.fixture(scope='module')
def client(wms_process):
    rclpy.init()
    node = Node('wms_test_client')
    yield node
    node.destroy_node()
    rclpy.shutdown()


def call(node, srv_type, name, **fields):
    cli = node.create_client(srv_type, f'/warehouse/{name}')
    assert cli.wait_for_service(timeout_sec=20.0), f'service {name} not available'
    req = srv_type.Request()
    for k, v in fields.items():
        setattr(req, k, v)
    fut = cli.call_async(req)
    rclpy.spin_until_future_complete(node, fut, timeout_sec=10.0)
    assert fut.done(), f'{name} timed out'
    return fut.result()


def latest(node, msg_type, topic, predicate, timeout=10.0):
    box = {}
    sub = node.create_subscription(msg_type, topic, lambda m: box.__setitem__('m', m), LATCHED)
    end = time.time() + timeout
    try:
        while time.time() < end:
            rclpy.spin_once(node, timeout_sec=0.1)
            if 'm' in box and predicate(box['m']):
                return box['m']
        raise AssertionError(f'no {topic} message satisfying predicate')
    finally:
        node.destroy_subscription(sub)


def test_registration_to_storage_to_dispatch(client):
    r = call(client, RegisterContainer, 'register_container', container_type='IBC_1000L', supplier='Bistro 7',
             declared_volume_l=900.0, location='UNLOAD')
    assert r.success and r.container_id == 'UCO-0001', r.message
    cid = r.container_id
    assert call(client, UpdateStatus, 'update_status', container_id=cid, status='WEIGHED', weight_kg=880.0,
                measured_volume_l=889.0).success
    r = call(client, UpdateStatus, 'update_status', container_id=cid, status='APPROVED', quality_status='PASS')
    assert r.success and r.message.endswith('APPROVED')
    assert call(client, UpdateLocation, 'update_location', container_id=cid, location='HOLD-01').success

    r = call(client, AssignStorage, 'assign_storage', container_id=cid)
    assert r.success and r.slot_id == 'A-01-R01'
    r = call(client, CreateTask, 'create_task', task_type='STORE', container_id=cid)
    assert r.success and r.task_id == 'T-0001'
    tid = r.task_id
    assert call(client, AssignAgv, 'assign_agv', task_id=tid, agv_id='AGV-01').success
    tasks = latest(client, TaskList, '/warehouse/tasks',
                   lambda m: any(t.id == tid and t.status == 'ASSIGNED' for t in m.tasks))
    assert tasks.tasks[0].assigned_agv == 'AGV-01'

    for status, phase in (('IN_PROGRESS', 'TO_PICKUP'), ('PICKED', ''), ('IN_PROGRESS', 'TO_DROPOFF'),
                          ('COMPLETED', '')):
        r = call(client, UpdateTask, 'update_task', task_id=tid, status=status, phase=phase)
        assert r.success, (status, r.message)

    inv = latest(client, Inventory, '/warehouse/inventory', lambda m: m.storage_occupied == 1)
    c = next(c for c in inv.containers if c.id == cid)
    assert (c.status, c.location) == ('STORED', 'A-01-R01')
    assert next(s for s in inv.slots if s.id == 'A-01-R01').container_id == cid
    assert inv.storage_capacity == 32 and inv.containers_received_total == 1

    r = call(client, RequestDispatch, 'request_dispatch', count=1)
    assert r.success and list(r.container_ids) == [cid] and len(r.task_ids) == 1


def test_errors_are_reported_not_raised(client):
    r = call(client, AssignStorage, 'assign_storage', container_id='UCO-9999')
    assert not r.success and 'Unknown container' in r.message
    r = call(client, UpdateTask, 'update_task', task_id='T-0001', status='BOGUS')
    assert not r.success
