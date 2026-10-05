"""Task lifecycle, AGV assignment, retries, cancellation, dispatch (FR-TSK-01..03, FR-WMS-07)."""
import pytest

from conftest import receive, store_container
from uco_wms.store import (ASSIGNED, CANCELLED, COMPLETED, DISPATCHED, FAILED, IN_PROGRESS, IN_TRANSIT,
                           PENDING, PROCESSING_LOCATION, RESERVED, STORED, WmsError)


def test_store_task_full_lifecycle(store, clock):
    cid = receive(store, hold='HOLD-02')
    slot = store.assign_storage(cid)
    tid = store.create_task('STORE', cid)
    t = store.get_task(tid)
    assert (t.status, t.source, t.destination) == (PENDING, 'HOLD-02', slot)
    clock.advance(5)
    store.assign_agv(tid, 'AGV-01')
    assert store.get_task(tid).status == ASSIGNED
    assert store.get_container(cid).assigned_agv == 'AGV-01'
    store.start_task(tid, 'TO_PICKUP')
    clock.advance(30)
    store.mark_picked(tid)
    c = store.get_container(cid)
    assert (c.status, c.location) == (IN_TRANSIT, 'AGV-01')
    assert next(s for s in store.slots('HOLDING') if s.id == 'HOLD-02').container_id == ''
    store.start_task(tid, 'TO_DROPOFF')
    assert store.get_task(tid).phase == 'TO_DROPOFF'
    clock.advance(60)
    store.complete_task(tid)
    t, c = store.get_task(tid), store.get_container(cid)
    assert t.status == COMPLETED and t.completed_time - t.created_time == pytest.approx(95)
    assert (c.status, c.location, c.destination, c.assigned_agv) == (STORED, slot, '', '')
    assert store.check_consistency() == []


def test_task_requires_destination_and_valid_state(store):
    cid = receive(store)
    with pytest.raises(WmsError, match='no destination'):
        store.create_task('STORE', cid)
    with pytest.raises(WmsError, match='Unknown task type'):
        store.create_task('TELEPORT', cid)
    store.assign_storage(cid)
    store.create_task('STORE', cid)
    with pytest.raises(WmsError, match='already has active task'):
        store.create_task('STORE', cid)


def test_cannot_complete_before_pick(store):
    cid = receive(store)
    store.assign_storage(cid)
    tid = store.create_task('STORE', cid)
    store.assign_agv(tid, 'AGV-01')
    store.start_task(tid)
    with pytest.raises(WmsError, match='before the container was picked'):
        store.complete_task(tid)


def test_assign_only_pending(store):
    cid = receive(store)
    store.assign_storage(cid)
    tid = store.create_task('STORE', cid)
    store.assign_agv(tid, 'AGV-01')
    with pytest.raises(WmsError, match='only PENDING'):
        store.assign_agv(tid, 'AGV-02')


def test_failure_before_pick_retries_then_fails(store):
    cid = receive(store)
    slot = store.assign_storage(cid)
    tid = store.create_task('STORE', cid)
    for attempt in (1, 2):
        store.assign_agv(tid, 'AGV-01')
        store.start_task(tid)
        assert store.fail_task(tid, 'NAV_FAILED') == PENDING
        assert store.get_task(tid).attempts == attempt
        assert store.get_task(tid).assigned_agv == ''
    store.assign_agv(tid, 'AGV-01')
    store.start_task(tid)
    assert store.fail_task(tid, 'NAV_FAILED') == FAILED          # max_task_attempts = 3
    c = store.get_container(cid)
    assert c.status == 'APPROVED' and c.destination == ''        # reservation released
    assert next(s for s in store.slots('STORAGE') if s.id == slot).reserved_for == ''
    assert store.summary()['tasks_failed'] == 1


def test_failure_after_pick_always_retries_same_agv(store):
    cid = receive(store)
    store.assign_storage(cid)
    tid = store.create_task('STORE', cid)
    store.assign_agv(tid, 'AGV-01')
    store.start_task(tid)
    store.mark_picked(tid)
    for _ in range(5):
        assert store.fail_task(tid, 'NAV_FAILED') == PENDING
        t = store.get_task(tid)
        assert t.assigned_agv == 'AGV-01' and t.source == 'AGV-01' and t.picked
        store.assign_agv(tid, 'AGV-01')
        store.start_task(tid)
    store.complete_task(tid)
    assert store.get_container(cid).status == STORED


def test_cancel_releases_reservation(store):
    cid = receive(store)
    slot = store.assign_storage(cid)
    tid = store.create_task('STORE', cid)
    store.cancel_task(tid, 'operator')
    assert store.get_task(tid).status == CANCELLED
    assert store.get_container(cid).status == 'APPROVED'
    assert next(s for s in store.slots('STORAGE') if s.id == slot).reserved_for == ''
    with pytest.raises(WmsError, match='already CANCELLED'):
        store.cancel_task(tid)


def test_cancel_refused_while_on_agv(store):
    cid = receive(store)
    store.assign_storage(cid)
    tid = store.create_task('STORE', cid)
    store.assign_agv(tid, 'AGV-01')
    store.start_task(tid)
    store.mark_picked(tid)
    with pytest.raises(WmsError, match='must be delivered'):
        store.cancel_task(tid)


def test_unassign_and_requeue_on_agv_offline(store):
    a, b = receive(store), receive(store)
    for cid in (a, b):
        store.assign_storage(cid)
    ta, tb = store.create_task('STORE', a), store.create_task('STORE', b)
    store.assign_agv(ta, 'AGV-01')
    store.assign_agv(tb, 'AGV-01')
    store.start_task(tb)
    assert sorted(store.requeue_agv_tasks('AGV-01', 'heartbeat lost')) == sorted([ta, tb])
    assert store.get_task(ta).status == PENDING and store.get_task(ta).attempts == 0
    assert store.get_task(tb).status == PENDING and store.get_task(tb).attempts == 1


def test_priority_ordering(store, clock):
    ids = []
    for prio in (0, 5, 1):
        cid = receive(store)
        store.assign_storage(cid)
        ids.append(store.create_task('STORE', cid, priority=prio))
        clock.advance(1)
    assert [t.id for t in store.pending_tasks()] == [ids[1], ids[2], ids[0]]


def test_dispatch_fifo_and_handover(store, clock):
    stored = []
    for _ in range(3):
        cid = receive(store)
        store_container(store, cid)
        stored.append(cid)
        clock.advance(100)
    q = receive(store, 'MARGINAL')                      # in quarantine: never dispatched
    store_container(store, q)
    chosen, tasks, msg = store.request_dispatch(2)
    assert chosen == stored[:2] and len(tasks) == 2, msg     # oldest first
    for cid in chosen:
        c = store.get_container(cid)
        assert c.status == RESERVED and c.destination.startswith('DSP-')
    for tid in tasks:
        store.assign_agv(tid, 'AGV-01')
        store.start_task(tid)
        store.mark_picked(tid)
        store.complete_task(tid)
    c = store.get_container(chosen[0])
    assert c.status == DISPATCHED and c.location.startswith('DSP-')
    store.hand_over(chosen[0])
    assert store.get_container(chosen[0]).location == PROCESSING_LOCATION
    assert store.summary()['dispatched_total'] == 1
    assert chosen[0] not in [x.id for x in store.containers()]   # gone from live inventory
    assert store.check_consistency() == []


def test_dispatch_limited_by_buffer(store):
    for _ in range(6):
        store_container(store, receive(store))
    chosen, tasks, msg = store.request_dispatch(6)
    assert len(chosen) == 4 and 'buffer full' in msg          # 4 dispatch slots


def test_dispatch_explicit_selection_validation(store):
    cid = receive(store)
    with pytest.raises(WmsError, match='not in storage'):
        store.request_dispatch(container_ids=[cid])
    _, _, msg = store.request_dispatch(1)
    assert 'No approved containers' in msg


def test_cancel_retrieve_returns_to_stored(store):
    cid = receive(store)
    store_container(store, cid)
    _, (tid,), _ = store.request_dispatch(1)
    store.cancel_task(tid, 'processing request withdrawn')
    c = store.get_container(cid)
    assert c.status == STORED and c.destination == ''
    assert all(s.reserved_for == '' for s in store.slots('DISPATCH'))


def test_in_progress_status_and_summary(store):
    cid = receive(store)
    store.assign_storage(cid)
    tid = store.create_task('STORE', cid)
    store.assign_agv(tid, 'AGV-01')
    store.start_task(tid, 'TO_PICKUP')
    assert store.get_task(tid).status == IN_PROGRESS
    assert store.summary()['tasks_active'] == 1


def test_unassign_in_progress_does_not_count_attempt(store):
    cid = receive(store)
    store.assign_storage(cid)
    tid = store.create_task('STORE', cid)
    store.assign_agv(tid, 'AGV-01')
    store.start_task(tid, 'TO_PICKUP')
    store.unassign(tid, 'LOW_BATTERY')
    t = store.get_task(tid)
    assert (t.status, t.attempts, t.assigned_agv) == (PENDING, 0, '')
    store.assign_agv(tid, 'AGV-01')
    store.start_task(tid)
    store.mark_picked(tid)
    with pytest.raises(WmsError, match='cannot unassign'):
        store.unassign(tid, 'LOW_BATTERY')
