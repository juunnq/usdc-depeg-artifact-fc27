"""Number 3 — cross-stablecoin placebo: verdict + per-coin troughs vs the frozen snapshot."""
import pytest

import data_io
import placebo


def test_verdict_exposed_broke_unexposed_held():
    r = placebo.placebo()
    assert r["placebo_pass"] is True
    assert r["USDC"]["broke"] is True   # direct SVB exposure
    assert r["DAI"]["broke"] is True    # contagion (USDC-collateralized)
    assert r["USDT"]["broke"] is False  # no exposure


def test_troughs_match_frozen_data():
    r = placebo.placebo()
    for s in ("USDC", "DAI", "USDT"):
        assert r[s]["trough"] == pytest.approx(data_io.observed_trough(s))
    # USDC and DAI clear the 1%-below-par break threshold; USDT does not.
    assert r["USDC"]["trough"] < r["break_threshold"]
    assert r["DAI"]["trough"] < r["break_threshold"]
    assert r["USDT"]["trough"] >= r["break_threshold"]


def test_usdt_flight_to_safety_premium():
    # USDT attracted a flight-to-safety premium (peak above par) and did not break.
    r = placebo.placebo()
    assert r["USDT"]["max_premium_bps"] == pytest.approx(270.0)
    assert r["USDT"]["peak"] > 1.0


# --- frozen-snapshot gating (S6): pin the paper's placebo troughs ---------------
def test_frozen_troughs_pinned():
    """Pin the per-coin troughs and the aggregate USDT premium the paper's
    tab:placebo quotes, against the frozen snapshot."""
    import pytest
    r = placebo.placebo()
    assert r["USDC"]["trough"] == pytest.approx(0.876675, abs=1e-4)
    assert r["DAI"]["trough"] == pytest.approx(0.885938, abs=1e-4)
    assert r["USDT"]["trough"] == pytest.approx(0.992437, abs=1e-4)
    assert r["USDT"]["max_premium_bps"] == pytest.approx(270.0, abs=1.0)


def test_trough_print_quality_pinned():
    """S10: the trough is a material-volume print, pin the Binance min low, its
    timestamp, and the ~$109M trough-hour volume against the frozen klines."""
    import pytest
    q = placebo.trough_print_quality()
    assert q["min_low"] == pytest.approx(0.882, abs=1e-3)
    assert q["min_low_utc"].startswith("2023-03-11T14:00")
    assert q["volume_in_trough_hour_usdc"] == pytest.approx(1.0896e8, rel=1e-3)
    assert q["composite_trough"] == pytest.approx(0.876675, abs=1e-4)


# --- excluded coins (USDP / GUSD / BUSD): pin the frozen exclusion evidence -----
def test_excluded_coins_crisis_troughs_pinned():
    """Pin each excluded coin's crisis trough / max deviation and the fact that
    ALL THREE cross the 1%-break threshold on the frozen composite feed."""
    r = placebo.excluded_coins()
    assert r["USDP"]["crisis_trough"] == pytest.approx(0.948243, abs=1e-3)
    assert r["GUSD"]["crisis_trough"] == pytest.approx(0.928187, abs=1e-3)
    assert r["BUSD"]["crisis_trough"] == pytest.approx(0.985461, abs=1e-3)
    assert r["USDP"]["crisis_max_deviation_bps"] == pytest.approx(517.6, abs=10.0)
    assert r["GUSD"]["crisis_max_deviation_bps"] == pytest.approx(718.1, abs=10.0)
    assert r["BUSD"]["crisis_max_deviation_bps"] == pytest.approx(145.4, abs=10.0)
    for s in ("USDP", "GUSD", "BUSD"):
        assert r[s]["crosses_break_threshold"] is True


def test_excluded_coins_calm_false_positives():
    """The exclusion rationale: on these coins' composite feeds the break test
    fires even in the calm pre-SVB window (2023-02-16 to 2023-03-08), so a
    crisis-window crossing is not diagnostic."""
    r = placebo.excluded_coins()
    assert r["USDP"]["calm_hours_below_threshold"] == 7
    assert r["GUSD"]["calm_hours_below_threshold"] == 1
    assert r["BUSD"]["calm_hours_below_threshold"] == 1
    for s in ("USDP", "GUSD", "BUSD"):
        assert r[s]["calm_false_positive"] is True
        assert r[s]["calm_hours_total"] == 480


def test_excluded_coins_artifact_persisted_and_matches():
    """report.py must persist results/placebo_excluded_coins.json (the paper's
    exclusion-paragraph source); the artifact must agree with a fresh recomputation."""
    import json
    from constants import RESULTS_DIR
    path = RESULTS_DIR / "placebo_excluded_coins.json"
    assert path.exists(), "run report.py: results/placebo_excluded_coins.json is a required artifact"
    frozen = json.load(open(path))
    r = placebo.excluded_coins()
    for s in ("USDP", "GUSD", "BUSD"):
        for key in ("crisis_trough", "crisis_max_deviation_bps",
                    "crosses_break_threshold", "calm_min_price",
                    "calm_hours_below_threshold", "calm_false_positive"):
            if isinstance(r[s][key], float):
                assert frozen[s][key] == pytest.approx(r[s][key])
            else:
                assert frozen[s][key] == r[s][key]
    assert frozen["venue_volumes"] == r["venue_volumes"]
