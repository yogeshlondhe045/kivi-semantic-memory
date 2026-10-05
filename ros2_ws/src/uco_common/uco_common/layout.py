"""Warehouse layout model.

Pure Python (no ROS imports) so that the world generator, the WMS core and the unit tests can
use it without a running ROS system. The layout YAML is the single source of truth for the
facility geometry; see config/warehouse_layout.yaml.
"""
from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import yaml

# Location kinds
STATION = 'STATION'
HOLDING = 'HOLDING'
STORAGE = 'STORAGE'
QUARANTINE = 'QUARANTINE'
DISPATCH = 'DISPATCH'
CHARGER = 'CHARGER'
PARKING = 'PARKING'
SLOT_KINDS = (HOLDING, STORAGE, QUARANTINE, DISPATCH)

# Zone kinds
ZONE_AREA = 'AREA'
ZONE_SPEED = 'SPEED'
ZONE_RESTRICTED = 'RESTRICTED'

DEFAULT_LAYOUT_RELPATH = os.path.join('config', 'warehouse_layout.yaml')


@dataclass(frozen=True)
class Pose2D:
    x: float
    y: float
    yaw: float  # radians

    def distance_to(self, other: 'Pose2D') -> float:
        return math.hypot(self.x - other.x, self.y - other.y)


@dataclass(frozen=True)
class Rect:
    xmin: float
    ymin: float
    xmax: float
    ymax: float

    def contains(self, x: float, y: float) -> bool:
        return self.xmin <= x <= self.xmax and self.ymin <= y <= self.ymax

    @property
    def center(self) -> Tuple[float, float]:
        return (0.5 * (self.xmin + self.xmax), 0.5 * (self.ymin + self.ymax))

    @property
    def size(self) -> Tuple[float, float]:
        return (self.xmax - self.xmin, self.ymax - self.ymin)


@dataclass(frozen=True)
class Zone:
    id: str
    name: str
    kind: str
    rect: Rect
    speed_limit: float = 0.0
    color: Tuple[float, float, float] = (0.5, 0.5, 0.5)

    @property
    def restricted(self) -> bool:
        return self.kind == ZONE_RESTRICTED


@dataclass(frozen=True)
class Location:
    id: str
    kind: str
    zone: str
    slot: Pose2D                     # where a container stands (yaw = AGV docking heading)
    access: Optional[Pose2D] = None  # where the AGV stops; None for STATION
    label: str = ''
    aisle: str = ''
    bay: int = 0
    row: str = ''

    @property
    def is_slot(self) -> bool:
        return self.kind in SLOT_KINDS


@dataclass(frozen=True)
class Obstacle:
    id: str
    shape: str                    # box | cylinder
    center: Tuple[float, float]
    size: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    radius: float = 0.0
    height: float = 0.0
    z: float = 0.0
    yaw: float = 0.0
    color: Tuple[float, float, float] = (0.5, 0.5, 0.5)
    collision: bool = True

    @property
    def top(self) -> float:
        return self.z + (self.size[2] if self.shape == 'box' else self.height)


@dataclass(frozen=True)
class Door:
    id: str
    wall: str                    # north | south | east | west
    span: Tuple[float, float]
    label: str = ''


@dataclass(frozen=True)
class ContainerType:
    id: str
    label: str
    nominal_volume_l: float
    tare_kg: float
    footprint: Tuple[float, float]
    height: float


@dataclass
class Layout:
    name: str
    world_name: str
    size: Tuple[float, float]
    wall_thickness: float
    wall_height: float
    map_resolution: float
    map_margin: float
    min_obstacle_height: float
    approach_distance: float
    deck_height: float
    default_speed_limit: float
    density_kg_per_l: float
    material_name: str
    zones: List[Zone] = field(default_factory=list)
    obstacles: List[Obstacle] = field(default_factory=list)
    doors: List[Door] = field(default_factory=list)
    locations: Dict[str, Location] = field(default_factory=dict)
    container_types: Dict[str, ContainerType] = field(default_factory=dict)
    source_path: str = ''

    # ------------------------------------------------------------------ queries
    def location(self, location_id: str) -> Location:
        resolved = self.resolve(location_id)
        if resolved is None:
            raise KeyError(f'unknown location "{location_id}"')
        return self.locations[resolved]

    def resolve(self, name: str) -> Optional[str]:
        """Resolve a location id or a short alias.

        Accepted aliases (case-insensitive): exact id ("A-03-R02"), "A03" / "A3" / "A-03"
        (row R01 of that bay), "A03R2", "charger", "home", "dispatch" (first DSP slot).
        """
        if not name:
            return None
        key = name.strip().upper()
        if key in self.locations:
            return key
        aliases = {'CHARGER': 'CHG-01', 'HOME': 'PARK-01', 'PARK': 'PARK-01',
                   'DISPATCH': 'DSP-01', 'BUFFER': 'DSP-01'}
        if key in aliases and aliases[key] in self.locations:
            return aliases[key]
        m = re.fullmatch(r'([A-Z])-?0*(\d{1,2})(?:-?R?0*(\d))?', key)
        if m:
            aisle, bay, row = m.group(1), int(m.group(2)), int(m.group(3) or 1)
            candidate = f'{aisle}-{bay:02d}-R{row:02d}'
            if candidate in self.locations:
                return candidate
        return None

    def locations_of_kind(self, kind: str) -> List[Location]:
        return [loc for loc in self.locations.values() if loc.kind == kind]

    def slots(self) -> List[Location]:
        return [loc for loc in self.locations.values() if loc.is_slot]

    def zones_at(self, x: float, y: float) -> List[Zone]:
        return [z for z in self.zones if z.rect.contains(x, y)]

    def speed_limit_at(self, x: float, y: float) -> float:
        """Lowest applicable speed limit at (x, y); default limit outside speed zones."""
        limits = [z.speed_limit for z in self.zones_at(x, y)
                  if z.kind == ZONE_SPEED and z.speed_limit > 0]
        return min(limits) if limits else self.default_speed_limit

    def restricted_zone_at(self, x: float, y: float) -> Optional[Zone]:
        for z in self.zones_at(x, y):
            if z.restricted:
                return z
        return None

    def nearest_location(self, x: float, y: float, max_distance: float = 0.6) -> Optional[str]:
        """Location whose AGV access pose is within max_distance of (x, y)."""
        best, best_d = None, max_distance
        for loc in self.locations.values():
            if loc.access is None:
                continue
            d = math.hypot(loc.access.x - x, loc.access.y - y)
            if d <= best_d:
                best, best_d = loc.id, d
        return best

    def inside_building(self, x: float, y: float) -> bool:
        return 0.0 <= x <= self.size[0] and 0.0 <= y <= self.size[1]


# ---------------------------------------------------------------------- loading
def _pose_from_slot(slot: Sequence[float], face_deg: float) -> Pose2D:
    return Pose2D(float(slot[0]), float(slot[1]), math.radians(face_deg))


def _access_pose(slot: Pose2D, approach: float) -> Pose2D:
    return Pose2D(slot.x - approach * math.cos(slot.yaw),
                  slot.y - approach * math.sin(slot.yaw), slot.yaw)


def default_layout_path() -> str:
    """Installed share path if ament_index is available, else the source tree copy."""
    try:
        from ament_index_python.packages import get_package_share_directory
        return os.path.join(get_package_share_directory('uco_common'), DEFAULT_LAYOUT_RELPATH)
    except Exception:  # noqa: BLE001 - ament not available / package not installed
        here = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(os.path.dirname(here), DEFAULT_LAYOUT_RELPATH)


def load_layout(path: Optional[str] = None) -> Layout:
    path = path or default_layout_path()
    with open(path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)
    return parse_layout(data, source_path=path)


def parse_layout(data: dict, source_path: str = '') -> Layout:
    fac = data['facility']
    approach = float(data.get('agv', {}).get('approach_distance', 1.6))
    layout = Layout(
        name=fac['name'],
        world_name=fac['world_name'],
        size=(float(fac['size'][0]), float(fac['size'][1])),
        wall_thickness=float(fac.get('wall_thickness', 0.2)),
        wall_height=float(fac.get('wall_height', 3.0)),
        map_resolution=float(data['map']['resolution']),
        map_margin=float(data['map'].get('margin', 1.0)),
        min_obstacle_height=float(data['map'].get('min_obstacle_height', 0.05)),
        approach_distance=approach,
        deck_height=float(data.get('agv', {}).get('deck_height', 0.35)),
        default_speed_limit=float(data.get('default_speed_limit', 1.0)),
        density_kg_per_l=float(data.get('material', {}).get('density_kg_per_l', 0.92)),
        material_name=data.get('material', {}).get('name', 'Used Cooking Oil'),
        source_path=source_path,
    )
    for z in data.get('zones', []):
        layout.zones.append(Zone(
            id=z['id'], name=z.get('name', z['id']), kind=z['kind'], rect=Rect(*map(float, z['rect'])),
            speed_limit=float(z.get('speed_limit', 0.0)), color=tuple(z.get('color', (0.5, 0.5, 0.5)))))
    for o in data.get('static_obstacles', []):
        layout.obstacles.append(Obstacle(
            id=o['id'], shape=o['shape'], center=(float(o['center'][0]), float(o['center'][1])),
            size=tuple(float(v) for v in o.get('size', (0, 0, 0))), radius=float(o.get('radius', 0.0)),
            height=float(o.get('height', 0.0)), z=float(o.get('z', 0.0)),
            yaw=math.radians(float(o.get('yaw', 0.0))), color=tuple(o.get('color', (0.5, 0.5, 0.5))),
            collision=bool(o.get('collision', True))))
    for d in data.get('doors', []):
        layout.doors.append(Door(id=d['id'], wall=d['wall'], span=tuple(d['span']), label=d.get('label', '')))

    def add(loc: Location):
        if loc.id in layout.locations:
            raise ValueError(f'duplicate location id {loc.id}')
        layout.locations[loc.id] = loc

    for loc in data.get('locations', []):
        slot = _pose_from_slot(loc['slot'], float(loc.get('face', 0.0)))
        if loc['kind'] == STATION:
            access = None
        elif 'access' in loc:
            a = loc['access']
            access = Pose2D(float(a[0]), float(a[1]), math.radians(float(a[2])))
        else:
            access = _access_pose(slot, float(loc.get('approach', approach)))
        add(Location(id=loc['id'], kind=loc['kind'], zone=loc.get('zone', ''), slot=slot,
                     access=access, label=loc.get('label', loc['id'])))

    for block in data.get('storage_blocks', []):
        aisle = block['aisle']
        bays = block['bays']
        for i in range(int(bays['count'])):
            x = float(bays['start_x']) + i * float(bays['pitch'])
            for row in block['rows']:
                slot = _pose_from_slot((x, row['slot_y']), float(row['face']))
                loc_id = f"{aisle}-{i + 1:02d}-{row['row']}"
                add(Location(id=loc_id, kind=STORAGE, zone='STORAGE', slot=slot,
                             access=_access_pose(slot, approach), label=loc_id,
                             aisle=aisle, bay=i + 1, row=row['row']))

    for type_id, ct in data.get('container_types', {}).items():
        layout.container_types[type_id] = ContainerType(
            id=type_id, label=ct.get('label', type_id), nominal_volume_l=float(ct['nominal_volume_l']),
            tare_kg=float(ct['tare_kg']), footprint=tuple(float(v) for v in ct['footprint']),
            height=float(ct['height']))
    return layout


def yaw_to_quaternion(yaw: float) -> Tuple[float, float, float, float]:
    """(x, y, z, w) quaternion for a rotation about z."""
    return (0.0, 0.0, math.sin(yaw / 2.0), math.cos(yaw / 2.0))


def quaternion_to_yaw(x: float, y: float, z: float, w: float) -> float:
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))
