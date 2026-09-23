"""USDT-premium PEAK TIMING, pins the three peak timestamps (Coinbase, DeFiLlama
composite, Kraken March-2023) and their before/after flags against the disclosure, USDC
trough hour, and joint-statement (federal backstop) reference points, so a future data
refresh or module edit cannot silently drift them."""
import pytest

import usdt_premium_timing as upt


def test_reference_points_match_frozen_canonical_values():
    r = upt.report()
    refs = r["reference_points_utc"]
    assert refs["disclosure"] == "2023-03-11T03:11:00Z"
    assert refs["joint_statement_treasury_fed_fdic"] == "2023-03-12T22:15:00Z"
    assert refs["usdc_trough_hour"] == "2023-03-11T07:57:53Z"


def test_coinbase_peak_pinned():
    p = upt.report()["peaks"]["coinbase_usdt_usd"]
    assert p["status"] == "OK"
    assert p["timestamp_utc"] == "2023-03-11T02:00:00Z"
    assert p["premium_bps"] == pytest.approx(265.9, abs=0.5)  # matches tab:usdt's +266 bps
    rel = p["relative_to"]
    assert rel["disclosure"]["flag"] == "before"
    assert rel["disclosure"]["delta_minutes"] == pytest.approx(-71.0, abs=0.5)
    assert rel["usdc_trough_hour"]["flag"] == "before"
    assert rel["joint_statement_treasury_fed_fdic"]["flag"] == "before"
    assert rel["joint_statement_treasury_fed_fdic"]["delta_minutes"] == pytest.approx(-2655.0, abs=1.0)


def test_composite_peak_pinned():
    p = upt.report()["peaks"]["composite_usdt"]
    assert p["status"] == "OK"
    assert p["timestamp_utc"] == "2023-03-13T19:00:56Z"
    assert p["premium_bps"] == pytest.approx(270.0, abs=0.5)  # matches fc27.tex's +270 bps
    rel = p["relative_to"]
    # The composite's peak comes AFTER all three reference points, unlike Coinbase's.
    assert rel["disclosure"]["flag"] == "after"
    assert rel["usdc_trough_hour"]["flag"] == "after"
    assert rel["joint_statement_treasury_fed_fdic"]["flag"] == "after"
    assert rel["joint_statement_treasury_fed_fdic"]["delta_minutes"] == pytest.approx(1245.93, abs=1.0)


def test_kraken_march2023_peak_pinned():
    p = upt.report()["peaks"]["kraken_usdt_usd_march2023"]
    assert p["status"] == "OK"
    assert p["source_file"] == "data/n1/kraken_usdtusd/hourly.csv"  # the 2023 series, not 2019
    assert p["timestamp_utc"] == "2023-03-11T02:00:00Z"
    assert p["premium_bps"] == pytest.approx(600.0, abs=1.0)
    rel = p["relative_to"]
    assert rel["disclosure"]["flag"] == "before"
    assert rel["usdc_trough_hour"]["flag"] == "before"
    assert rel["joint_statement_treasury_fed_fdic"]["flag"] == "before"


def test_results_artifact_persisted_and_matches():
    from constants import RESULTS_DIR
    import json
    path = RESULTS_DIR / "usdt_premium_timing.json"
    assert path.exists(), "run `python usdt_premium_timing.py` to produce this artifact"
    frozen = json.load(open(path))
    fresh = upt.report()
    assert frozen["reference_points_utc"] == fresh["reference_points_utc"]
    for name in fresh["peaks"]:
        assert frozen["peaks"][name]["timestamp_utc"] == fresh["peaks"][name]["timestamp_utc"]
        assert frozen["peaks"][name]["premium_bps"] == pytest.approx(fresh["peaks"][name]["premium_bps"])
