"""Thin client for the bridged Gazebo world services (spawn / remove / set_pose)."""
from __future__ import annotations

import threading

from ros_gz_interfaces.msg import Entity
from ros_gz_interfaces.srv import DeleteEntity, SetEntityPose, SpawnEntity

from uco_common.layout import yaw_to_quaternion


class SimWorld:
    def __init__(self, node, world_name: str, callback_group=None):
        self.node = node
        self.spawn_cli = node.create_client(SpawnEntity, f'/world/{world_name}/create', callback_group=callback_group)
        self.remove_cli = node.create_client(DeleteEntity, f'/world/{world_name}/remove', callback_group=callback_group)
        self.pose_cli = node.create_client(SetEntityPose, f'/world/{world_name}/set_pose', callback_group=callback_group)

    @staticmethod
    def _wait(fut, timeout):
        ev = threading.Event()
        fut.add_done_callback(lambda _: ev.set())
        return ev.wait(timeout) and fut.result() is not None and bool(fut.result().success)

    def available(self) -> bool:
        return self.spawn_cli.service_is_ready() and self.pose_cli.service_is_ready()

    def spawn(self, name: str, sdf: str, x: float, y: float, z: float, yaw: float, timeout=10.0) -> bool:
        if not self.spawn_cli.wait_for_service(timeout_sec=timeout):
            return False
        req = SpawnEntity.Request()
        ef = req.entity_factory
        ef.name, ef.sdf, ef.allow_renaming = name, sdf, False
        ef.pose.position.x, ef.pose.position.y, ef.pose.position.z = x, y, z
        _, _, ef.pose.orientation.z, ef.pose.orientation.w = yaw_to_quaternion(yaw)
        return self._wait(self.spawn_cli.call_async(req), timeout)

    def remove(self, name: str, timeout=10.0) -> bool:
        if not self.remove_cli.wait_for_service(timeout_sec=timeout):
            return False
        req = DeleteEntity.Request()
        req.entity.name, req.entity.type = name, Entity.MODEL
        return self._wait(self.remove_cli.call_async(req), timeout)

    def set_pose(self, name: str, x: float, y: float, z: float, yaw: float, timeout=10.0) -> bool:
        if not self.pose_cli.wait_for_service(timeout_sec=timeout):
            return False
        req = SetEntityPose.Request()
        req.entity.name, req.entity.type = name, Entity.MODEL
        req.pose.position.x, req.pose.position.y, req.pose.position.z = x, y, z
        _, _, req.pose.orientation.z, req.pose.orientation.w = yaw_to_quaternion(yaw)
        return self._wait(self.pose_cli.call_async(req), timeout)
