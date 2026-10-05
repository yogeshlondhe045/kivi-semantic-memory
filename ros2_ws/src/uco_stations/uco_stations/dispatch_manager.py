"""dispatch_manager: processing requests and hand-over to the biodiesel plant.

Service  /stations/request_processing (uco_interfaces/RequestDispatch): forwards to the WMS, which
         selects approved stored containers (FIFO) and creates RETRIEVE tasks to the dispatch buffer.
Hand-over: a container that has been DISPATCHED to a buffer position for `handover_delay_s` is
         collected by the processing plant (removed from Gazebo, WMS location PROCESSING_PLANT).
"""
from __future__ import annotations

import threading
import time
from typing import Dict

import rclpy
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import ExternalShutdownException, MultiThreadedExecutor
from rclpy.node import Node

from uco_common.alerts import AlertPublisher
from uco_common.layout import load_layout
from uco_common.qos import LATCHED
from uco_interfaces.msg import Inventory
from uco_interfaces.srv import RequestDispatch, UpdateLocation

from .sim_world import SimWorld


class DispatchManager(Node):
    def __init__(self):
        super().__init__('dispatch_manager')
        for k, v in (('layout_file', ''), ('handover_delay_s', 20.0), ('spawn_in_sim', True)):
            self.declare_parameter(k, v)
        p = lambda n: self.get_parameter(n).value  # noqa: E731
        self.layout = load_layout(p('layout_file') or None)
        self.alerts = AlertPublisher(self, 'DISPATCH')
        cb = ReentrantCallbackGroup()
        self.sim = SimWorld(self, self.layout.world_name, cb) if p('spawn_in_sim') else None
        self.wms_dispatch = self.create_client(RequestDispatch, '/warehouse/request_dispatch', callback_group=cb)
        self.wms_location = self.create_client(UpdateLocation, '/warehouse/update_location', callback_group=cb)
        self.create_service(RequestDispatch, '/stations/request_processing', self._on_request, callback_group=cb)
        self.create_subscription(Inventory, '/warehouse/inventory', self._on_inventory, LATCHED, callback_group=cb)
        self.arrived: Dict[str, float] = {}
        self.handing_over = set()

    def _now(self) -> float:
        return self.get_clock().now().nanoseconds * 1e-9

    @staticmethod
    def _wait(fut, timeout=10.0):
        ev = threading.Event()
        fut.add_done_callback(lambda _: ev.set())
        return fut.result() if ev.wait(timeout) else None

    def _on_request(self, req, res):
        if not self.wms_dispatch.wait_for_service(timeout_sec=5.0):
            res.success, res.message = False, 'WMS not available'
            return res
        r = self._wait(self.wms_dispatch.call_async(req))
        if r is None:
            res.success, res.message = False, 'WMS did not answer'
            return res
        self.alerts.info('PROCESSING_REQUEST', f'Processing request for {req.count or len(req.container_ids)} '
                         f'container(s): {r.message}')
        return r

    def _on_inventory(self, inv: Inventory) -> None:
        now = self._now()
        at_buffer = {c.id for c in inv.containers if c.status == 'DISPATCHED' and c.location.startswith('DSP-')}
        for cid in at_buffer:
            self.arrived.setdefault(cid, now)
        for cid in list(self.arrived):
            if cid not in at_buffer and cid not in self.handing_over:
                del self.arrived[cid]
        for cid, t in list(self.arrived.items()):
            if now - t >= self.get_parameter('handover_delay_s').value and cid not in self.handing_over:
                self.handing_over.add(cid)
                threading.Thread(target=self._hand_over, args=(cid,), daemon=True).start()

    def _hand_over(self, cid: str) -> None:
        try:
            req = UpdateLocation.Request()
            req.container_id, req.location = cid, 'PROCESSING_PLANT'
            r = self._wait(self.wms_location.call_async(req)) if self.wms_location.wait_for_service(5.0) else None
            if r is None or not r.success:
                self.alerts.error('HANDOVER_FAILED', f'{cid}: hand-over failed ({r.message if r else "no WMS"})')
                return
            if self.sim is not None:
                self.sim.remove(cid)
            self.alerts.info('DISPATCHED', f'{cid} collected by the processing plant (transfer door)')
        finally:
            self.arrived.pop(cid, None)
            time.sleep(0.1)
            self.handing_over.discard(cid)


def main(args=None):
    rclpy.init(args=args)
    node = DispatchManager()
    ex = MultiThreadedExecutor(num_threads=4)
    ex.add_node(node)
    try:
        ex.spin()
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
