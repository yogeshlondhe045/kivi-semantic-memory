"""Storage allocation (FR-WMS-03, FR-WMS-04, FR-WMS-08)."""
import pytest

from conftest import receive, store_container
from uco_common.layout import load_layout
from uco_wms.store import STORAGE_ASSIGNED, StorageFullError, WarehouseStore, WmsError


def test_assign_reserves_nearest_free_slot(store):
    cid = receive(store, hold='HOLD-01')
    slot = store.assign_storage(cid)
    # HOLD-01 is in the north-west; nearest storage access poses are in aisle A, west end
    assert slot == 'A-01-R01'
    c = store.get_container(cid)
    assert c.status == STORAGE_ASSIGNED and c.destination == slot
    s = next(s for s in store.slots('STORAGE') if s.id == slot)
    assert s.reserved_for == cid and s.container_id == ''


def test_reserved_slot_not_given_twice(store):
    a, b = receive(store), receive(store)
    assert store.assign_storage(a) != store.assign_storage(b)


def test_slot_id_format(store):
    import re
    for s in store.slots('STORAGE'):
        assert re.fullmatch(r'[AB]-0[1-8]-R0[12]', s.id)


def test_quarantine_for_non_approved(store):
    cid = receive(store, 'MARGINAL')
    with pytest.raises(WmsError, match='not approved'):
        store.assign_storage(cid, 'STORAGE')
    assert store.assign_storage(cid).startswith('Q-')     # default kind follows quality


def test_storage_full(store):
    for s in store.slots('STORAGE'):
        store.set_slot_enabled(s.id, False)
    cid = receive(store)
    with pytest.raises(StorageFullError):
        store.assign_storage(cid)
    assert store.get_container(cid).status == 'APPROVED'   # unchanged, can retry later
    store.set_slot_enabled('B-08-R02', True)
    assert store.assign_storage(cid) == 'B-08-R02'


def test_storage_full_fault_flag(store):
    store.storage_full_fault = True
    with pytest.raises(StorageFullError):
        store.assign_storage(receive(store))


def test_occupancy_after_store_and_dispatch(store):
    cid = receive(store)
    _, slot = store_container(store, cid)
    summary = store.summary()
    assert summary['storage_occupied'] == 1 and summary['storage_occupancy'] == pytest.approx(1 / 32)
    assert next(s for s in store.slots('STORAGE') if s.id == slot).container_id == cid
    store.request_dispatch(1)
    tid = store.pending_tasks()[0].id
    store.assign_agv(tid, 'AGV-01')
    store.start_task(tid)
    store.mark_picked(tid)
    assert next(s for s in store.slots('STORAGE') if s.id == slot).container_id == ''   # freed on pick
    assert store.summary()['storage_occupied'] == 0


def test_fills_whole_warehouse_without_duplicates(store, clock):
    slots = set()
    for _ in range(32):
        cid = receive(store)
        _, slot = store_container(store, cid)
        slots.add(slot)
        clock.advance(10)
    assert len(slots) == 32
    assert store.summary()['storage_occupancy'] == pytest.approx(1.0)
    with pytest.raises(StorageFullError):
        store.assign_storage(receive(store))
    assert store.check_consistency() == []


def test_sequential_policy(layout, clock):
    s = WarehouseStore(layout, ':memory:', clock=clock, allocation_policy='sequential')
    assert s.assign_storage(receive(s)) == 'A-01-R01'
    assert s.assign_storage(receive(s)) == 'A-01-R02'
