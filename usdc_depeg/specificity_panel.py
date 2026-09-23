"""Specificity panel: applies Proposition 1's rational-pricing bound
(rational_bound.py: floor_worstcase, implied_failure_prob, below_floor_share) to
historical stablecoin de-peg episodes, plus a pointer-only footer of the
non-qualifying candidates surveyed in the manuscript's Appendix D.

ANALYSIS ONLY on already-frozen inputs (see class docstrings below for each row's
sources). No input is ever adjusted to change an outcome: every row reports what the
frozen numbers say, fires or not.

An adversarial review KILLED the former Row 3 (Reserve Primary Fund, September 2008):
its base was a one-parameter root-solve misattributed to the wrong quantity, and an
unexplained markdown violates A1 (the fund's disclosed Lehman position does not
reconcile to its declared NAV). Row 3 and the row3_reserve() function that built it
are removed from this module. No manuscript text or other module cites
row3_reserve_2008 (grepped clean), so nothing else needs updating.

No interpretation / narrative is written here, only numbers, provenance, and the JSON
this module writes to results/specificity_panel.json.
"""
import csv
import json
from datetime import datetime, timezone

from rational_bound import floor_worstcase, implied_failure_prob, below_floor_share
from constants import DATA_DIR, RESULTS_DIR

N1_DIR = DATA_DIR / "n1"
PAXOS_DIR = N1_DIR / "paxos2023"

# N7/C3+C7's restated precondition (i): the old manuscript wording ("an impairment
# fraction phi that THE ISSUER disclosed publicly, contemporaneously and accurately")
# is dropped in favour of a criterion that also folds in the timing test that used to
# be a separate precondition (iii), a figure quantified only AFTER the exposure it
# describes has already been resolved (Paxos/Row 5) fails this exactly as much as a
# figure that was never public at all. Every row below is checked against this one
# restated sentence.
PRECONDITION_I = "publicly quantified while the exposure was unresolved"


def _combo(trough_price, phi, phi_label, trough_label, trough_feed, trough_hour_utc, **extra):
    """One (phi, trough) reading: floor / q* / fires? / silent_margin, via rational_bound."""
    floor = floor_worstcase(phi)
    q_star = implied_failure_prob(trough_price, phi, 0.0)
    fires = trough_price < floor
    row = {
        "phi": round(phi, 4),
        "phi_label": phi_label,
        "floor": round(floor, 4),
        "trough_price": trough_price,
        "trough_label": trough_label,
        "trough_feed": trough_feed,
        "trough_hour_utc": trough_hour_utc,
        "q_star_rho0": round(q_star, 4),
        "fires": fires,
        "silent_margin": round(trough_price - floor, 4),
    }
    row.update(extra)
    return row


# --------------------------------------------------------------------------------------
# Row 1, USDC March 2023 (true positive)
# Sources: results/phi_dynamic.json (dynamic phi at trough, alt-reserve-total phi),
#          results/trough_corroboration.json (cross-feed troughs: composite headline
#          + Kraken close/vwap/low), results/impossibility_window.json / number1b (static
#          phi=0.08 baseline).
# --------------------------------------------------------------------------------------
def row1_usdc(phi_dynamic: dict, trough_corrob: dict) -> dict:
    phi_variants = [
        ("static_phi_svb_0.08", 0.08),
        ("static_phi_altreserve_42.1B_0.0784", phi_dynamic["third_line_alt_reserve_total"]["phi"]),
        ("static_phi_3.3_over_40.0_0.0825", 3.3e9 / 40.0e9),
        ("dynamic_phi_at_composite_trough_hour", phi_dynamic["variant_i"]["phi_at_trough"]),
    ]
    series = trough_corrob["firing_report"]["series"]
    troughs = [
        ("headline_print", "composite", series["composite"]["min_print"], series["composite"]["min_print_utc"]),
        ("low", "kraken_usdc", series["kraken_usdc_low"]["min_print"], series["kraken_usdc_low"]["min_print_utc"]),
        ("close", "kraken_usdc", series["kraken_usdc_close"]["min_print"], series["kraken_usdc_close"]["min_print_utc"]),
        ("vwap", "kraken_usdc", series["kraken_usdc_vwap"]["min_print"], series["kraken_usdc_vwap"]["min_print_utc"]),
    ]
    combos = []
    for phi_label, phi in phi_variants:
        for trough_label, feed, price, hour in troughs:
            hour_matched = (phi_label != "dynamic_phi_at_composite_trough_hour") or (feed == "composite")
            combos.append(_combo(
                price, phi, phi_label, trough_label, feed, hour,
                phi_trough_hour_matched=hour_matched,
            ))
    return {
        "episode": "USDC March 2023",
        "classification": "true positive",
        "precondition_i": PRECONDITION_I,
        "disclosure_source": "Circle official account, restated ~8% in Circle 2023-03-12 press release",
        "disclosure_ts_utc": phi_dynamic["disclosure_ts_utc"],
        "combinations": combos,
        "all_combinations_fire": all(c["fires"] for c in combos),
    }


# --------------------------------------------------------------------------------------
# Row 2, Tether April-May 2019 (candidate true negative)
# Sources: data/n1/tether2019/disclosure_passages_nyag.json (three distinct dollar
#          figures, verbatim, D14), data/n1/tether2019/usdt_liabilities_wayback.json
#          (same-day 2019-04-24 liabilities), data/n1/kraken_usdtusd_2019/hourly.csv
#          (post-disclosure trough, sliced 2019-04-25 onward per the task window).
# --------------------------------------------------------------------------------------
def row2_tether(nyag_passages: dict, wayback: dict) -> dict:
    disclosure_date = nyag_passages["passages"][0]["date"]  # "2019-04-25", on-page date

    # L: same-day (2019-04-24, the NYAG order date) liabilities, verify from the frozen file.
    l_entry = next(
        c for c in wayback["liabilities_by_capture"] if c["capture_timestamp"].startswith("2019-04-24")
    )
    L = float(l_entry["reported_figure_verbatim"].replace("$", "").replace(",", ""))

    # Dilution check: liabilities at window start (2019-04-24 capture, used as L) vs at
    # window end (last capture, 2019-05-15), verify the frozen series rose, not fell.
    l_start = L
    l_end_entry = wayback["liabilities_by_capture"][-1]
    l_end = float(l_end_entry["reported_figure_verbatim"].replace("$", "").replace(",", ""))
    liabilities_rose = l_end > l_start

    numerators = {p["category"]: p for p in nyag_passages["passages"]}
    phi_k_specs = [
        ("phi_700M_amount_already_drawn", 700e6, numerators["amount_drawn"]["verbatim_passage"]),
        ("phi_850M_ag_apparent_loss_characterisation", 850e6, numerators["impairment_amount"]["verbatim_passage"]),
        ("phi_900M_credit_line_ceiling", 900e6, numerators["credit_line_ceiling"]["verbatim_passage"]),
    ]

    # Post-disclosure Kraken USDT/USD trough, sliced from the frozen 2019-04-20-start series.
    with open(N1_DIR / "kraken_usdtusd_2019" / "hourly.csv", newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["hour_utc"] >= f"{disclosure_date}T00:00:00Z"]
    for r in rows:
        r["low"] = float(r["low"]); r["close"] = float(r["close"]); r["vwap"] = float(r["vwap"])
    trough_low = min(rows, key=lambda r: r["low"])
    trough_close = min(rows, key=lambda r: r["close"])
    trough_vwap = min(rows, key=lambda r: r["vwap"])
    troughs = [
        ("low", trough_low["low"], trough_low["hour_utc"]),
        ("close", trough_close["close"], trough_close["hour_utc"]),
        ("vwap", trough_vwap["vwap"], trough_vwap["hour_utc"]),
    ]

    combos = []
    for phi_label, numerator, verbatim in phi_k_specs:
        phi = numerator / L
        for trough_label, price, hour in troughs:
            combos.append(_combo(
                price, phi, phi_label, trough_label, "kraken_usdtusd_2019", hour,
                numerator_usd=numerator, numerator_verbatim_passage=verbatim,
            ))

    return {
        "episode": "Tether April-May 2019",
        "classification": "candidate true negative (per D14)",
        "category": "publicly quantified while unresolved; regulator-quantified, "
                     "issuer-contested on characterization; counsel percentage unfrozen",
        "precondition_i": PRECONDITION_I,
        "disclosure_source": "NYAG press release, ag.ny.gov, on-page date 2019-04-25 "
                              "(exact intraday UTC publication time NOT in the frozen record; "
                              "corroborated same-day by Tether's own response, Wayback-captured "
                              "2019-04-26T02:57:29Z, referencing 'Earlier today')",
        "disclosure_ts_utc_gap": "date-level only (2019-04-25); no intraday UTC timestamp frozen",
        "liabilities_L_2019-04-24_usd": L,
        "liabilities_L_source_capture": l_entry["capture_timestamp"],
        "post_disclosure_window": f"{disclosure_date} 00:00 UTC through 2019-05-15 23:00 UTC "
                                   "(series slice; frozen series itself starts 2019-04-20)",
        "dilution_check": {
            "liabilities_at_window_start_usd": l_start,
            "liabilities_at_window_end_usd": l_end,
            "liabilities_rose_over_window": liabilities_rose,
            "note": "Liabilities ROSE over the post-disclosure window (verified from "
                    "usdt_liabilities_wayback.json) -- no residual-base adjustment (of the kind "
                    "applied to Reserve 2008's post-redemption base) applies here.",
        },
        "counsel_percentage_gap": "The ~2019-04-30 Tether counsel (Hoegner) affirmation reporting a "
                                   "cash/cash-equivalent backing percentage is UNFROZEN (see "
                                   "disclosure_passage_n1cretry.json instrument-gap log) -- would be a "
                                   "confirming source per D14, not required for this panel.",
        "combinations": combos,
        "any_combination_fires": any(c["fires"] for c in combos),
    }


# --------------------------------------------------------------------------------------
# Row 4, USDT May 2022 Terra (degenerate, phi = 0)
# Source: results/second_event_terra.json ONLY.
# --------------------------------------------------------------------------------------
def row4_terra(terra: dict) -> dict:
    return {
        "episode": terra["event"],
        "classification": "degenerate (phi = 0 by construction)",
        "precondition_i": PRECONDITION_I,
        "degenerate": True,
        "phi": terra["phi_terra_exposure"],
        "floor": terra["floor_worstcase"],
        "disclosure_source": "no USDT/USDC Terra/UST/LUNA reserve exposure (phi ~= 0 by construction)",
        "USDT": {
            "trough": terra["USDT"]["trough"],
            "fires": terra["USDT"]["trough"] < terra["floor_worstcase"],
            "silent_margin": round(terra["USDT"]["trough"] - terra["floor_worstcase"], 6),
            "below_floor_nonfundamental_share": terra["USDT"]["below_floor_nonfundamental_share"],
        },
        "USDC": {
            "trough": terra["USDC"]["trough"],
            "fires": terra["USDC"]["trough"] < terra["floor_worstcase"],
            "silent_margin": round(terra["USDC"]["trough"] - terra["floor_worstcase"], 6),
            "below_floor_nonfundamental_share": terra["USDC"]["below_floor_nonfundamental_share"],
        },
    }


# --------------------------------------------------------------------------------------
# Footer, the eight surveyed non-qualifying candidates (manuscript Appendix D,
# paper/fc27/fc27.tex lines 880-941). Pointer only, no analysis.
# --------------------------------------------------------------------------------------
def footer_appendix_d() -> list:
    return [
        {"candidate": "BUSD", "fails": "no disclosed own-reserve impairment (phi=0); "
                                        "sets degenerate Terra-style null, not a second identification"},
        {"candidate": "Tether (2022)", "fails": "no disclosed own-reserve impairment (phi=0); "
                                                 "disclosures are asset-composition breakdowns / rumour "
                                                 "denials, not an impairment fraction"},
        # FDUSD removed under D25(d), G6. The manuscript asserted it as a screened
        # candidate with no supporting clause anywhere in the paper, while BUSD and
        # Tether (2022) each carried one, and the author ruled it out of the list at both
        # manuscript sites. This record follows the manuscript rather than diverging from
        # it: leaving the entry here would have the paper screen seven candidates while
        # the results file the claim map points at screens eight. The screen is now
        # SEVEN. Restoring FDUSD means restoring it at both manuscript sites and in
        # tests/test_specificity_panel.py, which asserts the count.
        {"candidate": "DAI (2020, 'Black Thursday')", "fails": "has a disclosed bad-debt figure, but is a "
                                                                 "collateral-liquidation cascade requiring a "
                                                                 "different fundamental-value model -- "
                                                                 "violates precondition (iii)"},
        {"candidate": "USDR", "fails": "tokenized-real-estate hybrid -- violates (A1)"},
        {"candidate": "USDP", "fails": "fails the diagnostic-print test (calm-window baseline already "
                                        "crosses the 1% threshold on this feed)"},
        {"candidate": "GUSD", "fails": "fails the diagnostic-print test (calm-window baseline already "
                                        "crosses the 1% threshold on this feed)"},
        {"candidate": "TUSD", "fails": "fails precondition (i), the public-information scope condition: "
                                        "the alleged impairment (SEC 2024 complaint) sat in nonpublic "
                                        "internal records, became public ~18mo after the analysis window; "
                                        "phi drawn from the contemporaneous public information set was "
                                        "effectively zero",
         "precondition_i": PRECONDITION_I,
         "precondition_i_failure": "the SEC complaint that quantified TUSD's impairment was not made "
                                    "public until 2024 -- roughly 18 months after the March 2023 window "
                                    "had already resolved one way or the other. Nothing was publicly "
                                    "quantified DURING the unresolved period; the quantification exists "
                                    "only in hindsight, so the row fails precondition (i) on the same "
                                    "'quantified while still unresolved' test that disqualifies Paxos "
                                    "(row5) on timing, not on the fact of quantification itself."},
    ]


# --------------------------------------------------------------------------------------
# Row 5, Paxos/USDP March 2023 (disqualified candidate on timing).
#
# Was appended out-of-band to results/specificity_panel.json by
# data_fetch_n1.py::laneP_main because build_panel() didn't originally cover this row;
# that function's own docstring flagged that a re-run of this script would silently drop
# the row. This function brings it into build_panel() so the row survives a re-run, reading
# ONLY the frozen files already captured (no network), disclosure_passages_paxos.json,
# usdp_supply_daily.csv, joint_statement_treasury_fed_fdic.json, and
# data/excluded_coins_hourly.csv (same file/filter placebo.excluded_coins() uses).
#
# an adversarial review: this is not a rejected panel candidate but a resolved-impairment
# control, Paxos's $250M Signature Bank exposure was quantified 80.4 minutes AFTER
# the joint Treasury/Fed/FDIC backstop had already resolved it, so the episode tests
# the SCARCITY/q-dimension (what happens once backstop risk is retired) rather than
# the panel's phi-dimension screen. It still fails precondition (iii) as originally
# defined (quantified only after resolution) and is not counted as a panel member.
# --------------------------------------------------------------------------------------
def row5_paxos() -> dict:
    with open(PAXOS_DIR / "disclosure_passages_paxos.json", encoding="utf-8") as f:
        passages = json.load(f)
    quantified = next(p for p in passages if p["quantified"])
    disclosure_ts_utc = quantified["capture_timestamp"]
    disclosure_dt = datetime.strptime(disclosure_ts_utc, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)

    with open(PAXOS_DIR / "usdp_supply_daily.csv", newline="", encoding="utf-8") as f:
        supply_rows = list(csv.DictReader(f))
    supply_row = next(r for r in supply_rows if int(r["date"]) == 1678579200)  # 2023-03-12T00:00:00Z
    supply_usd = float(supply_row["circulating_usd"])

    with open(PAXOS_DIR / "joint_statement_treasury_fed_fdic.json", encoding="utf-8") as f:
        joint_stmt = json.load(f)
    joint_dt = datetime.strptime(joint_stmt["joint_statement_ts_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)

    # Post-disclosure USDP trough, same file, symbol, and window filter as
    # placebo.excluded_coins() (USDP, window == 'crisis'), sliced to hours at or after
    # the disclosure timestamp.
    with open(DATA_DIR / "excluded_coins_hourly.csv", newline="", encoding="utf-8") as f:
        px_rows = [r for r in csv.DictReader(f) if r["symbol"] == "USDP" and r["window"] == "crisis"]
    disclosure_ts_epoch = int(disclosure_dt.timestamp())
    post = [r for r in px_rows if int(r["timestamp"]) >= disclosure_ts_epoch]
    if not post:
        raise RuntimeError("INSTRUMENT GAP: no excluded_coins_hourly.csv USDP crisis-window rows at or "
                            f"after {disclosure_ts_utc}.")
    trough_row = min(post, key=lambda r: float(r["price"]))
    trough_price = float(trough_row["price"])
    trough_hour_utc = datetime.fromtimestamp(int(trough_row["timestamp"]), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    phi = 250e6 / supply_usd
    combo = _combo(
        trough_price, phi, "phi_250M_signature_bank_over_contemporaneous_usdp_supply",
        "low", "excluded_coins_hourly_usdp_crisis", trough_hour_utc,
    )

    criterion_i_quantified = True   # $250M is a dollar figure, not "limited exposure"
    criterion_ii_public = True      # Paxos's own official Twitter account, not a press report of one
    criterion_iii_pre_resolution = disclosure_dt < joint_dt
    qualifies = criterion_i_quantified and criterion_ii_public and criterion_iii_pre_resolution
    minutes_after = round((disclosure_dt - joint_dt).total_seconds() / 60.0, 2)

    return {
        "episode": "Paxos/USDP March 2023 (Silvergate/Signature Bank exposure)",
        "classification": ("candidate true negative -- DISQUALIFIED (disclosed after resolution)"
                            if not qualifies else "candidate true negative"),
        "category": "resolved-impairment control (q-dimension)",
        "precondition_i": PRECONDITION_I,
        "disclosure_source": quantified["source_url"],
        "disclosure_ts_utc": disclosure_ts_utc,
        "disclosure_verbatim_passage": quantified["verbatim_passage"],
        "dollar_exposure_usd": 250e6,
        "dollar_exposure_source": "Paxos official Twitter statement (@PaxosGlobal), 2023-03-12",
        "contemporaneous_usdp_supply_usd": supply_usd,
        "contemporaneous_usdp_supply_source": "DeFiLlama stablecoincharts, USDP id=11, 2023-03-12T00:00:00Z point",
        "combinations": [combo],
        "post_disclosure_trough": {
            "trough_price": trough_price,
            "trough_hour_utc": trough_hour_utc,
            "disclosure_ts_utc": disclosure_ts_utc,
            "n_post_disclosure_hours": len(post),
            "source_file": "data/excluded_coins_hourly.csv (already frozen; read-only, matches "
                            "placebo.excluded_coins()'s symbol/window filter)",
        },
        "joint_statement_ts_utc": joint_stmt["joint_statement_ts_utc"],
        "joint_statement_source": joint_stmt["primary_frozen_source"]["url"],
        "qualification": {
            "qualifies": qualifies,
            "criterion_i_quantified": criterion_i_quantified,
            "criterion_ii_public": criterion_ii_public,
            "criterion_iii_pre_resolution": criterion_iii_pre_resolution,
            "minutes_after_joint_statement": minutes_after,
            "reason": ("Disclosure FOLLOWED the joint Treasury/Fed/FDIC statement (2023-03-12T22:15:00Z) by "
                       f"{round(minutes_after, 1)} minutes -- the government had already guaranteed all "
                       "Signature Bank depositors before Paxos posted the $250M figure (the same tweet "
                       "thread explicitly references the guarantee). A disclosure issued after the exposure "
                       "was resolved cannot test whether the bound stays silent under live uncertainty."
                       if not qualifies else
                       "Disclosure preceded the joint statement -- issued while the exposure was still "
                       "unresolved."),
        },
        "status": "disclosed-after-resolution, not a panel member" if not qualifies else "panel member",
        "non_quantified_pre_backstop_context": (
            "Paxos's only pre-backstop statements on this exposure were qualitative, not quantified: "
            "2023-03-08 'virtually no exposure to Silvergate' and 2023-03-10 'no relationship with Silicon "
            "Valley Bank' (see disclosure_passages_paxos.json) -- so even setting the timing issue aside, no "
            "quantified PRE-resolution disclosure exists for this episode."),
        "category_evidence": {
            "framing": "C7: evidence for the paper's SCARCITY mechanism as a resolved-impairment control, "
                       "not a rejected panel candidate -- the bound stays silent once backstop risk is "
                       "retired, dated to the minute.",
            "post_backstop_offset_minutes": minutes_after,
            "phi": combo["phi"],
            "floor": combo["floor"],
            "trough_price": combo["trough_price"],
            "silent_margin": combo["silent_margin"],
        },
    }


def for_print(rows: dict) -> dict:
    """Flat keys for the figures the extremes of Row 2 are printed in.

    Row 2's impairment readings exist only as `phi` fields scattered through its
    `combinations` list. A dotted-key lookup -- which is how every printed figure in
    this project is traced back to its source -- can walk dictionaries but not list
    elements, so a number lifted from that list has nowhere to point. The extremes are
    therefore lifted out here, once, at full stored precision.

    They are also the pair that has to reconcile in print: the manuscript quotes both an
    impairment range and a floor range, and the floor is 1 - phi by definition. Rounding
    phi to three decimals breaks that identity visibly (1 - 0.259 = 0.741, but the floor
    prints as 0.742, because it rounds from 0.7415), so the assertion below pins the
    identity at full precision and the prose prints four decimals.
    """
    combos = rows["row2_tether_2019"]["combinations"]
    phis = sorted({c["phi"] for c in combos})
    floors = sorted({c["floor"] for c in combos})
    assert len(phis) == len(floors) == 3, (phis, floors)
    for phi, floor in zip(phis, reversed(floors)):
        assert abs((1.0 - phi) - floor) < 1e-9, f"floor is not 1 - phi: {phi} {floor}"
    q_low = [c["q_star_rho0"] for c in combos if c["trough_label"] == "low"]
    assert len(q_low) == 3, q_low

    # The screened set, and the subset a BELOW-PAR floor test can be run on at all.
    # The two counts differ by one and both are quoted, in different documents, so the
    # difference is recorded here rather than left to be rediscovered.
    footer = rows["footer_appendix_d_non_qualifying_candidates"]
    excluded = "DAI (2020, 'Black Thursday')"
    assert any(c["candidate"] == excluded for c in footer), excluded

    return {
        "tether2019_phi_lo": phis[0],
        "tether2019_phi_hi": phis[-1],
        "tether2019_floor_lo": floors[0],
        "tether2019_floor_hi": floors[-1],
        "tether2019_q_star_low_feed_lo": min(q_low),
        "tether2019_q_star_low_feed_hi": max(q_low),
        "screened_candidates": [c["candidate"] for c in footer],
        "screened_candidates_n": len(footer),
        "below_par_screen_excludes": excluded,
        "below_par_screen_exclusion_basis":
            "A RULING, not a measurement from this snapshot. DAI traded ABOVE par "
            "through the March 2020 episode, so precondition (ii) -- a fall of more "
            "than one percent BELOW par while the disclosure is live -- is not "
            "testable on it, and it is not a candidate for a below-par floor test. "
            "No DAI 2020 price series is frozen here, so this file records the ruling "
            "and its basis rather than deriving it. The candidate stays in the "
            "screened list above with its own separate failure reason.",
        "below_par_screen_n": len(footer) - 1,
    }


def build_panel() -> dict:
    with open(RESULTS_DIR / "phi_dynamic.json", encoding="utf-8") as f:
        phi_dynamic = json.load(f)
    with open(RESULTS_DIR / "trough_corroboration.json", encoding="utf-8") as f:
        trough_corrob = json.load(f)
    with open(N1_DIR / "tether2019" / "disclosure_passages_nyag.json", encoding="utf-8") as f:
        nyag_passages = json.load(f)
    with open(N1_DIR / "tether2019" / "usdt_liabilities_wayback.json", encoding="utf-8") as f:
        wayback = json.load(f)
    with open(RESULTS_DIR / "second_event_terra.json", encoding="utf-8") as f:
        terra = json.load(f)

    panel = {
        "method_source": "rational_bound.py (floor_worstcase, implied_failure_prob, below_floor_share)",
        "precondition_i": PRECONDITION_I,
        "row1_usdc_march_2023": row1_usdc(phi_dynamic, trough_corrob),
        "row2_tether_2019": row2_tether(nyag_passages, wayback),
        "row4_terra_2022": row4_terra(terra),
        "row5_paxos2023_candidate": row5_paxos(),
        "footer_appendix_d_non_qualifying_candidates": footer_appendix_d(),
    }
    panel["for_print"] = for_print(panel)
    return panel


if __name__ == "__main__":
    panel = build_panel()
    out_path = RESULTS_DIR / "specificity_panel.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(panel, f, indent=2)
    print(f"wrote {out_path}")
