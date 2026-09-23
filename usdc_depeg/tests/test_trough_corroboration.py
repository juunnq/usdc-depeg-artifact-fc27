"""Pins the cross-feed trough corroboration numbers so a future edit to
trough_corroboration.py (or a data refresh) cannot silently drift the reported values."""
import pytest

import trough_corroboration as tc
from constants import PHI_SVB
import rational_bound as rb


def test_floor_matches_rational_bound():
    """The floor is DERIVED from rational_bound, never hardcoded."""
    r = tc.report()
    assert r["floor_worstcase"] == pytest.approx(rb.floor_worstcase(PHI_SVB), abs=1e-9)
    assert r["floor_worstcase"] == pytest.approx(0.92, abs=1e-9)


def test_deflation_direction_not_inverted():
    """USDT traded at a premium during the crisis, so deflating Binance's USDCUSDT print
    into USD terms must move the price UP relative to the raw USDT-terms print, for both
    the Coinbase and the Kraken USDT/USD legs."""
    r = tc.report()["deflation_direction_check"]
    assert r["coinbase_leg"]["moved_up"] is True
    assert r["kraken_leg"]["moved_up"] is True
    assert r["coinbase_leg"]["deflated_low_at_same_hour"] > r["coinbase_leg"]["raw_min_low_usdt_terms"]
    assert r["kraken_leg"]["deflated_low_at_same_hour"] > r["kraken_leg"]["raw_min_low_usdt_terms"]


def test_kraken_usdc_minimum_low_and_hour():
    """Kraken USDC/USD's own minimum low print and the UTC hour it fell in."""
    s = tc.firing_report()["series"]["kraken_usdc_low"]
    assert s["min_print"] == pytest.approx(0.874, abs=1e-6)
    assert s["min_print_utc"] == "2023-03-11T07:00:00+00:00"


def test_firing_counts_per_feed_against_static_floor():
    """Pins the firing count for every labeled (feed, price-convention) series."""
    s = tc.firing_report()["series"]
    expected = {
        "composite": 9,
        "kraken_usdc_low": 12,
        "kraken_usdc_close": 9,
        "kraken_usdc_vwap": 10,
        "binance_deflated_coinbase_low": 3,
        "binance_deflated_coinbase_close": 1,
        "binance_deflated_kraken_low": 3,
        "binance_deflated_kraken_close": 1,
    }
    for label, count in expected.items():
        assert s[label]["firing_count"] == count, label


def test_binance_snapshot_does_not_cover_the_trough():
    """KNOWN GAP: the frozen Binance snapshot's first row is 14:00 UTC on 11 March --
    it misses the 06:00-13:00 UTC window containing the composite/Kraken trough
    entirely. Must never be silently papered over."""
    s = tc.firing_report()["series"]
    assert s["binance_deflated_coinbase_low"]["n_hours_observed"] == 131
    assert s["binance_deflated_coinbase_low"]["first_firing_utc"] == "2023-03-11T14:00:00+00:00"


def test_trough_hour_table_07_and_08_utc():
    """Full trough-hour table: every feed's print at 07:00 and 08:00 UTC on 11 March
    2023, with the below-$0.92 boolean. Binance is 'not available' both hours (the
    coverage gap above). The composite/Kraken close both recover just above the floor
    at 08:00, the known non-contiguous mid-region recovery."""
    rows = tc.trough_hour_table()
    assert len(rows) == 2
    r07, r08 = rows[0], rows[1]
    assert r07["hour_utc"] == "2023-03-11T07:00:00Z"
    assert r08["hour_utc"] == "2023-03-11T08:00:00Z"

    assert r07["feeds"]["composite"]["print"] == pytest.approx(0.876675, abs=1e-6)
    assert r07["feeds"]["composite"]["below_floor"] is True
    assert r08["feeds"]["composite"]["print"] == pytest.approx(0.921553, abs=1e-6)
    assert r08["feeds"]["composite"]["below_floor"] is False

    assert r07["feeds"]["kraken_usdc"]["low"] == pytest.approx(0.874, abs=1e-6)
    assert r07["feeds"]["kraken_usdc"]["low_below_floor"] is True
    assert r07["feeds"]["kraken_usdc"]["close"] == pytest.approx(0.9059, abs=1e-6)
    assert r07["feeds"]["kraken_usdc"]["close_below_floor"] is True
    assert r08["feeds"]["kraken_usdc"]["low"] == pytest.approx(0.884, abs=1e-6)
    assert r08["feeds"]["kraken_usdc"]["low_below_floor"] is True
    assert r08["feeds"]["kraken_usdc"]["close"] == pytest.approx(0.9216, abs=1e-6)
    assert r08["feeds"]["kraken_usdc"]["close_below_floor"] is False  # recovers just above 0.92

    for hour_row in (r07, r08):
        for leg in ("binance_deflated_coinbase", "binance_deflated_kraken"):
            assert hour_row["feeds"][leg]["low"] is None
            assert hour_row["feeds"][leg]["close"] is None
            assert "not available" in hour_row["feeds"][leg]["note"]


def test_cross_feed_agreement_at_trough_hours():
    """3 independent feeds (composite, Kraken USDC, Binance-deflated-by-Coinbase); at
    07:00 Binance is unavailable so 2 of 2 available feeds agree below-floor, and at
    08:00 both available feeds recover above the floor (0 of 2)."""
    cfa = tc.cross_feed_agreement()
    at07, at08 = cfa["at_07_00_utc"], cfa["at_08_00_utc"]
    assert at07["feeds_available"] == 2
    assert at07["feeds_below_floor"] == 2
    assert set(at07["below_floor_feeds"]) == {"composite", "kraken_usdc"}
    assert at08["feeds_available"] == 2
    assert at08["feeds_below_floor"] == 0
    # Kraken-deflated Binance leg is a robustness check only, never a 4th vote, and is
    # unavailable at both trough hours (same coverage gap).
    assert at07["binance_deflated_kraken_close"] is None
    assert at08["binance_deflated_kraken_close"] is None


def test_independence_composite_matches_impossibility_window():
    """The composite AR(1) here must match impossibility_window.effective_n('USDC')
    exactly, same series, same estimator."""
    import impossibility_window as iw
    ind = tc.independence_caveat()
    iw_ind = iw.effective_n("USDC")
    assert ind["composite"]["lag1_autocorr"] == pytest.approx(iw_ind["lag1_autocorr"], abs=1e-6)
    assert ind["composite"]["effective_n"] == pytest.approx(iw_ind["effective_n"], abs=1e-6)
    assert ind["composite"]["lag1_autocorr"] == pytest.approx(0.9631, abs=1e-3)  # ~0.96, verified


def test_independence_kraken_usdc_new_statistic():
    """AR(1) / effective-N for the Kraken USDC series (no equivalent
    existed before; impossibility_window only covers the composite)."""
    ind = tc.independence_caveat()["kraken_usdc_close"]
    assert set(ind) == {"n_hours", "lag1_autocorr", "effective_n"}
    assert ind["n_hours"] == 240
    assert ind["lag1_autocorr"] == pytest.approx(0.9453, abs=1e-3)
    assert ind["effective_n"] == pytest.approx(6.7, abs=0.2)
    assert ind["effective_n"] < ind["n_hours"] / 10  # order-of-magnitude discount, like the composite


def test_onchain_hook_is_structural_only():
    """The on-chain hook must be a placeholder shape, never a fabricated number."""
    hook = tc.onchain_placeholder()
    assert hook["available"] is False
    assert "series" in hook["expected_keys"]


def test_results_artifact_matches_live_report():
    """results/trough_corroboration.json must match a live recomputation."""
    import json
    from constants import RESULTS_DIR
    path = RESULTS_DIR / "trough_corroboration.json"
    if not path.exists():
        pytest.skip("trough_corroboration.py has not been run to persist the JSON yet")
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    live = tc.report()
    assert on_disk["floor_worstcase"] == live["floor_worstcase"]
    assert (on_disk["firing_report"]["series"]["composite"]["firing_count"]
            == live["firing_report"]["series"]["composite"]["firing_count"])


# --- N7 adversarial pass, ruling C1 -------------------------------------------------
# C1 established that the minimum-print statistic is a single 3,373.52-USDC trade
# (~$2,948, 0.01% of its hour) and must not be the headline. These pin the executed
# sub-floor DEPTH, which is the number the manuscript may quote instead.

def test_below_floor_depth_pins_executed_volume():
    d = tc.below_floor_depth()
    assert d["n_trades_below_floor"] == 37303
    assert d["volume_usdc_below_floor"] == pytest.approx(169898078.33, abs=0.01)
    assert d["notional_usd_below_floor"] == pytest.approx(154007404.51, abs=0.01)


def test_below_floor_depth_window_is_one_intraday_episode():
    """All sub-floor execution sits inside 03:00-15:00 UTC on 11 March, the reason
    an hourly firing COUNT overstates independence (AR(1) effective n ~ 6.7)."""
    d = tc.below_floor_depth()
    assert d["first_hour_utc"] == "2023-03-11T03:00:00Z"
    assert d["last_hour_utc"] == "2023-03-11T15:00:00Z"


def test_min_print_is_a_single_small_trade_not_the_headline():
    """The guard against reinstating $0.8740 as the lead number: it is one trade."""
    d = tc.below_floor_depth()
    assert d["min_print"] == 0.874
    assert d["min_print_trade_volume_usdc"] == pytest.approx(3373.51681752, abs=1e-6)
    # ...worth well under $3k, i.e. immaterial as a clearing price
    assert d["min_print"] * d["min_print_trade_volume_usdc"] < 3000


def test_trough_hour_cleared_entirely_below_the_floor():
    """In 07:00 UTC every Kraken trade cleared below $0.92, the strongest single
    statement the tick data supports, and it is about depth, not about a minimum."""
    d = tc.below_floor_depth()
    hour = d["per_hour"]["2023-03-11T07:00:00Z"]
    assert hour["n_trades"] == 5119
    assert hour["share_of_hour_trades"] == 1.0
    assert hour["volume_usdc"] == pytest.approx(32726956.66, abs=0.01)


def test_kraken_low_trade_size_wired_off_existing_field():
    """An adversarial review: kraken_low_trade_size_usdc / kraken_low_is_single_trade must be
    wired directly off min_print_trade_volume_usdc, not a separate computation."""
    d = tc.below_floor_depth()
    assert d["kraken_low_trade_size_usdc"] == pytest.approx(3373.52, abs=0.01)
    assert d["kraken_low_trade_size_usdc"] == pytest.approx(d["min_print_trade_volume_usdc"], abs=0.01)
    assert d["kraken_low_is_single_trade"] is True


def test_feed_count_independent_is_two():
    """An adversarial review: only the two fiat-settled feeds that actually cover the trough
    (composite, kraken_usdc) count toward independence; Binance's deflated legs don't
    cover the window and are not a third vote."""
    cfa = tc.cross_feed_agreement()
    assert cfa["feed_count_independent"] == 2


def test_no_feed_invariant_wording_anywhere_in_the_report():
    """An adversarial review: 'feed-invariant' / 'invariant' must not appear anywhere in this
    module's output, the two-feed concordance is partial (6 of 11 hours), not an
    invariant."""
    import json
    blob = json.dumps(tc.report(), default=str).lower()
    assert "invariant" not in blob


def test_kraken_low_not_implied_as_primary_statistic():
    """An adversarial review: nothing in the module's output may nominate the low-print firing
    count (12, wick-inflated) as the primary Kraken statistic, close (9) and vwap
    (10) are the headline numbers."""
    note = tc.firing_report()["kraken_low_convention_note"]
    assert "not" in note.lower() and "headline" in note.lower()
