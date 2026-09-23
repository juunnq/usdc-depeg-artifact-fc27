"""Publication figures for the paper, generated from the frozen snapshot and
results/ JSONs. No network; deterministic PDF output. Writes to ``figs/``.

Figures (the D21 set; every prior generator is retired, nothing from the older
inline styling in this module survives):
  fig_event         -- two-panel de-peg event figure: (a) USDC/DAI/USDT hourly
                        prices over the full 8-17 March 2023 analysis window
                        against par and the disclosed floor, one shaded breach
                        region, event markers A/B; (b) the same three coins
                        hour-by-hour on 11 March only, plus Kraken USDC/USD
                        close and VWAP corroboration, per-hour hatched breach
                        shading with the genuine 08:57 UTC recovery hour left
                        visibly unshaded.
  fig_margin_paths  -- single-panel margin-trajectory plot (D21, replacing the
                        retired fig_floor_trough dot-and-segment plot): one
                        line per specificity-panel episode (USDC 2023, Tether
                        2019, Terra 2022 -- Paxos/USDP is excluded from this
                        panel by ruling, kept to a one-sentence body mention
                        elsewhere), each episode's own price minus its own
                        floor (cents) plotted against hours since its own
                        disclosure/anchor timestamp, -24h to +120h.

All styling comes from figstyle.py (fonts, palette, line/marker conventions,
sizing, save_figure's determinism check) -- this module contains no rcParams,
hex colours, or dash tuples of its own.
"""
from __future__ import annotations

import json
from datetime import timedelta

import matplotlib.dates as mdates
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd

import constants
import figstyle
from constants import DATA_DIR, PKG_DIR, RESULTS_DIR

FIGS_DIR = PKG_DIR / "figs"
# tables.py imports this constant from here (not from constants.py) -- kept for
# that cross-module dependency even though neither figure builder below uses it
# itself.
TABLES_DIR = PKG_DIR.parent / "paper" / "fc27" / "tables"

# Number of labelled y-ticks passed to figstyle.set_price_axis for fig_event.
# The default (5) lands ticks at a 0.10 step for this figure's range
# (0.80/0.90/1.00/1.10), leaving no visible tick between 0.80 (outside ylim)
# and 0.90 (above the 0.88 ceiling this figure must clear). n_ticks=6 lands a
# 0.05 step (0.85/0.90/.../1.05), clearing both tick requirements -- verified
# in fig_event() by reproducing figstyle's own MaxNLocator call rather than
# assumed.
_EVENT_N_TICKS = 6


def _iso_to_dt(iso_str: str):
    """Parse an ISO-8601 timestamp (with 'Z' or '+00:00' offset) to a tz-aware
    python datetime, the type figstyle's plotting calls expect."""
    return pd.Timestamp(iso_str).tz_convert("UTC").to_pydatetime()


def _day_month_formatter(x, _pos=None) -> str:
    """'8 Mar', '17 Mar', ... -- avoids the non-portable '%-d' strftime code
    (glibc-only, not available on Windows, where this repo also builds)."""
    d = mdates.num2date(x)
    return f"{d.day} {d.strftime('%b')}"


def _merge_close_event_labels(fig, ax) -> None:
    """add_event_markers draws one correctly-positioned vertical line per event
    and one tick label per event on its secondary top axis -- both accurate,
    but on fig_event's panel (a), whose x-axis spans 9 days, event B (02:00)
    and event A (03:11, 11 March) are only 71 minutes apart, so their tick
    labels render on top of each other with no way for a reader to tell which
    line each letter names. Vertically staggering the colliding label was
    tried and rejected in QA: stacking breaks the "tick label sits directly
    above its own line" property that makes the mapping obvious for every
    OTHER marker in this figure, so a stacked "A"/"B" read as ambiguous rather
    than merely close. Fix: when two tick labels' rendered bboxes overlap,
    replace them with ONE combined label ("B,A") at their shared midpoint x --
    still a tick label (D22 permits tick labels, not a callout), and both
    vertical lines stay exactly where they belong; only the label rendering is
    merged. A no-op for panel (b), where A and B are hours apart and never
    collide."""
    secax = ax.child_axes[-1]
    fig.canvas.draw()
    labels = secax.get_xticklabels()
    if len(labels) < 2:
        return
    renderer = fig.canvas.get_renderer()
    order = sorted(range(len(labels)), key=lambda i: labels[i].get_window_extent(renderer).x0)
    ticks = list(secax.get_xticks())
    merged_ticks, merged_text = [], []
    i = 0
    while i < len(order):
        idx = order[i]
        box_i = labels[idx].get_window_extent(renderer)
        if i + 1 < len(order):
            idx2 = order[i + 1]
            box_j = labels[idx2].get_window_extent(renderer)
            if box_j.x0 < box_i.x1:  # horizontal overlap -- merge this pair
                merged_ticks.append((ticks[idx] + ticks[idx2]) / 2.0)
                merged_text.append(f"{labels[idx].get_text()},{labels[idx2].get_text()}")
                i += 2
                continue
        merged_ticks.append(ticks[idx])
        merged_text.append(labels[idx].get_text())
        i += 1
    secax.set_xticks(merged_ticks)
    secax.set_xticklabels(merged_text, fontsize=labels[0].get_fontsize(),
                           fontfamily=labels[0].get_fontfamily())
    fig.canvas.draw()


def _visible_yticks(ylim: tuple, n_ticks: int) -> list:
    """Reproduce the MaxNLocator call figstyle.set_price_axis makes internally,
    so fig_event's own assertions check what will ACTUALLY be drawn (ticks
    outside ylim are computed by the locator but never rendered), not merely
    what the locator returns unfiltered."""
    locator = mticker.MaxNLocator(nbins=n_ticks - 1, steps=[1, 2, 5])
    ticks = locator.tick_values(*ylim)
    return [t for t in ticks if ylim[0] <= t <= ylim[1]]


def fig_event(short: bool = False):
    """fig:event -- see this module's own docstring. Every plotted value is
    read live from data/price_hourly.csv and results/*.json (see the
    `sources=` dict passed to figstyle.save_figure below)."""
    price = pd.read_csv(DATA_DIR / "price_hourly.csv")
    imposs = json.loads((RESULTS_DIR / "impossibility_window.json").read_text(encoding="utf-8"))
    timing = json.loads((RESULTS_DIR / "usdt_premium_timing.json").read_text(encoding="utf-8"))
    panel_b_src = json.loads((RESULTS_DIR / "panel_11mar.json").read_text(encoding="utf-8"))

    floor = imposs["floor_worstcase"]
    assert abs(floor - (1 - constants.PHI_SVB)) < 1e-9, (
        f"floor_worstcase={floor} != 1 - PHI_SVB={1 - constants.PHI_SVB}"
    )

    a_dt = _iso_to_dt(timing["reference_points_utc"]["disclosure"])
    b_dt = _iso_to_dt(timing["peaks"]["coinbase_usdt_usd"]["timestamp_utc"])

    # The interval over which the worst-case floor is the operative bound: from the
    # disclosure that quantifies phi to the federal backstop that removes the exposure.
    # Both read from the results JSON rather than written here.
    live_start = _iso_to_dt(timing["reference_points_utc"]["disclosure"])
    live_end = _iso_to_dt(
        timing["reference_points_utc"]["joint_statement_treasury_fed_fdic"])
    assert live_start < live_end, "floor-live interval is inverted"

    breach_start = _iso_to_dt(imposs["impossibility_region_usdc"]["first_breach_utc"])
    breach_end = _iso_to_dt(imposs["impossibility_region_usdc"]["last_breach_utc"])

    # ---- panel (a): full-window composite series, one per coin (each coin's
    # rows carry its OWN per-hour timestamp offset by a few seconds -- e.g.
    # USDC's first row is 1678233469, DAI's is 1678233352 -- so the three
    # series do NOT share one x-grid; plot_coin_series is therefore called
    # once per coin below, each with its own x, rather than once with one
    # shared x for all three). ----
    mask = (
        price["symbol"].isin(list(figstyle.COIN_ORDER))
        & (price["timestamp"] >= constants.WINDOW_START_S)
        & (price["timestamp"] <= constants.WINDOW_END_S)
    )
    win = price.loc[mask].copy()
    win["dt"] = pd.to_datetime(win["timestamp"], unit="s", utc=True)

    per_coin_a = {}
    for coin in figstyle.COIN_ORDER:
        sub = win[win["symbol"] == coin].sort_values("dt")
        per_coin_a[coin] = (sub["dt"].dt.to_pydatetime(), sub["price"].to_numpy())

    # ---- panel (b): 11 March composite series (same per-coin-x caveat) +
    # Kraken USDC/USD close and vwap (low is explicitly excluded -- the
    # single-trade wick is discussed in the paper body, not this figure). ----
    per_coin_b = {}
    for coin in figstyle.COIN_ORDER:
        rows = panel_b_src["panel"][f"composite_{coin}"]
        x = [_iso_to_dt(r["hour_utc"]) for r in rows]
        y = [r["price"] for r in rows]
        per_coin_b[coin] = (x, y)

    kraken_rows = panel_b_src["panel"]["kraken_usdcusd"]
    # hour_utc is the bar's OPEN. Verified against the raw tape: the bar labelled
    # 03:00 has close 0.9150, which is the last trade at 03:59:59, and its rebuilt
    # VWAP matches the stored value to five decimals; the closing-label hypothesis
    # reproduces neither. Plotting a close at its bar's open therefore draws it a full
    # hour early -- which put the first sub-floor close at 03:00, left of the floor's
    # own start and before the 03:11 disclosure that created the floor.
    #
    # The short rendering places each statistic at the instant it describes: a close at
    # the bar's END, a VWAP at the bar's MIDPOINT. The regular rendering is frozen and
    # keeps the open-stamped positions.
    kraken_open = [_iso_to_dt(r["hour_utc"]) for r in kraken_rows]
    if short:
        kraken_close_x = [t + timedelta(hours=1) for t in kraken_open]
        kraken_vwap_x = [t + timedelta(minutes=30) for t in kraken_open]
    else:
        kraken_close_x = list(kraken_open)
        kraken_vwap_x = list(kraken_open)
    kraken_close = [r["close"] for r in kraken_rows]
    kraken_vwap = [r["vwap"] for r in kraken_rows]

    # ================================================================== y-range
    # Union of every plotted value across BOTH panels, plus floor and par --
    # computed once via figstyle.compute_price_ylim and applied to both axes
    # (D22: neither panel is independently rescaled).
    all_values = []
    all_values += list(win["price"])
    for coin in figstyle.COIN_ORDER:
        all_values += per_coin_b[coin][1]
    all_values += kraken_close + kraken_vwap
    all_values += [floor, 1.0]

    ylim = figstyle.compute_price_ylim(all_values)
    assert ylim[0] <= 0.86, f"ylim[0]={ylim[0]} exceeds the 0.86 task ceiling"

    usdt_window_max = win.loc[win["symbol"] == "USDT", "price"].max()
    assert ylim[1] >= usdt_window_max, (
        f"ylim[1]={ylim[1]} is below the window's USDT maximum {usdt_window_max}"
    )

    visible = _visible_yticks(ylim, _EVENT_N_TICKS)
    assert any(t <= 0.88 for t in visible), (
        f"no labelled y-tick <= 0.88 among visible ticks {visible} for ylim={ylim}"
    )
    assert any(t > usdt_window_max for t in visible), (
        f"no labelled y-tick above the USDT max {usdt_window_max} among "
        f"visible ticks {visible} for ylim={ylim}"
    )

    # ================================================================== figure
    figstyle.apply_rcparams()
    # D36: the short rendering is 7.2cm, the regular one 8.6. Panel ratios are
    # untouched, so both panels shrink by the same factor.
    fig, (ax_a, ax_b) = figstyle.new_full_two_panel(
        # H2 T8 rung (ii): 7.20 -> 6.80 cm for the short target only. The regular
        # target keeps the SIZES default and is not touched.
        height_cm=6.8 if short else None)

    # Reserve a top margin for the figure-level legend. constrained_layout does
    # not otherwise account for the secondary top x-axis add_event_markers adds
    # to panel (a) -- without this, the legend (placed at figure-fraction
    # y=1.02 by figstyle.add_shared_legend) and panel (a)'s event-letter tick
    # row (placed just above ax_a's own spine) land in the same vertical band
    # and overlap. Only the OUTER rect changes; panel (a):(b) height_ratios
    # (2.9:3.1, figstyle's own numbers) and the overall 12.2x6.4cm page size
    # are untouched.
    # The reserved top band exists for the REGULAR rendering, whose panel (a) carries a
    # secondary top axis of event-letter ticks that constrained_layout does not account
    # for. The short rendering has no event letters in either panel, so 16% of a 6.8cm
    # canvas was being held empty between the legend and panel (a). Giving it back to
    # the panels changes no outer dimension and leaves the legend outside the axes.
    fig.get_layout_engine().set(rect=(0, 0, 1, 0.95 if short else 0.84))

    # ---- panel (a) ----
    lines_a = []
    for coin in figstyle.COIN_ORDER:
        x, y = per_coin_a[coin]
        lines_a += figstyle.plot_coin_series(ax_a, x, {coin: y})
    for coin, line in zip(figstyle.COIN_ORDER, lines_a):
        line.set_label(coin)

    ax_a.set_xlim(
        pd.Timestamp(constants.WINDOW_START_S, unit="s", tz="UTC").to_pydatetime(),
        pd.Timestamp(constants.WINDOW_END_S, unit="s", tz="UTC").to_pydatetime(),
    )

    # The floor exists only while the impairment is disclosed and unresolved. In the
    # short rendering it is drawn over exactly that interval; the regular rendering is
    # frozen and keeps the full-width axhline.
    if short:
        figstyle.add_floor_segment(ax_a, floor, live_start, live_end)
    else:
        figstyle.add_floor_line(ax_a, floor)
    # S-13: the short rendering darkens par. At 0.6pt in #999999 both G9 reviewers said
    # the 1.00 reference would vanish in grayscale print.
    figstyle.add_par_line(ax_a, variant=("par_dark" if short else "par"))
    figstyle.add_breach_shading(ax_a, breach_start, breach_end)
    # S-13: panel (a) carries no event labels in the short rendering. A and B are 71
    # minutes apart on a nine-day axis, so their tick labels collide and merge into an
    # unreadable "B,A"; panel (b) is the 11 March zoom where they separate. Both G9
    # reviewers reached the same conclusion about panel (a) unprompted.
    if not short:
        figstyle.add_event_markers(ax_a, [("A", a_dt), ("B", b_dt)])
        _merge_close_event_labels(fig, ax_a)

    ax_a.xaxis.set_major_locator(mdates.DayLocator())
    ax_a.xaxis.set_major_formatter(mticker.FuncFormatter(_day_month_formatter))
    ax_a.set_xlabel("date (UTC), 2023")
    ax_a.set_ylabel("price (USD)")
    figstyle.set_price_axis(ax_a, ylim, n_ticks=_EVENT_N_TICKS)
    figstyle.add_panel_label(ax_a, "a")

    # ---- panel (b) ----
    for coin in figstyle.COIN_ORDER:
        x, y = per_coin_b[coin]
        figstyle.plot_coin_series(ax_b, x, {coin: y})

    close_style = figstyle.coin_feed_style("USDC", "close")
    close_every = close_style.pop("marker_every_hours")
    (kraken_close_line,) = ax_b.plot(kraken_close_x, kraken_close, markevery=close_every,
                                      **close_style)

    vwap_style = figstyle.coin_feed_style("USDC", "vwap")
    vwap_every = vwap_style.pop("marker_every_hours")
    (kraken_vwap_line,) = ax_b.plot(kraken_vwap_x, kraken_vwap, markevery=vwap_every,
                                     **vwap_style)

    if short:
        # The defect this convention fixes, asserted so it cannot come back: no
        # sub-floor Kraken close may be drawn before the disclosure created the floor.
        disclosure = _iso_to_dt(timing["reference_points_utc"]["disclosure"])
        sub_floor_x = [x for x, y in zip(kraken_close_x, kraken_close) if y < floor]
        assert sub_floor_x, "no sub-floor Kraken close found; the panel data changed"
        assert min(sub_floor_x) >= disclosure, (
            f"a sub-floor Kraken close is drawn at {min(sub_floor_x)}, before the "
            f"{disclosure} disclosure"
        )
        assert min(sub_floor_x) == _iso_to_dt("2023-03-11T04:00:00+00:00"), (
            f"first sub-floor close drawn at {min(sub_floor_x)}, expected 04:00 UTC"
        )

    b_start = pd.Timestamp("2023-03-11T00:00:00Z").to_pydatetime()
    b_end = pd.Timestamp("2023-03-12T00:00:00Z").to_pydatetime()
    ax_b.set_xlim(b_start, b_end)

    if short:
        figstyle.add_floor_segment(ax_b, floor, live_start, live_end)
    else:
        figstyle.add_floor_line(ax_b, floor)
    figstyle.add_par_line(ax_b, variant=("par_dark" if short else "par"))

    # Per-hour breach shading of the COMPOSITE USDC firing hours (not one
    # span). Each firing row shades from its own hour to the NEXT row's hour
    # (or +1h for the last row); a non-firing row (e.g. 08:57, whose coverage-
    # gap check confirms it is a genuine recovery hour, not a data gap) is
    # simply never the start of a shaded span, which is what leaves the
    # visible unshaded gap -- no special-casing of that hour.
    usdc_rows_b = panel_b_src["panel"]["composite_USDC"]
    shaded = []
    for i, row in enumerate(usdc_rows_b):
        if not row["firing"]:
            continue
        start = _iso_to_dt(row["hour_utc"])
        if i + 1 < len(usdc_rows_b):
            end = _iso_to_dt(usdc_rows_b[i + 1]["hour_utc"])
        else:
            end = start + timedelta(hours=1)
        figstyle.add_breach_shading(ax_b, start, end)
        shaded.append((start, end))

    # What the shading must be, asserted rather than eyeballed. The nine breaching
    # readings form TWO blocks, not one: the 08:57 reading recovers above the floor, so
    # it is never the start of a span and leaves a visible gap. A change to the panel
    # data that merged or split those blocks would otherwise pass silently.
    assert len(shaded) == 9, f"expected 9 breaching composite readings, got {len(shaded)}"
    blocks = []
    for start, end in shaded:
        if blocks and start == blocks[-1][1]:
            blocks[-1][1] = end
        else:
            blocks.append([start, end])
    assert len(blocks) == 2, (
        f"expected 2 contiguous shaded blocks, got {len(blocks)}: "
        f"{[(b[0].isoformat(), b[1].isoformat()) for b in blocks]}"
    )
    assert blocks[0][0] == _iso_to_dt("2023-03-11T06:59:51+00:00"), blocks[0][0]
    assert blocks[0][1] == _iso_to_dt("2023-03-11T08:57:55+00:00"), blocks[0][1]
    assert blocks[1][0] == _iso_to_dt("2023-03-11T10:00:11+00:00"), blocks[1][0]

    # S-13 extended: the short rendering carries NO event letters in either panel. A and
    # B are in-axes text, which the house style excludes; the two timestamps they marked
    # are stated in the citing prose instead.
    if not short:
        figstyle.add_event_markers(ax_b, [("A", a_dt), ("B", b_dt)])

    ax_b.xaxis.set_major_locator(mdates.HourLocator(interval=3))
    ax_b.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax_b.set_xlabel("hour (UTC), 11 March 2023")
    ax_b.set_ylabel("price (USD)")
    figstyle.set_price_axis(ax_b, ylim, n_ticks=_EVENT_N_TICKS)
    figstyle.add_panel_label(ax_b, "b")

    # ---- shared legend ----
    # Regular rendering (frozen): the 3 coin series only.
    #
    # Short rendering: every drawn series is identified. The previous "self-evident
    # without a legend entry" argument for omitting Kraken's close and VWAP does not
    # survive a cold read -- a reader who cannot name a series cannot tell a real
    # sub-floor print from an artifact, and Kraken's close does dip below the floor at
    # 03:00, four hours before the composite's own breach window opens. Floor and par
    # stay out of the legend in both renderings (caption-only convention, matching every
    # reference line in this style system).
    if short:
        coin_handles, coin_labels = ax_a.get_legend_handles_labels()
        handles = list(coin_handles) + [kraken_close_line, kraken_vwap_line]
        labels = list(coin_labels) + ["Kraken close", "Kraken VWAP"]
        assert len(handles) == 5, f"short legend must name 5 series, got {len(handles)}"
        fig.legend(handles, labels, loc="upper center", bbox_to_anchor=(0.5, 1.02),
                   ncol=5, frameon=False)
    else:
        figstyle.add_shared_legend(fig, (ax_a, ax_b))

    result = figstyle.save_figure(
        fig, "fig_event_short" if short else "fig_event",
        sources={
            "data/price_hourly.csv": ["symbol", "timestamp", "price"],
            "results/impossibility_window.json": [
                "floor_worstcase",
                "impossibility_region_usdc.first_breach_utc",
                "impossibility_region_usdc.last_breach_utc",
            ],
            "results/usdt_premium_timing.json": [
                "reference_points_utc.disclosure",
                "peaks.coinbase_usdt_usd.timestamp_utc",
            ],
            "results/panel_11mar.json": [
                "panel.composite_USDC[*].hour_utc,price,firing",
                "panel.composite_DAI[*].hour_utc,price",
                "panel.composite_USDT[*].hour_utc,price",
                "panel.kraken_usdcusd[*].hour_utc,close,vwap",
                "coverage_gap_check_08_57_utc.genuine_data_gap",
            ],
        },
    )
    return result


def _find_combo(combinations: list[dict], **match) -> dict:
    """First entry in `combinations` whose fields match every kwarg exactly.
    Raises KeyError (loudly) rather than silently returning nothing, so a
    typo'd label or a JSON schema change breaks the build instead of drawing a
    wrong row."""
    for c in combinations:
        if all(c.get(k) == v for k, v in match.items()):
            return c
    raise KeyError(f"no combination matching {match!r} in the given list")


def fig_margin_paths():
    """fig:margin_paths -- see this module's own docstring. Replaces the
    retired fig_floor_trough (D21): rather than a single static floor-vs-
    trough dot-and-line comparison per episode, this figure draws each
    episode's full margin TRAJECTORY -- price minus its own floor, in cents --
    against hours since its own disclosure/anchor timestamp, -24h to +120h.
    Every plotted value is read live from data/*.csv and results/*.json (see
    the `sources=` dict passed to figstyle.save_figure below)."""
    price = pd.read_csv(DATA_DIR / "price_hourly.csv")
    timing = json.loads((RESULTS_DIR / "usdt_premium_timing.json").read_text(encoding="utf-8"))
    imposs = json.loads((RESULTS_DIR / "impossibility_window.json").read_text(encoding="utf-8"))
    spec = json.loads((RESULTS_DIR / "specificity_panel.json").read_text(encoding="utf-8"))
    terra_result = json.loads((RESULTS_DIR / "second_event_terra.json").read_text(encoding="utf-8"))

    WINDOW_LO_H, WINDOW_HI_H = -24.0, 120.0

    # ---- Episode 1: USDC, March 2023 -- composite series, same read/filter
    # pattern as fig_event(). ----
    usdc = price.loc[price["symbol"] == "USDC"].copy()
    anchor_usdc_s = _iso_to_dt(timing["reference_points_utc"]["disclosure"]).timestamp()
    usdc["hours"] = (usdc["timestamp"] - anchor_usdc_s) / 3600.0
    usdc = usdc[(usdc["hours"] >= WINDOW_LO_H) & (usdc["hours"] <= WINDOW_HI_H)].sort_values("hours")

    floor_usdc = 1 - constants.PHI_SVB
    assert abs(imposs["floor_worstcase"] - floor_usdc) < 1e-9, (
        f"floor_worstcase={imposs['floor_worstcase']} != 1 - PHI_SVB={floor_usdc}"
    )
    margin_usdc = (usdc["price"] - floor_usdc) * 100.0

    # ---- Episode 2: Tether, April-May 2019 -- Kraken USDT/USD hourly CLOSE.
    # Deliberately NOT `low` (Table 3's headline single-point-trough
    # convention): `low` is a per-hour EXTREME wick, right for a worst-print
    # comparison but a noisy, spiky path. This figure's job is showing
    # trajectory SHAPE, not re-asserting a specific trough number, so `close`
    # is the deliberately smoother, representative series. This is a genuine,
    # documented divergence from Table 3's convention -- do not "fix" it back
    # to `low` without understanding why. ----
    tether = pd.read_csv(DATA_DIR / "n1" / "kraken_usdtusd_2019" / "hourly.csv")
    tether_ts_s = pd.to_datetime(tether["hour_utc"], utc=True).astype("int64") // 10**9
    tether["hours"] = (tether_ts_s - constants.TETHER_2019_DISCLOSURE_S) / 3600.0
    tether = tether[(tether["hours"] >= WINDOW_LO_H) & (tether["hours"] <= WINDOW_HI_H)].sort_values("hours")

    row2 = spec["row2_tether_2019"]
    combo_ceiling = _find_combo(row2["combinations"], phi_label="phi_900M_credit_line_ceiling")
    combo_drawn = _find_combo(row2["combinations"], phi_label="phi_700M_amount_already_drawn")
    floor2_lo, floor2_hi = combo_ceiling["floor"], combo_drawn["floor"]
    floor2_mid = (floor2_lo + floor2_hi) / 2.0
    margin_tether = (tether["close"] - floor2_mid) * 100.0
    band_halfwidth = ((floor2_hi - floor2_lo) / 2.0) * 100.0
    band_lo = margin_tether - band_halfwidth
    band_hi = margin_tether + band_halfwidth

    # ---- Episode 3: Terra, May 2022 -- composite USDT leg (NOT USDC; D21 is
    # explicit this control is USDT-denominated), phi=0. Terra has NO actual
    # disclosure event (phi=0 BY CONSTRUCTION -- see second_event_terra.json's
    # own degenerate_caveat), so this series' x-axis origin reuses the
    # existing canonical TERRA_WINDOW_START_S window-start constant as its
    # "hours since X" anchor, rather than inventing a disclosure timestamp
    # that does not exist -- a deliberate, documented substitution specific to
    # this one degenerate control, not an oversight. ----
    terra_price = pd.read_csv(DATA_DIR / "terra_price_hourly.csv")
    terra_usdt = terra_price.loc[terra_price["symbol"] == "USDT"].copy()
    terra_usdt["hours"] = (terra_usdt["timestamp"] - constants.TERRA_WINDOW_START_S) / 3600.0
    terra_usdt = terra_usdt[
        (terra_usdt["hours"] >= WINDOW_LO_H) & (terra_usdt["hours"] <= WINDOW_HI_H)
    ].sort_values("hours")
    # DATA COVERAGE GAP (confirmed, not a bug to fix): the frozen USDT rows
    # start at ~TERRA_WINDOW_START_S itself (~t=+0.03h), not -24h -- there is
    # no real data for this episode's -24h..~0h portion. Nothing is
    # extrapolated or fabricated to fill it; the line simply starts wherever
    # the real data starts, which is the honest rendering of a real coverage
    # constraint, not a rendering error.

    floor_terra = terra_result["floor_worstcase"]
    assert floor_terra == 1.0, f"floor_worstcase={floor_terra} != 1.0 (Terra is phi=0, floor=par)"
    margin_terra = (terra_usdt["price"] - floor_terra) * 100.0

    # ================================================================== y-range
    all_values = (list(margin_usdc) + list(margin_tether) + list(band_lo) + list(band_hi)
                  + list(margin_terra) + [0.0])
    ylim = figstyle.compute_price_ylim(all_values, tick_step=5.0, margin_ticks=1)

    # ================================================================== figure
    figstyle.apply_rcparams()
    fig, ax = figstyle.new_half_height()

    # Zero on the y-axis IS the floor by construction (margin = price - own
    # floor); reuse the floor reference-line style (REFERENCE_STYLE['floor']
    # via add_floor_line) at value=0.0 -- NOT add_par_line, which draws at
    # price=$1.00, the wrong axis here. D21: only ONE reference line on this
    # figure (zero/floor); no separate par-minus-floor line is drawn.
    figstyle.add_floor_line(ax, 0.0)

    # Tether's floor uncertainty band -- translated into margin space around
    # its own line (not a static band around y=0), drawn BELOW the coin lines
    # (zorder 2, under plot_coin_series's zorder=3) so every line stays crisp
    # on top. No edge, no label: pure graphics, D22-compliant (no in-axes
    # text). Does not collide with the USDC or Terra lines -- the band sits at
    # ~22-34 cents while USDC ranges -4 to +9 cents and Terra -1 to +1 cent.
    ax.fill_between(tether["hours"], band_lo, band_hi, color=figstyle.PALETTE["dai"],
                     alpha=0.15, linewidth=0, zorder=2)

    # Episode identity is carried by the three EXISTING COIN_STYLE roles
    # directly, not a new figure-specific style: this figure has three
    # EPISODES but only two distinct COINS (USDC once; USDT twice, as
    # Tether-2019 and Terra-2022). USDC-2023 -> COIN_STYLE['USDC'] (solid,
    # genuinely the same coin). Tether-2019 -> COIN_STYLE['DAI'] (dashed,
    # vermillion) -- REPURPOSED for episode identity; this is NOT literally
    # DAI, it is Tether/USDT 2019, borrowing DAI's dash+colour role so the
    # figure needs only the 3 existing hues rather than inventing a 4th
    # (figstyle's own "two figures using three hues ... read as one system"
    # principle). Terra-2022 -> COIN_STYLE['USDT'] (dotted, green) --
    # coincidentally ALSO accurate, since Terra's series genuinely is USDT.
    ax.plot(usdc["hours"], margin_usdc, zorder=3, **figstyle.COIN_STYLE["USDC"])
    ax.plot(tether["hours"], margin_tether, zorder=3, **figstyle.COIN_STYLE["DAI"])
    ax.plot(terra_usdt["hours"], margin_terra, zorder=3, **figstyle.COIN_STYLE["USDT"])

    ax.set_xlim(WINDOW_LO_H, WINDOW_HI_H)
    ax.xaxis.set_major_locator(mticker.FixedLocator([-24, 0, 24, 48, 72, 96, 120]))
    ax.xaxis.set_major_formatter(mticker.FormatStrFormatter("%.0f"))
    ax.set_xlabel("hours since disclosure")

    ax.set_ylim(*ylim)
    ax.yaxis.set_major_locator(mticker.MaxNLocator(nbins=5, steps=[1, 2, 5]))
    ax.yaxis.set_major_formatter(mticker.FormatStrFormatter("%.0f"))
    ax.set_ylabel("margin (cents)")

    result = figstyle.save_figure(
        fig, "fig_margin_paths",
        sources={
            "data/price_hourly.csv": ["symbol", "timestamp", "price"],
            "data/n1/kraken_usdtusd_2019/hourly.csv": ["hour_utc", "close"],
            "data/terra_price_hourly.csv": ["symbol", "timestamp", "price"],
            "results/usdt_premium_timing.json": ["reference_points_utc.disclosure"],
            "results/impossibility_window.json": ["floor_worstcase"],
            "results/specificity_panel.json": [
                "row2_tether_2019.combinations[phi_label=phi_900M_credit_line_ceiling].floor",
                "row2_tether_2019.combinations[phi_label=phi_700M_amount_already_drawn].floor",
            ],
            "results/second_event_terra.json": ["floor_worstcase"],
        },
    )
    return result



def fig_event_short():
    """The short paper's rendering of fig_event. See fig_event's `short` parameter."""
    return fig_event(short=True)


# Feeds drawn by fig_share_vs_phi, and the results key each trough is read from. The
# order is figstyle.FEED_CURVE_ORDER; the JSON key is recorded beside each so a reviewer
# can trace every curve to a frozen value without reading the plotting code.
# Kraken's low is NOT drawn. Its minimum print is a single trade, and the manuscript
# explicitly declines to read the cross-check off that convention; drawing it here put a
# curve on the page that the text disowns, overlapping the composite for most of the
# range. The three curves left are the three the paper actually quotes.
SHARE_VS_PHI_FEEDS = (
    ("composite", "composite"),
    ("kraken_close", "kraken_usdc_close"),
    ("kraken_vwap", "kraken_usdc_vwap"),
)
PHI_AXIS_LO, PHI_AXIS_HI = 0.06, 0.14


def fig_share_vs_phi():
    r"""fig:share_vs_phi -- the lower-edge non-fundamental share as a function of the
    disclosed impairment, one curve per price feed.

    Plots $s_{\mathrm{nf}}(0) = 1 - \phi/(1-P_{\mathrm{obs}})$ over
    $\phi \in [0.06, 0.14]$. Every trough is read live from
    results/trough_corroboration.json, key
    firing_report.series.<feed>.min_print -- nothing is hard-coded.

    WHAT THE FIGURE IS FOR. Each curve crosses zero at its own $1-P_{\mathrm{obs}}$,
    which is the largest impairment that feed's trough could still be rationalized by.
    For the composite that crossing is 0.1233, and that number IS the manuscript's
    "unrationalizable for any impairment below 12.3%". The figure therefore makes the
    paper's central robustness claim visible rather than asserted, and shows the 35.1%
    and 19.2% edges quoted in the abstract and Section 7 as the heights of two curves
    at the disclosed phi.

    LINE-STYLE MAP (figstyle.FEED_CURVE_STYLE): composite solid, Kraken close dashed,
    Kraken VWAP dotted, Kraken low dash-dot. All four take PALETTE["usdc"] because all
    four ARE USDC readings, so the curves separate on DASH PATTERN alone and the figure
    carries no information in colour at all. This is a different map from FEED_STYLE,
    which modifies a coin's time series; see figstyle's note on why it is not called
    FEED_STYLES.

    THE PHI RULE IS NOT AN EVENT MARKER. figstyle.EVENT_LETTERS assigns A-D to dated
    events, and "A" already means Circle's disclosure in fig_event -- in this same
    paper. Re-using A here for a parameter value would make one letter mean two things
    across two figures, so the disclosed-phi rule is identified in the outside-axes
    legend instead, which the brief permits and which keeps the axes clear of text.
    """
    corr = json.loads((RESULTS_DIR / "trough_corroboration.json").read_text(encoding="utf-8"))
    series = corr["firing_report"]["series"]

    troughs = {}
    for style_key, json_key in SHARE_VS_PHI_FEEDS:
        troughs[style_key] = float(series[json_key]["min_print"])

    phi_disclosed = constants.PHI_SVB
    assert PHI_AXIS_LO < phi_disclosed < PHI_AXIS_HI, (
        f"disclosed phi {phi_disclosed} is outside the plotted range"
    )

    phi = np.linspace(PHI_AXIS_LO, PHI_AXIS_HI, 801)

    # H2 T8 rung (i): 5.00 -> 4.50 cm. The y-label stays two lines (see below); at
    # 4.50 cm the rotated single-line version is clipped harder, not less.
    fig, ax = figstyle.new_full_single(height_in=4.5 / 2.54)
    # Reserve a top band for the figure-level legend, the same way fig_event does.
    # add_shared_legend anchors at figure-fraction y=1.02, and constrained layout does
    # not reserve room for it, so without this the legend lands on top of the y-axis
    # label. Only the outer rect moves; the figure's own 12.2 x 5.0cm page is untouched.
    # Five entries at ncol=2 is THREE legend rows, and three rows do not fit above a
    # 4.5 cm canvas however much rect is lowered: the legend is anchored at the FIGURE
    # top and grows downward, so extra rows reach further into the axes rather than
    # being pushed clear. ncol=3 makes it two rows, which is what actually fixes it; the
    # band label is shortened so the longest entry still fits a one-third column: at
    # ncol=3 each column is about 4.07 cm, of which roughly 3.4 cm is text, and the full
    # phrase "all cash outside BNY Mellon impaired" overran it and clipped at both ends
    # of the legend. The caption carries the word "impaired".
    fig.get_layout_engine().set(rect=(0, 0, 1, 0.72))

    # Zero rule: the share is only defined as a bound where it is positive, so the
    # crossing is the reading that matters. Drawn as a reference line, beneath the data.
    ax.axhline(0.0, **{k: v for k, v in figstyle.REFERENCE_STYLE["par"].items()
                        if k != "zorder"}, zorder=1.0)

    # The disclosed-phi rule, in the event-marker line style but identified in the
    # legend rather than by a letter (see the docstring).
    phi_rule_handle = ax.axvline(
        phi_disclosed, label=r"disclosed $\phi$",
        **{k: v for k, v in figstyle.EVENT_STYLE.items() if k != "zorder"},
        zorder=0.8)

    # The worst case the disclosure itself admits: every cash deposit outside the G-SIB
    # custodian impaired. Drawn as a span because the two denominators the disclosure
    # supports give two different phi, and the estimator's verdict differs across that
    # interval. Read from the results JSON, not written here.
    cash_legs = json.loads(
        (RESULTS_DIR / "phi_cash_leg_scenarios.json").read_text(encoding="utf-8"))
    oc_lo = cash_legs["for_print"]["outside_custodian_phi_reserve_composition"]
    oc_hi = cash_legs["for_print"]["outside_custodian_phi_headline_40B"]
    assert oc_lo < oc_hi, f"outside-custodian phi span is inverted: {oc_lo} {oc_hi}"
    span_handle = ax.axvspan(oc_lo, oc_hi, label="all cash outside BNY Mellon",
                             **figstyle.SCENARIO_SPAN)

    curve_handles = {}
    crossings = {}
    for style_key in figstyle.FEED_CURVE_ORDER:
        if style_key not in troughs:
            continue
        p_obs = troughs[style_key]
        crossing = 1.0 - p_obs
        crossings[style_key] = crossing
        # The share is only a bound where it is positive: below the crossing the
        # estimator returns no verdict, and a negative "share" is not a reading of
        # anything. Each curve therefore STOPS at its own crossing rather than being
        # drawn into a region where it means nothing.
        drawn = phi[phi <= crossing]
        share_pct = 100.0 * (1.0 - drawn / (1.0 - p_obs))
        style = dict(figstyle.FEED_CURVE_STYLE[style_key])
        # Sparse markers: five per curve regardless of how far the curve runs before its
        # own crossing, so a short curve does not end up more densely marked than a long
        # one. markevery is an index stride over an 801-point grid, not a phi step.
        if "marker" in style:
            style["markevery"] = max(1, len(drawn) // 5)
        (line,) = ax.plot(drawn, share_pct, zorder=3,
                          label=figstyle.FEED_CURVE_LABEL[style_key], **style)
        curve_handles[style_key] = line

    # Crossings and the values at the disclosed phi, asserted against the frozen troughs.
    expected_crossings = {"composite": 0.1233, "kraken_vwap": 0.1071, "kraken_close": 0.0990}
    for key, want in expected_crossings.items():
        assert abs(crossings[key] - want) < 5e-5, (
            f"{key} crossing {crossings[key]:.6f} != {want}"
        )
    expected_at_disclosed = {"composite": 35.13, "kraken_vwap": 25.33, "kraken_close": 19.19}
    for key, want in expected_at_disclosed.items():
        got = 100.0 * (1.0 - phi_disclosed / (1.0 - troughs[key]))
        assert abs(got - want) < 5e-3, f"{key} at phi=0.08 is {got:.4f}%, expected {want}%"

    ax.set_xlim(PHI_AXIS_LO, PHI_AXIS_HI)
    ax.xaxis.set_major_locator(mticker.FixedLocator([0.06, 0.08, 0.10, 0.12, 0.14]))
    ax.xaxis.set_major_formatter(mticker.FormatStrFormatter("%.2f"))
    ax.set_xlabel(r"impairment fraction $\phi$")

    # Percent, matching the caption and the percentages the text quotes. The axis starts
    # at 0 because no curve is drawn below its crossing any more, so there is nothing
    # negative to show.
    ax.set_ylim(0.0, 60.0)
    ax.yaxis.set_major_locator(mticker.MaxNLocator(nbins=5, steps=[1, 2, 5]))
    ax.yaxis.set_major_formatter(mticker.FormatStrFormatter("%.0f"))
    # THREE lines, not two. The label is rotated, so its length is consumed from the
    # figure's HEIGHT: at 4.5cm anything longer than about 21 characters on a line is
    # clipped at both ends. Adding "(percent)" to the old two-line wording pushed the
    # first line to 26 characters and clipped it, which is why it gets its own line.
    # Two lines, not three. Rotated, this label's vertical extent is its longest line,
    # and a three-line version reached above the axes into the legend's bottom row.
    # "lower-edge" moves to the caption, which has room for it.
    ax.set_ylabel("non-fundamental\nshare (percent)")

    # ncol=2, so five entries wrap to three rows. The scenario entry is the longest
    # label in the figure set; at ncol=3 its column is about 4cm wide and the label runs
    # past the frame, clipping both it and the last feed entry.
    # Explicit legend order: the three curves in the order a reader meets them in the
    # text (composite first, then the two Kraken conventions), then the two reference
    # marks. Matplotlib's own order is creation order, which would put the shaded band
    # first -- ahead of every curve it is meant to annotate.
    ordered = [curve_handles["composite"], curve_handles["kraken_vwap"],
               curve_handles["kraken_close"], phi_rule_handle, span_handle]
    # Shorter handles and tighter spacing than the rcParam default. The legend is
    # centred on the figure, so its width sets where its LEFT edge falls: at the default
    # handle length the left column's handles reached back over the rotated y-axis
    # label. Narrowing the entries moves that edge right without moving the legend.
    fig.legend(ordered, [h.get_label() for h in ordered],
               loc="upper center", bbox_to_anchor=(0.5, 1.02), ncol=2, frameon=False,
               handlelength=1.1, handletextpad=0.4, columnspacing=1.2)

    result = figstyle.save_figure(
        fig, "fig_share_vs_phi",
        sources={
            "results/trough_corroboration.json": [
                f"firing_report.series.{json_key}.min_print"
                for _, json_key in SHARE_VS_PHI_FEEDS
            ],
        },
    )
    return result


def main():
    """Regenerate every figure this module defines. Each fig_* function is
    responsible for its own save_figure() call and its own sources sidecar.
    Function list is maintained here (not by each builder editing main()
    concurrently) to avoid a multi-writer collision on this one function."""
    FIGS_DIR.mkdir(parents=True, exist_ok=True)
    produced = []
    # fig_event is rendered twice: the regular paper's, and a short-paper
    # rendering with S-13's legibility fixes. Same builder, same frozen data.
    for name in ("fig_event", "fig_event_short", "fig_margin_paths",
                 "fig_share_vs_phi"):
        fn = globals().get(name)
        if fn is None:
            print(f"WARNING: {name} not yet defined in figures.py -- skipped")
            continue
        result = fn()
        produced.append((name, result))
        print(f"{name}: {result}")
    print(f"Wrote {len(produced)} figure(s) to {FIGS_DIR}")
    return produced


if __name__ == "__main__":
    main()
