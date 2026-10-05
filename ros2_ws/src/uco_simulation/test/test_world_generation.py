"""Generated world/map must match the layout file and be valid SDF."""
import os
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
GEN = os.path.join(PKG, 'scripts', 'generate_world.py')
WORLD = os.path.join(PKG, 'worlds', 'uco_warehouse.sdf')
SRC = os.path.dirname(PKG)
sys.path.insert(0, os.path.join(PKG, 'scripts'))
sys.path.insert(0, os.path.join(SRC, 'uco_common'))


def test_generated_files_up_to_date():
    result = subprocess.run([sys.executable, GEN, '--check'], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(shutil.which('gz') is None, reason='gz CLI not available')
def test_world_is_valid_sdf():
    result = subprocess.run(['gz', 'sdf', '-k', WORLD], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0 and 'Valid' in result.stdout, result.stdout + result.stderr


def test_map_free_at_access_poses_and_blocked_at_walls():
    import numpy as np
    import generate_world as gw
    from uco_common.layout import load_layout
    layout = load_layout(os.path.join(SRC, 'uco_common', 'config', 'warehouse_layout.yaml'))
    img = gw.occupancy(layout)
    keep = gw.keepout_mask(layout)

    def cell(x, y):
        col = int((x + layout.map_margin) / layout.map_resolution)
        row = int((y + layout.map_margin) / layout.map_resolution)
        return row, col

    for loc in layout.locations.values():
        if loc.access is not None:
            assert img[cell(loc.access.x, loc.access.y)] == 254, loc.id
            assert keep[cell(loc.access.x, loc.access.y)] == 254, loc.id
    assert img[cell(-0.02, 5.0)] == 0         # west wall inner face
    assert img[cell(-0.12, 5.0)] == 205       # wall interior -> unknown (outline-only map)
    assert img[cell(-0.02, 20.0)] == 0        # dock door (closed shutter)
    assert img[cell(31.2, 2.6 - 1.08)] == 0   # tank 1 surface
    assert img[cell(31.2, 2.6)] == 205        # tank 1 interior
    assert keep[cell(32.0, 4.0)] == 0         # tank farm keep-out
    assert np.count_nonzero(img == 0) > 1000
