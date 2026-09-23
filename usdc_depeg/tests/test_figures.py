"""Smoke test: figures.py produces exactly the D21 figure set (fig_event,
fig_margin_paths), each deterministic, each with a sources sidecar, each at
its budgeted height, each free of in-axes text. Every prior generator
(fig_11mar_panel, fig_depeg_paths, fig_supply, fig_floor_trough, and the
earlier-cut fig3_phi_rho_sweep / fig4_model_validation / fig1_depeg_paths /
fig2_supply_outflow) is gone -- re-adding any of them needs a fresh ruling,
not a silent revert. fig_event's own budget moved 6.4->8.6cm and
fig_floor_trough was replaced by fig_margin_paths (4.0cm) under D21, both
already reflected below."""
import sys
from pathlib import Path

import fitz

import figures
import figstyle
from constants import PKG_DIR

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import figqa  # noqa: E402  (path insert must precede this import)

# fig_event_short is the short paper's rendering of fig_event: same builder, same
# frozen data, with G10's legibility fixes (no merged event tick in panel (a), a
# darker par line). It is a separate stem because the regular paper is frozen and
# shares this builder.
EXPECTED_STEMS = ["fig_event", "fig_event_short", "fig_margin_paths",
                  "fig_share_vs_phi"]
# fig_event_short moved 8.6 -> 7.2cm under D36: the short paper is adding two figures
# to a page budget with ~1.2cm of margin, and 1.4cm of that comes from shrinking the
# one figure already in it. The REGULAR paper's fig_event stays at 8.6 -- it is frozen
# and its budget is not under pressure.
# H2 T8 rungs (i) and (ii) moved the two short-paper figures again, to recover page-gate
# lines after Figures 1 and 3 were placed: fig_share_vs_phi 5.0 -> 4.5 and
# fig_event_short 7.2 -> 6.8. Both re-ran figqa at the new budget and PASSed. The
# REGULAR paper's fig_event stays at 8.6, untouched and frozen.
HEIGHT_BUDGET_CM = {"fig_event": 8.6, "fig_event_short": 6.8, "fig_margin_paths": 4.0,
                    "fig_share_vs_phi": 4.5}
CUT_OR_ORPHANED = [
    "fig_11mar_panel", "fig_depeg_paths", "fig_supply", "fig_floor_trough",
    "fig3_phi_rho_sweep", "fig4_model_validation",
    "fig1_depeg_paths", "fig2_supply_outflow",
]


def test_every_current_figure_produced_and_deterministic():
    results = figures.main()
    produced = dict(results)
    assert set(produced) == set(EXPECTED_STEMS), (
        f"figures.main() produced {sorted(produced)}, expected exactly {sorted(EXPECTED_STEMS)}"
    )
    figs_dir = PKG_DIR / "figs"
    for stem, result in produced.items():
        f = Path(result["path"])
        assert f == figs_dir / f"{stem}.pdf"
        assert f.exists() and f.stat().st_size > 0, f"{stem}.pdf missing or empty"
        # figstyle.save_figure asserts determinism internally (raises rather than
        # falling back to a non-deterministic format) -- a successful return here
        # already proves it. Re-verified independently below via a second call.


def test_determinism_via_double_render():
    """Calls each builder TWICE (independent Python-level calls, not just
    save_figure's own internal double-render probe) and asserts identical
    SHA-256 both times -- a regression guard on the builder functions
    themselves, not only on save_figure's internal mechanism."""
    for name in EXPECTED_STEMS:
        fn = getattr(figures, name)
        r1 = fn()
        r2 = fn()
        assert r1["sha256"] == r2["sha256"], (
            f"{name}: two independent calls produced different PDF bytes "
            f"({r1['sha256'][:12]} vs {r2['sha256'][:12]})"
        )


def test_every_figure_has_a_sources_sidecar():
    figures.main()
    figs_dir = PKG_DIR / "figs"
    for stem in EXPECTED_STEMS:
        sidecar = figs_dir / f"{stem}.sources.txt"
        assert sidecar.exists() and sidecar.stat().st_size > 0, (
            f"{stem}.sources.txt missing or empty -- every number on a figure must "
            f"trace to a results-file key listed here"
        )


def test_cut_and_orphaned_figures_are_not_regenerated():
    """Regression guard: none of the prior generators may reappear as a side
    effect of some future figures.py edit."""
    for name in CUT_OR_ORPHANED:
        assert not hasattr(figures, name), (
            f"{name}'s generating function has returned to figures.py -- it was cut / "
            f"found orphaned; re-adding it needs a fresh ruling, not a silent revert"
        )


def test_figure_heights_match_their_d21_budget():
    results = dict(figures.main())
    for stem, budget_cm in HEIGHT_BUDGET_CM.items():
        doc = fitz.open(results[stem]["path"])
        height_cm = doc[0].rect.height / 72.0 * 2.54
        width_cm = doc[0].rect.width / 72.0 * 2.54
        doc.close()
        assert abs(height_cm - budget_cm) < 0.01, (
            f"{stem}: height {height_cm:.3f}cm does not match its D21 budget {budget_cm}cm"
        )
        assert abs(width_cm - figstyle.LNCS_WIDTH_CM) < 0.01, (
            f"{stem}: width {width_cm:.3f}cm does not match LNCS_WIDTH_CM"
        )


def test_figures_pass_figqa_zero_in_axes_text_and_style_conformance():
    """Runs figqa's EXACT (live-Figure-object) checks -- not the weaker
    PDF-only heuristic -- against a freshly-built live figure for each stem,
    mirroring the QA loop this figure set went through before being wired in."""
    builders = {"fig_event": figures.fig_event,
                "fig_event_short": figures.fig_event_short,
                "fig_margin_paths": figures.fig_margin_paths,
                "fig_share_vs_phi": figures.fig_share_vs_phi}
    for stem, fn in builders.items():
        result = fn()
        import matplotlib.pyplot as plt
        fig = plt.gcf()
        report = figqa.run_qa(result["path"], fig=fig, height_budget_cm=HEIGHT_BUDGET_CM[stem])
        plt.close(fig)
        for check in report["checks"]:
            if check["check"] in ("in_axes_text", "style_conformance", "min_font_size",
                                    "height_budget"):
                assert check["pass"] is True, (
                    f"{stem}: figqa check {check['check']!r} failed: {check}"
                )


# ======================================================================================
# fig_share_vs_phi: the three numbers the manuscript prints must regenerate from the
# frozen JSON, not from literals in the plotting code.
# ======================================================================================
def _share_lower_edge(p_obs: float, phi: float) -> float:
    """s_nf(0) = 1 - phi/(1 - P_obs), the lower edge of equation (2) at zero recovery."""
    return 1.0 - phi / (1.0 - p_obs)


def _troughs_from_json() -> dict:
    import json
    corr = json.loads(
        (PKG_DIR / "results" / "trough_corroboration.json").read_text(encoding="utf-8"))
    series = corr["firing_report"]["series"]
    return {style_key: float(series[json_key]["min_print"])
            for style_key, json_key in figures.SHARE_VS_PHI_FEEDS}


def test_share_vs_phi_zero_crossings_match_the_manuscript():
    """Each curve crosses zero at its own 1 - P_obs, and the composite's crossing IS
    the manuscript's "unrationalizable for any impairment below 12.3%"."""
    troughs = _troughs_from_json()

    composite_crossing = 1.0 - troughs["composite"]
    assert abs(composite_crossing - 0.1233) < 0.0005, (
        f"composite crosses zero at phi={composite_crossing:.6f}, manuscript prints "
        f"12.3% (results/trough_corroboration.json "
        f"firing_report.series.composite.min_print={troughs['composite']})")

    close_crossing = 1.0 - troughs["kraken_close"]
    assert abs(close_crossing - 0.0990) < 0.0005, (
        f"Kraken close crosses zero at phi={close_crossing:.6f}, expected 0.0990 "
        f"(firing_report.series.kraken_usdc_close.min_print={troughs['kraken_close']})")

    # The crossing is where the curve is zero, so it must actually evaluate to zero.
    for key, crossing in (("composite", composite_crossing),
                          ("kraken_close", close_crossing)):
        assert abs(_share_lower_edge(troughs[key], crossing)) < 1e-9


def test_share_vs_phi_values_at_the_disclosed_phi_match_the_manuscript():
    """At the disclosed phi the composite curve is the abstract's 35% and the Kraken
    close curve is Section 4's and Section 7's 19.2%."""
    import constants
    troughs = _troughs_from_json()
    phi = constants.PHI_SVB
    assert phi == 0.08, f"PHI_SVB is {phi}, this assertion is written for the disclosed 0.08"

    composite = _share_lower_edge(troughs["composite"], phi)
    assert abs(composite - 0.3513) < 0.0005, (
        f"composite at phi=0.08 is {composite:.6f}, manuscript prints 35% "
        f"(firing_report.series.composite.min_print={troughs['composite']})")

    close = _share_lower_edge(troughs["kraken_close"], phi)
    assert abs(close - 0.1919) < 0.0005, (
        f"Kraken close at phi=0.08 is {close:.6f}, manuscript prints 19.2% "
        f"(firing_report.series.kraken_usdc_close.min_print={troughs['kraken_close']})")


def test_share_vs_phi_plots_every_feed_in_the_curve_style_map():
    """One curve per feed in the map, each using its FEED_CURVE_STYLE dash pattern --
    the only channel separating them, since they are all USDC and share one colour.

    The map is the specification: a feed that stops being drawn is removed from it
    rather than excused here, so this stays an equality and not a subset check."""
    import matplotlib.pyplot as plt
    figures.fig_share_vs_phi()
    fig = plt.gcf()
    ax = fig.axes[0]
    labelled = {ln.get_label(): ln for ln in ax.get_lines()
                if ln.get_label() and not ln.get_label().startswith("_")}
    for key in figstyle.FEED_CURVE_ORDER:
        label = figstyle.FEED_CURVE_LABEL[key]
        assert label in labelled, f"no curve labelled {label!r}; found {sorted(labelled)}"
    # figqa._line_signature is this project's canonical way to read a drawn line's
    # (colour, width, dash) signature, normalized the same way the style-conformance
    # check reads it. Reusing it here means the test and the QA harness cannot disagree
    # about what "a distinct dash pattern" is.
    sigs = {figqa._line_signature(ln)
            for lbl, ln in labelled.items()
            if lbl in figstyle.FEED_CURVE_LABEL.values()}
    assert len(sigs) == len(figstyle.FEED_CURVE_ORDER), (
        f"the four feed curves do not have four distinct style signatures: {sigs}")
    # All four are USDC readings, so colour carries nothing and dash must carry it all.
    colors = {s[0] for s in sigs}
    assert len(colors) == 1, (
        f"the feed curves use {len(colors)} colours; they are all USDC readings and "
        f"must separate on dash pattern alone so the figure survives grayscale: {colors}")
    plt.close(fig)
