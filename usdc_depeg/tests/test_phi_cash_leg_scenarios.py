"""The cash-leg scenario grid must stay pinned to the frozen capture it is parsed from.

Two classes of assertion here, and the distinction matters. The PARSE tests check that the
capture still says what the module reads out of it -- if the capture were ever replaced,
these fail rather than letting a changed dollar figure propagate into a published number.
The ARITHMETIC tests check the published cells themselves.
"""
import hashlib
import json

import pytest

import phi_cash_leg_scenarios as mod
from constants import DATA_DIR, RESULTS_DIR

RESULT_PATH = RESULTS_DIR / "phi_cash_leg_scenarios.json"


@pytest.fixture(scope="module")
def result():
    if not RESULT_PATH.exists():
        pytest.skip(f"{RESULT_PATH.name} not generated; run phi_cash_leg_scenarios.py")
    return json.loads(RESULT_PATH.read_text(encoding="utf-8"))


def cell(result, scenario, denominator, feed):
    for g in result["grid"]:
        if (g["scenario"] == scenario and g["denominator"] == denominator
                and g["feed"] == feed):
            return g
    raise AssertionError(f"no cell for {scenario} x {denominator} x {feed}")


# --------------------------------------------------------------------- parse / identity
def test_capture_exists_and_hash_is_recorded(result):
    cap = DATA_DIR.parent / result["disclosure"]["capture_file"]
    assert cap.exists(), f"frozen capture missing: {cap}"
    live = hashlib.sha256(cap.read_bytes()).hexdigest()
    assert live == result["disclosure"]["capture_sha256"], (
        "the frozen capture's bytes changed since this result was generated; "
        "regenerate and re-verify every figure parsed from it"
    )


def test_capture_hash_matches_the_manifest(result):
    """The capture is manifest-tracked; the parse must be reading the manifested bytes.

    This capture is pinned under sources[], not files{} -- the two sections carry
    different kinds of entry (sources[] records provenance for a fetched artifact, files{}
    records a hash for a derived or bundled one) and a lookup in only one of them reports
    an untracked file that is in fact tracked.
    """
    manifest = json.loads((DATA_DIR / "MANIFEST.json").read_text(encoding="utf-8"))
    rel = result["disclosure"]["capture_file"].split("data/", 1)[1]
    hashes = {}
    entry = manifest.get("files", {}).get(rel)
    if entry:
        hashes["files"] = entry["sha256"]
    for s in manifest.get("sources", []):
        if s.get("file") == rel and "sha256" in s:
            hashes["sources"] = s["sha256"]
    assert hashes, f"{rel} is tracked in neither files{{}} nor sources[] of MANIFEST.json"
    for section, digest in hashes.items():
        assert digest == result["disclosure"]["capture_sha256"], (
            f"MANIFEST.json {section} pins a different sha256 for {rel}"
        )


def test_the_two_accounting_identities_hold(result):
    f = result["disclosure"]["figures_usd_bn"]
    assert f["bny_mellon_usd_bn"] + f["svb_usd_bn"] + f["customers_bank_usd_bn"] == \
        pytest.approx(9.7, abs=1e-9)
    assert f["tbills_usd_bn"] + f["cash_usd_bn"] == pytest.approx(42.1, abs=1e-9)
    assert result["disclosure"]["itemised_cash_sum_usd_bn"] == pytest.approx(9.7, abs=1e-9)
    assert result["disclosure"]["reserve_total_usd_bn"] == pytest.approx(42.1, abs=1e-9)


def test_individual_figures_parse_from_the_capture(result):
    f = result["disclosure"]["figures_usd_bn"]
    assert f["tbills_usd_bn"] == 32.4
    assert f["cash_usd_bn"] == 9.7
    assert f["bny_mellon_usd_bn"] == 5.4
    assert f["svb_usd_bn"] == 3.3
    assert f["customers_bank_usd_bn"] == 1.0
    assert result["disclosure"]["silvergate_zero_exposure_stated"] is True


def test_reparsing_the_capture_reproduces_the_stored_figures():
    """The parse is deterministic and does not depend on the stored JSON."""
    fresh = mod.parse_cash_leg()
    stored = json.loads(RESULT_PATH.read_text(encoding="utf-8"))["disclosure"]
    assert fresh["figures_usd_bn"] == stored["figures_usd_bn"]
    assert fresh["capture_sha256"] == stored["capture_sha256"]


# ------------------------------------------------------------------------------- phi
def test_outside_custodian_phi_on_both_denominators(result):
    c = cell(result, "outside_custodian", "reserve_composition", "composite_min_headline")
    assert c["phi"] == pytest.approx(0.1021, abs=5e-5)
    assert c["impaired_usd_bn"] == pytest.approx(4.3, abs=1e-9)
    c40 = cell(result, "outside_custodian", "headline_40B", "composite_min_headline")
    assert c40["phi"] == pytest.approx(0.1075, abs=5e-5)


# --------------------------------------------------------------------------- verdicts
def test_composite_still_fires_under_the_worst_case(result):
    c = cell(result, "outside_custodian", "reserve_composition", "composite_min_headline")
    assert c["fires"] is True
    assert c["s_nf0"] == pytest.approx(0.172, abs=5e-4)
    c40 = cell(result, "outside_custodian", "headline_40B", "composite_min_headline")
    assert c40["fires"] is True
    assert c40["s_nf0"] == pytest.approx(0.128, abs=5e-4)


def test_vwap_fires_only_on_the_reserve_composition_denominator(result):
    c = cell(result, "outside_custodian", "reserve_composition", "kraken_vwap_min")
    assert c["fires"] is True
    assert c["s_nf0"] == pytest.approx(0.047, abs=5e-4)
    c40 = cell(result, "outside_custodian", "headline_40B", "kraken_vwap_min")
    assert c40["fires"] is False
    assert c40["verdict"] == "none"


def test_kraken_close_returns_no_verdict_under_either_denominator(result):
    for den in ("reserve_composition", "headline_40B"):
        c = cell(result, "outside_custodian", den, "kraken_close_min")
        assert c["fires"] is False, f"kraken close should not fire at {den}"
        assert c["q_star"] is None and c["s_nf0"] is None


def test_full_cash_leg_returns_no_verdict_anywhere(result):
    cells = [g for g in result["grid"] if g["scenario"] == "full_cash_leg"]
    assert len(cells) == 6
    assert all(g["fires"] is False for g in cells)
    assert all(g["verdict"] == "none" for g in cells)


def test_svb_only_fires_on_every_feed_and_denominator(result):
    cells = [g for g in result["grid"] if g["scenario"] == "svb_only"]
    assert len(cells) == 6
    assert all(g["fires"] is True for g in cells), (
        "the disclosed reading must fire on all three feeds; if this breaks, the paper's "
        "headline result is gone"
    )


# ------------------------------------------------------------------------- crossings
def test_zero_crossings_match_one_minus_the_trough(result):
    x = result["feed_zero_crossings_phi"]
    assert x["composite_min_headline"] == pytest.approx(0.1233, abs=5e-5)
    assert x["kraken_vwap_min"] == pytest.approx(0.1071, abs=5e-5)
    assert x["kraken_close_min"] == pytest.approx(0.0990, abs=5e-5)


def test_the_worst_case_straddles_two_crossings(result):
    """This is the finding the scenario exists to state: the verdict is feed-dependent."""
    x = result["feed_zero_crossings_phi"]
    phi_rc = cell(result, "outside_custodian", "reserve_composition",
                  "composite_min_headline")["phi"]
    phi_40 = cell(result, "outside_custodian", "headline_40B",
                  "composite_min_headline")["phi"]
    assert x["kraken_close_min"] < phi_rc < x["kraken_vwap_min"] < x["composite_min_headline"]
    assert x["kraken_vwap_min"] < phi_40 < x["composite_min_headline"]


def test_grid_is_complete(result):
    assert len(result["grid"]) == 3 * 2 * 3


def test_module_refuses_to_write_into_frozen_data():
    with pytest.raises(mod.FrozenFileError):
        mod._assert_safe_write_path(DATA_DIR / "phi_cash_leg_scenarios.json")
