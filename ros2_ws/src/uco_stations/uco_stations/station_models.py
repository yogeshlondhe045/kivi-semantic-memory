"""Physical truth and measurement models of the receiving stations (pure Python).

The simulator ("physical world") draws the true content of every delivered container from a
supplier quality profile. Stations then *measure* that truth with realistic sensor error and apply
the plant's acceptance rules, exactly as a real load cell and lab test would.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Dict

PASS, MARGINAL, FAIL = 'PASS', 'MARGINAL', 'FAIL'


@dataclass
class QualityProfile:
    ffa_mean: float          # free fatty acids, %
    ffa_sd: float
    water_mean: float        # water content, %
    water_sd: float
    contamination_p: float   # probability of foreign material (plastic, food residue)
    fill_min: float = 0.85   # true fill as fraction of the declared volume
    fill_max: float = 1.0


PROFILES: Dict[str, QualityProfile] = {
    'good': QualityProfile(ffa_mean=2.8, ffa_sd=0.7, water_mean=0.35, water_sd=0.12, contamination_p=0.0),
    'mixed': QualityProfile(ffa_mean=5.5, ffa_sd=4.0, water_mean=0.9, water_sd=0.8, contamination_p=0.1),
    'poor': QualityProfile(ffa_mean=14.0, ffa_sd=5.0, water_mean=2.5, water_sd=1.0, contamination_p=0.3),
}


@dataclass
class SampleTruth:
    volume_l: float
    ffa_percent: float
    water_percent: float
    contaminated: bool


@dataclass
class AcceptanceRules:
    pass_max_ffa: float = 5.0
    pass_max_water: float = 1.0
    marginal_max_ffa: float = 15.0
    marginal_max_water: float = 3.0


def draw_truth(profile: QualityProfile, declared_volume_l: float, rng: random.Random) -> SampleTruth:
    vol = declared_volume_l * rng.uniform(profile.fill_min, profile.fill_max)
    return SampleTruth(volume_l=vol,
                       ffa_percent=max(0.1, rng.gauss(profile.ffa_mean, profile.ffa_sd)),
                       water_percent=max(0.0, rng.gauss(profile.water_mean, profile.water_sd)),
                       contaminated=rng.random() < profile.contamination_p)


def measure_weight(true_gross_kg: float, rng: random.Random, noise_sd: float = 0.3,
                   resolution: float = 0.5, offset: float = 0.0) -> float:
    """Load cell: gaussian noise + calibration offset, quantised to the display resolution."""
    raw = true_gross_kg + offset + rng.gauss(0.0, noise_sd)
    return round(raw / resolution) * resolution


def classify(ffa: float, water: float, contaminated: bool, rules: AcceptanceRules) -> str:
    if contaminated:
        return FAIL
    if ffa <= rules.pass_max_ffa and water <= rules.pass_max_water:
        return PASS
    if ffa <= rules.marginal_max_ffa and water <= rules.marginal_max_water:
        return MARGINAL
    return FAIL


def measure_quality(truth_ffa: float, truth_water: float, truth_contaminated: bool, rng: random.Random,
                    ffa_sd: float = 0.15, water_sd: float = 0.05, detection_p: float = 0.95):
    """Lab measurement with titration / Karl-Fischer style noise and imperfect contamination detection."""
    ffa = max(0.0, truth_ffa + rng.gauss(0.0, ffa_sd))
    water = max(0.0, truth_water + rng.gauss(0.0, water_sd))
    detected = truth_contaminated and rng.random() < detection_p
    return ffa, water, detected
