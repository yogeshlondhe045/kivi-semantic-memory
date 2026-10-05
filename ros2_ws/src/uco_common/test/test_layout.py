import math
import os

import pytest

from uco_common.containers import container_mass, container_sdf
from uco_common.layout import (CHARGER, DISPATCH, HOLDING, QUARANTINE, STORAGE, load_layout)

LAYOUT_PATH = os.path.join(os.path.dirname(__file__), '..', 'config', 'warehouse_layout.yaml')


@pytest.fixture(scope='module')
def layout():
    return load_layout(LAYOUT_PATH)


def test_location_counts(layout):
    assert len(layout.locations_of_kind(STORAGE)) == 32
    assert len(layout.locations_of_kind(HOLDING)) == 5
    assert len(layout.locations_of_kind(QUARANTINE)) == 3
    assert len(layout.locations_of_kind(DISPATCH)) == 4
    assert len(layout.locations_of_kind(CHARGER)) == 1


def test_storage_id_format_and_pose(layout):
    loc = layout.location('A-03-R02')
    assert loc.aisle == 'A' and loc.bay == 3 and loc.row == 'R02'
    assert loc.slot.x == pytest.approx(13.0 + 2 * 1.6)
    assert loc.slot.y == pytest.approx(7.6)
    # R02 faces south: the AGV waits north of the slot, in aisle A
    assert loc.access.y == pytest.approx(7.6 + 1.6)
    assert loc.access.yaw == pytest.approx(-math.pi / 2)


@pytest.mark.parametrize('alias,expected', [
    ('A03', 'A-03-R01'), ('a3', 'A-03-R01'), ('A-03', 'A-03-R01'), ('A03R2', 'A-03-R02'),
    ('b-08-r02', 'B-08-R02'), ('charger', 'CHG-01'), ('DSP-02', 'DSP-02'), ('home', 'PARK-01'),
])
def test_alias_resolution(layout, alias, expected):
    assert layout.resolve(alias) == expected


def test_unknown_location(layout):
    assert layout.resolve('Z-99') is None
    with pytest.raises(KeyError):
        layout.location('nowhere')


def test_access_poses_inside_building_and_not_restricted(layout):
    for loc in layout.locations.values():
        if loc.access is None:
            continue
        assert layout.inside_building(loc.access.x, loc.access.y), loc.id
        assert layout.restricted_zone_at(loc.access.x, loc.access.y) is None, loc.id


def test_access_poses_clear_of_obstacles(layout):
    """AGV footprint radius (0.61 m) at every access pose must not overlap a static obstacle."""
    radius = 0.61
    for loc in layout.locations.values():
        if loc.access is None:
            continue
        ax, ay = loc.access.x, loc.access.y
        for ob in layout.obstacles:
            if not ob.collision or ob.top < 0.05:
                continue
            if ob.shape == 'box':
                hx, hy = ob.size[0] / 2, ob.size[1] / 2
                dx = max(abs(ax - ob.center[0]) - hx, 0.0)
                dy = max(abs(ay - ob.center[1]) - hy, 0.0)
                d = math.hypot(dx, dy)
            else:
                d = math.hypot(ax - ob.center[0], ay - ob.center[1]) - ob.radius
            assert d > radius, f'{loc.id} access pose collides with {ob.id} (d={d:.2f})'


def test_slots_do_not_overlap(layout):
    slots = layout.slots()
    for i, a in enumerate(slots):
        for b in slots[i + 1:]:
            assert a.slot.distance_to(b.slot) >= 1.3, (a.id, b.id)


def test_speed_limits(layout):
    assert layout.speed_limit_at(5.0, 15.0) == pytest.approx(0.4)   # receiving
    assert layout.speed_limit_at(18.0, 9.8) == pytest.approx(0.6)   # storage aisle
    assert layout.speed_limit_at(27.0, 14.5) == pytest.approx(layout.default_speed_limit)


def test_restricted_zones(layout):
    assert layout.restricted_zone_at(32.0, 4.0).id == 'TANK_FARM'
    assert layout.restricted_zone_at(27.0, 21.0).id == 'MAINTENANCE'
    assert layout.restricted_zone_at(18.0, 9.8) is None


def test_nearest_location(layout):
    acc = layout.location('B-05-R01').access
    assert layout.nearest_location(acc.x + 0.1, acc.y - 0.1) == 'B-05-R01'
    assert layout.nearest_location(27.0, 22.0) is None


def test_container_sdf_and_mass(layout):
    ibc = layout.container_types['IBC_1000L']
    assert container_mass(ibc, 900, 0.92) == pytest.approx(62 + 828)
    sdf = container_sdf(ibc, 'UCO-0001', 900)
    assert '<model name="UCO-0001">' in sdf and '<mass>890.00</mass>' in sdf
    drum = layout.container_types['DRUM_200L']
    assert '<cylinder>' in container_sdf(drum, 'UCO-0002', 180)
