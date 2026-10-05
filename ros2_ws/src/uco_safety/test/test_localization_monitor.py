import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from uco_safety.localization_monitor import (LocalizationMonitor, add_rectangles, near_obstacle_mask,  # noqa
                                             scan_match_score)


def room():
    """10 x 10 m room, 0.05 m cells, walls on the border, origin (0, 0)."""
    g = np.zeros((200, 200), dtype=int)
    g[0, :] = g[-1, :] = g[:, 0] = g[:, -1] = 100
    return g


def fake_scan(x, y, n=360):
    """Ray-cast the square room from (x, y)."""
    ranges = []
    for i in range(n):
        a = -math.pi + i * 2 * math.pi / n
        c, s = math.cos(a), math.sin(a)
        lo, hi = 0.025, 9.975              # centres of the wall cells
        t = min(((hi - x) / c) if c > 1e-9 else ((lo - x) / c) if c < -1e-9 else 1e9,
                ((hi - y) / s) if s > 1e-9 else ((lo - y) / s) if s < -1e-9 else 1e9)
        ranges.append(t)
    return np.array(ranges), -math.pi, 2 * math.pi / n


def test_mask_dilation():
    m = near_obstacle_mask(room(), 0.05, 0.25)
    assert m[0, 100] and m[5, 100] and not m[6, 100] and not m[100, 100]


def test_correct_pose_scores_high_wrong_pose_low():
    mask = near_obstacle_mask(room(), 0.05, 0.25)
    r, amin, inc = fake_scan(3.0, 4.0)
    good = scan_match_score(r, amin, inc, 3.0, 4.0, 0.0, mask, 0, 0, 0.05, max_range=20)
    shifted = scan_match_score(r, amin, inc, 5.0, 6.5, 0.0, mask, 0, 0, 0.05, max_range=20)
    rotated = scan_match_score(r, amin, inc, 3.0, 4.0, math.radians(30), mask, 0, 0, 0.05, max_range=20)
    assert good > 0.95 and shifted < 0.3 and rotated < 0.5


def test_too_few_points():
    mask = near_obstacle_mask(room(), 0.05, 0.25)
    assert scan_match_score(np.full(100, np.inf), 0, 0.01, 1, 1, 0, mask, 0, 0, 0.05) is None


def test_monitor_persistence():
    m = LocalizationMonitor(degraded_below=0.4, lost_below=0.2, persist_s=5.0)
    m.update(0.8, 0.0)
    assert m.state(0.0) == 'OK'
    m.update(0.1, 1.0)
    assert m.state(4.0) == 'OK'            # not yet persistent
    m.update(0.1, 6.5)
    assert m.state(6.5) == 'LOST'
    m.update(0.3, 7.0)                       # between thresholds: lost timer reset, degraded continues
    assert m.state(7.0) == 'DEGRADED'
    m.update(0.9, 8.0)
    assert m.state(8.0) == 'OK'


def test_known_containers_raise_the_score():
    """A container in front of the robot (unknown to the static map) lowers the score unless the
    WMS-known container footprint is added to the mask."""
    grid = room()
    mask = near_obstacle_mask(grid, 0.05, 0.25)
    # simulate a 1.2 m container centred at (5, 5): rays from (5, 3) hitting its south face at y = 4.4
    n = 360
    ranges = []
    for i in range(n):
        a = -math.pi + i * 2 * math.pi / n
        if math.radians(70) < a < math.radians(110):      # rays that hit the 1.2 m container face
            ranges.append((4.4 - 3.0) / math.sin(a))
        else:
            ranges.append(np.inf)
    ranges = np.array(ranges)
    base = scan_match_score(ranges, -math.pi, 2 * math.pi / n, 5.0, 3.0, 0.0, mask, 0, 0, 0.05)
    with_box = add_rectangles(mask, [(4.15, 4.15, 5.85, 5.85)], 0, 0, 0.05)
    known = scan_match_score(ranges, -math.pi, 2 * math.pi / n, 5.0, 3.0, 0.0, with_box, 0, 0, 0.05)
    assert base < 0.1 and known > 0.9
