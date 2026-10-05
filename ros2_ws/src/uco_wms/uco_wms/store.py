"""Warehouse management core: SQLite-backed inventory, storage allocation and task records.

Pure Python (no ROS). `wms_node` is a thin ROS adapter around `WarehouseStore`.

Operations (requirement FR-WMS-02):
    REGISTER_CONTAINER  register_container()
    ASSIGN_STORAGE      assign_storage()
    CREATE_TRANSPORT_TASK create_task()
    ASSIGN_AGV          assign_agv()
    UPDATE_LOCATION     update_location()
    UPDATE_STATUS       update_status()
    COMPLETE_TASK       complete_task()      (+ start_task / mark_picked / fail_task / cancel_task)
    REQUEST_DISPATCH    request_dispatch()
"""
from __future__ import annotations

import json
import math
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from uco_common.layout import DISPATCH, HOLDING, QUARANTINE, STORAGE, Layout

# ----------------------------------------------------------------------------- constants
ARRIVED = 'ARRIVED'
REGISTERED = 'REGISTERED'
WEIGHED = 'WEIGHED'
APPROVED = 'APPROVED'
QUARANTINED = 'QUARANTINED'
REJECTED = 'REJECTED'
STORAGE_ASSIGNED = 'STORAGE_ASSIGNED'
IN_TRANSIT = 'IN_TRANSIT'
STORED = 'STORED'
RESERVED = 'RESERVED'
DISPATCHED = 'DISPATCHED'

Q_PENDING, Q_PASS, Q_MARGINAL, Q_FAIL = 'PENDING', 'PASS', 'MARGINAL', 'FAIL'

# Allowed container status transitions (FR-WMS-06).
TRANSITIONS: Dict[str, Tuple[str, ...]] = {
    ARRIVED: (REGISTERED,),
    REGISTERED: (WEIGHED,),
    WEIGHED: (APPROVED, QUARANTINED, REJECTED),
    APPROVED: (STORAGE_ASSIGNED,),
    QUARANTINED: (STORAGE_ASSIGNED,),
    REJECTED: (STORAGE_ASSIGNED,),
    STORAGE_ASSIGNED: (IN_TRANSIT, APPROVED, QUARANTINED, REJECTED),   # revert = cancelled task
    IN_TRANSIT: (STORED, DISPATCHED),
    STORED: (RESERVED, STORAGE_ASSIGNED),
    RESERVED: (IN_TRANSIT, STORED),
    DISPATCHED: (),
}

T_STORE, T_RETRIEVE, T_QUARANTINE, T_MOVE = 'STORE', 'RETRIEVE', 'QUARANTINE', 'MOVE'
TASK_TYPES = (T_STORE, T_RETRIEVE, T_QUARANTINE, T_MOVE)
PENDING, ASSIGNED, IN_PROGRESS, COMPLETED, FAILED, CANCELLED = (
    'PENDING', 'ASSIGNED', 'IN_PROGRESS', 'COMPLETED', 'FAILED', 'CANCELLED')
ACTIVE_TASK_STATES = (PENDING, ASSIGNED, IN_PROGRESS)
FINAL_TASK_STATES = (COMPLETED, FAILED, CANCELLED)
PROCESSING_LOCATION = 'PROCESSING_PLANT'


class WmsError(Exception):
    """Operation rejected; message is suitable for an operator."""


class StorageFullError(WmsError):
    pass


class InvalidTransitionError(WmsError):
    pass


@dataclass
class Container:
    id: str
    container_type: str
    material: str
    supplier: str
    declared_volume_l: float
    measured_volume_l: float
    weight_kg: float
    status: str
    quality_status: str
    location: str
    destination: str
    assigned_agv: str
    arrival_time: float
    updated_time: float


@dataclass
class Slot:
    id: str
    kind: str
    zone: str
    x: float
    y: float
    yaw: float
    container_id: str
    reserved_for: str
    enabled: bool


@dataclass
class Task:
    id: str
    task_type: str
    container_id: str
    source: str
    destination: str
    status: str
    phase: str
    assigned_agv: str
    priority: int
    attempts: int
    created_time: float
    assigned_time: float
    started_time: float
    completed_time: float
    failure_reason: str
    picked: bool


_SCHEMA = """
CREATE TABLE IF NOT EXISTS containers (
  id TEXT PRIMARY KEY, container_type TEXT NOT NULL, material TEXT NOT NULL, supplier TEXT,
  declared_volume_l REAL, measured_volume_l REAL DEFAULT 0, weight_kg REAL DEFAULT 0,
  status TEXT NOT NULL, quality_status TEXT NOT NULL DEFAULT 'PENDING', location TEXT,
  destination TEXT DEFAULT '', assigned_agv TEXT DEFAULT '', arrival_time REAL, updated_time REAL);
CREATE TABLE IF NOT EXISTS slots (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, zone TEXT, x REAL, y REAL, yaw REAL,
  container_id TEXT DEFAULT '', reserved_for TEXT DEFAULT '', enabled INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY, task_type TEXT NOT NULL, container_id TEXT NOT NULL, source TEXT, destination TEXT,
  status TEXT NOT NULL, phase TEXT DEFAULT '', assigned_agv TEXT DEFAULT '', priority INTEGER DEFAULT 0,
  attempts INTEGER DEFAULT 0, created_time REAL, assigned_time REAL DEFAULT 0, started_time REAL DEFAULT 0,
  completed_time REAL DEFAULT 0, failure_reason TEXT DEFAULT '', picked INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, stamp REAL, entity TEXT, entity_id TEXT, event TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_events_entity ON events(entity_id);
"""


class WarehouseStore:
    def __init__(self, layout: Layout, path: str = ':memory:', clock: Callable[[], float] = time.time,
                 allocation_policy: str = 'nearest', max_task_attempts: int = 3):
        if allocation_policy not in ('nearest', 'sequential'):
            raise ValueError(f'unknown allocation policy {allocation_policy}')
        self.layout = layout
        self.clock = clock
        self.policy = allocation_policy
        self.max_attempts = max_task_attempts
        self._lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(_SCHEMA)
        self._sync_slots()
        # simulated faults (FR-FLT-01)
        self.fail_next_registrations = 0
        self.storage_full_fault = False

    # ------------------------------------------------------------------ setup / helpers
    def _sync_slots(self) -> None:
        with self._lock, self.db:
            for loc in self.layout.slots():
                self.db.execute('INSERT OR IGNORE INTO slots(id, kind, zone, x, y, yaw) VALUES (?,?,?,?,?,?)',
                                (loc.id, loc.kind, loc.zone, loc.slot.x, loc.slot.y, loc.slot.yaw))

    def reset(self) -> None:
        """Delete all operational data (containers, tasks, events); keep the slot table."""
        with self._lock, self.db:
            for table in ('containers', 'tasks', 'events', 'meta'):
                self.db.execute(f'DELETE FROM {table}')
            self.db.execute("UPDATE slots SET container_id='', reserved_for='', enabled=1")

    def _next_id(self, key: str, prefix: str, width: int = 4) -> str:
        row = self.db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        n = int(row['value']) + 1 if row else 1
        self.db.execute('INSERT OR REPLACE INTO meta(key, value) VALUES (?,?)', (key, str(n)))
        return f'{prefix}{n:0{width}d}'

    def _counter(self, key: str, inc: int = 0) -> int:
        row = self.db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        n = int(row['value']) if row else 0
        if inc:
            n += inc
            self.db.execute('INSERT OR REPLACE INTO meta(key, value) VALUES (?,?)', (key, str(n)))
        return n

    def _event(self, entity: str, entity_id: str, event: str, **detail) -> None:
        self.db.execute('INSERT INTO events(stamp, entity, entity_id, event, detail) VALUES (?,?,?,?,?)',
                        (self.clock(), entity, entity_id, event, json.dumps(detail, sort_keys=True)))

    def _container_row(self, cid: str) -> sqlite3.Row:
        row = self.db.execute('SELECT * FROM containers WHERE id=?', (cid,)).fetchone()
        if row is None:
            raise WmsError(f'Unknown container {cid}')
        return row

    def _task_row(self, tid: str) -> sqlite3.Row:
        row = self.db.execute('SELECT * FROM tasks WHERE id=?', (tid,)).fetchone()
        if row is None:
            raise WmsError(f'Unknown task {tid}')
        return row

    def _slot_row(self, sid: str) -> Optional[sqlite3.Row]:
        return self.db.execute('SELECT * FROM slots WHERE id=?', (sid,)).fetchone()

    def _set_status(self, cid: str, new: str, **fields) -> None:
        row = self._container_row(cid)
        old = row['status']
        if new != old and new not in TRANSITIONS.get(old, ()):
            raise InvalidTransitionError(f'{cid}: illegal status transition {old} -> {new}')
        fields['status'] = new
        fields['updated_time'] = self.clock()
        cols = ', '.join(f'{k}=?' for k in fields)
        self.db.execute(f'UPDATE containers SET {cols} WHERE id=?', (*fields.values(), cid))
        if new != old:
            self._event('container', cid, 'STATUS', old=old, new=new)

    @staticmethod
    def _quality_status_to_status(q: str) -> str:
        return {Q_PASS: APPROVED, Q_MARGINAL: QUARANTINED, Q_FAIL: REJECTED}.get(q, APPROVED)

    def _access_xy(self, location_id: str) -> Optional[Tuple[float, float]]:
        loc = self.layout.locations.get(location_id)
        if loc is None:
            return None
        p = loc.access or loc.slot
        return (p.x, p.y)

    # ------------------------------------------------------------------ containers
    def register_container(self, container_type: str, supplier: str = '', declared_volume_l: float = 0.0,
                           location: str = 'UNLOAD', container_id: str = '') -> str:
        with self._lock, self.db:
            if self.fail_next_registrations > 0:
                self.fail_next_registrations -= 1
                self._event('system', container_id or '-', 'REGISTRATION_FAILED', reason='simulated fault')
                raise WmsError('Container registration failed (simulated database/scanner fault)')
            if container_type not in self.layout.container_types:
                raise WmsError(f'Unknown container type {container_type}')
            ctype = self.layout.container_types[container_type]
            if declared_volume_l <= 0:
                declared_volume_l = ctype.nominal_volume_l
            if declared_volume_l > ctype.nominal_volume_l * 1.05:
                raise WmsError(f'Declared volume {declared_volume_l:.0f} L exceeds {container_type} capacity')
            if container_id:
                if self.db.execute('SELECT 1 FROM containers WHERE id=?', (container_id,)).fetchone():
                    raise WmsError(f'Container {container_id} already registered')
            else:
                container_id = self._next_id('container_seq', 'UCO-')
                while self.db.execute('SELECT 1 FROM containers WHERE id=?', (container_id,)).fetchone():
                    container_id = self._next_id('container_seq', 'UCO-')
            now = self.clock()
            self.db.execute(
                'INSERT INTO containers(id, container_type, material, supplier, declared_volume_l, status, '
                'quality_status, location, arrival_time, updated_time) VALUES (?,?,?,?,?,?,?,?,?,?)',
                (container_id, container_type, self.layout.material_name, supplier, declared_volume_l,
                 REGISTERED, Q_PENDING, location, now, now))
            self._counter('received_total', 1)
            self._event('container', container_id, 'REGISTERED', type=container_type, supplier=supplier,
                        declared_volume_l=declared_volume_l, location=location)
            return container_id

    def record_weight(self, cid: str, weight_kg: float, volume_l: float) -> None:
        with self._lock, self.db:
            self._set_status(cid, WEIGHED, weight_kg=weight_kg, measured_volume_l=volume_l)

    def record_inspection(self, cid: str, quality_status: str) -> str:
        if quality_status not in (Q_PASS, Q_MARGINAL, Q_FAIL):
            raise WmsError(f'Unknown quality status {quality_status}')
        new = self._quality_status_to_status(quality_status)
        with self._lock, self.db:
            self._set_status(cid, new, quality_status=quality_status)
        return new

    def update_status(self, cid: str, status: str, quality_status: str = '', weight_kg: float = 0.0,
                      measured_volume_l: float = 0.0, note: str = '') -> None:
        """Generic UPDATE_STATUS with state-machine validation."""
        fields = {}
        if quality_status:
            fields['quality_status'] = quality_status
        if weight_kg > 0:
            fields['weight_kg'] = weight_kg
        if measured_volume_l > 0:
            fields['measured_volume_l'] = measured_volume_l
        with self._lock, self.db:
            self._set_status(cid, status, **fields)
            if note:
                self._event('container', cid, 'NOTE', note=note)

    def update_location(self, cid: str, location: str) -> None:
        with self._lock, self.db:
            self._container_row(cid)
            self.db.execute('UPDATE containers SET location=?, updated_time=? WHERE id=?',
                            (location, self.clock(), cid))
            self._event('container', cid, 'LOCATION', location=location)

    def place_in_slot(self, cid: str, slot_id: str) -> None:
        """Physically put a container into a slot (used by the receiving conveyor for HOLD slots)."""
        with self._lock, self.db:
            slot = self._slot_row(slot_id)
            if slot is None:
                raise WmsError(f'Unknown slot {slot_id}')
            if slot['container_id'] and slot['container_id'] != cid:
                raise WmsError(f'Slot {slot_id} occupied by {slot["container_id"]}')
            self.db.execute("UPDATE slots SET container_id=?, reserved_for='' WHERE id=?", (cid, slot_id))
            self.db.execute('UPDATE containers SET location=?, updated_time=? WHERE id=?',
                            (slot_id, self.clock(), cid))
            self._event('slot', slot_id, 'OCCUPIED', container=cid)

    def free_holding_slot(self) -> Optional[str]:
        with self._lock:
            row = self.db.execute(
                "SELECT id FROM slots WHERE kind=? AND enabled=1 AND container_id='' AND reserved_for='' "
                'ORDER BY id LIMIT 1', (HOLDING,)).fetchone()
            return row['id'] if row else None

    def get_container(self, cid: str) -> Container:
        with self._lock:
            return Container(**dict(self._container_row(cid)))

    def containers(self, include_finished: bool = False) -> List[Container]:
        with self._lock:
            q = 'SELECT * FROM containers'
            if not include_finished:
                q += f" WHERE NOT (status='{DISPATCHED}' AND location='{PROCESSING_LOCATION}')"
            return [Container(**dict(r)) for r in self.db.execute(q + ' ORDER BY arrival_time, id')]

    def history(self, entity_id: str) -> List[dict]:
        with self._lock:
            return [dict(r) for r in self.db.execute(
                'SELECT * FROM events WHERE entity_id=? ORDER BY id', (entity_id,))]

    # ------------------------------------------------------------------ storage allocation
    def slots(self, kind: Optional[str] = None) -> List[Slot]:
        with self._lock:
            q, args = 'SELECT * FROM slots', ()
            if kind:
                q, args = q + ' WHERE kind=?', (kind,)
            return [Slot(id=r['id'], kind=r['kind'], zone=r['zone'], x=r['x'], y=r['y'], yaw=r['yaw'],
                         container_id=r['container_id'], reserved_for=r['reserved_for'], enabled=bool(r['enabled']))
                    for r in self.db.execute(q + ' ORDER BY id', args)]

    def set_slot_enabled(self, slot_id: str, enabled: bool) -> None:
        with self._lock, self.db:
            if self._slot_row(slot_id) is None:
                raise WmsError(f'Unknown slot {slot_id}')
            self.db.execute('UPDATE slots SET enabled=? WHERE id=?', (1 if enabled else 0, slot_id))
            self._event('slot', slot_id, 'ENABLED' if enabled else 'DISABLED')

    def _free_slots(self, kind: str) -> List[sqlite3.Row]:
        if kind == STORAGE and self.storage_full_fault:
            return []
        return list(self.db.execute(
            "SELECT * FROM slots WHERE kind=? AND enabled=1 AND container_id='' AND reserved_for='' ORDER BY id",
            (kind,)))

    def _choose_slot(self, kind: str, from_location: str) -> Optional[str]:
        free = self._free_slots(kind)
        if not free:
            return None
        if self.policy == 'sequential':
            return free[0]['id']
        origin = self._access_xy(from_location) or (0.0, 0.0)

        def cost(row):
            target = self._access_xy(row['id'])
            # Manhattan distance approximates travel along orthogonal aisles; id breaks ties.
            return (abs(target[0] - origin[0]) + abs(target[1] - origin[1]), row['id'])
        return min(free, key=cost)['id']

    def assign_storage(self, cid: str, kind: str = '') -> str:
        """Reserve a free slot for the container (STORAGE, or QUARANTINE for non-approved material)."""
        with self._lock, self.db:
            c = self._container_row(cid)
            if not kind:
                kind = STORAGE if c['quality_status'] == Q_PASS else QUARANTINE
            if kind not in (STORAGE, QUARANTINE):
                raise WmsError(f'Cannot assign storage of kind {kind}')
            if kind == STORAGE and c['quality_status'] != Q_PASS:
                raise WmsError(f'{cid} is not approved (quality {c["quality_status"]}); use quarantine')
            slot = self._choose_slot(kind, c['location'])
            if slot is None:
                self._event('container', cid, 'ALLOCATION_FAILED', kind=kind)
                raise StorageFullError(f'No free {kind.lower()} position available for {cid}')
            self._set_status(cid, STORAGE_ASSIGNED, destination=slot)
            self.db.execute('UPDATE slots SET reserved_for=? WHERE id=?', (cid, slot))
            self._event('container', cid, 'STORAGE_ASSIGNED', slot=slot)
            return slot

    # ------------------------------------------------------------------ tasks
    def create_task(self, task_type: str, cid: str, destination: str = '', priority: int = 0) -> str:
        if task_type not in TASK_TYPES:
            raise WmsError(f'Unknown task type {task_type}')
        with self._lock, self.db:
            c = self._container_row(cid)
            active = self.db.execute(
                f"SELECT id FROM tasks WHERE container_id=? AND status IN {ACTIVE_TASK_STATES}", (cid,)).fetchone()
            if active:
                raise WmsError(f'{cid} already has active task {active["id"]}')
            destination = destination or c['destination']
            if not destination:
                raise WmsError(f'{cid} has no destination; assign storage first')
            if destination not in self.layout.locations:
                raise WmsError(f'Unknown destination {destination}')
            expected = {T_STORE: (STORAGE_ASSIGNED,), T_QUARANTINE: (STORAGE_ASSIGNED,),
                        T_RETRIEVE: (RESERVED,), T_MOVE: (STORAGE_ASSIGNED, STORED)}[task_type]
            if c['status'] not in expected:
                raise WmsError(f'{cid} has status {c["status"]}; {task_type} needs {"/".join(expected)}')
            tid = self._next_id('task_seq', 'T-')
            self.db.execute(
                'INSERT INTO tasks(id, task_type, container_id, source, destination, status, priority, created_time) '
                'VALUES (?,?,?,?,?,?,?,?)',
                (tid, task_type, cid, c['location'], destination, PENDING, priority, self.clock()))
            self._event('task', tid, 'CREATED', type=task_type, container=cid, source=c['location'],
                        destination=destination)
            return tid

    def get_task(self, tid: str) -> Task:
        with self._lock:
            r = dict(self._task_row(tid))
            r['picked'] = bool(r['picked'])
            return Task(**r)

    def tasks(self, statuses: Optional[Iterable[str]] = None, limit_finished: int = 50) -> List[Task]:
        with self._lock:
            out = []
            rows = list(self.db.execute(
                f"SELECT * FROM tasks WHERE status IN {ACTIVE_TASK_STATES} ORDER BY priority DESC, created_time"))
            rows += list(self.db.execute(
                f"SELECT * FROM tasks WHERE status IN {FINAL_TASK_STATES} ORDER BY completed_time DESC LIMIT ?",
                (limit_finished,)))
            for r in rows:
                d = dict(r)
                d['picked'] = bool(d['picked'])
                t = Task(**d)
                if statuses is None or t.status in statuses:
                    out.append(t)
            return out

    def pending_tasks(self) -> List[Task]:
        return self.tasks(statuses=(PENDING,))

    def assign_agv(self, tid: str, agv_id: str) -> None:
        with self._lock, self.db:
            t = self._task_row(tid)
            if t['status'] != PENDING:
                raise WmsError(f'{tid} is {t["status"]}, only PENDING tasks can be assigned')
            self.db.execute('UPDATE tasks SET status=?, assigned_agv=?, assigned_time=? WHERE id=?',
                            (ASSIGNED, agv_id, self.clock(), tid))
            self.db.execute('UPDATE containers SET assigned_agv=?, updated_time=? WHERE id=?',
                            (agv_id, self.clock(), t['container_id']))
            self._event('task', tid, 'ASSIGNED', agv=agv_id)

    def unassign(self, tid: str, reason: str) -> None:
        """AGV rejected / became unavailable before starting: back to PENDING."""
        with self._lock, self.db:
            t = self._task_row(tid)
            if t['status'] != ASSIGNED:
                return
            self.db.execute("UPDATE tasks SET status=?, assigned_agv='' WHERE id=?", (PENDING, tid))
            self.db.execute("UPDATE containers SET assigned_agv='' WHERE id=?", (t['container_id'],))
            self._event('task', tid, 'UNASSIGNED', reason=reason)

    def start_task(self, tid: str, phase: str = '') -> None:
        with self._lock, self.db:
            t = self._task_row(tid)
            if t['status'] == IN_PROGRESS:
                if phase and phase != t['phase']:
                    self.db.execute('UPDATE tasks SET phase=? WHERE id=?', (phase, tid))
                    self._event('task', tid, 'PHASE', phase=phase)
                return
            if t['status'] != ASSIGNED:
                raise WmsError(f'{tid} is {t["status"]}, cannot start')
            started = t['started_time'] or self.clock()
            self.db.execute('UPDATE tasks SET status=?, phase=?, started_time=? WHERE id=?',
                            (IN_PROGRESS, phase, started, tid))
            self._event('task', tid, 'STARTED', phase=phase)

    def mark_picked(self, tid: str) -> None:
        """Container left its source slot and is on the AGV."""
        with self._lock, self.db:
            t = self._task_row(tid)
            if t['picked']:
                return
            cid, agv = t['container_id'], t['assigned_agv']
            self._set_status(cid, IN_TRANSIT, location=agv)
            self.db.execute("UPDATE slots SET container_id='' WHERE container_id=?", (cid,))
            self.db.execute('UPDATE tasks SET picked=1, source=? WHERE id=?', (agv, tid))
            self._event('task', tid, 'PICKED', container=cid, from_slot=t['source'], agv=agv)

    def complete_task(self, tid: str) -> None:
        with self._lock, self.db:
            t = self._task_row(tid)
            if t['status'] in FINAL_TASK_STATES:
                raise WmsError(f'{tid} already {t["status"]}')
            if not t['picked']:
                raise WmsError(f'{tid} cannot complete before the container was picked')
            cid, dest = t['container_id'], t['destination']
            final = DISPATCHED if t['task_type'] == T_RETRIEVE else STORED
            self._set_status(cid, final, location=dest, destination='', assigned_agv='')
            self.db.execute("UPDATE slots SET container_id=?, reserved_for='' WHERE id=?", (cid, dest))
            self.db.execute("UPDATE tasks SET status=?, phase='', completed_time=? WHERE id=?",
                            (COMPLETED, self.clock(), tid))
            self._event('task', tid, 'COMPLETED', container=cid, destination=dest)
            if final == STORED:
                self._event('slot', dest, 'OCCUPIED', container=cid)

    def fail_task(self, tid: str, reason: str) -> str:
        """Record a failed attempt. Returns the resulting task status (PENDING = will retry)."""
        with self._lock, self.db:
            t = self._task_row(tid)
            if t['status'] in FINAL_TASK_STATES:
                return t['status']
            attempts = t['attempts'] + 1
            if attempts < self.max_attempts or t['picked']:
                # A container already on an AGV must always be delivered: keep retrying (same AGV).
                agv = t['assigned_agv'] if t['picked'] else ''
                self.db.execute(
                    "UPDATE tasks SET status=?, attempts=?, failure_reason=?, assigned_agv=?, phase='' WHERE id=?",
                    (PENDING, attempts, reason, agv, tid))
                if not t['picked']:
                    self.db.execute("UPDATE containers SET assigned_agv='' WHERE id=?", (t['container_id'],))
                self._event('task', tid, 'RETRY', attempt=attempts, reason=reason)
                return PENDING
            self.db.execute("UPDATE tasks SET status=?, attempts=?, failure_reason=?, completed_time=?, phase='' "
                            'WHERE id=?', (FAILED, attempts, reason, self.clock(), tid))
            self._release_destination(t)
            self._counter('failed_total', 1)
            self._event('task', tid, 'FAILED', reason=reason, attempts=attempts)
            return FAILED

    def cancel_task(self, tid: str, reason: str = '') -> None:
        with self._lock, self.db:
            t = self._task_row(tid)
            if t['status'] in FINAL_TASK_STATES:
                raise WmsError(f'{tid} already {t["status"]}')
            if t['picked']:
                raise WmsError(f'{tid}: container {t["container_id"]} is on {t["assigned_agv"]}; '
                               'cannot cancel, it must be delivered')
            self.db.execute("UPDATE tasks SET status=?, failure_reason=?, completed_time=?, phase='' WHERE id=?",
                            (CANCELLED, reason, self.clock(), tid))
            self._release_destination(t)
            self._event('task', tid, 'CANCELLED', reason=reason)

    def _release_destination(self, t: sqlite3.Row) -> None:
        cid = t['container_id']
        c = self._container_row(cid)
        self.db.execute("UPDATE slots SET reserved_for='' WHERE reserved_for=?", (cid,))
        if c['status'] == STORAGE_ASSIGNED:
            self._set_status(cid, self._quality_status_to_status(c['quality_status']), destination='',
                             assigned_agv='')
        elif c['status'] == RESERVED:
            self._set_status(cid, STORED, destination='', assigned_agv='')

    def requeue_agv_tasks(self, agv_id: str, reason: str) -> List[str]:
        """AGV offline: tasks not yet started go back to the pool. Started ones are failed (retry)."""
        out = []
        for t in self.tasks(statuses=(ASSIGNED, IN_PROGRESS)):
            if t.assigned_agv != agv_id:
                continue
            if t.status == ASSIGNED:
                self.unassign(t.id, reason)
            elif not t.picked:
                self.fail_task(t.id, reason)
            out.append(t.id)
        return out

    # ------------------------------------------------------------------ dispatch
    def request_dispatch(self, count: int = 0, container_ids: Optional[List[str]] = None,
                         priority: int = 5) -> Tuple[List[str], List[str], str]:
        """Reserve containers for processing and create RETRIEVE tasks. Returns (containers, tasks, message)."""
        with self._lock, self.db:
            if container_ids:
                candidates = []
                for cid in container_ids:
                    c = self._container_row(cid)
                    slot = self._slot_row(c['location'])
                    if c['status'] != STORED or slot is None or slot['kind'] != STORAGE:
                        raise WmsError(f'{cid} is not in storage (status {c["status"]}, location {c["location"]})')
                    if c['quality_status'] != Q_PASS:
                        raise WmsError(f'{cid} is not approved for processing')
                    candidates.append(cid)
            else:
                rows = self.db.execute(
                    "SELECT c.id FROM containers c JOIN slots s ON s.id=c.location "
                    "WHERE c.status=? AND c.quality_status=? AND s.kind=? ORDER BY c.arrival_time, c.id",
                    (STORED, Q_PASS, STORAGE)).fetchall()
                candidates = [r['id'] for r in rows][:max(0, count)]
            chosen, tasks = [], []
            for cid in candidates:
                c = self._container_row(cid)
                slot = self._choose_slot(DISPATCH, c['location'])
                if slot is None:
                    break
                self._set_status(cid, RESERVED, destination=slot)
                self.db.execute('UPDATE slots SET reserved_for=? WHERE id=?', (cid, slot))
                self._event('container', cid, 'RESERVED_FOR_DISPATCH', slot=slot)
                tasks.append(self.create_task(T_RETRIEVE, cid, slot, priority))
                chosen.append(cid)
            wanted = len(container_ids) if container_ids else count
            if not candidates:
                msg = 'No approved containers in storage'
            elif len(chosen) < wanted:
                msg = f'{len(chosen)} of {wanted} containers scheduled (dispatch buffer full or not enough stock)'
            else:
                msg = f'{len(chosen)} containers scheduled for processing'
            return chosen, tasks, msg

    def hand_over(self, cid: str) -> None:
        """Container collected from the dispatch buffer by the processing plant."""
        with self._lock, self.db:
            c = self._container_row(cid)
            if c['status'] != DISPATCHED:
                raise WmsError(f'{cid} is {c["status"]}, not at dispatch')
            self.db.execute("UPDATE slots SET container_id='' WHERE container_id=?", (cid,))
            self.db.execute('UPDATE containers SET location=?, updated_time=? WHERE id=?',
                            (PROCESSING_LOCATION, self.clock(), cid))
            self._counter('dispatched_total', 1)
            self._event('container', cid, 'HANDED_OVER', to=PROCESSING_LOCATION)

    # ------------------------------------------------------------------ reporting
    def summary(self) -> dict:
        with self._lock:
            storage = self.slots(STORAGE)
            occupied = sum(1 for s in storage if s.container_id)
            stored_volume = sum(c.measured_volume_l or c.declared_volume_l for c in self.containers()
                                if c.location in {s.id for s in storage if s.container_id})
            counts = {r['status']: r['n'] for r in self.db.execute(
                'SELECT status, COUNT(*) n FROM tasks GROUP BY status')}
            return {
                'storage_capacity': len(storage),
                'storage_occupied': occupied,
                'storage_occupancy': occupied / len(storage) if storage else 0.0,
                'stored_volume_l': stored_volume,
                'received_total': self._counter('received_total'),
                'dispatched_total': self._counter('dispatched_total'),
                'tasks_completed': counts.get(COMPLETED, 0),
                'tasks_failed': counts.get(FAILED, 0),
                'tasks_cancelled': counts.get(CANCELLED, 0),
                'tasks_active': sum(counts.get(s, 0) for s in ACTIVE_TASK_STATES),
            }

    def check_consistency(self) -> List[str]:
        """Invariants between slots, containers and tasks. Returns a list of violations (empty = OK)."""
        problems = []
        with self._lock:
            for s in self.slots():
                if s.container_id:
                    c = self.get_container(s.container_id)
                    if c.location != s.id:
                        problems.append(f'slot {s.id} holds {c.id} but container location is {c.location}')
                if s.reserved_for:
                    c = self.get_container(s.reserved_for)
                    if c.destination != s.id:
                        problems.append(f'slot {s.id} reserved for {c.id} whose destination is {c.destination!r}')
            for c in self.containers():
                held = [s.id for s in self.slots() if s.container_id == c.id]
                if len(held) > 1:
                    problems.append(f'{c.id} occupies several slots {held}')
                if c.status in (STORED, DISPATCHED) and c.location != PROCESSING_LOCATION and held != [c.location]:
                    problems.append(f'{c.id} is {c.status} at {c.location} but slot table says {held}')
        return problems


def distance(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])
