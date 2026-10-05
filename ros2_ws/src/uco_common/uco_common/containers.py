"""SDF generation for used-cooking-oil containers (pure Python).

The model origin is at the bottom centre of the container so it can be placed directly on the
floor (z = 0) or on the AGV deck (z = deck height).
"""
from __future__ import annotations

from .layout import ContainerType

_DRUM_COLOR = '0.10 0.25 0.55 1'
_PALLET_COLOR = '0.62 0.47 0.28 1'
_IBC_TANK_COLOR = '0.92 0.92 0.85 0.75'
_IBC_OIL_COLOR = '0.85 0.62 0.12 1'
_IBC_CAGE_COLOR = '0.7 0.72 0.75 1'


def _box_inertia(m: float, x: float, y: float, z: float) -> str:
    ixx = m * (y * y + z * z) / 12.0
    iyy = m * (x * x + z * z) / 12.0
    izz = m * (x * x + y * y) / 12.0
    return (f'<inertia><ixx>{ixx:.4f}</ixx><iyy>{iyy:.4f}</iyy><izz>{izz:.4f}</izz>'
            f'<ixy>0</ixy><ixz>0</ixz><iyz>0</iyz></inertia>')


def _visual_box(name: str, x: float, y: float, z: float, cz: float, color: str, cx: float = 0.0,
                cy: float = 0.0) -> str:
    return (f'<visual name="{name}"><pose>{cx:.3f} {cy:.3f} {cz:.3f} 0 0 0</pose><geometry><box>'
            f'<size>{x:.3f} {y:.3f} {z:.3f}</size></box></geometry><material><ambient>{color}</ambient>'
            f'<diffuse>{color}</diffuse></material></visual>')


def container_mass(ctype: ContainerType, volume_l: float, density_kg_per_l: float) -> float:
    return ctype.tare_kg + max(0.0, volume_l) * density_kg_per_l


def container_sdf(ctype: ContainerType, model_name: str, volume_l: float,
                  density_kg_per_l: float = 0.92) -> str:
    """Return an SDF <model> string for a filled container of the given type."""
    fx, fy = ctype.footprint
    h = ctype.height
    mass = container_mass(ctype, volume_l, density_kg_per_l)
    fill = max(0.05, min(1.0, volume_l / ctype.nominal_volume_l))
    pallet_h = 0.14
    visuals = [_visual_box('pallet', fx, fy, pallet_h, pallet_h / 2, _PALLET_COLOR)]
    if ctype.id.startswith('DRUM'):
        r = min(fx, fy) * 0.36
        dh = h - pallet_h
        visuals.append(
            f'<visual name="drum"><pose>0 0 {pallet_h + dh / 2:.3f} 0 0 0</pose><geometry><cylinder>'
            f'<radius>{r:.3f}</radius><length>{dh:.3f}</length></cylinder></geometry><material>'
            f'<ambient>{_DRUM_COLOR}</ambient><diffuse>{_DRUM_COLOR}</diffuse></material></visual>')
        visuals.append(
            f'<visual name="lid"><pose>0 0 {h + 0.003:.3f} 0 0 0</pose><geometry><cylinder>'
            f'<radius>{r * 0.98:.3f}</radius><length>0.01</length></cylinder></geometry><material>'
            f'<ambient>0.8 0.8 0.8 1</ambient><diffuse>0.8 0.8 0.8 1</diffuse></material></visual>')
    else:
        th = h - pallet_h
        visuals.append(_visual_box('oil', fx * 0.9, fy * 0.9, th * fill * 0.98,
                                   pallet_h + th * fill * 0.49, _IBC_OIL_COLOR))
        visuals.append(_visual_box('tank', fx * 0.92, fy * 0.92, th, pallet_h + th / 2, _IBC_TANK_COLOR))
        # steel cage: four vertical corner bars + top frame
        for i, (sx, sy) in enumerate(((1, 1), (1, -1), (-1, 1), (-1, -1))):
            visuals.append(_visual_box(f'cage_{i}', 0.04, 0.04, th, pallet_h + th / 2, _IBC_CAGE_COLOR,
                                       cx=sx * fx * 0.48, cy=sy * fy * 0.48))
        visuals.append(_visual_box('cage_top', fx * 0.98, fy * 0.98, 0.03, h - 0.015, _IBC_CAGE_COLOR))
    collision = (f'<collision name="collision"><pose>0 0 {h / 2:.3f} 0 0 0</pose><geometry><box>'
                 f'<size>{fx:.3f} {fy:.3f} {h:.3f}</size></box></geometry><surface><friction><ode>'
                 f'<mu>0.9</mu><mu2>0.9</mu2></ode></friction></surface></collision>')
    return (f'<?xml version="1.0"?><sdf version="1.9"><model name="{model_name}"><link name="body">'
            f'<inertial><pose>0 0 {h * 0.45:.3f} 0 0 0</pose><mass>{mass:.2f}</mass>'
            f'{_box_inertia(mass, fx, fy, h)}</inertial>{collision}{"".join(visuals)}</link></model></sdf>')
