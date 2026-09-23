"""Hourly evaluation of Proposition 1, duration and specificity of the impossibility
region, pinned against the frozen snapshot. Guards the honesty statistic too: the raw
hour counts must never ship without their AR(1)-adjusted effective N."""
import json

import pytest

import impossibility_window as iw
from constants import PHI_SVB, RESULTS_DIR


def test_series_max_reproduces_the_headline_point_estimate():
    """The hourly series maximum must equal the paper's headline q and share exactly.
    If these diverge, the hourly path and the trough print disagree and one is wrong."""
    r = iw.report()
    usdc = r["coins"]["USDC"]
    assert usdc["max_q"] == pytest.approx(1.5416, abs=1e-3)              # headline q
    assert usdc["max_below_floor_share"] == pytest.approx(0.3513, abs=1e-3)  # headline 35.1%
    assert usdc["min_price"] == pytest.approx(0.876675, abs=1e-6)        # trough print


def test_impossibility_region_dated_and_bounded():
    """The region is a dated 10-hour span on 2023-03-11 containing 9 breaching hours."""
    reg = iw.impossibility_region("USDC")
    assert reg["breaches"] is True
    assert reg["breaching_hours"] == 9
    assert reg["hours_observed"] == 212
    assert reg["span_hours"] == pytest.approx(9.0, abs=0.1)
    assert reg["first_breach_utc"].startswith("2023-03-11T06")
    assert reg["peak_utc"].startswith("2023-03-11T07")
    assert reg["last_breach_utc"].startswith("2023-03-11T16")
    # NOT contiguous: one hour (08:57, $0.9216) recovers just above the floor mid-region.
    assert reg["contiguous"] is False


def test_cross_coin_firing_order():
    """Breaching-hour counts restate the placebo as a rate comparison: USDC > DAI > USDT=0."""
    c = iw.report()["coins"]
    assert c["USDC"]["breaching_hours"] == 9
    assert c["DAI"]["breaching_hours"] == 2
    assert c["USDT"]["breaching_hours"] == 0
    assert c["USDC"]["breaching_hours"] > c["DAI"]["breaching_hours"] > c["USDT"]["breaching_hours"] - 1
    # USDT's worst hour sits an order of magnitude below the firing threshold.
    assert c["USDT"]["max_q"] == pytest.approx(0.0945, abs=1e-3)
    assert c["USDT"]["max_q"] < 0.1


def test_effective_n_is_reported_and_small():
    """THE HONESTY GATE. Hourly prices in one episode are near-unit-root, so the hour
    counts are not an evidence base. If effective N ever drifts upward materially,
    the near-unit-root caveat in the paper needs revisiting, not quiet deletion."""
    c = iw.report()["coins"]
    for sym in ("USDC", "DAI", "USDT"):
        ind = c[sym]["independence"]
        assert set(ind) == {"n_hours", "lag1_autocorr", "effective_n"}
        assert ind["effective_n"] < ind["n_hours"] / 10          # order-of-magnitude discount
    assert c["USDC"]["independence"]["effective_n"] == pytest.approx(4.0, abs=0.5)
    assert c["DAI"]["independence"]["effective_n"] == pytest.approx(4.9, abs=0.5)
    assert c["USDC"]["independence"]["lag1_autocorr"] > 0.95


def test_excluded_coins_never_breach_the_bound_floor():
    """Distinct from placebo's 1% BREAK_THRESHOLD: none of USDP/GUSD/BUSD comes near the
    0.92 impossibility floor in 2,086 hours, crisis included. GUSD's crisis trough
    ($0.9282) is the closest approach and still clears it."""
    fs = iw.floor_specificity()
    assert fs["floor"] == pytest.approx(1.0 - PHI_SVB, abs=1e-9)
    assert fs["total_hours"] == 2086
    assert fs["total_hours_below_floor"] == 0
    for sym in ("USDP", "GUSD", "BUSD"):
        for win in ("calm", "crisis"):
            assert fs["coins"][sym][win]["hours_below_floor"] == 0
    assert fs["coins"]["GUSD"]["crisis"]["min_price"] == pytest.approx(0.928187, abs=1e-5)


def test_share_guard_handles_at_or_above_par():
    """below_floor_share rejects p >= 1; the hourly path must not crash on par prints."""
    assert iw._share(1.0, PHI_SVB) == 0.0
    assert iw._share(1.0004, PHI_SVB) == 0.0
    assert iw._share(0.876675, PHI_SVB) == pytest.approx(0.3513, abs=1e-3)


def test_results_artifact_persisted_and_matches():
    """report.py must persist results/impossibility_window.json."""
    path = RESULTS_DIR / "impossibility_window.json"
    if not path.exists():
        pytest.skip("run report.py first")
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    live = iw.report()
    assert on_disk["coins"]["USDC"]["breaching_hours"] == live["coins"]["USDC"]["breaching_hours"]
    assert on_disk["impossibility_region_usdc"]["max_q"] == live["impossibility_region_usdc"]["max_q"]
    assert "independence" in on_disk["coins"]["USDC"]
