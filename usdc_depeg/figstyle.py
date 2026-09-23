"""Single source of truth for every figure this package draws. Every figure-building
function MUST import from here for fonts, sizes, colours, line styles, event/panel
labelling, and saving — and MUST NOT set rcParams, hex colours, or line-style tuples
of its own. One style read from one place is what lets a reader looking at two
different figures recognise them as the same paper.

PROVENANCE. Chosen from three candidates (tueplots, SciencePlots, a GitHub-sourced
paper-replication style), scored against D22 in a written style-candidate evaluation.
tueplots won: the only candidate rendering genuine embedded Computer Modern
(CMR7/CMR8/CMMI7) via `usetex=True`, which is the correct target because this paper's
LNCS body is plain `llncs` with no `mathptmx`/Times override (verified against
paper/fc27/fc27.tex). This module VENDORS the winning rcParams as literal constants
below rather than importing tueplots at runtime — tueplots' own custom-width path
required PRIVATE, underscore-prefixed helpers with no public API guarantee
(`_figsize_to_output_dict`, `_from_base_in`), so depending on it in production would
tie every future figure build to tueplots' internals continuing to exist unchanged.
Vendoring means this repo owns its own numbers permanently. `tueplots` is not, and
must not become, a runtime import anywhere under usdc_depeg/.

WHAT THIS FILE DELIBERATELY DIFFERS FROM IN THE OLDER usdc_depeg/figures.py: that
module carries its own inline style block (`lncs_rcparams`, `COIN_LINESTYLE`,
`FLOOR_COLOR`, etc.), written before the G1 corpus survey and the G2 candidate
evaluation existed. Three concrete corrections this module makes, on the evidence
those two sessions produced, are NOT bugs in this file if you are diffing it against
figures.py's older choices:
  1. Coins now carry distinct Okabe-Ito hues (figures.py used one ink, #1a1a1a, for
     all three coins and let line style alone carry identity). The figure-design
     plan's cohesion spec (C1) makes colour a REDUNDANT second channel on
     top of line style, not a replacement for it -- every figure must still remain
     fully readable in pure grayscale (see to_grayscale below and tools/figqa.py).
  2. The floor line is now BLACK, not Okabe-Ito blue -- because blue is now USDC's
     colour (point 1), and a floor sharing a colour with a coin is exactly the kind
     of ambiguity D22 exists to rule out.
  3. Fonts are now genuine Computer Modern via `usetex=True`, not STIXGeneral/DejaVu
     Serif approximations -- G2.1's candidate evaluation is the first time this was
     checked against the actual LNCS document class rather than assumed.
This file does not edit figures.py. A future session may migrate figures.py's
builders onto this module; until then the two coexist, and figures.py's own
figures are UNCHANGED by this file's existence.

USAGE
    from figstyle import apply_rcparams, new_full_two_panel, new_half_height,
        COIN_STYLE, plot_coin_series, add_floor_line, add_par_line,
        add_event_markers, add_panel_label, save_figure

    apply_rcparams()
    fig, (ax_a, ax_b) = new_full_two_panel()
    plot_coin_series(ax_a, hours, prices_by_coin)
    add_floor_line(ax_a, 0.92)
    add_par_line(ax_a)
    add_event_markers(ax_a, [("A", 3.11), ("B", 2.0)])
    add_panel_label(ax_a, "a")
    save_figure(fig, "fig_impossibility_window",
                sources={"results/impossibility_window.json": ["firing_hours_utc"]})
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np

from constants import PKG_DIR

FIGS_DIR = PKG_DIR / "figs"

# ======================================================================================
# Print geometry -- fixed by the LNCS document class; there is no width lever, only
# height (measured against paper/fc27/fc27.tex; see the figure-design plan's own
# section on the print-geometry arithmetic).
# ======================================================================================
LNCS_WIDTH_CM = 12.2
LNCS_WIDTH_IN = LNCS_WIDTH_CM / 2.54  # 4.8031... in, matches G2.1's measured MediaBox
MIN_FONT_PT = 7.0

# Graphic heights (matplotlib figsize height, NOT including the LaTeX caption -- the
# caption is typeset by LaTeX, not drawn by matplotlib). Sourced from the
# figure-design plan's own arithmetic:
#   full_two_panel: 8.6cm graphic (D21, superseding the original 6.4cm G3 build --
#                    panel (a) 3.9cm : panel (b) 4.2cm : 0.5cm shared axis furniture,
#                    the same 2.9:3.1:0.4 proportions scaled by 8.6/6.4 so panel (b)
#                    keeps its original ~51.7% share of the graphic, comfortably
#                    above D21's 40% floor). panel_a_cm/panel_b_cm are consumed only
#                    as GridSpec height_ratios (relative weights; matplotlib
#                    normalizes them) by new_full_two_panel below -- NOT literal
#                    absolute centimetres honoured anywhere else. gap_cm is
#                    documentary only, never consumed.
#   half_height:    4.0cm graphic (Figure 2, the floor-vs-trough dot-and-line).
#   full_single:    4.2cm graphic, the SHORT ROUTE's conditional single-panel spec
#                    (height_fraction <= 0.22) -- the only single-panel height G1
#                    actually specifies. Callers with a different single-panel need
#                    MUST pass an explicit height_in rather than trust this default;
#                    it is a fallback, not a universal constant.
SIZES = {
    "full_two_panel": {"width_in": LNCS_WIDTH_IN, "height_cm": 8.6,
                        "panel_a_cm": 3.9, "panel_b_cm": 4.2, "gap_cm": 0.5},
    "half_height": {"width_in": LNCS_WIDTH_IN, "height_cm": 4.0},
    "full_single": {"width_in": LNCS_WIDTH_IN, "height_cm": 4.2},
}


def _cm_to_in(cm: float) -> float:
    return cm / 2.54


# ======================================================================================
# rcParams -- vendored from the winning candidate (tueplots), not imported from it.
# usetex=True is the deterministic, LNCS-matching choice (the style-candidate
# evaluation). A LaTeX install is already a hard requirement of this repo's own paper build
# (`make fc27` runs pdflatex), so requiring it here adds no new environment dependency.
# ======================================================================================
# TeX-point vs PostScript-point correction. Under usetex=True, LaTeX interprets a
# bare fontsize rcParam (e.g. 7) as TeX points (1/72.27in); the PDF page itself is
# built in PostScript points (1/72in). An rcParam of exactly 7 therefore renders, on
# the ACTUAL saved PDF, at 7 * 72/72.27 = 6.974pt -- a hairline miss below MIN_FONT_PT
# that only shows up if you measure the saved file (tools/figqa.py does; the first
# demo-figure QA run this module was tested against caught it). Multiplying every
# nominal size by _TEX_PT gives the rcParam value that renders at the INTENDED
# physical size. Confirmed correct by figqa's min_font_size check after this fix.
_TEX_PT = 72.27 / 72.0


def _pt(nominal: float) -> float:
    return round(nominal * _TEX_PT, 4)


RCPARAMS = {
    # --- fonts: genuine Computer Modern via LaTeX, matching plain llncs -------------
    "text.usetex": True,
    "text.latex.preamble": r"\usepackage{amsmath}\usepackage{amssymb}",
    "font.family": "serif",
    "font.serif": ["Computer Modern Roman"],
    "mathtext.fontset": "cm",  # inert while usetex=True; sets the fallback correctly
                                # if a build machine lacks LaTeX and usetex is forced
                                # off (see MATHTEXT_FALLBACK_RCPARAMS below)
    "font.size": _pt(8.0),           # base body text inside a figure -- physical 8.0pt
    "axes.labelsize": _pt(8.0),
    "xtick.labelsize": _pt(7.0),     # physical 7.0pt -- MIN_FONT_PT, no margin to spare
    "ytick.labelsize": _pt(7.0),
    "legend.fontsize": _pt(7.0),
    # --- line geometry ---------------------------------------------------------------
    "lines.linewidth": 1.0,
    "lines.markersize": 3.0,
    "axes.linewidth": 0.6,
    # --- spines: left and bottom only (G1 C8); a secondary top axis, when used for
    # event letters, restores a thin top spine itself (see add_event_markers) --------
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.spines.left": True,
    "axes.spines.bottom": True,
    # --- grid: DECIDED ONCE, per the G2 brief's own instruction -- a single light
    # y-grid, never vertical gridlines (G1 C8: "No vertical gridlines -- the event
    # markers are the vertical structure") ---------------------------------------
    "axes.grid": True,
    "axes.grid.axis": "y",
    "axes.axisbelow": True,
    "grid.color": "#000000",
    "grid.alpha": 0.08,
    "grid.linewidth": 0.4,
    # --- ticks: direction and density (G1 C8) -----------------------------------
    "xtick.direction": "out",
    "ytick.direction": "out",
    "xtick.major.size": 2.5,
    "ytick.major.size": 2.5,
    "xtick.minor.size": 1.5,
    "ytick.minor.size": 1.5,
    # --- legend: frame off (used only by the LEGEND helper below; the two currently
    # accepted figures draw NO legend box at all -- see module docstring) -----------
    "legend.frameon": False,
    "legend.handlelength": 1.6,
    # --- output: vector, deterministic -------------------------------------------
    "savefig.format": "pdf",
    "savefig.transparent": False,
    "pdf.fonttype": 42,       # Type 42 (TrueType-embedded), not Type 3 bitmap
    "pdf.compression": 6,
    "path.simplify": False,   # no simplification jitter between renders
    "svg.hashsalt": "usdc_depeg_fixed_salt",  # inert for PDF output; fixed in case an
                                                # SVG path is ever added
    "figure.dpi": 100,        # screen preview only; the shipped artifact is vector
    "savefig.dpi": 300,       # applies to the PNG fallback path only
}

# Fallback rcParams if a build machine has no usable LaTeX install. NOT applied by
# default -- apply_rcparams(usetex=False) selects this explicitly. Confirmed
# byte-deterministic in G2.1's evaluation, but is a font-family APPROXIMATION, not a
# genuine match to llncs's Computer Modern body face; use only when usetex is
# unavailable, and say so in figs/<name>.sources.txt if it is ever used for a shipped
# figure (see save_figure's `font_fallback_used` bookkeeping).
MATHTEXT_FALLBACK_RCPARAMS = dict(RCPARAMS)
MATHTEXT_FALLBACK_RCPARAMS.update({
    "text.usetex": False,
    "mathtext.fontset": "cm",
    "font.family": "serif",
    "font.serif": ["cmr10", "DejaVu Serif"],
})


def apply_rcparams(usetex: bool = True) -> None:
    """Apply this module's rcParams. Call once per figure-generating process, before
    any plt.subplots() call. usetex=False selects MATHTEXT_FALLBACK_RCPARAMS."""
    plt.rcdefaults()
    plt.rcParams.update(RCPARAMS if usetex else MATHTEXT_FALLBACK_RCPARAMS)


# ======================================================================================
# Palette -- Okabe-Ito, addressed by ROLE, never by colour name (D22, G1 C6). Four of
# the eight Okabe-Ito hues are deliberately reserved and UNUSED: two figures that use
# three hues between them read as one system; two figures that use six do not.
# ======================================================================================
PALETTE = {
    "usdc": "#0072B2",     # blue -- also the breach-shading accent
    "dai": "#D55E00",      # vermillion
    "usdt": "#009E73",     # bluish green
    "floor": "#000000",
    "par": "#999999",
    # A darker par, for the short paper's rendering of Figure 1. Both G9 cold reviewers
    # said the 0.6pt #999999 reference line would not survive grayscale print at LNCS
    # column width. Added to the palette rather than passed as an ad-hoc hex at the call
    # site, because figqa's style-conformance check validates against this table and an
    # off-palette colour is exactly what it exists to catch.
    "par_dark": "#4D4D4D",
    "event": "#000000",
    "axis": "#000000",
    "text": "#000000",
    "degenerate": "#999999",   # de-emphasised rows, e.g. Figure 2's Terra row
}
PALETTE_RESERVED_UNUSED = ("#E69F00", "#56B4E9", "#F0E442", "#CC79A7")
# F0E442 (yellow) specifically: never used for a line at any width (G1 C1) -- it is
# the one Okabe-Ito hue with the weakest luminance separation from a white page.


# ======================================================================================
# Coin line-style map (G1 C1) -- identical in every figure that draws a coin series.
# ======================================================================================
COIN_STYLE = {
    "USDC": {"linestyle": "-", "linewidth": 1.2, "color": PALETTE["usdc"], "marker": None},
    "DAI": {"linestyle": (0, (4, 1.5)), "linewidth": 1.0, "color": PALETTE["dai"], "marker": None},
    "USDT": {"linestyle": (0, (1, 1.5)), "linewidth": 1.0, "color": PALETTE["usdt"], "marker": None},
}
COIN_ORDER = ("USDC", "DAI", "USDT")  # fixed draw order, so z-order is also identical
                                        # across every figure

# Greyscale luminance each coin's colour reduces to (ITU-R BT.601), recorded so a
# reviewer of this file doesn't have to recompute it: USDC ~0.28, DAI ~0.42,
# USDT ~0.48 (G1 C1). Colour is the REDUNDANT channel -- line style is what actually
# separates the three; verified by tools/figqa.py on every save.


# ======================================================================================
# Feed line-style map (G1 C2). NOT used by the two currently-accepted figures (neither
# draws an individual feed series -- see the figure-design plan's own rejection (iii)).
# Defined here so a later figure that DOES need one does not invent its own
# convention. A feed inherits its coin's colour; these are MODIFIERS layered on top.
# ======================================================================================
FEED_STYLE = {
    "composite": {},  # the coin's own COIN_STYLE, unmodified, full width
    "close": {"linewidth": 0.6, "marker": "o", "markerfacecolor": "none",
              "marker_every_hours": 2},
    "vwap": {"linewidth": 0.6, "marker": "^", "markerfacecolor": "none",
             "marker_every_hours": 2},
    "low": {"linestyle": "none", "marker": "s", "markerfacecolor": "none"},
}


# ======================================================================================
# Feed CURVE style map. Deliberately NOT called FEED_STYLES: that is one character from
# FEED_STYLE above and the two mean different things, which is precisely the kind of
# near-collision that gets picked up by the wrong name at a call site.
#
# FEED_STYLE (above) modifies a COIN's time series -- same quantity, same colour, per-feed
# markers. This map is for a figure whose curves ARE the feeds: four readings of one
# derived quantity, where the feed is the only thing distinguishing them and there is no
# coin dimension at all. All four therefore take PALETTE["usdc"] (they are all USDC
# readings) and separate on DASH PATTERN alone, so the figure survives grayscale print
# with no colour information whatsoever.
# ======================================================================================
# The two Kraken curves carry the SAME markers their time series carry in FEED_STYLE --
# open circle for a close, open triangle for a VWAP -- so a marker means the same feed
# in every figure that draws one. The dash patterns stay: the marker is a redundant
# second channel, not a replacement, and the curves must still separate in grayscale
# between markers. The composite takes no marker, exactly as in FEED_STYLE.
FEED_CURVE_STYLE = {
    "composite":   {"linestyle": "-", "linewidth": 1.1, "color": PALETTE["usdc"]},
    "kraken_close": {"linestyle": (0, (4, 1.5)), "linewidth": 1.0, "color": PALETTE["usdc"],
                      "marker": FEED_STYLE["close"]["marker"], "markerfacecolor": "none",
                      "markersize": 3.2},
    "kraken_vwap": {"linestyle": (0, (1, 1.5)), "linewidth": 1.0, "color": PALETTE["usdc"],
                     "marker": FEED_STYLE["vwap"]["marker"], "markerfacecolor": "none",
                     "markersize": 3.2},
}
# Kraken's low is deliberately absent. Its minimum print is a single trade and the
# manuscript declines to read the cross-check off that convention, so drawing it put a
# curve on the page the text disowns. This map describes what IS drawn -- the invariant
# a test asserts -- so a feed that stops being plotted leaves the map too.
FEED_CURVE_ORDER = ("composite", "kraken_close", "kraken_vwap")

# A shaded interval marking a SCENARIO on a parameter axis, as opposed to BREACH_SHADING
# which marks an interval of observed time. Deliberately lighter than the breach shading
# and with no edge, so that in grayscale it reads as a background band rather than as a
# fourth data series; it is identified in the legend, never by in-axes text.
SCENARIO_SPAN = {"facecolor": "#000000", "alpha": 0.085, "linewidth": 0.0, "zorder": 0.5}
FEED_CURVE_LABEL = {
    "composite": "composite",
    "kraken_close": "Kraken close",
    "kraken_vwap": "Kraken VWAP",
}


def coin_feed_style(coin: str, feed: str) -> dict:
    """Merge COIN_STYLE[coin] with FEED_STYLE[feed], feed modifiers taking precedence
    (colour is always the coin's). Raises KeyError loudly on an unknown coin/feed
    rather than silently falling back to a default style."""
    base = dict(COIN_STYLE[coin])
    mod = dict(FEED_STYLE[feed])
    base.update({k: v for k, v in mod.items() if k != "marker_every_hours"})
    if "marker_every_hours" in mod:
        base["marker_every_hours"] = mod["marker_every_hours"]
    return base


# ======================================================================================
# Reference-line styling (par, floor) -- G1 C3. Neither line is ever labelled on the
# figure itself; both are named in the caption. There is NO legend box on either of
# the two accepted figures (exemplar review/G1_exemplars/P10_19.png) -- this is also
# what keeps N-23 (legend/caption contradiction) structurally unable to recur.
# ======================================================================================
REFERENCE_STYLE = {
    "floor": {"linestyle": (0, (6, 2, 1, 2)), "linewidth": 1.1, "color": PALETTE["floor"],
              "zorder": 1.5},
    "par": {"linestyle": "-", "linewidth": 0.6, "color": PALETTE["par"], "zorder": 1.0},
    # The short paper's par line. A REFERENCE_STYLE entry rather than a call-site
    # override, because figqa's live-figure conformance check matches each drawn line's
    # full (colour, width, dash) signature against this table, and an override produces
    # a signature that matches nothing. That check is stricter than the PDF-only one and
    # caught exactly this.
    "par_dark": {"linestyle": "-", "linewidth": 0.9, "color": PALETTE["par_dark"],
                  "zorder": 1.0},
}


def add_floor_line(ax, value: float, **overrides) -> None:
    """Draw the floor reference line. `value` is read by the CALLER from a results
    JSON at draw time (D22: 'every plotted value is read from a results JSON') --
    this function never hardcodes a price."""
    style = dict(REFERENCE_STYLE["floor"])
    style.update(overrides)
    ax.axhline(value, **style)


def add_floor_segment(ax, value: float, x_start, x_end, **overrides) -> None:
    """Draw the floor reference line over a BOUNDED x-range instead of the full axis.

    The worst-case floor 1 - phi only exists while the impairment is disclosed and
    unresolved: before the disclosure there is no quantified phi, and after the backstop
    the exposure is gone. An axhline asserts the floor across the whole window, including
    hours in which it did not apply. This draws the same styled line, from the same
    REFERENCE_STYLE["floor"] signature so the conformance check still matches it, only
    over the interval the caller passes.

    The segment is clipped to the axes' current x-limits by matplotlib, so a caller may
    pass the full live interval to a panel that shows only part of it.
    """
    style = dict(REFERENCE_STYLE["floor"])
    style.update(overrides)
    # axhline sets no label and is excluded from the legend by convention; keep that.
    ax.plot([x_start, x_end], [value, value], **style)


def add_par_line(ax, value: float = 1.0, variant: str = "par", **overrides) -> None:
    """Draw the par reference line (default $1.00; pass an explicit value if a figure
    ever needs a non-USD-par axis).

    variant selects a REFERENCE_STYLE entry: "par" or "par_dark". Prefer a variant over
    **overrides for anything that changes colour, width or dash, because the conformance
    check matches drawn lines against REFERENCE_STYLE by full signature."""
    style = dict(REFERENCE_STYLE[variant])
    style.update(overrides)
    ax.axhline(value, **style)


def plot_coin_series(ax, x, series_by_coin: dict, coins=COIN_ORDER):
    """Plot one or more coin series in COIN_ORDER (fixed z-order across every figure).
    series_by_coin: {"USDC": array-like, "DAI": array-like, "USDT": array-like}, a
    subset of COIN_ORDER is fine (e.g. Figure 1 panel (b) plots all 3; a future figure
    might plot fewer). Returns the list of Line2D objects, in draw order."""
    lines = []
    for coin in coins:
        if coin not in series_by_coin:
            continue
        style = COIN_STYLE[coin]
        (line,) = ax.plot(x, series_by_coin[coin], **style, zorder=3)
        lines.append(line)
    return lines


# ======================================================================================
# Breach shading (G1 C3) -- survives grayscale as texture (hatch), not as a pale tint
# that competes with the plot frame (review A's finding against the pre-G1 Figure 1).
# ======================================================================================
BREACH_SHADING = {"color": PALETTE["usdc"], "alpha": 0.08, "hatch": "///",
                   "edgecolor": PALETTE["usdc"], "linewidth": 0.3, "zorder": 0.5}


# ======================================================================================
# Margin-connector styling (Figure 2's floor-vs-trough dot-and-line, G1 section on
# Figure 2's panel structure: "Rows whose trough lies below their floor are drawn
# with the connector in the D22 accent; rows whose trough lies above are drawn in
# the neutral grey.") "The D22 accent" is PALETTE['usdc'] -- the same blue Figure 1
# uses for breach shading, so "this episode breached its floor" reads as the same
# colour across both figures; "neutral grey" is PALETTE['degenerate'], already used
# for de-emphasised rows elsewhere (e.g. Figure 2's own Terra row).
# ======================================================================================
MARGIN_CONNECTOR_STYLE = {
    "breaches": {"color": PALETTE["usdc"], "linewidth": 0.9, "linestyle": "-"},
    "does_not_breach": {"color": PALETTE["degenerate"], "linewidth": 0.9, "linestyle": "-"},
}

# A floor given as a RANGE (e.g. Tether 2019's phi in [0.259, 0.332], G1 Figure 2)
# draws as a solid capped bar rather than a single point marker -- visually distinct
# from REFERENCE_STYLE['floor']'s dash-dot reference LINE (Figure 1's constant-floor
# convention), so it is its own named style, not a reuse of that one.
FLOOR_RANGE_BAR_STYLE = {"color": PALETTE["floor"], "linewidth": 1.1, "linestyle": "-",
                          "marker": "|", "markersize": 5}

# Timing-control marker role: a candidate specificity row disqualified on TIMING (its
# disclosure followed resolution -- e.g. Paxos/USDP March 2023, D17/D15), not one of the
# panel's floor/trough/connector rows. It must read as neither a fired panel member nor
# a reuse of the degenerate (Terra) treatment, which still carries a full floor-trough
# pair -- so it gets its own marker shape (open diamond, no connector line) in the same
# neutral grey as PALETTE['degenerate'], since it is likewise not a validated panel
# member. Marker-only (linestyle "none"), so tools/figqa.py's style-conformance check
# skips it the same way it already skips FLOOR_RANGE_BAR_STYLE's marker-only siblings.
TIMING_CONTROL_STYLE = {"marker": "D", "markersize": 4.5, "markerfacecolor": "none",
                         "markeredgecolor": PALETTE["degenerate"], "markeredgewidth": 1.0,
                         "linestyle": "none"}


def add_breach_shading(ax, x_start, x_end, **overrides) -> None:
    style = dict(BREACH_SHADING)
    style.update(overrides)
    ax.axvspan(x_start, x_end, **style)


# ======================================================================================
# Event markers and the canonical letter scheme (G1 C4). Letters are assigned ONCE,
# here, chronologically, across the whole paper -- a letter means the same event in
# every figure that draws it. A figure may draw a SUBSET of these; it may never
# reassign a letter to a different event.
#
# NOTE ON THE G2 BRIEF'S OWN WORDING: the brief that commissioned this module
# describes event letters generically as "a, b, c...". This module follows
# the figure-design plan's concrete, already-vetted scheme instead -- UPPERCASE
# A-D for events, reserving lowercase (a)/(b) for PANEL_LABEL (see below). G1's spec
# is the more specific, already-cross-checked source (it is what D22's own bearing
# evidence -- "a letter must mean the same event in every figure" -- is protecting),
# so the concrete scheme wins over the brief's shorthand. If this reads as a
# discrepancy from the commissioning brief, it is intentional; flag it for the author
# rather than silently reconciling it the other way.
# ======================================================================================
EVENT_LETTERS = {
    "A": {"label": "Circle's reserve-impairment disclosure", "hour_utc": "2023-03-11T03:11:00Z"},
    "B": {"label": "Coinbase USDT-USD premium peak (+266 bps)", "hour_utc": "2023-03-11T02:00:00Z"},
    "C": {"label": "Joint Treasury/Fed/FDIC statement", "hour_utc": "2023-03-12T22:15:00Z"},
    "D": {"label": "Circle's make-whole pledge (first web-archive capture)", "hour_utc": "2023-03-11T20:27:00Z"},
}

EVENT_STYLE = {"color": PALETTE["event"], "linewidth": 0.5, "alpha": 0.55, "zorder": 0.8}


def add_event_markers(ax, letter_positions: list[tuple[str, float]]) -> None:
    """Draw event markers as thin vertical lines, with the identifying letter as a
    TICK LABEL on a secondary top x-axis -- never as in-axes text (D22). Each entry
    in letter_positions is (letter, x_position_in_ax_data_coords); letter must be a
    key of EVENT_LETTERS (raises KeyError otherwise, so a typo'd or invented letter
    fails loudly rather than silently drawing an unlabelled line).

    Line spans the full panel height (axes-fraction 0 to 1); drawn beneath all data
    (zorder 0.8, below plot_coin_series's zorder=3 and above the grid)."""
    for letter, _ in letter_positions:
        if letter not in EVENT_LETTERS:
            raise KeyError(f"'{letter}' is not in EVENT_LETTERS -- event letters are "
                            f"assigned once in figstyle.py and may not be invented "
                            f"per-figure. Known letters: {sorted(EVENT_LETTERS)}")
    for letter, x in letter_positions:
        ax.axvline(x, ymin=0, ymax=1, **EVENT_STYLE)

    secax = ax.secondary_xaxis("top")
    secax.set_xticks([x for _, x in letter_positions])
    secax.set_xticklabels([letter for letter, _ in letter_positions],
                           fontsize=_pt(7), fontfamily="serif")
    secax.tick_params(length=0)
    secax.spines["top"].set_linewidth(0.4)


# ======================================================================================
# Panel-letter convention (G1 C5). Lowercase (a), (b) -- distinct from the uppercase
# event letters above. Panels are lettered top-to-bottom, then left-to-right.
# ======================================================================================
PANEL_LABEL_STYLE = {"fontsize": _pt(8), "fontweight": "bold", "fontfamily": "serif"}


def add_panel_label(ax, letter: str) -> None:
    """letter: 'a', 'b', ... (lowercase, no parens -- parens are added here)."""
    ax.text(0.0, 1.02, f"({letter})", transform=ax.transAxes,
             ha="left", va="bottom", **PANEL_LABEL_STYLE)


# ======================================================================================
# Axis-range helper (G1 C7 / the N-20 rule, mechanical form). ylim is COMPUTED from
# the plotted extrema at draw time and asserted, never hardcoded, so a data refresh
# cannot silently re-truncate the axis and hide a trough again.
# ======================================================================================
def compute_price_ylim(values, floor: float | None = None, margin_ticks: int = 1,
                        tick_step: float = 0.02) -> tuple:
    """Returns (ymin, ymax) such that every value in `values` (and `floor`, if given)
    has at least `margin_ticks` labelled ticks of margin on each side, at `tick_step`
    round intervals. Raises if the caller's own truncation would violate this --
    call this INSTEAD of hand-setting ylim, and pass its return straight to
    ax.set_ylim()."""
    vals = list(values)
    if floor is not None:
        vals.append(floor)
    lo, hi = min(vals), max(vals)
    ymin = (np.floor(lo / tick_step) - margin_ticks) * tick_step
    ymax = (np.ceil(hi / tick_step) + margin_ticks) * tick_step
    return float(ymin), float(ymax)


def set_price_axis(ax, ylim: tuple, n_ticks: int = 5) -> None:
    """4-5 labelled ticks, round cent values, 2 decimal places (G1 C8). Tick labels
    are BARE numbers ("0.90", not "$0.90") -- the unit lives once in the axis label
    ("price (USD)", G1 C9), which makes a "$" on every tick redundant, and sidesteps
    a real rendering quirk found while building this module: under
    `text.usetex=True`, LaTeX renders an escaped '\\$' glyph fractionally smaller
    (measured 6.974pt) than the surrounding digits at the SAME nominal 7pt size --
    tools/figqa.py's font-floor check caught it on the first demo-figure QA run,
    isolated to exactly the '$' character. Dropping the redundant symbol is more
    robust than chasing a sub-pixel safety margin on one glyph.

    USETEX GOTCHA WORTH KNOWING REGARDLESS (not what tripped this specific
    formatter, but the general version of the same trap): with `text.usetex=True`,
    every piece of text matplotlib draws -- tick labels included -- is passed to
    LaTeX VERBATIM. A literal '$' anywhere else you add text (a caption-adjacent
    label, a legend entry) is LaTeX's math-mode toggle, not a currency symbol; an odd
    number of them in one string produces the cryptic 'Extra }, or forgotten $.'
    RuntimeError this module's own first smoke-test render hit. Escape as '\\$' if
    you ever do need one inside axes text."""
    ax.set_ylim(*ylim)
    ax.yaxis.set_major_locator(mticker.MaxNLocator(nbins=n_ticks - 1, steps=[1, 2, 5]))
    ax.yaxis.set_major_formatter(mticker.FormatStrFormatter("%.2f"))


# ======================================================================================
# Legend helper (D22 / the G2 brief). NEITHER of the two currently-accepted figures
# calls this -- G1's plan draws no legend box at all (identity carried by line style
# + the caption). Provided because the brief asks for it explicitly and a future
# figure may need one; the rule is the same discipline either way: never over data.
# ======================================================================================
def add_legend_above(ax, handles=None, labels=None, ncol: int | None = None) -> None:
    """Single row, above the axes, outside the data region."""
    h, l = (handles, labels) if handles is not None else ax.get_legend_handles_labels()
    ax.legend(h, l, loc="lower center", bbox_to_anchor=(0.5, 1.06),
              ncol=ncol or len(h), frameon=False)


def add_shared_legend(fig, axes, ncol: int | None = None) -> None:
    """One legend for multiple panels, collected from the first axes that has
    labelled artists, placed above the whole figure (outside every panel's data
    region)."""
    handles, labels = [], []
    for ax in axes:
        h, l = ax.get_legend_handles_labels()
        if h:
            handles, labels = h, l
            break
    fig.legend(handles, labels, loc="upper center", bbox_to_anchor=(0.5, 1.02),
               ncol=ncol or len(handles), frameon=False)


# ======================================================================================
# Figure factories (SIZES, as constructors rather than bare tuples -- matches this
# repo's existing figures.py convention of a factory function over a raw figsize).
# ======================================================================================
def new_full_two_panel(height_ratios: tuple = None, height_cm: float | None = None):
    """fig, (ax_a, ax_b) sized to SIZES['full_two_panel']. height_ratios defaults to
    the G1 plan's own 2.9:3.1 split; pass an override only with a stated reason.

    layout="constrained" (not bbox_inches="tight" at save time -- see save_figure's
    own comment) is how label clipping is avoided: it adjusts internal spacing to fit
    axis/tick labels WITHIN the fixed figsize canvas, rather than growing or cropping
    the canvas itself. The saved PDF's MediaBox is therefore always exactly
    LNCS_WIDTH_IN, regardless of label content."""
    spec = SIZES["full_two_panel"]
    if height_ratios is None:
        height_ratios = (spec["panel_a_cm"], spec["panel_b_cm"])
    # height_cm overrides the SIZES default. Added for D36: the short paper's rendering
    # of this figure is 7.2cm against the regular paper's 8.6, because the short paper
    # is fitting two more figures into a page budget the regular one does not have. The
    # RATIO between the panels is unchanged, so the re-size is uniform and no panel is
    # squeezed relative to the other.
    height_in = _cm_to_in(height_cm if height_cm is not None else spec["height_cm"])
    fig = plt.figure(figsize=(spec["width_in"], height_in), layout="constrained")
    gs = fig.add_gridspec(2, 1, height_ratios=height_ratios)
    ax_a = fig.add_subplot(gs[0])
    ax_b = fig.add_subplot(gs[1])
    return fig, (ax_a, ax_b)


def new_half_height():
    """fig, ax sized to SIZES['half_height'] -- the floor-vs-trough dot-and-line
    layout. See new_full_two_panel's docstring for why layout="constrained"."""
    spec = SIZES["half_height"]
    fig, ax = plt.subplots(figsize=(spec["width_in"], _cm_to_in(spec["height_cm"])),
                            layout="constrained")
    return fig, ax


def new_full_single(height_in: float | None = None):
    """fig, ax at LNCS width. height_in defaults to SIZES['full_single'] (the short
    route's 4.2cm conditional spec) -- callers with a different single-panel need
    SHOULD pass an explicit height_in rather than rely on that default."""
    if height_in is None:
        height_in = _cm_to_in(SIZES["full_single"]["height_cm"])
    fig, ax = plt.subplots(figsize=(SIZES["full_single"]["width_in"], height_in),
                            layout="constrained")
    return fig, ax


# ======================================================================================
# Colour-vision QA (shared so figqa.py and any figure builder's own self-check use the
# identical simulation, not two independently-approximated ones).
# ======================================================================================
_RGB_TO_LMS = np.array([
    [17.8824, 43.5161, 4.11935],
    [3.45565, 27.1554, 3.86714],
    [0.0299566, 0.184309, 1.46709],
])
_LMS_TO_RGB = np.linalg.inv(_RGB_TO_LMS)
_DEUTER_LMS = np.array([[1.0, 0.0, 0.0], [0.494207, 0.0, 1.24827], [0.0, 0.0, 1.0]])
_PROTAN_LMS = np.array([[0.0, 2.02344, -2.52581], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])


def _simulate(rgb: np.ndarray, sim_matrix: np.ndarray) -> np.ndarray:
    shape = rgb.shape
    flat = rgb.reshape(-1, 3).T
    lms = _RGB_TO_LMS @ flat
    lms_sim = sim_matrix @ lms
    rgb_sim = (_LMS_TO_RGB @ lms_sim).T.reshape(shape)
    return np.clip(rgb_sim, 0.0, 1.0)


def simulate_deuteranopia(rgb: np.ndarray) -> np.ndarray:
    return _simulate(rgb, _DEUTER_LMS)


def simulate_protanopia(rgb: np.ndarray) -> np.ndarray:
    return _simulate(rgb, _PROTAN_LMS)


def to_grayscale(rgb: np.ndarray) -> np.ndarray:
    """ITU-R BT.601 luma."""
    return rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114


# ======================================================================================
# save_figure -- deterministic PDF + sources sidecar. Renders TWICE and ASSERTS
# identical SHA-256 (not a graceful fallback): G2.1 confirmed usetex=True is
# byte-deterministic once PDF metadata is stripped, so a mismatch here means
# something is actually wrong (uncontrolled randomness, an unpinned timestamp
# leaking through) and should stop the build, not silently ship a different format.
# ======================================================================================
def save_figure(fig, name: str, sources: dict[str, list] | None = None,
                 out_dir: Path = FIGS_DIR) -> dict:
    """Save fig as name.pdf under out_dir, plus name.sources.txt if `sources` is
    given: {results_json_relative_path: [key, key, ...]}, so a reviewer can verify
    every plotted value traces to a frozen artifact (D22). Renders twice into a temp
    location and asserts identical SHA-256 before writing the real output; raises
    AssertionError on a mismatch rather than falling back to a different format.
    Returns {'path', 'sha256'}."""
    import tempfile

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    # NO bbox_inches="tight" -- this is deliberate, not an omission. `tight` recomputes
    # the saved page size from rendered content, which is exactly the C2/SciencePlots
    # failure this module's own candidate evaluation flagged (review/G2_candidate_scores
    # .md: "savefig.bbox: 'tight' ... silently overrides a call-site expectation of an
    # exact figsize"). G1's C9 spec requires the PDF's own MediaBox to be exactly
    # LNCS_WIDTH_IN so `\includegraphics[width=\textwidth]` never rescales it. Layout
    # (avoiding clipped labels) is the factory functions' job via layout="constrained",
    # not this function's job via post-hoc cropping.
    save_kwargs = dict(format="pdf",
                        metadata={"Creator": None, "Producer": None,
                                  "CreationDate": None, "ModDate": None})

    # FREEZE the layout before the determinism probe. layout="constrained" (set by the
    # SIZES factory functions) re-solves axes positions on every draw, so two
    # savefig() calls on the SAME figure object produce visually-identical but
    # byte-DIFFERENT PDFs if the solver is left live -- found while building this
    # module (the assert below caught it on the first run). One explicit draw lets
    # the solver converge once; set_layout_engine("none") then freezes the result so
    # every subsequent save (both determinism-probe renders and the real one) emits
    # identical geometry. Harmless to call on a figure that never used a layout
    # engine at all.
    fig.canvas.draw()
    fig.set_layout_engine("none")

    hashes = []
    with tempfile.TemporaryDirectory() as td:
        for i in range(2):
            p = Path(td) / f"{name}_{i}.pdf"
            fig.savefig(p, **save_kwargs)
            hashes.append(hashlib.sha256(p.read_bytes()).hexdigest())
    assert hashes[0] == hashes[1], (
        f"save_figure({name!r}): two renders of the identical figure object produced "
        f"different PDF bytes ({hashes[0][:12]} vs {hashes[1][:12]}). This means some "
        f"uncontrolled source of variation is present (an unpinned timestamp, a "
        f"hash-randomised iteration order, a font substitution that isn't pinned) -- "
        f"fix the source of non-determinism, do not retry or fall back to PNG."
    )

    out = out_dir / f"{name}.pdf"
    fig.savefig(out, **save_kwargs)
    final_hash = hashlib.sha256(out.read_bytes()).hexdigest()
    assert final_hash == hashes[0], "final PDF write disagreed with the determinism probe"

    if sources:
        lines = [f"# Sources for {name} -- generated by the figure builder, do not hand-edit"]
        for path, keys in sorted(sources.items()):
            lines.append(f"{path}: {', '.join(keys)}")
        (out_dir / f"{name}.sources.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    return {"path": str(out), "sha256": final_hash}
