"""Time-value discount magnitudes, pins the computed values against the
frozen Treasury par yield curve so fc27.tex's four p.7/p.15 figures cannot silently drift."""
import json

import pytest

import time_value
from constants import RESULTS_DIR


def test_par_yields_trace_to_frozen_file():
    y = time_value.par_yields()
    assert y["1mo"] == pytest.approx(0.0481)
    assert y["3mo"] == pytest.approx(0.0501)


def test_par_yields_missing_date_raises():
    with pytest.raises(ValueError):
        time_value.par_yields(date="01/01/2099")


def test_discount_bps_2to3_day_range():
    r = time_value.report()
    lo, hi = r["discount_bps_range_2to3d"]
    assert lo == pytest.approx(2.6356, abs=1e-3)
    assert hi == pytest.approx(4.1178, abs=1e-3)


def test_delay_days_to_trough_near_one_year():
    """At 2023-03-10 Treasury yields, discounting the $0.92 floor to the composite
    trough takes 343-358 days, close to but under a year, at both tenors."""
    r = time_value.report()
    lo, hi = r["delay_days_to_trough_range"]
    assert 340 < lo < hi < 360


def test_annualised_rate_for_full_gap_is_absurd():
    """Rationalising the FULL 1,233-bps par-to-trough gap over just 3 days would need
    an annualised rate near 1,500%, three orders of magnitude above the 2023-03-10
    money-market yields, the rhetorical point of computation (iii)."""
    r = time_value.report()
    rate = r["annualised_rate_for_full_gap_over_3d"]
    assert rate == pytest.approx(15.0045, abs=1e-2)
    assert rate > 100 * r["par_yields"]["3mo"]


def test_results_artifact_persisted_and_matches():
    path = RESULTS_DIR / "time_value.json"
    assert path.exists(), "run `python time_value.py` to produce results/time_value.json"
    frozen = json.load(open(path))
    fresh = time_value.report()
    assert frozen["par_yields"] == fresh["par_yields"]
    assert frozen["discount_bps_range_2to3d"] == fresh["discount_bps_range_2to3d"]
    assert frozen["delay_days_to_trough_range"] == fresh["delay_days_to_trough_range"]
    assert frozen["annualised_rate_for_full_gap_over_3d"] == pytest.approx(
        fresh["annualised_rate_for_full_gap_over_3d"])
