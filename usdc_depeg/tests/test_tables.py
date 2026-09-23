"""Pinning tests for tables.py (TABLE BUILDER GROUP 1: T1/T3/T4).

Checks each fragment is a bare booktabs tabular (no \\begin{table}/\\caption/\\label,
matching the table-fragment convention), carries a leading
`% Source: ...` comment, and that the numbers that matter most, the ones an
adversarial review called out by name, are present and correctly derived from their
source JSON/CSV, not hardcoded independent of it.
"""
import hashlib
import json

import pandas as pd
import pytest

import tables
from constants import DATA_DIR, RESULTS_DIR
from figures import TABLES_DIR


FRAGMENT_FNS = {
    "tab_sources": tables.write_tab_sources,
    "tab_placebo": tables.write_tab_placebo,
}

# TABLE BUILDER GROUP 2, these five fragments already existed on disk
# with no generator behind them; write_tab_panel/write_tab_hhi/write_tab_dai/
# write_tab2_sentence/write_share_range were added to reproduce them. The hashes pin the
# CURRENT committed fragment bytes (as checked out on this repo/platform), if a
# generator fix ever changes a fragment's content on purpose, re-derive the hash from the
# newly-committed file, don't loosen the assertion.
GROUP2_FNS = {
    "tab_panel": tables.write_tab_panel,
    "tab_hhi": tables.write_tab_hhi,
    "tab_dai": tables.write_tab_dai,
    "tab2_sentence": tables.write_tab2_sentence,
    "share_range": tables.write_share_range,
}
GROUP2_SHA256 = {
    # tab_panel/tab_hhi/tab_dai were re-pinned for the width pass: narrower p{}-only
    # column specs, Category column replaced by footnote marks, \texttt{}-in-cell removed.
    # See the tables.py docstrings on write_tab_panel/write_tab_hhi/write_tab_dai.
    #
    # tab_panel re-pinned again after two fixes: an internal process identifier was
    # stripped from an emitted % comment, and the Tether silent_margin now prints 0.288
    # rather than 0.287. The margin is 0.9551 - 0.6676 = 0.2875 exactly, so three decimals
    # give 0.288; the old value came from f"{x:.3f}" rounding the FLOAT, whose nearest
    # double sits just below 0.2875. The prose in fc27.tex has always said 28.8, the
    # table was the side that disagreed. See _r3 in tables.py.
    #
    # G5b re-pinned three of these against the prose audit's number reconciliations:
    #   tab_panel      the third row is named "USDT, Terra episode (May 2022), not
    #                  counted" rather than "USDT 2022", which collided with the screened
    #                  candidate Tether (2022) and disagreed with all four body sites
    #                  (D24(c), audit 16.8). Footnote [b] re-worded off a dangling subject.
    #   tab_hhi        the entity-HHI bootstrap row is gone and the remaining CI prints
    #                  four decimals. With 0 multi-address entities the two intervals
    #                  described one distribution and differed only by RNG stream
    #                  position (audit 16.13).
    #   tab2_sentence  q* prints two decimals, matching the 1.54 headline instead of
    #                  1.5416 (audit 16.31); \star not *; the spaced en dash became a
    #                  sentence break; Proposition/Corollary are \ref, not hardcoded 1.
    "tab_panel": "96a6ab22850b2e6122a9d5059d0f7f8f9aa7f85662a572582f99899bcce8ca02",
    "tab_hhi": "2c7f4ce6b949e696d05065abc9f454ae5249b386306793cf7e5dbd6d58c14f26",
    "tab_dai": "1de6b0b9b065527237e203cb1c1e70c47fba8d1879060422744471f4ed2051c1",
    "tab2_sentence": "dd98fa1323655e59bda3056a50d99768defc34ad1591da613f14645f202d2bb0",
    "share_range": "3514bfcb3381a01f573daf027ce041d4edd4badbaa589fa813098df176f89368",
}


@pytest.fixture(scope="module")
def written():
    # These writers target the FULL paper's tables/. Without this the fixture would
    # call them in a tree that has no full paper, and _write would refuse -- or, before
    # _write was guarded, would recreate paper/fc27/tables/ and put the full paper's
    # fragments back into an export built to exclude them.
    if not tables.REGULAR_PAPER_PRESENT:
        pytest.skip("full paper absent; its fragments are not generated here")
    return {stem: fn() for stem, fn in FRAGMENT_FNS.items()}


@pytest.fixture(scope="module")
def written_group2():
    return {stem: fn() for stem, fn in GROUP2_FNS.items()}


@pytest.mark.skipif(not tables.REGULAR_PAPER_PRESENT,
                    reason="full paper absent; its fragments are not regenerated")
@pytest.mark.parametrize("stem", sorted(GROUP2_FNS))
def test_group2_fragment_matches_pinned_sha256(written_group2, stem):
    """Not just existence, the regenerated fragment must be byte-identical to the
    committed one (paper/fc27/tables/<stem>.tex), pinned by its SHA-256."""
    digest = hashlib.sha256(written_group2[stem].read_bytes()).hexdigest()
    assert digest == GROUP2_SHA256[stem], (
        f"{stem}.tex regenerated with a different hash -- either the generator drifted "
        f"from the committed fragment, or the fragment changed on purpose (in which case "
        f"re-derive GROUP2_SHA256[{stem!r}] from the new committed file)."
    )


def test_fragments_are_bare_tabulars_with_a_source_comment(written):
    for stem, path in written.items():
        text = path.read_text(encoding="utf-8")
        assert text.lstrip().startswith("%"), f"{stem}: missing leading source comment"
        assert r"\begin{tabular}" in text and text.rstrip().endswith(r"\end{tabular}"), (
            f"{stem}: not a bare tabular fragment"
        )
        for forbidden in (r"\begin{table}", r"\caption", r"\label"):
            assert forbidden not in text, f"{stem}: contains {forbidden!r} (fragment convention forbids it)"
        assert text.count("{") == text.count("}"), f"{stem}: unbalanced braces"


def test_tab_sources_row_counts_trace_to_frozen_files(written):
    text = written["tab_sources"].read_text(encoding="utf-8")
    # Original snapshot rows, read directly (not copied from prose). The width pass
    # reordered columns to Series/Source/Rows/Coverage (Rows is no longer last, so it's
    # followed by "&", not by the row-end "\\").
    assert f"& {len(pd.read_csv(DATA_DIR / 'price_hourly.csv'))} &" in text
    with open(DATA_DIR / "redemptions_by_wallet.csv", encoding="utf-8") as f:
        n_redemptions = sum(1 for _ in f) - 1
    assert n_redemptions == 776, "the manuscript's '776 funding addresses' no longer matches"
    # N1 series rows.
    for stem, expected in [
        ("n1/kraken_usdcusd/hourly.csv", 240),
        ("n1/kraken_usdtusd/hourly.csv", 240),
        ("n1/kraken_usdtusd_2019/hourly.csv", 624),
        ("n1/onchain/hourly_block_anchors.csv", 240),
    ]:
        with open(DATA_DIR / stem, encoding="utf-8") as f:
            n = sum(1 for _ in f) - 1
        assert n == expected, f"{stem}: row count drifted from {expected} to {n}"
        assert f"& {n} &" in text, f"{stem}: {n} not found in tab_sources.tex"


def test_tab_placebo_usdt_split_matches_timing_json(written):
    text = written["tab_placebo"].read_text(encoding="utf-8")
    with open(RESULTS_DIR / "usdt_premium_timing.json", encoding="utf-8") as f:
        timing = json.load(f)
    coinbase = timing["peaks"]["coinbase_usdt_usd"]
    composite = timing["peaks"]["composite_usdt"]
    assert round(coinbase["premium_bps"]) == 266
    assert round(composite["premium_bps"]) == 270
    assert "266 bps (11 Mar 02:00 UTC)" in text
    assert "270 bps (13 Mar 19:00 UTC)" in text
    assert r"$\dagger$" in text, "composite (post-backstop) row missing its footnote marker"
    # Composite's own timing must actually be post-backstop, guards against a future
    # data refresh silently flipping the direction this footnote asserts.
    assert composite["relative_to"]["joint_statement_treasury_fed_fdic"]["flag"] == "after"
    # Feed column present and both USDT rows split out.
    assert text.count("USDT & none") == 2
    assert "composite" in text and "Coinbase" in text


def test_r3_rounds_half_up_on_the_exact_decimal():
    """Regression guard for the Tether silent_margin.

    tab_panel printed 0.287 where three decimals of 0.2875 are 0.288, because the
    display helper rounded the FLOAT: 0.2875 has no binary representation, the nearest
    double sits just below it, and f"{x:.3f}" / round() therefore never reach the tie
    rule and round DOWN. The manuscript prose has always said 28.8, so the table was the
    side that was wrong, an automated provenance sweep read it the other way round and
    nearly had the correct prose "corrected" instead.
    """
    assert tables._r3(0.2875) == "0.288", (
        "_r3 must round half-UP on the exact decimal; a float-based round() regresses "
        "this to 0.287 and puts Table 3 back in conflict with the prose's 28.8")
    # Decimal(x) (as opposed to Decimal(repr(x))) reintroduces the same bug, because it
    # takes the exact binary expansion 0.28749999..., pin that this is not what we do.
    assert tables._r3(0.1235) == "0.124"
    assert tables._r3(0.1234) == "0.123"  # ordinary values are unaffected
    # The value actually printed by the Tether row, read from its own source of truth.
    with open(RESULTS_DIR / "specificity_panel.json", encoding="utf-8") as f:
        panel = json.load(f)
    low = [c for c in panel["row2_tether_2019"]["combinations"] if c["trough_label"] == "low"]
    assert tables._r3(max(c["silent_margin"] for c in low)) == "0.288"
