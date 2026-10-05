"""Inventory manager and container state machine (FR-WMS-01, FR-WMS-06, FR-RCV-02)."""
import pytest

from conftest import receive
from uco_wms.store import (APPROVED, InvalidTransitionError, QUARANTINED, REGISTERED, REJECTED, STORED,
                           WEIGHED, WmsError)


def test_register_generates_sequential_ids(store):
    a = store.register_container('DRUM_200L', 'R1', 180)
    b = store.register_container('IBC_1000L', 'R2', 850)
    assert (a, b) == ('UCO-0001', 'UCO-0002')
    c = store.get_container(a)
    assert c.status == REGISTERED and c.quality_status == 'PENDING'
    assert c.material == 'Used Cooking Oil' and c.location == 'UNLOAD' and c.arrival_time == 1000.0


def test_register_with_tag_and_duplicate(store):
    assert store.register_container('DRUM_200L', 'R1', 150, container_id='UCO-9001') == 'UCO-9001'
    with pytest.raises(WmsError, match='already registered'):
        store.register_container('DRUM_200L', 'R1', 150, container_id='UCO-9001')


def test_register_validation(store):
    with pytest.raises(WmsError, match='Unknown container type'):
        store.register_container('TANKER', 'R1', 100)
    with pytest.raises(WmsError, match='exceeds'):
        store.register_container('DRUM_200L', 'R1', 400)
    cid = store.register_container('DRUM_200L', 'R1', 0)          # 0 -> nominal volume
    assert store.get_container(cid).declared_volume_l == 200


def test_quality_outcomes(store):
    assert store.get_container(receive(store, 'PASS')).status == APPROVED
    assert store.get_container(receive(store, 'MARGINAL')).status == QUARANTINED
    assert store.get_container(receive(store, 'FAIL')).status == REJECTED


def test_illegal_transitions_rejected(store):
    cid = store.register_container('DRUM_200L', 'R1', 150)
    with pytest.raises(InvalidTransitionError):
        store.update_status(cid, STORED)              # cannot skip weighing/inspection/transport
    store.record_weight(cid, 170, 148)
    assert store.get_container(cid).status == WEIGHED
    with pytest.raises(InvalidTransitionError):
        store.update_status(cid, REGISTERED)          # no going back


def test_weight_and_location_updates(store):
    cid = store.register_container('IBC_1000L', 'R1', 900)
    store.record_weight(cid, 885.5, 895.1)
    store.update_location(cid, 'WEIGH')
    c = store.get_container(cid)
    assert c.weight_kg == pytest.approx(885.5) and c.measured_volume_l == pytest.approx(895.1)
    assert c.location == 'WEIGH'


def test_event_history_is_traceable(store):
    cid = receive(store)
    events = [e['event'] for e in store.history(cid)]
    assert events[:2] == ['REGISTERED', 'STATUS']
    assert 'LOCATION' not in events          # place_in_slot logs on the slot
    assert any(e['event'] == 'OCCUPIED' for e in store.history(store.get_container(cid).location))


def test_simulated_registration_failure(store):
    store.fail_next_registrations = 1
    with pytest.raises(WmsError, match='simulated'):
        store.register_container('DRUM_200L', 'R1', 150)
    assert store.register_container('DRUM_200L', 'R1', 150) == 'UCO-0001'   # next one works


def test_persistence_on_disk(tmp_path, clock, layout):
    from uco_wms.store import WarehouseStore
    path = str(tmp_path / 'wms.db')
    s1 = WarehouseStore(layout, path, clock=clock)
    cid = receive(s1)
    s1.db.close()
    s2 = WarehouseStore(layout, path, clock=clock)
    assert s2.get_container(cid).status == APPROVED
    assert s2.register_container('DRUM_200L', 'R', 100) == 'UCO-0002'   # sequence survives restart
