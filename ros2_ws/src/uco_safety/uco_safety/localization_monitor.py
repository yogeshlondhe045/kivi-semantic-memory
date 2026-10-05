"""Localisation quality monitor (pure Python + numpy).

AMCL can be confidently wrong (e.g. after the robot was pushed: "kidnapped robot"), in which case
its covariance stays small. A model-free check is to project the lidar endpoints with the
estimated pose into the static map and count how many land on (or next to) a mapped obstacle.
A well-localised robot in this warehouse scores ~0.6-0.95; unmapped objects (stored containers,
pallets, people) lower it, so only a *sustained* low score is reported.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np


def near_obstacle_mask(occupancy: np.ndarray, resolution: float, tolerance_m: float) -> np.ndarray:
    """Boolean mask of cells within tolerance_m of an occupied cell (square dilation)."""
    occ = occupancy >= 65            # nav_msgs/OccupancyGrid: 100 occupied, -1 unknown, 0 free
    r = max(1, int(round(tolerance_m / resolution)))
    pad = np.pad(occ, r).astype(np.int32)
    cs = np.pad(np.cumsum(np.cumsum(pad, 0), 1), ((1, 0), (1, 0)))
    h, w = occ.shape
    k = 2 * r + 1
    window = cs[k:k + h, k:k + w] - cs[0:h, k:k + w] - cs[k:k + h, 0:w] + cs[0:h, 0:w]
    return window > 0


def add_rectangles(mask: np.ndarray, rects, origin_x: float, origin_y: float, resolution: float) -> np.ndarray:
    """Return a copy of mask with axis-aligned rectangles (xmin, ymin, xmax, ymax) set.

    Used to add the containers the WMS knows about (occupied slots) to the expected-obstacle mask:
    the digital twin's best knowledge of what the lidar should see beyond the static map.
    """
    out = mask.copy()
    h, w = out.shape
    for xmin, ymin, xmax, ymax in rects:
        c0 = max(0, int(np.floor((xmin - origin_x) / resolution)))
        c1 = min(w, int(np.ceil((xmax - origin_x) / resolution)))
        r0 = max(0, int(np.floor((ymin - origin_y) / resolution)))
        r1 = min(h, int(np.ceil((ymax - origin_y) / resolution)))
        if c0 < c1 and r0 < r1:
            out[r0:r1, c0:c1] = True
    return out


def scan_match_score(ranges: np.ndarray, angle_min: float, angle_increment: float, sensor_x: float,
                     sensor_y: float, sensor_yaw: float, mask: np.ndarray, origin_x: float, origin_y: float,
                     resolution: float, max_range: float = 8.0) -> Optional[float]:
    """Fraction of valid lidar endpoints that fall on the near-obstacle mask (None if too few)."""
    r = np.asarray(ranges, dtype=float)
    ang = sensor_yaw + angle_min + np.arange(r.size) * angle_increment
    ok = np.isfinite(r) & (r > 0.1) & (r < max_range)
    if np.count_nonzero(ok) < 20:
        return None
    x = sensor_x + r[ok] * np.cos(ang[ok])
    y = sensor_y + r[ok] * np.sin(ang[ok])
    col = np.floor((x - origin_x) / resolution).astype(int)
    row = np.floor((y - origin_y) / resolution).astype(int)
    inside = (col >= 0) & (col < mask.shape[1]) & (row >= 0) & (row < mask.shape[0])
    hits = np.zeros(col.size, dtype=bool)
    hits[inside] = mask[row[inside], col[inside]]
    return float(np.count_nonzero(hits)) / float(col.size)


class LocalizationMonitor:
    """Turns a stream of scores into DEGRADED / LOST states with persistence (time-based)."""

    def __init__(self, degraded_below: float = 0.4, lost_below: float = 0.2, persist_s: float = 5.0):
        self.degraded_below = degraded_below
        self.lost_below = lost_below
        self.persist_s = persist_s
        self.score: Optional[float] = None
        self._low_since: Optional[float] = None
        self._lost_since: Optional[float] = None

    def update(self, score: Optional[float], now: float) -> None:
        if score is None:
            return
        self.score = score
        if score < self.degraded_below:
            self._low_since = now if self._low_since is None else self._low_since
        else:
            self._low_since = None
        if score < self.lost_below:
            self._lost_since = now if self._lost_since is None else self._lost_since
        else:
            self._lost_since = None

    def state(self, now: float) -> str:
        if self._lost_since is not None and now - self._lost_since >= self.persist_s:
            return 'LOST'
        if self._low_since is not None and now - self._low_since >= self.persist_s:
            return 'DEGRADED'
        return 'OK'
