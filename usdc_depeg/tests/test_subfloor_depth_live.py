"""Sub-floor depth restricted to the window in which the floor actually existed.

The headline assertion here is the one that would be easy to lose: every sub-floor print
falls AFTER the disclosure, so restricting to the live window removes nothing. That is a
substantive fact about the episode, not a tautology, and if a future data refresh ever
broke it the paper's "while the disclosure was live" framing would need rewriting.
"""
import json

import pytest

import subfloor_depth_live as mod
from constants import DATA_DIR, RESULTS_DIR

RESULT_PATH = RESULTS_DIR / "subfloor_depth_live.json"


@pytest.fixture(scope="module")
def result():
    if not RESULT_PATH.exists():
        pytest.skip(f"{RESULT_PATH.name} not generated; run subfloor_depth_live.py")
    return json.loads(RESULT_PATH.read_text(encoding="utf-8"))


def test_window_bounds_are_the_disclosure_and_the_backstop(result):
    assert result["window"]["start_utc"] == "2023-03-11T03:11:00Z"
    assert result["window"]["end_utc"] == "2023-03-12T22:15:00Z"
    assert result["window"]["interval"] == "[start, end)"


def test_unrestricted_totals_reproduce_the_existing_figures(result):
    """Cross-check against trough_corroboration.json's below_floor_depth."""
    u = result["unrestricted"]
    assert u["n_trades"] == 37303
    assert u["volume_usdc"] == pytest.approx(169898078.33, abs=0.5)
    assert u["notional_usd"] == pytest.approx(154007404.51, abs=0.5)
    assert result["cross_check_vs_trough_corroboration"]["matches"] is True


def test_the_two_depth_figures_are_different_quantities(result):
    """169.9M is COINS; the dollar figure is 154.0M. Conflating them is a unit error."""
    u = result["unrestricted"]
    assert u["volume_usdc"] > u["notional_usd"], (
        "coins exceed dollars here because every sub-floor trade cleared below par"
    )
    assert abs(u["volume_usdc"] - u["notional_usd"]) > 15e6
    assert "must not be written as '$169.9M'" in result["units_note"]


def test_no_subfloor_trade_precedes_the_disclosure(result):
    """The substantive finding: the floor existed for every sub-floor print."""
    assert result["excluded_before_disclosure"]["n_trades"] == 0
    assert result["excluded_before_disclosure"]["volume_usdc"] == 0.0


def test_no_subfloor_trade_follows_the_backstop(result):
    assert result["excluded_after_backstop"]["n_trades"] == 0


def test_live_window_equals_the_unrestricted_totals(result):
    """Follows from the two exclusions being empty, and is what lets the paper say
    'while the disclosure was live' without changing any published figure."""
    live, unres = result["live"], result["unrestricted"]
    for key in ("n_trades", "volume_usdc", "notional_usd", "clock_hours_touched"):
        assert live[key] == unres[key], f"{key} differs between live and unrestricted"


def test_live_window_shape(result):
    live = result["live"]
    assert live["n_trades"] == 37303
    assert live["clock_hours_touched"] == 12
    assert live["first_trade_utc"] == "2023-03-11T03:58:58Z"
    assert live["last_trade_utc"] == "2023-03-11T15:43:52Z"


def test_first_subfloor_trade_is_after_the_disclosure_minute(result):
    first = result["live"]["first_trade_utc"]
    assert first > result["window"]["start_utc"], (
        "the first sub-floor print must fall after the disclosure for the live-window "
        "framing to hold"
    )


def test_timestamp_unit_is_seconds_not_milliseconds():
    """A ms parse of this column succeeds silently and yields 1970 dates."""
    df = mod.load_trades()
    assert df["ts"].min().year == 2023, "trades parsed to the wrong epoch unit"
    assert df["ts"].max().year == 2023


def test_every_counted_trade_is_below_the_floor():
    df = mod.load_trades()
    sub = df[df["price"] < mod.FLOOR]
    assert len(sub) == 37303
    assert sub["price"].max() < mod.FLOOR


def test_module_refuses_to_write_into_frozen_data():
    with pytest.raises(mod.FrozenFileError):
        mod._assert_safe_write_path(DATA_DIR / "subfloor_depth_live.json")
