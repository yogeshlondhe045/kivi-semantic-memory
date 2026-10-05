import os
import random
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from uco_stations.station_models import (FAIL, MARGINAL, PASS, PROFILES, AcceptanceRules, classify,  # noqa
                                         draw_truth, measure_quality, measure_weight)

RULES = AcceptanceRules()


@pytest.mark.parametrize('ffa,water,cont,expected', [
    (2.0, 0.3, False, PASS), (5.0, 1.0, False, PASS), (5.1, 0.3, False, MARGINAL),
    (3.0, 1.5, False, MARGINAL), (15.0, 3.0, False, MARGINAL), (16.0, 0.5, False, FAIL),
    (3.0, 3.5, False, FAIL), (1.0, 0.1, True, FAIL),
])
def test_acceptance_rules(ffa, water, cont, expected):
    assert classify(ffa, water, cont, RULES) == expected


def test_weight_quantised_and_accurate():
    rng = random.Random(1)
    readings = [measure_weight(893.27, rng) for _ in range(500)]
    assert all(abs(r * 2 - round(r * 2)) < 1e-9 for r in readings)      # 0.5 kg resolution
    assert sum(readings) / len(readings) == pytest.approx(893.27, abs=0.1)
    assert max(abs(r - 893.27) for r in readings) < 1.5


def test_good_profile_mostly_passes_and_poor_mostly_not():
    rng = random.Random(7)

    def pass_rate(profile):
        n = 400
        ok = 0
        for _ in range(n):
            t = draw_truth(PROFILES[profile], 200, rng)
            ffa, water, det = measure_quality(t.ffa_percent, t.water_percent, t.contaminated, rng)
            ok += classify(ffa, water, det, RULES) == PASS
        return ok / n
    assert pass_rate('good') > 0.95
    assert pass_rate('poor') < 0.1


def test_truth_volume_within_declared():
    rng = random.Random(3)
    for _ in range(100):
        t = draw_truth(PROFILES['good'], 1000, rng)
        assert 850 <= t.volume_l <= 1000


def test_seed_reproducible():
    a = [draw_truth(PROFILES['mixed'], 200, random.Random(42)) for _ in range(3)]
    b = [draw_truth(PROFILES['mixed'], 200, random.Random(42)) for _ in range(3)]
    assert a == b
