#!/usr/bin/env python3
"""Draw the warehouse layout (zones, obstacles, slots, AGV access poses) from the layout YAML.

    scripts/plot_layout.py [--out project_report/figures/warehouse_layout.png]
"""
import argparse
import math
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(ROOT, 'ros2_ws', 'src', 'uco_common'))

import matplotlib  # noqa: E402
matplotlib.use('Agg')
import matplotlib.patches as mp  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

from uco_common.layout import ZONE_RESTRICTED, load_layout  # noqa: E402

KIND_COLOR = {'STORAGE': '#3b7dd8', 'HOLDING': '#e0a020', 'QUARANTINE': '#e06a10',
              'DISPATCH': '#2ca02c', 'CHARGER': '#8a5ad0', 'PARKING': '#777777', 'STATION': '#c03030'}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--layout', default=os.path.join(ROOT, 'ros2_ws/src/uco_common/config/warehouse_layout.yaml'))
    p.add_argument('--out', default=os.path.join(ROOT, 'project_report/figures/warehouse_layout.png'))
    a = p.parse_args()
    L = load_layout(a.layout)
    W, H = L.size
    fig, ax = plt.subplots(figsize=(15, 10.5))
    ax.add_patch(mp.Rectangle((0, 0), W, H, fill=False, lw=4, ec='#444'))
    for d in L.doors:
        (a0, b0) = d.span
        if d.wall in ('west', 'east'):
            x = 0 if d.wall == 'west' else W
            ax.plot([x, x], [a0, b0], color='#3a78b0', lw=7, solid_capstyle='butt')
            ax.text(x + (0.3 if x == 0 else -0.3), (a0 + b0) / 2, d.label, rotation=90, va='center',
                    ha='left' if x == 0 else 'right', fontsize=7, color='#3a78b0')
        else:
            y = 0 if d.wall == 'south' else H
            ax.plot([a0, b0], [y, y], color='#3a78b0', lw=7, solid_capstyle='butt')
    for z in L.zones:
        r = z.rect
        hatch = '//' if z.kind == ZONE_RESTRICTED else None
        ax.add_patch(mp.Rectangle((r.xmin, r.ymin), r.size[0], r.size[1], fc=(*z.color, 0.12),
                                  ec=z.color, lw=1.5, hatch=hatch))
        label = z.name + (f'\n≤ {z.speed_limit} m/s' if z.speed_limit else '') + \
            ('\nKEEP-OUT' if z.kind == ZONE_RESTRICTED else '')
        ax.text(r.xmin + 0.2, r.ymax - 0.25, label, fontsize=7.5, va='top', color='#222', weight='bold')
    for ob in L.obstacles:
        c = ob.color
        if ob.shape == 'box':
            ax.add_patch(mp.Rectangle((ob.center[0] - ob.size[0] / 2, ob.center[1] - ob.size[1] / 2),
                                      ob.size[0], ob.size[1], fc=c, ec='k', lw=0.4,
                                      alpha=0.35 if not ob.collision else 0.9))
        else:
            ax.add_patch(mp.Circle(ob.center, ob.radius, fc=c, ec='k', lw=0.6))
    for loc in L.locations.values():
        col = KIND_COLOR.get(loc.kind, 'k')
        if loc.is_slot:
            ax.add_patch(mp.Rectangle((loc.slot.x - 0.6, loc.slot.y - 0.6), 1.2, 1.2, fc=col, alpha=0.25, ec=col))
        else:
            ax.plot(loc.slot.x, loc.slot.y, 's', color=col, ms=7)
        if loc.access is not None:
            ac = loc.access
            ax.annotate('', xy=(ac.x + 0.45 * math.cos(ac.yaw), ac.y + 0.45 * math.sin(ac.yaw)),
                        xytext=(ac.x, ac.y), arrowprops=dict(arrowstyle='->', color=col, lw=1.2))
        ax.text(loc.slot.x, loc.slot.y, loc.id, fontsize=5.2, ha='center', va='center')
    ax.set_xlim(-1.5, W + 1.5)
    ax.set_ylim(-1.5, H + 1.5)
    ax.set_aspect('equal')
    ax.set_xlabel('x [m]')
    ax.set_ylabel('y [m]')
    ax.set_title(f'{L.name} — {W:.0f} m × {H:.0f} m  ({len(L.locations_of_kind("STORAGE"))} storage positions; '
                 f'arrows = AGV access poses)')
    ax.grid(alpha=0.2)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    fig.savefig(a.out, dpi=110, bbox_inches='tight')
    print('wrote', a.out)


if __name__ == '__main__':
    main()
