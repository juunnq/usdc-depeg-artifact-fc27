"""Pins panel_11mar.json (the new-Figure-1 data) so a future data refresh
or edit cannot silently drift the panel, the 08:57 UTC coverage-gap finding, or the
cross-coin/cross-window summary it reads (not recomputes) from impossibility_window
.json and placebo_excluded_coins.json."""
from pathlib import Path

import pytest

import panel_11mar as p11
from constants import DATA_DIR


def test_floor_is_static_and_says_so():
    r = p11.report()
    assert r["floor"] == pytest.approx(0.92)
    assert "static" in r["floor_source"]
    assert "no hourly phi(t) series" in r["floor_source"]


def test_panel_covers_24_hours_per_series():
    r = p11.report()
    assert len(r["panel"]["composite_USDC"]) == 24
    assert len(r["panel"]["composite_DAI"]) == 24
    assert len(r["panel"]["kraken_usdcusd"]) == 24
    # USDT's composite feed genuinely has one fewer hourly pull this day, not a bug.
    assert len(r["panel"]["composite_USDT"]) == 23


def test_composite_usdc_firing_hours_match_impossibility_window():
    """The 9 USDC firing hours here must be the exact same 9 as impossibility_window
    .json's impossibility_region_usdc (same static floor, same source CSV)."""
    r = p11.report()
    fired = [row["hour_utc"] for row in r["panel"]["composite_USDC"] if row["firing"]]
    expected = [
        "2023-03-11T06:59:51+00:00", "2023-03-11T07:57:53+00:00",
        "2023-03-11T10:00:11+00:00", "2023-03-11T11:00:53+00:00",
        "2023-03-11T12:00:48+00:00", "2023-03-11T12:59:32+00:00",
        "2023-03-11T14:00:00+00:00", "2023-03-11T15:00:01+00:00",
        "2023-03-11T16:00:05+00:00",
    ]
    assert fired == expected


def test_08_57_is_not_a_genuine_data_gap():
    """N7's specificity-panel language ('interrupted at 08:57') is economic, not a
    missing row, the composite USDC row at 08:57:55 UTC exists in the frozen CSV."""
    r = p11.report()
    gap = r["coverage_gap_check_08_57_utc"]
    assert gap["genuine_data_gap"] is False
    assert gap["row_utc"] == "2023-03-11T08:57:55+00:00"
    assert gap["price"] == pytest.approx(0.921553, abs=1e-6)
    assert gap["price"] > r["floor"]  # this is the recovery hour, not a breach


def test_kraken_08_00_hour_matches_appD_recovery_number():
    """review/loci/appD.md:84-87 quotes Kraken's close at the 08:57-adjacent hour as
    $0.9216, pin it against the frozen Kraken CSV."""
    r = p11.report()
    row_08 = next(row for row in r["panel"]["kraken_usdcusd"]
                  if row["hour_utc"] == "2023-03-11T08:00:00+00:00")
    assert row_08["close"] == pytest.approx(0.9216, abs=1e-4)
    assert row_08["close_firing"] is False


def test_cross_coin_summary_matches_source_files_verbatim():
    """Cross-coin firing counts must equal impossibility_window.json's own numbers --
    this module reads them, it must never recompute or drift from them."""
    r = p11.report()
    counts = r["cross_coin_cross_window_summary"]["cross_coin_firing_counts_full_record"]
    assert counts["USDC"]["breaching_hours"] == 9
    assert counts["USDC"]["hours_observed"] == 212
    assert counts["DAI"]["breaching_hours"] == 2
    assert counts["USDT"]["breaching_hours"] == 0

    excl = r["cross_coin_cross_window_summary"]["excluded_coin_floor_check_0.92"]
    assert excl["total_hours"] == 2086
    assert excl["total_hours_below_floor"] == 0

    thresh = r["cross_coin_cross_window_summary"]["excluded_coin_1pct_break_threshold_check"]
    assert thresh["break_threshold"] == pytest.approx(0.99)
    assert set(thresh["coins"]) == {"USDP", "GUSD", "BUSD"}


def test_guard_refuses_to_write_under_data_dir():
    with pytest.raises(p11.FrozenPathWriteError):
        p11._guard_not_frozen(DATA_DIR / "some_new_file.json")


def test_guard_allows_results_dir():
    # Should not raise.
    p11._guard_not_frozen(Path(p11.OUT_FILE))
