"""Pins phi_denominator_sensitivity.py's 3-phi x 5-trough grid
and the ruled-required phi=0.08 cross-feed range, so a future edit cannot silently drift
either without a visible test failure."""
import pytest

import phi_denominator_sensitivity as pds


def test_grid_has_fifteen_combinations():
    result = pds.build_grid()
    assert len(result["phi_conventions"]) == 3
    assert len(result["trough_readings"]) == 5
    assert len(result["grid"]) == 15


def test_all_fifteen_combinations_fire():
    result = pds.build_grid()
    assert all(c["fires"] for c in result["grid"])


def _cell(grid, phi_label, trough_label):
    return next(c for c in grid
                if c["phi_label"] == phi_label and c["trough_label"] == trough_label)


# --- corners named in the task brief --------------------------------------------------

def test_corner_phi_0784_composite_min():
    result = pds.build_grid()
    c = _cell(result["grid"], "static_phi_altreserve_42.1B_0.0784", "composite_min_headline")
    assert c["phi"] == pytest.approx(0.0784, abs=1e-4)
    assert c["trough_price"] == pytest.approx(0.876675, abs=1e-6)
    assert c["floor"] == pytest.approx(0.9216, abs=1e-4)
    assert c["q_star_rho0"] == pytest.approx(1.573, abs=1e-3)
    assert c["s_nf0"] == pytest.approx(0.3643, abs=1e-3)
    assert c["fires"] is True


def test_corner_phi_0825_kraken_vwap_min():
    result = pds.build_grid()
    c = _cell(result["grid"], "static_phi_3.3_over_40.0_0.0825", "kraken_vwap_min")
    assert c["phi"] == pytest.approx(0.0825, abs=1e-4)
    assert c["trough_price"] == pytest.approx(0.892860, abs=1e-4)
    assert c["floor"] == pytest.approx(0.9175, abs=1e-4)
    assert c["q_star_rho0"] == pytest.approx(1.2987, abs=1e-3)
    assert c["s_nf0"] == pytest.approx(0.2300, abs=1e-3)
    assert c["fires"] is True


def test_extreme_lowest_snf0_phi_0825_kraken_close_min():
    """The two extremes of the 15-cell grid: the shallowest reading (weakest phi,
    closest-to-par trough) and the deepest (strongest phi, deepest trough)."""
    result = pds.build_grid()
    c = _cell(result["grid"], "static_phi_3.3_over_40.0_0.0825", "kraken_close_min")
    assert c["s_nf0"] == pytest.approx(0.1667, abs=1e-3)
    assert min(row["s_nf0"] for row in result["grid"]) == pytest.approx(0.1667, abs=1e-3)


def test_extreme_highest_snf0_phi_0784_composite_min():
    result = pds.build_grid()
    c = _cell(result["grid"], "static_phi_altreserve_42.1B_0.0784", "composite_min_headline")
    assert c["s_nf0"] == pytest.approx(0.3643, abs=1e-3)
    assert max(row["s_nf0"] for row in result["grid"]) == pytest.approx(0.3643, abs=1e-3)


# --- D13: the phi=0.08 cross-feed range ------------------------------------------------

def test_cross_feed_range_at_phi_08():
    result = pds.build_grid()
    r = result["cross_feed_range_at_phi_0.08"]
    assert r["phi"] == pytest.approx(0.08, abs=1e-9)
    assert r["n_trough_readings"] == 5
    assert r["s_nf0_min"] == pytest.approx(0.1919, abs=1e-3)
    assert r["s_nf0_min_trough"] == "kraken_close_min"
    assert r["s_nf0_max"] == pytest.approx(0.3513, abs=1e-3)
    assert r["s_nf0_max_trough"] == "composite_min_headline"


# --- Feed-trough-only range, restricted to
# the four genuinely distinct price feeds, excluding composite_second_lowest ------------

def test_feed_trough_only_at_phi_08_excludes_second_composite_print():
    result = pds.build_grid()
    r = result["feed_trough_only_at_phi_0.08"]
    assert r["phi"] == pytest.approx(0.08, abs=1e-9)
    assert r["n_trough_readings"] == 4
    assert set(r["trough_labels_included"]) == {
        "composite_min_headline", "kraken_close_min", "kraken_vwap_min",
        "binance_usdcusdt_low_min",
    }
    assert "composite_second_lowest" not in r["trough_labels_included"]
    assert r["s_nf0_min"] == pytest.approx(0.1919, abs=1e-3)
    assert r["s_nf0_min_trough"] == "kraken_close_min"
    assert r["s_nf0_max"] == pytest.approx(0.3513, abs=1e-3)
    assert r["s_nf0_max_trough"] == "composite_min_headline"


def test_broader_grid_range_includes_non_feed_cells():
    """The broader sensitivity range (share_range.tex sentence 2) spans all 3 phi
    conventions x 5 trough readings, including composite_second_lowest and the two
    non-headline phi conventions."""
    result = pds.build_grid()
    all_s_nf0 = [c["s_nf0"] for c in result["grid"]]
    assert min(all_s_nf0) == pytest.approx(0.1667, abs=1e-3)
    assert max(all_s_nf0) == pytest.approx(0.3643, abs=1e-3)


# --- trough readings are computed from frozen data, not guessed -----------------------

def test_composite_second_lowest_matches_manuscript_0_8949():
    """paper/fc27/fc27.tex:402: 'even at the next-lowest composite hourly print
    ($0.8949) it remains 23.9%', this trough reading must reproduce that print."""
    result = pds.build_grid()
    t = next(x for x in result["trough_readings"] if x["trough_label"] == "composite_second_lowest")
    assert round(t["trough_price"], 4) == 0.8949


def test_composite_min_matches_ruled_headline():
    """The composite trough is $0.876675 at 07:00 UTC."""
    result = pds.build_grid()
    t = next(x for x in result["trough_readings"] if x["trough_label"] == "composite_min_headline")
    assert t["trough_price"] == pytest.approx(0.876675, abs=1e-6)


def test_kraken_troughs_match_trough_corroboration_frozen_values():
    """Cross-check against results/trough_corroboration.json's kraken_usdc_close /
    kraken_usdc_vwap min_print (already frozen, N7 C1), independently recomputed
    here from the raw hourly.csv, not trusted from that file."""
    result = pds.build_grid()
    close = next(x for x in result["trough_readings"] if x["trough_label"] == "kraken_close_min")
    vwap = next(x for x in result["trough_readings"] if x["trough_label"] == "kraken_vwap_min")
    assert close["trough_price"] == pytest.approx(0.901, abs=1e-4)
    assert vwap["trough_price"] == pytest.approx(0.89286, abs=1e-4)


def test_binance_low_min_matches_trough_volume_frozen_value():
    """Cross-check against results/trough_volume.json's min_low (0.882)."""
    result = pds.build_grid()
    t = next(x for x in result["trough_readings"] if x["trough_label"] == "binance_usdcusdt_low_min")
    assert t["trough_price"] == pytest.approx(0.882, abs=1e-6)


# --- write guard -------------------------------------------------------------------

def test_write_guard_refuses_path_under_data_dir():
    from constants import DATA_DIR
    with pytest.raises(pds.FrozenFileError):
        pds._assert_safe_write_path(DATA_DIR / "price_hourly.csv")


def test_write_guard_refuses_manifested_filename_anywhere():
    from constants import RESULTS_DIR
    with pytest.raises(pds.FrozenFileError):
        pds._assert_safe_write_path(RESULTS_DIR / "price_hourly.csv")


def test_write_guard_allows_own_output_path():
    pds._assert_safe_write_path(pds.OUT_PATH)  # must not raise
