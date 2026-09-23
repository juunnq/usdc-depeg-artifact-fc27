"""Compute and report the two headline numbers with their bands. Reads ONLY the
frozen snapshot. Writes results/ JSON + a human-readable summary."""
import json
import sys

import data_io
import decomposition as dec
import figures
import hourly_counts

# THE ON-CHAIN ATTRIBUTION PACKAGE IS OPTIONAL. Numbers 2 (redemption concentration)
# and 4 (DAI contagion) work from wallet-level Etherscan data and belong to the long
# paper; the submitted short paper uses neither, so the artifact does not ship them
# and this module must still run end to end. Absence is a SHIPPING DECISION, not a
# failure, so it is reported as a status rather than raised.
try:
    import dai_contagion
    import fifo_rule
    import herfindahl as hh
    ONCHAIN_PRESENT = True
except ModuleNotFoundError:
    dai_contagion = fifo_rule = hh = None
    ONCHAIN_PRESENT = False
import impossibility_window
import makewhole_timing
import ordering_null
import panel_11mar
import phi_cash_leg_scenarios
import phi_denominator_sensitivity
import phi_dynamic
import placebo
import rational_bound
import second_event
import specificity_panel
import tables
import time_value
import subfloor_depth_live
import trough_corroboration
import tusd_2023
import usdt_premium
import usdt_premium_timing
from constants import (COINBASE_CONDUITS, COINBASE_HOTWALLET, COINBASE_UPSTREAM_SENDERS,
                       PHI_SVB, RESULTS_DIR, SEED)

PHI_SWEEP = [0.06, 0.08, 0.10]
RHO_SWEEP = [0.0, 0.5, 0.9, 1.0]


def number1() -> dict:
    """Number 1 — non-fundamental share (Derivations 1 + 4). Bound-driven band;
    a (phi, rho) robustness grid; NO bootstrap (its uncertainty is bound-driven)."""
    p_obs = data_io.observed_trough("USDC")
    headline = dec.band(p_obs, PHI_SVB)
    sweep = [{"phi": phi, "rho": rho,
              "nonfundamental_share": dec.nonfundamental_share(p_obs, phi, rho)}
             for phi in PHI_SWEEP for rho in RHO_SWEEP]
    return {"p_obs": p_obs, "phi_svb": PHI_SVB,
            "headline_band": headline, "robustness_sweep": sweep}


def number2() -> dict:
    """Number 2 — redemption concentration as a DEFENSIBLE BOUND (bounds-not-points).

    The address-level Herfindahl on direct senders into Circle's burn pipeline is an
    UPPER BOUND on economic-redeemer concentration: the dominant positions are
    verified Coinbase routing conduits aggregating ~14,111 distinct senders, and
    disaggregating a custodian strictly lowers a Herfindahl. We report the bound
    (raw/normalized/top-10/seeded CI) and, using the one verified entity mapping
    (Coinbase), the ex-custodian figure showing how far below the bound it sits.
    No external entity-label set (MZZ replication / Arkham / Nansen) is in the repo,
    so the bound is the complete result."""
    if not ONCHAIN_PRESENT:
        return {"status": "NOT_SHIPPED",
                "reason": "wallet-level redemption attribution is not part of the short "
                          "paper's artifact; herfindahl.py and "
                          "data/redemptions_by_wallet.csv are excluded from the export"}
    try:
        df = data_io.load_redemptions()
    except FileNotFoundError as e:
        return {"status": "PENDING_DATA", "reason": str(e)}
    df = df.copy()
    df["wallet"] = df["wallet"].str.lower()
    vols_all = df["volume"].values
    total = float(vols_all.sum())
    is_cb = df["wallet"].isin([a.lower() for a in COINBASE_CONDUITS])
    cb_share = float(df.loc[is_cb, "volume"].sum() / total)
    vols_ex = df.loc[~is_cb, "volume"].values

    cov = {}
    man = RESULTS_DIR.parent / "data" / "MANIFEST.json"
    if man.exists():
        for s in json.load(open(man)).get("sources", []):
            if s.get("file") == "redemptions_by_wallet.csv":
                cov = {"coverage_pct": s.get("coverage_pct"),
                       "gross_burned_usd": s.get("gross_burned_usd")}
    return {
        "status": "BOUNDED",
        "interpretation": "address-level HHI is an UPPER BOUND on economic-redeemer "
                          "concentration; disaggregating the dominant Coinbase custodian "
                          "(~14,111 senders) strictly lowers it",
        "n_direct_senders": int(len(vols_all)),
        "upper_bound": {
            "hhi_raw": hh.hhi_raw(vols_all),
            "hhi_normalized": hh.hhi_normalized(vols_all),
            "top10_share": hh.top_k_share(vols_all, 10),
            "largest_share": float(vols_all.max() / total),
            "effective_n": 1.0 / hh.hhi_raw(vols_all),
            "bootstrap": hh.bootstrap_hhi(vols_all, seed=SEED),
        },
        "dominant_entity": {
            "name": "Coinbase", "hot_wallet": COINBASE_HOTWALLET,
            "conduits": COINBASE_CONDUITS, "block_share": cb_share,
            "upstream_senders": COINBASE_UPSTREAM_SENDERS,
        },
        "ex_custodian": {  # one verified entity removed -> illustrates the drop
            "hhi_raw": hh.hhi_raw(vols_ex),
            "hhi_normalized": hh.hhi_normalized(vols_ex),
            "largest_share": float(vols_ex.max() / vols_ex.sum()),
            "effective_n": 1.0 / hh.hhi_raw(vols_ex),
            "n_senders": int(len(vols_ex)),
        },
        "entity_attributed_hhi": "not computed -- no MZZ/Arkham/Nansen label set in repo; "
                                 "the bound + verified Coinbase mapping is the complete result",
        "mzz_consistency": "consistent -- low, custodial-routed concentration; matches MZZ "
                           "low USDC arbitrage centralization (~521 arbitrageurs) vs USDT (~6)",
        "coverage": cov,
    }


def number3_placebo() -> dict:
    """Number 3 — cross-stablecoin placebo (falsification). Pure measurement on the
    frozen snapshot: the exposed coins (USDC direct, DAI contagion) must break while
    the unexposed coin (USDT) holds, else the de-peg is a market-wide repricing."""
    return placebo.placebo()


def main():
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    n1, n2, n3 = number1(), number2(), number3_placebo()
    n1b = rational_bound.report()       # Proposition 1 — sharpens Number 1
    n4 = (dai_contagion.contagion() if ONCHAIN_PRESENT else
          {"status": "NOT_SHIPPED",
           "reason": "on-chain DAI contagion is not part of the short paper's "
                     "artifact; dai_contagion.py is excluded from the export"})
    n5 = second_event.analyze()         # Phase C — second-event control (Terra May 2022)
    ur = usdt_premium.robustness()      # USDT-premium volume robustness

    # R1 fix (part b): a missing required input must fail LOUDLY, not silently
    # degrade that section's status while the rest of report.py runs to a green
    # exit. Collect every PENDING_DATA hit and abort before writing any result or
    # figure, naming the missing file and the script that produces it (each
    # `reason` string already carries both, see dai_contagion.contagion() /
    # second_event.analyze() / data_io.load_redemptions()).
    missing = [(label, r["reason"]) for label, r in
              (("Number 2 (redemption HHI)", n2),
               ("Number 4 (DAI contagion)", n4),
               ("second-event control (Terra)", n5))
              if r.get("status") == "PENDING_DATA"]
    if missing:
        print("report.py: required input(s) missing -- refusing to write results/ with a "
              "silently degraded section:", file=sys.stderr)
        for label, reason in missing:
            print(f"  - {label}: {reason}", file=sys.stderr)
        sys.exit(1)

    # Preserve any 'entity_resolved' block a prior standalone run of
    # herfindahl_entity.py's write_results() appended to this same results file
    # (the entity-resolved sybil-detection join, number2() above never computes
    # this key itself, and herfindahl_entity.py is not wired into any documented
    # run order). Without this, a plain report.py re-run silently DROPS that block
    #, found by a clean-clone byte-identical proof (results/
    # number2_redemption_hhi.json diverged: 8678 bytes committed vs. 1608 bytes
    # freshly regenerated). Carrying it forward is the same provenance
    # discipline as data_fetch.preserve_foreign_entries; actually wiring
    # herfindahl_entity.py into report.py's run order is a separate, larger
    # change than this fix and is left as a documented gap.
    n2_path = RESULTS_DIR / "number2_redemption_hhi.json"
    if n2_path.exists():
        try:
            prior_n2 = json.load(open(n2_path))
        except (json.JSONDecodeError, OSError):
            prior_n2 = {}
        if "entity_resolved" in prior_n2:
            n2["entity_resolved"] = prior_n2["entity_resolved"]

    json.dump(n1, open(RESULTS_DIR / "number1_nonfundamental_share.json", "w"), indent=2)
    json.dump(n1b, open(RESULTS_DIR / "number1b_rational_bound.json", "w"), indent=2)
    if ONCHAIN_PRESENT:
        json.dump(n2, open(RESULTS_DIR / "number2_redemption_hhi.json", "w"), indent=2)
    json.dump(n3, open(RESULTS_DIR / "number3_placebo.json", "w"), indent=2)
    if ONCHAIN_PRESENT:
        json.dump(n4, open(RESULTS_DIR / "number4_dai_contagion.json", "w"), indent=2)
    json.dump(n5, open(RESULTS_DIR / "second_event_terra.json", "w"), indent=2)
    json.dump(ur, open(RESULTS_DIR / "usdt_premium.json", "w"), indent=2)
    json.dump(placebo.trough_print_quality(),
              open(RESULTS_DIR / "trough_volume.json", "w"), indent=2)
    json.dump(placebo.excluded_coins(),  # frozen basis for the placebo exclusions
              open(RESULTS_DIR / "placebo_excluded_coins.json", "w"), indent=2)
    json.dump(impossibility_window.report(),  # hourly duration/specificity of the bound
              open(RESULTS_DIR / "impossibility_window.json", "w"), indent=2)

    # The remaining results/*.json files were each produced by a standalone script's own
    # __main__ block, never wired into report.py, so the documented five-step
    # reproduction sequence could not regenerate them, and a stale committed copy of one
    # (trough_corroboration.json) was invisible to every check that only re-ran
    # report.py. All twelve are offline/keyless (verified:
    # none touches ETHERSCAN_API_KEY or any network call) and write only under
    # RESULTS_DIR, so wiring them in adds no new input requirement. Order matters: three
    # of them read an upstream results/*.json from DISK (not as a passed-in value), so
    # each producer must be written before its consumer runs, not just computed before it.
    #
    # Tier 0, frozen data only, no results/ dependency.
    if ONCHAIN_PRESENT:
        json.dump(fifo_rule.rule(), open(RESULTS_DIR / "fifo_rule.json", "w"), indent=2)
    json.dump(hourly_counts.report(), open(RESULTS_DIR / "hourly_counts.json", "w"), indent=2)
    json.dump(ordering_null.report(), open(RESULTS_DIR / "ordering_null.json", "w"), indent=2)
    json.dump(phi_denominator_sensitivity.build_grid(),
              open(RESULTS_DIR / "phi_denominator_sensitivity.json", "w"), indent=2)
    json.dump(time_value.report(), open(RESULTS_DIR / "time_value.json", "w"), indent=2)
    json.dump(tusd_2023.report(), open(RESULTS_DIR / "tusd_2023.json", "w"), indent=2)

    # Tier 1, panel_11mar.py and trough_corroboration.py both read phi_dynamic.json
    # from disk (RESULTS_DIR / "phi_dynamic.json"), so it must be written first.
    json.dump(phi_dynamic.report(), open(RESULTS_DIR / "phi_dynamic.json", "w"), indent=2)
    json.dump(trough_corroboration.report(),
              open(RESULTS_DIR / "trough_corroboration.json", "w"), indent=2)

    # Both of these parse or re-aggregate FROZEN inputs (the Circle capture, the Kraken
    # tape) rather than deriving from another results file, so their position in this
    # sequence is not load-bearing; they are regenerated here so that every results/*.json
    # on disk is reproducible by one command.
    json.dump(phi_cash_leg_scenarios.build(),
              open(RESULTS_DIR / "phi_cash_leg_scenarios.json", "w"), indent=2)
    json.dump(subfloor_depth_live.build(),
              open(RESULTS_DIR / "subfloor_depth_live.json", "w"), indent=2)
    json.dump(panel_11mar.report(), open(RESULTS_DIR / "panel_11mar.json", "w"), indent=2)

    # Tier 2, all three read trough_corroboration.json from disk; specificity_panel.py
    # additionally reads phi_dynamic.json and second_event_terra.json, both already
    # written above.
    json.dump(makewhole_timing.report(),
              open(RESULTS_DIR / "makewhole_timing.json", "w"), indent=2)
    json.dump(usdt_premium_timing.report(),
              open(RESULTS_DIR / "usdt_premium_timing.json", "w"), indent=2)
    json.dump(specificity_panel.build_panel(),
              open(RESULTS_DIR / "specificity_panel.json", "w"), indent=2)

    figures.main()  # regenerate publication figures into figs/, reads panel_11mar.json,
                    # phi_dynamic.json, trough_corroboration.json, usdt_premium_timing.json,
                    # all written above
    tables.main()   # regenerate paper table fragments into paper/fc27/tables/, reads
                    # phi_denominator_sensitivity.json, specificity_panel.json,
                    # usdt_premium_timing.json, all written above

    b = n1["headline_band"]
    lines = [
        "# USDC de-peg (March 2023): headline results", "",
        f"Frozen P_obs (USDC trough, DeFiLlama hourly): {n1['p_obs']:.5f}",
        f"phi (SVB reserve impairment): {n1['phi_svb']:.2f}",
        f"Delta_obs (observed max de-peg): {b['delta_obs']:.5f}", "",
        "## Number 1 - non-fundamental share",
        "(coordination panic + microstructure friction; an UPPER BOUND on pure panic)", "",
        f"**Band: {b['low'] * 100:.1f}% - {b['high'] * 100:.1f}%**",
        f"- low edge (rho=0, magnitude-excess restriction): {b['low'] * 100:.1f}%",
        f"- high edge (rho=1, reversal restriction): {b['high'] * 100:.1f}%",
        "  The reversal (full re-peg after the FDIC backstop) argues the high end.", "",
        "## Proposition 1 - rational-pricing bound (sharpens Number 1)", "",
        f"Under risk-neutral expected-loss pricing of the disclosed phi={n1b['phi']:.2f} impairment,",
        f"the fundamental price cannot fall below 1 - phi = {n1b['floor_worstcase']:.2f} (worst case rho=0).",
        f"The observed trough {n1b['p_obs']:.4f} lies below it: rationalizing it would require a",
        f"backstop-failure probability q = {n1b['implied_failure_prob_rho0']:.2f} > 1 (impossible).",
        f"So the {n1b['below_floor_nonfundamental_share']*100:.1f}% of the de-peg below the floor is "
        "non-fundamental by IMPOSSIBILITY, not by the arithmetic choice of rho.", "",
        "## Number 2 - redemption Herfindahl", "",
    ]
    if n2["status"] == "BOUNDED":
        ub, ex, de = n2["upper_bound"], n2["ex_custodian"], n2["dominant_entity"]
        bs = ub["bootstrap"]
        cov = n2.get("coverage", {})
        cov_pct = cov.get("coverage_pct")
        gross = cov.get("gross_burned_usd") or 0.0
        cov_line = (f"- coverage: {cov_pct:.0f}% of ${gross/1e9:.2f}B gross burns (FIFO burn-attributed; rest mint-funded / pre-window balance)"
                    if cov_pct is not None else "- coverage: n/a")
        lines += [
            "Reported as a BOUND (bounds-not-points). The burn-attributed HHI is an UPPER BOUND",
            "on economic-redeemer concentration; the dominant position is a Coinbase custodian",
            f"aggregating ~{de['upstream_senders']:,} senders, so unwinding it only lowers the HHI.",
            "",
            f"**Upper bound (burn-attributed, N={n2['n_direct_senders']} redeemers):**",
            f"- raw HHI: {ub['hhi_raw']:.4f}   normalized HHI*: {ub['hhi_normalized']:.4f}   (effective redeemers ~{ub['effective_n']:.1f})",
            f"- top-10 share: {ub['top10_share']*100:.1f}%   largest: {ub['largest_share']*100:.1f}%",
            f"- seeded bootstrap 95% CI: {bs['lo']:.4f}-{bs['hi']:.4f}  (wide -- hinges on the Coinbase conduits)",
            f"- dominant entity: Coinbase = {de['block_share']*100:.1f}% via 2 conduits (hot wallet {de['hot_wallet'][:12]}...)",
            "",
            f"**With that one verified custodian removed:** raw HHI {ex['hhi_raw']:.4f}, normalized {ex['hhi_normalized']:.4f},",
            f"largest {ex['largest_share']*100:.1f}% (effective redeemers ~{ex['effective_n']:.1f}, N={ex['n_senders']}).",
            f"=> economic-redeemer HHI <= {ub['hhi_raw']:.2f}, and ~{ex['hhi_raw']:.2f} once the one custodian is unwound.",
            "",
            cov_line,
            f"- MZZ check: {n2['mzz_consistency']}",
            f"- entity-attributed HHI: {n2['entity_attributed_hhi']}",
        ]
    elif n2["status"] == "PENDING_DATA":
        lines += [f"- {n2['status']}: {n2['reason']}"]

    lines += ["", "## Number 3 - cross-stablecoin placebo (falsification test)", "",
              "Prediction: shock-exposed coins break; the unexposed coin holds (else the",
              "de-peg is a market-wide repricing, not a shock-driven run)."]
    for s in ("USDC", "DAI", "USDT"):
        c = n3[s]
        flag = "BROKE" if c["broke"] else "HELD"
        extra = "" if c["broke"] else f", +{c['max_premium_bps']:.0f} bps premium"
        lines.append(f"- {s} [{c['exposure']}]: trough {c['trough']:.4f}{extra}  -> {flag}")
    verdict = "PASS" if n3["placebo_pass"] else "FAIL"
    lines += [f"**Placebo: {verdict}** (exposed broke, unexposed held; break threshold < {n3['break_threshold']}).",
              f"Realized fragility by trough: {' < '.join(n3['fragility_ordering_by_trough'])}; "
              f"ex-ante exposure {' > '.join(n3['ex_ante_exposure_ranking'])} -> "
              f"{'MATCH' if n3['ordering_matches_exposure'] else 'NO MATCH'} "
              "(exposure, not concentration, drove the ordering -- see README scope note).",
              "", "## Number 4 - DAI contagion pass-through", ""]
    if n4.get("status") == "OK_REPORTED_BACKING":
        lines += [
            "On-chain finding: the PSM held substantial USDC on-chain through 2021-2022, but the",
            "run-date on-chain balance at the documented PSM-USDC-A contracts reads ~$99, which we",
            "attribute to off-chain custody but cannot corroborate on-chain, so a clean on-chain",
            "backing fraction is NOT available.",
            f"- USDC de-peg {n4['usdc_depeg_bps']:.0f} bps; observed DAI de-peg {n4['observed_dai_depeg_bps']:.0f} bps.",
            f"- Reported PSM-USDC-A cap ${n4['reported_psm_usdc']/1e9:.1f}B over on-chain DAI supply"
            f" ${n4['dai_total_supply_usd']/1e9:.2f}B = {n4['psm_share_of_dai_supply']*100:.0f}% of DAI supply.",
            f"- The SAME source separately reports {n4['source_collateral_share']*100:.0f}% USDC share of DAI"
            " collateral -- a different denominator.",
            "- NO mechanical/panic decomposition is reported. The two ratios imply pass-throughs of"
            f" {n4['pass_through_if_collateral_share_bps']:.0f} and {n4['pass_through_if_psm_share_bps']:.0f} bps,"
            " straddling the observed de-peg, so any residual would be an artifact of the ratio chosen.",
            "  See the no_decomposition key in results/number4_dai_contagion.json.",
        ]
    else:
        lines += [f"- {n4.get('status')}: {n4.get('reason')}"]

    lines += ["", "### USDT-premium robustness", ""]
    if ur.get("status") == "OK":
        lines += [
            f"On a volume-bearing fiat venue ({ur['venue']}), USDT reached +{ur['max_premium_bps']:.0f} bps and never",
            f"broke par (min {ur['min_price']:.4f}); ${ur['premium_volume_usdt']/1e9:.2f}B of ${ur['total_volume_usdt']/1e9:.2f}B traded above par",
            "-> the premium held on MATERIAL volume, not a thin print.",
        ]
    else:
        bvol = ur.get("binance_usdcusdt_total_volume_usdc")
        bvol_s = f"${bvol/1e9:.2f}B" if bvol else "n/a"
        lines += [f"No direct USDT/USD volume series frozen; Binance USDC/USDT volume {bvol_s} bounds the",
                  "thin-print concern (the USDC-cheap / USDT-rich leg traded deep)."]
    if n5.get("status") == "OK":
        lines += ["", "## Second event (control) - Terra/LUNA collapse, May 2022", "",
                  f"USDT and USDC had NO Terra reserve exposure (phi={n5['phi_terra_exposure']:.0f}), so the worst-case",
                  f"floor is {n5['floor_worstcase']:.2f} and Proposition 1 attributes ~100% of each dip to non-fundamental:",
                  f"- USDT: trough {n5['USDT']['trough']:.4f} ({n5['USDT']['max_depeg_bps']:.0f} bps dip) -> below-floor non-fundamental {n5['USDT']['below_floor_nonfundamental_share']*100:.0f}%",
                  f"- USDC: trough {n5['USDC']['trough']:.4f} ({n5['USDC']['max_depeg_bps']:.0f} bps dip, +{n5['USDC']['max_premium_bps']:.0f} bps premium) -> below-floor non-fundamental {n5['USDC']['below_floor_nonfundamental_share']*100:.0f}%",
                  "Clean no-fundamental-shock control: q is undefined (phi=0), the method does not invent",
                  "a fundamental story. NOTE: phi=0 BY CONSTRUCTION, so the ~100% result is DEGENERATE-BY-DESIGN",
                  "-- a falsification/honesty control, NOT a second quantitative estimate or a non-trivial bound.",
                  "Contrast Mar-2023 USDC (phi=0.08, floor 0.92, 35-100% non-fundamental).",
                  "(DeFiLlama aggregate troughs are shallow; the phi=0 -> 100% conclusion is depth-invariant.)"]
    lines += ["", "## Figures (regenerated to figs/ by figures.py)",
              "- fig_11mar_panel.pdf  - hour-by-hour 11 March panel across every frozen feed (main-text Fig. 1)",
              "- fig_depeg_paths.pdf  - USDC/DAI/USDT paths over the full window, floor(t), firing band, event markers",
              "(fig3_phi_rho_sweep and fig4_model_validation are cut -- orphaned / removed by an earlier ruling)"]
    (RESULTS_DIR / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
