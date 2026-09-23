"""Pins the specificity panel's headline numbers so a future edit to
specificity_panel.py or a change in the frozen inputs cannot silently drift the
episode-comparison table without a visible test failure."""
import pytest

import specificity_panel as sp


def test_tether_phi_all_three_numerators():
    panel = sp.build_panel()
    L = panel["row2_tether_2019"]["liabilities_L_2019-04-24_usd"]
    assert L == pytest.approx(2707970602.50, abs=0.01)

    phis = {c["phi_label"]: c["phi"] for c in panel["row2_tether_2019"]["combinations"]}
    assert phis["phi_700M_amount_already_drawn"] == pytest.approx(0.2585, abs=1e-4)
    assert phis["phi_850M_ag_apparent_loss_characterisation"] == pytest.approx(0.3139, abs=1e-4)
    assert phis["phi_900M_credit_line_ceiling"] == pytest.approx(0.3324, abs=1e-4)


def test_tether_trough_and_floor_margin():
    panel = sp.build_panel()
    combos = panel["row2_tether_2019"]["combinations"]
    low_700 = next(c for c in combos if c["phi_label"] == "phi_700M_amount_already_drawn" and c["trough_label"] == "low")
    assert low_700["trough_hour_utc"] == "2019-04-26T02:00:00Z"
    assert low_700["trough_price"] == pytest.approx(0.9551, abs=1e-4)
    assert low_700["floor"] == pytest.approx(0.7415, abs=1e-4)
    assert low_700["silent_margin"] == pytest.approx(0.2136, abs=1e-3)
    assert not panel["row2_tether_2019"]["any_combination_fires"]


def test_tether_liabilities_rose_no_dilution_adjustment():
    panel = sp.build_panel()
    assert panel["row2_tether_2019"]["dilution_check"]["liabilities_rose_over_window"] is True


def test_reserve_row_removed():
    """An adversarial review KILLED Row 3 (Reserve Primary Fund 2008): its base was a
    one-parameter root-solve misattributed to the wrong quantity, and an unexplained
    markdown violates A1. The row and its row3_reserve() builder are gone, record
    that removal, don't leave a silent gap where a pin used to be."""
    panel = sp.build_panel()
    assert "row3_reserve_2008" not in panel
    assert not hasattr(sp, "row3_reserve")


def test_row1_usdc_fires_under_every_reading():
    panel = sp.build_panel()
    row1 = panel["row1_usdc_march_2023"]
    assert row1["all_combinations_fire"] is True
    assert len(row1["combinations"]) == 16  # 4 phi variants x 4 trough readings
    assert all(c["fires"] for c in row1["combinations"])


def test_row4_terra_degenerate():
    panel = sp.build_panel()
    row4 = panel["row4_terra_2022"]
    assert row4["degenerate"] is True
    assert row4["phi"] == 0.0
    assert row4["USDT"]["fires"] is True
    assert row4["USDC"]["fires"] is True


def test_footer_has_seven_candidates():
    """Seven, not eight, since D25(d) (G6) removed FDUSD.

    The manuscript named it as a screened candidate with no supporting clause, while
    every other entry carries one. It is out of the paper at both sites, so it is out of
    this record too: the claim map points Appendix D's screen at this file, and a paper
    that screens seven against a results file that screens eight is a provenance defect a
    referee running the artifact would find."""
    panel = sp.build_panel()
    assert len(panel["footer_appendix_d_non_qualifying_candidates"]) == 7


# --------------------------------------------------------------------------------------
# Paxos/USDP March 2023 candidate row, an adversarial review's finding folded
# this into build_panel() itself (it used to be appended out-of-band to the JSON,
# which would have been silently dropped by a re-run); these pins now go
# through sp.build_panel() like every other row.
# --------------------------------------------------------------------------------------
def test_paxos2023_phi_and_floor():
    row5 = sp.build_panel()["row5_paxos2023_candidate"]
    combo = row5["combinations"][0]
    assert combo["phi"] == pytest.approx(0.3024, abs=1e-4)
    assert combo["floor"] == pytest.approx(0.6976, abs=1e-4)
    assert row5["dollar_exposure_usd"] == pytest.approx(250_000_000.0)
    assert row5["contemporaneous_usdp_supply_usd"] == pytest.approx(826_745_086.0)


def test_paxos2023_trough_stays_silent():
    row5 = sp.build_panel()["row5_paxos2023_candidate"]
    combo = row5["combinations"][0]
    assert combo["trough_price"] == pytest.approx(0.98293, abs=1e-4)
    assert combo["trough_hour_utc"] == "2023-03-13T08:01:02Z"
    assert combo["fires"] is False
    assert combo["silent_margin"] == pytest.approx(0.2853, abs=1e-3)


def test_paxos2023_disqualified_disclosed_after_resolution():
    """The crux: Paxos's only quantified Silvergate/Signature disclosure ($250M at
    Signature Bank) was posted ~80 minutes AFTER the 2023-03-12T22:15:00Z joint
    Treasury/Fed/FDIC statement, criterion (iii) fails, so this row does NOT qualify
    as a valid true-negative panel member despite the bound staying silent."""
    row5 = sp.build_panel()["row5_paxos2023_candidate"]
    qual = row5["qualification"]
    assert qual["criterion_i_quantified"] is True
    assert qual["criterion_ii_public"] is True
    assert qual["criterion_iii_pre_resolution"] is False
    assert qual["qualifies"] is False
    assert qual["minutes_after_joint_statement"] == pytest.approx(80.4, abs=0.1)
    assert row5["status"] == "disclosed-after-resolution, not a panel member"
    assert row5["joint_statement_ts_utc"] == "2023-03-12T22:15:00Z"
    assert row5["disclosure_ts_utc"] == "2023-03-12T23:35:23.891000Z"


def test_paxos2023_category_is_resolved_impairment_control():
    """An adversarial review: Paxos is reframed as a resolved-impairment control evidencing the
    SCARCITY mechanism, not a rejected panel candidate, the category field must carry
    that framing plus the already-computed offset/phi/floor/trough/margin, not
    recompute them."""
    row5 = sp.build_panel()["row5_paxos2023_candidate"]
    assert row5["category"] == "resolved-impairment control (q-dimension)"
    combo = row5["combinations"][0]
    ev = row5["category_evidence"]
    assert ev["post_backstop_offset_minutes"] == pytest.approx(80.4, abs=0.1)
    assert ev["phi"] == combo["phi"]
    assert ev["floor"] == combo["floor"]
    assert ev["trough_price"] == combo["trough_price"]
    assert ev["silent_margin"] == combo["silent_margin"]


# --------------------------------------------------------------------------------------
# Restated precondition (i) (C3/C7): every surviving row states it, and the TUSD footer
# entry states how it fails it.
# --------------------------------------------------------------------------------------
def test_precondition_i_on_every_surviving_row():
    panel = sp.build_panel()
    expected = "publicly quantified while the exposure was unresolved"
    for key in ("row1_usdc_march_2023", "row2_tether_2019", "row4_terra_2022", "row5_paxos2023_candidate"):
        assert panel[key]["precondition_i"] == expected


def test_tether_category_field():
    row2 = sp.build_panel()["row2_tether_2019"]
    assert row2["category"] == ("publicly quantified while unresolved; regulator-quantified, "
                                 "issuer-contested on characterization; counsel percentage unfrozen")


def test_tusd_footer_states_precondition_i_failure():
    footer = sp.build_panel()["footer_appendix_d_non_qualifying_candidates"]
    tusd = next(c for c in footer if c["candidate"] == "TUSD")
    assert "precondition_i_failure" in tusd
    assert "18" in tusd["precondition_i_failure"]  # ~18mo lag is the crux of the failure


# --------------------------------------------------------------------------------------
# for_print: the flat block the manuscript and the number audit read.
#
# Row 2's impairment readings live inside a LIST, and the audit's key resolver walks
# dictionaries only, so a figure taken from that list cannot be traced to a key. These
# pins are what make the four-decimal phi printable at all.
# --------------------------------------------------------------------------------------
def test_for_print_tether_bounds_are_the_extremes_of_row2():
    panel = sp.build_panel()
    fp = panel["for_print"]
    phis = sorted(c["phi"] for c in panel["row2_tether_2019"]["combinations"])
    assert fp["tether2019_phi_lo"] == pytest.approx(min(phis), abs=0)
    assert fp["tether2019_phi_hi"] == pytest.approx(max(phis), abs=0)
    assert fp["tether2019_phi_lo"] == pytest.approx(0.2585, abs=1e-9)
    assert fp["tether2019_phi_hi"] == pytest.approx(0.3324, abs=1e-9)


def test_for_print_floor_is_one_minus_phi_at_full_precision():
    """The whole reason this block exists.

    The manuscript prints an impairment range and a floor range side by side, and a
    reader checks one against the other. At three decimals they do not reconcile
    (1 - 0.259 = 0.741, while the floor prints 0.742 because it rounds from 0.7415), so
    the identity has to hold at the precision the block stores and the prose has to
    print four decimals.
    """
    fp = sp.build_panel()["for_print"]
    assert fp["tether2019_floor_hi"] == pytest.approx(1.0 - fp["tether2019_phi_lo"], abs=1e-12)
    assert fp["tether2019_floor_lo"] == pytest.approx(1.0 - fp["tether2019_phi_hi"], abs=1e-12)
    assert round(fp["tether2019_floor_hi"], 3) == 0.742
    assert round(fp["tether2019_floor_lo"], 3) == 0.668


def test_for_print_q_star_bounds_come_from_the_low_feed():
    panel = sp.build_panel()
    fp = panel["for_print"]
    q_low = [c["q_star_rho0"] for c in panel["row2_tether_2019"]["combinations"]
             if c["trough_label"] == "low"]
    assert fp["tether2019_q_star_low_feed_lo"] == pytest.approx(min(q_low), abs=0)
    assert fp["tether2019_q_star_low_feed_hi"] == pytest.approx(max(q_low), abs=0)


def test_for_print_below_par_screen_is_the_footer_minus_one_named_candidate():
    """Two counts of the same screen, and the difference is a recorded ruling.

    The footer screens seven candidates. Only six of them are candidates for a
    BELOW-PAR floor test, because DAI's March 2020 episode traded above par, so the
    precondition that the price fall below par is not testable on it. Both counts are
    quoted -- in different documents -- so a reader who finds them side by side must be
    able to see why they differ, and the basis string must say it is a ruling rather
    than something this snapshot measures.
    """
    fp = sp.build_panel()["for_print"]
    assert fp["screened_candidates_n"] == 7
    assert fp["below_par_screen_n"] == 6
    assert fp["below_par_screen_excludes"] in fp["screened_candidates"]
    assert "RULING" in fp["below_par_screen_exclusion_basis"]
    assert "above par" in fp["below_par_screen_exclusion_basis"].lower()
