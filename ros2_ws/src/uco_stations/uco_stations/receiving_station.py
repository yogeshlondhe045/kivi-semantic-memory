"""receiving_station: delivery arrival and the unload -> weigh -> inspect -> holding pipeline.

Service  /stations/receive_delivery (uco_interfaces/ReceiveDelivery): a truck arrives with N containers.

Each container goes through (one container per station at a time, like a real conveyor line):
  TRUCK      waiting on the truck until UNLOAD is free; registered in the WMS (ID tag scan) and
             spawned in Gazebo on the unloading station
  UNLOAD     unloading time, then conveyed to WEIGH
  WEIGH      load-cell measurement (/stations/weigh) -> WMS WEIGHED (mass, volume)
  INSPECT    quality test (/stations/inspect) -> WMS APPROVED / QUARANTINED / REJECTED
  HOLD       placed on a free holding position; storage (or quarantine) assigned and an AGV
             transport task created. Retried while storage is full.

The physical truth of each container (true fill, FFA, water, contamination) is drawn from the
supplier quality profile with a fixed seed so runs are reproducible.
"""
from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import rclpy
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import ExternalShutdownException, MultiThreadedExecutor
from rclpy.node import Node

from uco_common.alerts import AlertPublisher
from uco_common.containers import container_sdf
from uco_common.layout import HOLDING, load_layout
from uco_common.qos import LATCHED
from uco_interfaces.msg import Inventory
from uco_interfaces.srv import (AssignStorage, CreateTask, Inspect, ReceiveDelivery, RegisterContainer,
                                UpdateLocation, UpdateStatus, Weigh)

from .sim_world import SimWorld
from .station_models import PROFILES, draw_truth


@dataclass
class Item:
    key: str
    delivery: str
    supplier: str
    ctype: str
    declared_l: float
    truth: object
    cid: str = ''
    stage: str = 'TRUCK'
    since: float = 0.0
    quality: str = ''
    hold_slot: str = ''
    next_try: float = 0.0
    failures: int = 0
    done: bool = False
    log: List[str] = field(default_factory=list)


class ReceivingStation(Node):
    def __init__(self):
        super().__init__('receiving_station')
        for k, v in (('layout_file', ''), ('quality_profile', 'good'), ('seed', 2024), ('unload_time_s', 6.0),
                     ('weigh_time_s', 5.0), ('inspect_time_s', 8.0), ('station_height', 0.7),
                     ('retry_period_s', 5.0), ('spawn_in_sim', True)):
            self.declare_parameter(k, v)
        p = lambda n: self.get_parameter(n).value  # noqa: E731
        self.layout = load_layout(p('layout_file') or None)
        self.profile = PROFILES[p('quality_profile')]
        self.rng = random.Random(p('seed'))
        self.alerts = AlertPublisher(self, 'RECEIVING')
        cb = ReentrantCallbackGroup()
        self.sim = SimWorld(self, self.layout.world_name, cb) if p('spawn_in_sim') else None
        names = {'register': (RegisterContainer, '/warehouse/register_container'),
                 'status': (UpdateStatus, '/warehouse/update_status'),
                 'location': (UpdateLocation, '/warehouse/update_location'),
                 'assign': (AssignStorage, '/warehouse/assign_storage'),
                 'task': (CreateTask, '/warehouse/create_task'),
                 'weigh': (Weigh, '/stations/weigh'),
                 'inspect': (Inspect, '/stations/inspect')}
        self.cli = {k: self.create_client(t, n, callback_group=cb) for k, (t, n) in names.items()}
        self.items: List[Item] = []
        self.lock = threading.Lock()
        self.delivery_seq = 0
        self.free_holding: Optional[set] = None
        self.claimed_holding: Dict[str, str] = {}   # slot -> container (until the WMS confirms)
        self.create_subscription(Inventory, '/warehouse/inventory', self._on_inventory, LATCHED, callback_group=cb)
        self.create_service(ReceiveDelivery, '/stations/receive_delivery', self._on_delivery, callback_group=cb)
        self.worker = threading.Thread(target=self._run, daemon=True)
        self.worker.start()

    # ------------------------------------------------------------------ helpers
    def _now(self) -> float:
        return self.get_clock().now().nanoseconds * 1e-9

    def _call(self, name: str, timeout: float = 10.0, **fields):
        cli = self.cli[name]
        if not cli.wait_for_service(timeout_sec=timeout):
            return None
        req = cli.srv_type.Request()
        for k, v in fields.items():
            setattr(req, k, v)
        fut = cli.call_async(req)
        ev = threading.Event()
        fut.add_done_callback(lambda _: ev.set())
        if not ev.wait(timeout):
            return None
        return fut.result()

    def _on_inventory(self, inv: Inventory) -> None:
        free = {s.id for s in inv.slots if s.kind == HOLDING and s.enabled and not s.container_id and not s.reserved}
        with self.lock:
            for slot, cid in list(self.claimed_holding.items()):
                if any(s.id == slot and s.container_id == cid for s in inv.slots):
                    del self.claimed_holding[slot]          # WMS has it now
            self.free_holding = free - set(self.claimed_holding)

    def _on_delivery(self, req, res):
        if len(req.container_types) == 0:
            res.success, res.message = False, 'empty delivery'
            return res
        if req.declared_volumes_l and len(req.declared_volumes_l) != len(req.container_types):
            res.success, res.message = False, 'declared_volumes_l must match container_types'
            return res
        with self.lock:
            self.delivery_seq += 1
            did = f'D-{self.delivery_seq:03d}'
            for i, ctype in enumerate(req.container_types):
                if ctype not in self.layout.container_types:
                    res.success, res.message = False, f'unknown container type {ctype}'
                    return res
                declared = (req.declared_volumes_l[i] if req.declared_volumes_l else 0.0) or \
                    self.layout.container_types[ctype].nominal_volume_l * 0.9
                self.items.append(Item(key=f'{did}/{i + 1}', delivery=did, supplier=req.supplier or 'unknown',
                                       ctype=ctype, declared_l=declared,
                                       truth=draw_truth(self.profile, declared, self.rng), since=self._now()))
        res.success, res.delivery_id = True, did
        res.message = f'{did}: {len(req.container_types)} containers from {req.supplier or "unknown"} at the dock'
        self.alerts.info('DELIVERY_ARRIVED', f'Delivery {res.message}')
        return res

    def _station_busy(self, stage: str) -> bool:
        return any(i.stage == stage and not i.done for i in self.items)

    def _move(self, item: Item, location: str, z: float) -> bool:
        slot = self.layout.location(location).slot
        if self.sim is not None and not self.sim.set_pose(item.cid, slot.x, slot.y, z, 0.0):
            self.alerts.error('SIM_MOVE_FAILED', f'could not move {item.cid} to {location} in the simulation')
            return False
        r = self._call('location', container_id=item.cid, location=location)
        return bool(r and r.success)

    # ------------------------------------------------------------------ pipeline
    def _run(self) -> None:
        while rclpy.ok():
            try:
                self._step()
            except Exception as e:  # noqa: BLE001 - keep the line running, report the problem
                self.alerts.error('RECEIVING_EXCEPTION', f'receiving pipeline error: {e}', dedup=True)
            time.sleep(0.3)

    def _step(self) -> None:
        now = self._now()
        p = lambda n: self.get_parameter(n).value  # noqa: E731
        h = p('station_height') + 0.01
        with self.lock:
            items = [i for i in self.items if not i.done]
        # process from the end of the line backwards so stations free up in one pass
        for it in [i for i in items if i.stage == 'HOLD']:
            if now >= it.next_try:
                self._request_storage(it, now)
        for it in [i for i in items if i.stage == 'INSPECT']:
            if not it.quality:
                if now >= it.next_try:
                    self._inspect(it, now)
            elif now - it.since >= p('inspect_time_s'):
                self._to_holding(it, now)
        for it in [i for i in items if i.stage == 'WEIGH']:
            if now - it.since >= p('weigh_time_s') and not self._station_busy('INSPECT'):
                if self._move(it, 'INSPECT', h):
                    it.stage, it.since, it.next_try = 'INSPECT', now, now
        for it in [i for i in items if i.stage == 'UNLOAD']:
            if now - it.since >= p('unload_time_s') and not self._station_busy('WEIGH'):
                if self._move(it, 'WEIGH', h):
                    it.stage, it.since = 'WEIGH', now
                    self._weigh(it)
        trucks = [i for i in items if i.stage == 'TRUCK']
        if trucks and not self._station_busy('UNLOAD') and now >= trucks[0].next_try:
            self._unload(trucks[0], now, h)

    def _unload(self, it: Item, now: float, h: float) -> None:
        r = self._call('register', container_type=it.ctype, supplier=it.supplier, declared_volume_l=it.declared_l,
                       location='UNLOAD')
        if r is None or not r.success:
            it.failures += 1
            it.next_try = now + self.get_parameter('retry_period_s').value
            self.alerts.error('REGISTRATION_FAILED', f'Container registration failed for {it.key} '
                              f'(attempt {it.failures}): {r.message if r else "WMS not responding"}; retrying')
            return
        it.cid = r.container_id
        ctype = self.layout.container_types[it.ctype]
        if self.sim is not None:
            slot = self.layout.location('UNLOAD').slot
            sdf = container_sdf(ctype, it.cid, it.truth.volume_l, self.layout.density_kg_per_l)
            if not self.sim.spawn(it.cid, sdf, slot.x, slot.y, h, 0.0):
                self.alerts.error('SPAWN_FAILED', f'could not spawn {it.cid} in the simulation')
        it.stage, it.since = 'UNLOAD', now
        self.alerts.info('UNLOADED', f'{it.cid} ({ctype.label}, {it.declared_l:.0f} L declared) unloaded from '
                         f'delivery {it.delivery}')

    def _weigh(self, it: Item) -> None:
        ctype = self.layout.container_types[it.ctype]
        true_gross = ctype.tare_kg + it.truth.volume_l * self.layout.density_kg_per_l
        r = self._call('weigh', container_id=it.cid, container_type=it.ctype, simulated_gross_kg=true_gross)
        if r is None or not r.success:
            self.alerts.warn('WEIGH_FAILED', f'{it.cid}: weighing failed ({r.message if r else "no response"})')
            return
        self._call('status', container_id=it.cid, status='WEIGHED', weight_kg=r.gross_kg,
                   measured_volume_l=r.volume_l)

    def _inspect(self, it: Item, now: float) -> None:
        r = self._call('inspect', container_id=it.cid, simulated_ffa_percent=it.truth.ffa_percent,
                       simulated_water_percent=it.truth.water_percent, simulated_contaminated=it.truth.contaminated)
        if r is None or not r.success:
            it.next_try = now + self.get_parameter('retry_period_s').value
            self.alerts.warn('STATION_UNAVAILABLE', f'{it.cid} waiting at inspection: '
                             f'{r.message if r else "station not responding"}', dedup=True)
            return
        status = {'PASS': 'APPROVED', 'MARGINAL': 'QUARANTINED', 'FAIL': 'REJECTED'}[r.quality_status]
        u = self._call('status', container_id=it.cid, status=status, quality_status=r.quality_status)
        if u is None or not u.success:
            it.next_try = now + 2.0
            return
        it.quality, it.since = r.quality_status, now

    def _to_holding(self, it: Item, now: float) -> None:
        with self.lock:
            free = sorted(self.free_holding or [])
            if not free:
                return
            slot = free[0]
            self.free_holding.discard(slot)
            self.claimed_holding[slot] = it.cid
        if self._move(it, slot, 0.01):
            it.stage, it.hold_slot, it.since, it.next_try = 'HOLD', slot, now, now
        else:
            with self.lock:
                self.claimed_holding.pop(slot, None)

    def _request_storage(self, it: Item, now: float) -> None:
        kind = 'STORAGE' if it.quality == 'PASS' else 'QUARANTINE'
        r = self._call('assign', container_id=it.cid, kind=kind)
        if r is None or not r.success:
            it.next_try = now + self.get_parameter('retry_period_s').value
            self.alerts.warn('STORAGE_UNAVAILABLE', f'{it.cid} waiting in {it.hold_slot}: '
                             f'{r.message if r else "WMS not responding"}', dedup=True)
            return
        t = self._call('task', task_type='STORE' if kind == 'STORAGE' else 'QUARANTINE', container_id=it.cid)
        if t is None or not t.success:
            it.next_try = now + self.get_parameter('retry_period_s').value
            return
        it.done = True
        self.alerts.info('RECEIVED', f'{it.cid} received: quality {it.quality}, {kind.lower()} {r.slot_id}, '
                         f'task {t.task_id}')


def main(args=None):
    rclpy.init(args=args)
    node = ReceivingStation()
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
