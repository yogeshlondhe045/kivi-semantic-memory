#!/usr/bin/env python3
"""Generate the Gazebo world, the Nav2 occupancy map and the keepout mask from the layout YAML.

    generate_world.py                       # regenerate files in the source tree
    generate_world.py --check               # exit 1 if committed files are out of date

Outputs (defaults, relative to the ros2_ws/src directory):
    uco_simulation/worlds/<world_name>.sdf
    uco_navigation/maps/warehouse.{pgm,yaml}
    uco_navigation/maps/keepout_mask.{pgm,yaml}
"""
from __future__ import annotations

import argparse
import filecmp
import math
import os
import sys
import tempfile
from typing import List, Tuple

import numpy as np

SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(SRC_DIR, 'uco_common'))  # allow running without a built workspace

from uco_common.layout import Layout, Obstacle, ZONE_RESTRICTED, load_layout  # noqa: E402

FLOOR_COLOR = '0.62 0.63 0.62 1'
WALL_COLOR = '0.83 0.82 0.78 1'
DOOR_COLOR = '0.25 0.45 0.65 1'
MARK_Z = 0.002          # floor marking height (visual only)
LINE_W = 0.08


# ----------------------------------------------------------------------------- SDF helpers
def _mat(rgb) -> str:
    if isinstance(rgb, str):
        c = rgb
    else:
        c = ' '.join(f'{v:.3f}' for v in rgb) + (' 1' if len(rgb) == 3 else '')
    return f'<material><ambient>{c}</ambient><diffuse>{c}</diffuse></material>'


def _box_geom(sx, sy, sz) -> str:
    return f'<geometry><box><size>{sx:.3f} {sy:.3f} {sz:.3f}</size></box></geometry>'


def _static_box_model(name, cx, cy, cz, sx, sy, sz, color, yaw=0.0, collision=True) -> str:
    col = f'<collision name="c">{_box_geom(sx, sy, sz)}</collision>' if collision else ''
    return (f'<model name="{name}"><static>true</static><pose>{cx:.3f} {cy:.3f} {cz:.3f} 0 0 {yaw:.4f}</pose>'
            f'<link name="link">{col}<visual name="v">{_box_geom(sx, sy, sz)}{_mat(color)}</visual>'
            f'</link></model>')


def _obstacle_model(ob: Obstacle) -> str:
    if ob.shape == 'box':
        return _static_box_model(ob.id, ob.center[0], ob.center[1], ob.z + ob.size[2] / 2,
                                 ob.size[0], ob.size[1], ob.size[2], ob.color, ob.yaw, ob.collision)
    geom = f'<geometry><cylinder><radius>{ob.radius:.3f}</radius><length>{ob.height:.3f}</length></cylinder></geometry>'
    col = f'<collision name="c">{geom}</collision>' if ob.collision else ''
    return (f'<model name="{ob.id}"><static>true</static><pose>{ob.center[0]:.3f} {ob.center[1]:.3f} '
            f'{ob.z + ob.height / 2:.3f} 0 0 0</pose><link name="link">{col}<visual name="v">{geom}'
            f'{_mat(ob.color)}</visual></link></model>')


def wall_segments(layout: Layout) -> Tuple[List[tuple], List[tuple]]:
    """Return (walls, doors) as boxes (name, cx, cy, sx, sy). Inner faces at 0 and size."""
    W, H = layout.size
    t = layout.wall_thickness
    walls, doors = [], []
    # (wall name, axis along which it runs, fixed coordinate of its centre line, run start, run end)
    spec = {
        'south': ('x', -t / 2, -t, W + t),
        'north': ('x', H + t / 2, -t, W + t),
        'west': ('y', -t / 2, 0.0, H),
        'east': ('y', W + t / 2, 0.0, H),
    }
    for wall, (axis, fixed, start, end) in spec.items():
        gaps = sorted((d.span, d.id) for d in layout.doors if d.wall == wall)
        cursor = start
        pieces = []
        for (a, b), door_id in gaps:
            pieces.append((cursor, a))
            doors.append((door_id, axis, fixed, a, b))
            cursor = b
        pieces.append((cursor, end))
        for i, (a, b) in enumerate(pieces):
            if b - a <= 1e-6:
                continue
            mid, length = (a + b) / 2, b - a
            if axis == 'x':
                walls.append((f'wall_{wall}_{i}', mid, fixed, length, t))
            else:
                walls.append((f'wall_{wall}_{i}', fixed, mid, t, length))
    door_boxes = []
    for door_id, axis, fixed, a, b in doors:
        mid, length = (a + b) / 2, b - a
        if axis == 'x':
            door_boxes.append((door_id.lower(), mid, fixed, length, t))
        else:
            door_boxes.append((door_id.lower(), fixed, mid, t, length))
    return walls, door_boxes


def _rect_outline(prefix, xmin, ymin, xmax, ymax, color, w=LINE_W) -> List[str]:
    cx, cy = (xmin + xmax) / 2, (ymin + ymax) / 2
    sx, sy = xmax - xmin, ymax - ymin
    parts = [(f'{prefix}_s', cx, ymin + w / 2, sx, w), (f'{prefix}_n', cx, ymax - w / 2, sx, w),
             (f'{prefix}_w', xmin + w / 2, cy, w, sy), (f'{prefix}_e', xmax - w / 2, cy, w, sy)]
    return [f'<visual name="{n}"><pose>{x:.3f} {y:.3f} {MARK_Z} 0 0 0</pose>{_box_geom(a, b, 0.002)}{_mat(color)}</visual>'
            for n, x, y, a, b in parts]


def floor_markings(layout: Layout) -> str:
    visuals = []
    for z in layout.zones:
        r = z.rect
        visuals += _rect_outline(f'zone_{z.id.lower()}', r.xmin, r.ymin, r.xmax, r.ymax, z.color, 0.12)
        if z.kind == ZONE_RESTRICTED:
            # hatched keep-out: diagonal-looking stripes approximated by parallel bars
            cx, cy = r.center
            sx, sy = r.size
            n = max(1, int(sx / 0.8))
            for i in range(n):
                x = r.xmin + (i + 0.5) * sx / n
                visuals.append(f'<visual name="hatch_{z.id.lower()}_{i}"><pose>{x:.3f} {cy:.3f} {MARK_Z} 0 0 0</pose>'
                               f'{_box_geom(0.15, sy - 0.3, 0.002)}{_mat((0.85, 0.25, 0.2))}</visual>')
    for loc in layout.slots():
        fx, fy = 1.3, 1.3
        visuals += _rect_outline(f'slot_{loc.id.lower().replace("-", "_")}', loc.slot.x - fx / 2,
                                 loc.slot.y - fy / 2, loc.slot.x + fx / 2, loc.slot.y + fy / 2,
                                 (0.95, 0.95, 0.95), 0.05)
    # charger docking pad
    chg = layout.locations.get('CHG-01')
    if chg is not None:
        visuals.append(f'<visual name="charger_pad"><pose>{chg.access.x:.3f} {chg.access.y:.3f} {MARK_Z} 0 0 0</pose>'
                       f'{_box_geom(0.9, 1.2, 0.002)}{_mat((0.45, 0.3, 0.75))}</visual>')
    return ('<model name="floor_markings"><static>true</static><link name="link">'
            + ''.join(visuals) + '</link></model>')


def truck_model(layout: Layout) -> str:
    dock = next((d for d in layout.doors if d.id == 'DOCK_DOOR'), None)
    if dock is None:
        return ''
    cy = sum(dock.span) / 2
    body = ('<visual name="cargo">' '<pose>-1.0 0 1.6 0 0 0</pose>' + _box_geom(5.0, 2.4, 2.6)
            + _mat((0.92, 0.92, 0.92)) + '</visual>'
            '<visual name="cab"><pose>2.4 0 1.2 0 0 0</pose>' + _box_geom(1.6, 2.3, 1.9)
            + _mat((0.15, 0.45, 0.25)) + '</visual>'
            '<collision name="c"><pose>0 0 1.3 0 0 0</pose>' + _box_geom(6.6, 2.4, 2.6) + '</collision>')
    # truck reversed up to the dock door, outside the west wall
    return (f'<model name="delivery_truck"><static>true</static><pose>{-4.6:.2f} {cy:.2f} 0 0 0 3.14159</pose>'
            f'<link name="link">{body}</link></model>')


def build_world_sdf(layout: Layout) -> str:
    W, H = layout.size
    walls, doors = wall_segments(layout)
    h = layout.wall_height
    models = []
    for name, cx, cy, sx, sy in walls:
        models.append(_static_box_model(name, cx, cy, h / 2, sx, sy, h, WALL_COLOR))
    for name, cx, cy, sx, sy in doors:
        models.append(_static_box_model(name, cx, cy, h * 0.4, sx, sy, h * 0.8, DOOR_COLOR))
    models += [_obstacle_model(ob) for ob in layout.obstacles]
    models.append(floor_markings(layout))
    models.append(truck_model(layout))
    lights = []
    for i, (lx, ly) in enumerate(((W * 0.2, H * 0.3), (W * 0.5, H * 0.3), (W * 0.8, H * 0.3),
                                   (W * 0.2, H * 0.75), (W * 0.5, H * 0.75), (W * 0.8, H * 0.75))):
        lights.append(f'<light type="point" name="ceiling_light_{i}"><pose>{lx:.1f} {ly:.1f} {h - 0.3:.1f} 0 0 0</pose>'
                      '<diffuse>0.55 0.55 0.52 1</diffuse><specular>0.1 0.1 0.1 1</specular>'
                      '<attenuation><range>25</range><constant>0.6</constant><linear>0.02</linear>'
                      '<quadratic>0.001</quadratic></attenuation><cast_shadows>false</cast_shadows></light>')
    floor = (f'<model name="floor"><static>true</static><link name="link">'
             f'<collision name="c"><geometry><plane><normal>0 0 1</normal><size>{W + 40} {H + 40}</size></plane></geometry>'
             f'<surface><friction><ode><mu>1.0</mu><mu2>1.0</mu2></ode></friction></surface></collision>'
             f'<visual name="concrete"><pose>{W / 2:.2f} {H / 2:.2f} 0 0 0 0</pose><geometry><plane><normal>0 0 1</normal>'
             f'<size>{W + 0.4:.2f} {H + 0.4:.2f}</size></plane></geometry>{_mat(FLOOR_COLOR)}</visual>'
             f'<visual name="yard"><pose>{W / 2:.2f} {H / 2:.2f} -0.01 0 0 0</pose><geometry><plane><normal>0 0 1</normal>'
             f'<size>{W + 40} {H + 40}</size></plane></geometry>{_mat((0.35, 0.4, 0.33))}</visual>'
             f'</link></model>')
    return f'''<?xml version="1.0"?>
<!-- GENERATED by uco_simulation/scripts/generate_world.py from {os.path.basename(layout.source_path)}.
     Do not edit by hand: change the layout YAML and regenerate. -->
<sdf version="1.9">
  <world name="{layout.world_name}">
    <physics name="default_physics" type="ignored">
      <max_step_size>0.002</max_step_size>
      <real_time_factor>1.0</real_time_factor>
      <real_time_update_rate>500</real_time_update_rate>
    </physics>
    <plugin filename="gz-sim-physics-system" name="gz::sim::systems::Physics"/>
    <plugin filename="gz-sim-user-commands-system" name="gz::sim::systems::UserCommands"/>
    <plugin filename="gz-sim-scene-broadcaster-system" name="gz::sim::systems::SceneBroadcaster"/>
    <plugin filename="gz-sim-sensors-system" name="gz::sim::systems::Sensors">
      <render_engine>ogre2</render_engine>
    </plugin>
    <plugin filename="gz-sim-imu-system" name="gz::sim::systems::Imu"/>
    <gravity>0 0 -9.81</gravity>
    <scene>
      <ambient>0.55 0.55 0.55 1</ambient>
      <background>0.7 0.78 0.85 1</background>
      <shadows>false</shadows>
      <grid>false</grid>
    </scene>
    <light type="directional" name="sun">
      <cast_shadows>false</cast_shadows>
      <pose>0 0 20 0 0 0</pose>
      <diffuse>0.7 0.7 0.68 1</diffuse>
      <specular>0.2 0.2 0.2 1</specular>
      <direction>-0.4 0.3 -0.85</direction>
    </light>
    {"".join(lights)}
    {floor}
    {"".join(models)}
  </world>
</sdf>
'''


# ----------------------------------------------------------------------------- map rasterization
class Grid:
    def __init__(self, layout: Layout):
        self.res = layout.map_resolution
        self.ox = -layout.map_margin
        self.oy = -layout.map_margin
        self.w = int(round((layout.size[0] + 2 * layout.map_margin) / self.res))
        self.h = int(round((layout.size[1] + 2 * layout.map_margin) / self.res))
        # cell-centre coordinates (row 0 = bottom of the map)
        xs = self.ox + (np.arange(self.w) + 0.5) * self.res
        ys = self.oy + (np.arange(self.h) + 0.5) * self.res
        self.X, self.Y = np.meshgrid(xs, ys)

    def rect_mask(self, cx, cy, sx, sy, yaw=0.0):
        dx, dy = self.X - cx, self.Y - cy
        c, s = math.cos(-yaw), math.sin(-yaw)
        lx, ly = c * dx - s * dy, s * dx + c * dy
        return (np.abs(lx) <= sx / 2) & (np.abs(ly) <= sy / 2)

    def circle_mask(self, cx, cy, r):
        return (self.X - cx) ** 2 + (self.Y - cy) ** 2 <= r * r


def occupancy(layout: Layout) -> np.ndarray:
    """uint8 image in map_server convention: 0 = occupied, 254 = free, 205 = unknown."""
    g = Grid(layout)
    W, H = layout.size
    img = np.full((g.h, g.w), 205, dtype=np.uint8)
    img[(g.X >= 0) & (g.X <= W) & (g.Y >= 0) & (g.Y <= H)] = 254
    walls, doors = wall_segments(layout)
    for _, cx, cy, sx, sy in walls + doors:
        img[g.rect_mask(cx, cy, sx, sy)] = 0
    for ob in layout.obstacles:
        if not ob.collision or ob.top < layout.min_obstacle_height:
            continue
        if ob.z > 0.5:   # overhead structure, robot passes underneath
            continue
        if ob.shape == 'box':
            img[g.rect_mask(ob.center[0], ob.center[1], ob.size[0], ob.size[1], ob.yaw)] = 0
        else:
            img[g.circle_mask(ob.center[0], ob.center[1], ob.radius)] = 0
    return img


def keepout_mask(layout: Layout) -> np.ndarray:
    g = Grid(layout)
    img = np.full((g.h, g.w), 254, dtype=np.uint8)
    for z in layout.zones:
        if z.kind == ZONE_RESTRICTED:
            r = z.rect
            img[(g.X >= r.xmin) & (g.X <= r.xmax) & (g.Y >= r.ymin) & (g.Y <= r.ymax)] = 0
    return img


def write_pgm(path: str, img: np.ndarray) -> None:
    flipped = np.flipud(img)  # PGM row 0 is the top of the image
    with open(path, 'wb') as f:
        f.write(f'P5\n{img.shape[1]} {img.shape[0]}\n255\n'.encode())
        f.write(flipped.tobytes())


def write_map_yaml(path: str, image_name: str, layout: Layout) -> None:
    with open(path, 'w', encoding='utf-8') as f:
        f.write(f'# GENERATED by generate_world.py - do not edit\n'
                f'image: {image_name}\nmode: trinary\nresolution: {layout.map_resolution}\n'
                f'origin: [{-layout.map_margin}, {-layout.map_margin}, 0.0]\n'
                f'negate: 0\noccupied_thresh: 0.65\nfree_thresh: 0.25\n')


def generate(layout: Layout, world_dir: str, map_dir: str) -> List[str]:
    os.makedirs(world_dir, exist_ok=True)
    os.makedirs(map_dir, exist_ok=True)
    world_path = os.path.join(world_dir, f'{layout.world_name}.sdf')
    with open(world_path, 'w', encoding='utf-8') as f:
        f.write(build_world_sdf(layout))
    write_pgm(os.path.join(map_dir, 'warehouse.pgm'), occupancy(layout))
    write_map_yaml(os.path.join(map_dir, 'warehouse.yaml'), 'warehouse.pgm', layout)
    write_pgm(os.path.join(map_dir, 'keepout_mask.pgm'), keepout_mask(layout))
    write_map_yaml(os.path.join(map_dir, 'keepout_mask.yaml'), 'keepout_mask.pgm', layout)
    return [world_path] + [os.path.join(map_dir, n) for n in
                           ('warehouse.pgm', 'warehouse.yaml', 'keepout_mask.pgm', 'keepout_mask.yaml')]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--layout', default=os.path.join(SRC_DIR, 'uco_common', 'config', 'warehouse_layout.yaml'))
    parser.add_argument('--world-dir', default=os.path.join(SRC_DIR, 'uco_simulation', 'worlds'))
    parser.add_argument('--map-dir', default=os.path.join(SRC_DIR, 'uco_navigation', 'maps'))
    parser.add_argument('--check', action='store_true', help='verify committed files are up to date')
    args = parser.parse_args()
    layout = load_layout(args.layout)
    if args.check:
        with tempfile.TemporaryDirectory() as tmp:
            produced = generate(layout, os.path.join(tmp, 'w'), os.path.join(tmp, 'm'))
            stale = []
            for p in produced:
                target_dir = args.world_dir if p.endswith('.sdf') else args.map_dir
                target = os.path.join(target_dir, os.path.basename(p))
                if not os.path.exists(target) or not filecmp.cmp(p, target, shallow=False):
                    stale.append(target)
            if stale:
                print('Out of date (run generate_world.py):\n  ' + '\n  '.join(stale))
                return 1
            print('Generated files are up to date.')
            return 0
    for p in generate(layout, args.world_dir, args.map_dir):
        print('wrote', os.path.relpath(p, SRC_DIR))
    return 0


if __name__ == '__main__':
    sys.exit(main())
