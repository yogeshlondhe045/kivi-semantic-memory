import os
import sys

import pytest

SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
for pkg in ('uco_common', 'uco_wms'):
    sys.path.insert(0, os.path.join(SRC, pkg))

from uco_common.layout import load_layout  # noqa: E402
from uco_wms.store import WarehouseStore  # noqa: E402

LAYOUT = load_layout(os.path.join(SRC, 'uco_common', 'config', 'warehouse_layout.yaml'))


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def store(clock):
    return WarehouseStore(LAYOUT, ':memory:', clock=clock)


@pytest.fixture
def layout():
    return LAYOUT


def receive(store, quality='PASS', ctype='IBC_1000L', volume=900.0, hold=None):
    """Register -> weigh -> inspect -> place in a holding slot. Returns container id."""
    cid = store.register_container(ctype, 'Restaurant A', volume, 'UNLOAD')
    store.record_weight(cid, 62 + volume * 0.92, volume)
    store.record_inspection(cid, quality)
    store.place_in_slot(cid, hold or store.free_holding_slot())
    return cid


def store_container(store, cid, agv='AGV-01'):
    """Full STORE cycle; returns (task id, slot id)."""
    slot = store.assign_storage(cid)
    tid = store.create_task('STORE', cid)
    store.assign_agv(tid, agv)
    store.start_task(tid, 'TO_PICKUP')
    store.mark_picked(tid)
    store.start_task(tid, 'TO_DROPOFF')
    store.complete_task(tid)
    return tid, slot
