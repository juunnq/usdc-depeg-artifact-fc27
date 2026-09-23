"""Time-varying phi(t) generalization of Proposition 1, pinned against the frozen
snapshot. Guards that the dynamic firing-hour set is reproducible and that the
documented variant (ii) gap (no frozen per-block burn/mint file) stays documented."""
import json

import pytest

import phi_dynamic as pd_
from constants import RESULTS_DIR


def test_variant_i_phi_at_trough():
    """phi(t) at the trough hour, variant (i) (interpolated daily supply). Rounded to
    3dp, not 4: 4dp is finer than the daily-interpolated data can resolve."""
    r = pd_.report()
    assert r["variant_i"]["phi_at_trough"] == pytest.approx(0.079, abs=1e-4)
    assert r["variant_i"]["floor_at_trough"] == pytest.approx(0.921, abs=1e-4)
    assert r["variant_i"]["q_at_trough"] == pytest.approx(1.558, abs=1e-3)


def test_third_line_phi_is_constant_not_time_varying():
    """Circle's alternate ~$42.1B total is a single point figure, constant, not a
    daily series, and must be flagged as such."""
    r = pd_.report()
    line = r["third_line_alt_reserve_total"]
    assert line["is_time_varying"] is False
    assert line["phi"] == pytest.approx(0.0784, abs=1e-4)


def test_max_phi_across_window():
    """Max phi(t) across the window, variant (i)."""
    r = pd_.report()
    assert r["variant_i"]["max_phi"] == pytest.approx(0.09, abs=1e-4)
    assert r["variant_i"]["max_phi_hour_utc"].startswith("2023-03-16T22")


def test_max_and_min_phi_flagged_as_window_edge_artifacts():
    """An adversarial review: max_phi and min_phi are window-edge censoring artifacts, not true
    extrema, phi is monotone in a monotone supply series and continues past the
    window. Both must be explicitly flagged, never silently kept unflagged."""
    v = pd_.report()["variant_i"]
    assert v["max_phi_window_edge_artifact"] is True
    assert "window" in v["max_phi_window_edge_note"].lower()
    assert v["min_phi"] == pytest.approx(0.076, abs=1e-3)
    assert v["min_phi_hour_utc"].startswith("2023-03-07T23")
    assert v["min_phi_window_edge_artifact"] is True
    assert "window" in v["min_phi_window_edge_note"].lower()


def test_interpolation_band():
    """Narrowed per an adversarial review: the two daily supply snapshots bracketing the
    trough admit phi in [0.0782, 0.0812], over which the firing set ranges from 9 to 11
    hours, verified live against the frozen supply/price data, not carried over
    unchecked from an earlier snapshot."""
    band = pd_.report()["interpolation_band"]
    assert band["phi_lo"] == pytest.approx(0.0782, abs=1e-4)
    assert band["phi_hi"] == pytest.approx(0.0812, abs=1e-4)
    assert band["firing_count_lo"] == 11
    assert band["firing_count_hi"] == 9
    assert band["extra_firing_hours_utc"] == [
        "2023-03-11T08:57:55+00:00",
        "2023-03-11T17:00:19+00:00",
    ]


def test_firing_set_statement_retrievable_verbatim():
    """An adversarial review: the firing set is 9-11, not 'unchanged', the manuscript sentence
    must be retrievable verbatim from the results file."""
    fs = pd_.report()["firing_set"]
    assert fs["statement_verbatim"] == ("9 hours on the composite close; 9-11 across "
                                         "Kraken conventions")
    assert fs["composite_close_hours"] == 9
    assert fs["kraken_convention_range_hours"] == [9, 11]


def test_firing_hour_set_matches_static_9_hours():
    """The dynamic floor(t) fires the SAME 9 hours as the static PHI_SVB floor: the
    trough q is far enough above 1 (1.54 static, 1.56-1.57 dynamic) that the small
    phi(t) perturbation (0.076-0.09 vs the static 0.08) never flips a firing hour."""
    r = pd_.report()
    expected = [
        "2023-03-11T06:59:51+00:00",
        "2023-03-11T07:57:53+00:00",
        "2023-03-11T10:00:11+00:00",
        "2023-03-11T11:00:53+00:00",
        "2023-03-11T12:00:48+00:00",
        "2023-03-11T12:59:32+00:00",
        "2023-03-11T14:00:00+00:00",
        "2023-03-11T15:00:01+00:00",
        "2023-03-11T16:00:05+00:00",
    ]
    assert r["dynamic_firing"]["firing_hours_utc"] == sorted(expected)
    assert r["static_comparison"]["firing_hours_utc"] == sorted(expected)
    assert r["firing_set_identical"] is True
    assert r["flip_hours_utc"] == []


def test_variant_ii_gap_is_documented_not_fabricated():
    """Variant (ii) (residual base from per-block burn/mint timestamps) cannot be
    computed from any frozen file, redemptions_by_wallet.csv is wallet-aggregated
    only. Must be booked as an explicit gap, never approximated."""
    r = pd_.report()
    assert "variant_ii_gap" in r
    assert "redemptions_by_wallet.csv" in r["variant_ii_gap"]
    assert "ETHERSCAN_API_KEY" in r["variant_ii_gap"]


def test_results_artifact_persisted_and_matches():
    """phi_dynamic.py must persist results/phi_dynamic.json with matching numbers."""
    path = RESULTS_DIR / "phi_dynamic.json"
    if not path.exists():
        pytest.skip("run phi_dynamic.py first")
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    live = pd_.report()
    assert on_disk["variant_i"]["phi_at_trough"] == live["variant_i"]["phi_at_trough"]
    assert on_disk["dynamic_firing"]["firing_hours_utc"] == live["dynamic_firing"]["firing_hours_utc"]
    assert on_disk["variant_ii_gap"] == live["variant_ii_gap"]
