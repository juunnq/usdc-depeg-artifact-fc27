"""Pins the three results files makewhole_timing.json, ordering_null.json, and
hourly_counts.json produce so a future edit or data refresh cannot silently
drift them. One file, three sections (one per module), kept together because all three
are small, share no state, and were built as a single deliverable."""
import json

import pytest

import makewhole_timing as mw
import ordering_null as on
import hourly_counts as hc
from constants import RESULTS_DIR, DATA_DIR


# --- makewhole_timing.py ------------------------------------------------------------

def test_makewhole_pledge_timing_computed_from_frozen_wayback_capture():
    """RESOLVED: circle2023update is now frozen (first Wayback
    capture of the blog post), so this is a bound COMPUTED value, not a GAP, and the
    upper-bound gap is well under 24h, so 'more than a day' is a sound FALSE regardless
    of the (unknown) exact true publish instant."""
    r = mw.circle_makewhole_timing()
    assert r["status"] == "COMPUTED"
    assert r["bib_key"] == "circle2023update"
    assert r["more_than_a_day"] is False
    assert r["hours_after_trough_upper_bound"] == pytest.approx(12.5, abs=1e-3)
    by_ref = r["by_trough_reference"]
    assert by_ref["composite_headline"]["more_than_a_day"] is False
    assert by_ref["kraken_usdc_low"]["more_than_a_day"] is False
    assert by_ref["kraken_usdc_low"]["hours_after_trough_upper_bound"] == pytest.approx(13.4647, abs=1e-3)


def test_federal_action_timing_more_than_a_day_true():
    """The federal-action side of the claim IS computable from frozen data and is TRUE
    under both trough references (composite headline and Kraken low)."""
    r = mw.federal_action_timing()
    assert r["more_than_a_day"] is True
    assert r["hours_after_trough"] == pytest.approx(38.2853, abs=1e-3)
    by_ref = r["by_trough_reference"]
    assert by_ref["composite_headline"]["more_than_a_day"] is True
    assert by_ref["kraken_usdc_low"]["more_than_a_day"] is True
    assert by_ref["composite_headline"]["hours_after_trough"] == pytest.approx(38.2853, abs=1e-3)
    assert by_ref["kraken_usdc_low"]["hours_after_trough"] == pytest.approx(39.25, abs=1e-3)


def test_federal_action_ts_matches_frozen_joint_statement():
    r = mw.federal_action_timing()
    assert (r["by_trough_reference"]["composite_headline"]["federal_action_ts_utc"]
            == "2023-03-12T22:15:00Z")


def test_write_result_refuses_to_write_under_data_dir():
    with pytest.raises(PermissionError):
        mw._write_result(DATA_DIR / "should_not_write_here.json", {"x": 1})


def test_makewhole_timing_results_file_matches_live_report():
    path = RESULTS_DIR / "makewhole_timing.json"
    if not path.exists():
        pytest.skip("makewhole_timing.py has not been run to persist the JSON yet")
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    live = mw.report()
    assert on_disk["circle_makewhole_pledge"]["status"] == live["circle_makewhole_pledge"]["status"]
    assert (on_disk["federal_backstop_action"]["more_than_a_day"]
            == live["federal_backstop_action"]["more_than_a_day"])


# --- ordering_null.py ----------------------------------------------------------------

def test_ordering_null_is_one_half():
    """Ruling D16: DAI is mechanically linked to USDC through the PSM, so it is not an
    exchangeable draw and the USDC-DAI pair moves as one block. The single free
    comparison is USDT's placement relative to that block, giving 1 matching arrangement
    of 2, not 1 of 3! = 6. Enumerating all six would treat DAI as independent, which
    Appendix B's own contagion argument denies."""
    r = on.report()
    assert r["n_permutations"] == 2
    assert r["n_matching_claimed_order"] == 1
    assert r["probability_null_uniform"] == pytest.approx(1 / 2, abs=1e-12)
    assert r["probability_as_fraction"] == "1/2"


def test_ordering_null_claim_extraction_actually_quotes_the_claims():
    """Regression guard for the exact defect this module's docstring describes: an
    earlier revision hardcoded fc27.tex LINE NUMBERS to locate the two claims, and a
    later manuscript edit that shifted lines above them made the extraction silently
    quote unrelated text (a table caption) instead of raising or producing garbage that
    would fail some other check. Assert the extracted text actually contains the claims'
    defining content, and that a real (positive) line number was found for each, not
    just that SOME string came back."""
    claims = on._manuscript_claim_lines()
    assert "ex ante" in claims["ex_ante_claim"]
    assert "USDT held none" in claims["ex_ante_claim"]
    assert "USDC, DAI, USDT" in claims["ex_ante_claim"]
    assert claims["ex_ante_claim_tex_line"] > 0
    assert "Troughs are" in claims["realized_ordering_claim"]
    assert "0.877" in claims["realized_ordering_claim"]
    assert "0.886" in claims["realized_ordering_claim"]
    assert "0.992" in claims["realized_ordering_claim"]
    assert claims["realized_ordering_claim_tex_line"] > 0
    # the two claims must not be pointing at the same place
    assert claims["ex_ante_claim_tex_line"] != claims["realized_ordering_claim_tex_line"]


def test_ordering_null_missing_anchor_raises_not_silently_wrong():
    """The other direction of the regression guard above: if the manuscript ever drops
    an anchor phrase entirely, extraction must raise (loud), not return whatever
    happens to sit at a stale offset (silent-wrong, the original failure mode)."""
    with pytest.raises(ValueError):
        on._extract_between("nothing relevant in this text", "NOT PRESENT", "also absent")


def test_ordering_null_enumerates_the_two_admissible_arrangements_explicitly():
    """Under D16 the USDC-DAI block is not split, so the only arrangements enumerated
    are USDT outside the block and USDT inside it. Any arrangement that separates USDC
    from DAI would contradict the mechanical link Appendix B establishes."""
    perms = on.enumerate_permutations()
    assert len(perms) == 2
    orders = {tuple(p["order_most_fragile_first"]) for p in perms}
    assert orders == {("USDC", "DAI", "USDT"), ("USDT", "USDC", "DAI")}


def test_ordering_null_claimed_order_matches_manuscript():
    r = on.report()
    assert r["claimed_order_most_fragile_first"] == ["USDC", "DAI", "USDT"]
    matching = [p for p in r["permutations"] if p["matches_claimed_order"]]
    assert len(matching) == 1
    assert matching[0]["order_most_fragile_first"] == ["USDC", "DAI", "USDT"]


def test_ordering_null_results_file_matches_live_report():
    path = RESULTS_DIR / "ordering_null.json"
    if not path.exists():
        pytest.skip("ordering_null.py has not been run to persist the JSON yet")
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    live = on.report()
    assert on_disk["probability_null_uniform"] == live["probability_null_uniform"]
    assert on_disk["n_matching_claimed_order"] == live["n_matching_claimed_order"]

    # The manuscript line pointers too, not just the probability.
    #
    # This file records where in the manuscript each claim sits. Those offsets move
    # whenever ANY prose above them moves, so the file goes stale on edits that have
    # nothing to do with the ordering null. It did exactly that twice, in G5b and again
    # in G6, and both times the only thing that noticed was a clean-clone comparison,
    # because every assertion here was on the probability, which never changes.
    # Comparing the pointers turns a silent staleness into a test failure, and the fix
    # is always the same: re-run ordering_null.py.
    for key in ("ex_ante_claim_tex_line", "realized_ordering_claim_tex_line"):
        got = on_disk["manuscript_claim_lines"][key]
        want = live["manuscript_claim_lines"][key]
        assert got == want, (
            f"{key} on disk is {got}, the manuscript now says {want}. "
            f"results/ordering_null.json is stale; re-run `python ordering_null.py`."
        )


# --- hourly_counts.py ------------------------------------------------------------------

def test_hourly_counts_match_manuscript_table_1():
    r = hc.report()
    assert r["per_coin"]["USDC"]["n_observed"] == 212
    assert r["per_coin"]["DAI"]["n_observed"] == 214
    assert r["per_coin"]["USDT"]["n_observed"] == 213
    assert all(r["counts_match_manuscript"].values())


def test_hourly_counts_expected_window_is_216_hours():
    r = hc.report()
    assert r["window"]["expected_hours"] == 216
    assert r["window"]["start_utc"] == "2023-03-08T00:00:00Z"
    assert r["window"]["end_utc"] == "2023-03-17T00:00:00Z"


def test_hourly_counts_missing_hours_explicit_per_coin():
    r = hc.report()
    assert r["per_coin"]["USDC"]["missing_hours_utc"] == [
        "2023-03-13T17:00:00Z", "2023-03-14T14:00:00Z",
        "2023-03-14T17:00:00Z", "2023-03-15T12:00:00Z",
    ]
    assert r["per_coin"]["DAI"]["missing_hours_utc"] == [
        "2023-03-13T17:00:00Z", "2023-03-14T16:00:00Z",
    ]
    assert r["per_coin"]["USDT"]["missing_hours_utc"] == [
        "2023-03-11T20:00:00Z", "2023-03-13T17:00:00Z", "2023-03-14T17:00:00Z",
    ]


def test_hourly_counts_one_hour_missing_across_all_three_coins():
    """2023-03-13T17:00:00Z is a common provider gap, not a coin-specific effect."""
    r = hc.report()
    assert r["hours_missing_in_all_three_coins"] == ["2023-03-13T17:00:00Z"]


def test_hourly_counts_rounding_is_unambiguous():
    """Every raw row lands within 5 minutes of its target hour, well under the 30-minute
    ambiguity threshold the reconciliation relies on."""
    r = hc.report()
    for coin in ("USDC", "DAI", "USDT"):
        assert r["per_coin"][coin]["max_abs_rounding_offset_s"] < 1800


def test_hourly_counts_results_file_matches_live_report():
    path = RESULTS_DIR / "hourly_counts.json"
    if not path.exists():
        pytest.skip("hourly_counts.py has not been run to persist the JSON yet")
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    live = hc.report()
    assert on_disk["per_coin"] == live["per_coin"]
