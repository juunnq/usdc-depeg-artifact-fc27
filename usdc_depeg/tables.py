r"""Table fragments for the paper, TABLE BUILDER GROUP 1
(T1: tab_sources, T3: tab_placebo). T4, tab_usdt_premium, was retired in G6.

No table-writing convention existed yet in report.py (grepped first, report.py only
writes results/*.json and results/summary.md, nothing under paper/fc27/tables/), so this
is a new standalone module, matching the pattern TABLE BUILDER GROUP 2 already used for
tab_hhi.tex / tab_panel.tex / tab_dai.tex (a leading `% Source: ...` comment naming every
results/data file + key, then only `\begin{tabular}...\end{tabular}`, booktabs, no
`\begin{table}`, no `\caption`, no `\label`; those stay in fc27.tex, wired in separately).

Reuses figures.TABLES_DIR (the shared style module) for the output directory rather
than re-deriving it. No network; every number is read from an already-frozen data/ file
or a results/*.json, never estimated or hardcoded independent of its source.

Run directly (`python tables.py`) to regenerate every fragment this module defines.
"""
import csv
import json
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pandas as pd

from constants import DATA_DIR, PKG_DIR, RESULTS_DIR, WINDOW_END_S, WINDOW_START_S
from figures import TABLES_DIR

N1_DIR = DATA_DIR / "n1"

_MONTHS = {"01": "Jan", "02": "Feb", "03": "Mar", "04": "Apr", "05": "May", "06": "Jun",
           "07": "Jul", "08": "Aug", "09": "Sep", "10": "Oct", "11": "Nov", "12": "Dec"}


def _csv_rows(path: Path) -> int:
    """Data-row count (header excluded), read directly, never estimated."""
    with open(path, newline="", encoding="utf-8") as f:
        return sum(1 for _ in csv.reader(f)) - 1


def _texnum(n: int) -> str:
    """Thousands-grouped integer using the manuscript's existing `{,}` convention
    (see fc27.tex:260, `16{,}779{,}844`)."""
    return f"{n:,}".replace(",", "{,}")


def _bps(x: float) -> str:
    """Round to the nearest whole bp, matching the manuscript's existing bps
    formatting (no decimals: '70 bps', '1233 bps', ...)."""
    return f"{round(x):d}"


def _dm_hm_utc(ts_iso: str, year: bool = False) -> str:
    """'2023-03-13T19:00:56Z' -> '13 Mar 19:00 UTC' (year=True -> '13 Mar 2023 19:00 UTC').
    Width pass: single compact timestamp convention for every table cell --
    day-month-time-zone in that order, matching the required format
    ('11 Mar 02:00 UTC'). Replaces the old _hm_utc / _hm_utc_word pair (two different
    orderings, one of them spelling 'UTC' and one not) so every table reads the same way."""
    date_part, time_part = ts_iso.replace("Z", "").split("T")
    y, m, d = date_part.split("-")
    hh, mm = time_part.split(":")[:2]
    out = f"{int(d)} {_MONTHS[m]}"
    if year:
        out += f" {y}"
    return f"{out} {hh}:{mm} UTC"


def _date_parts(ts) -> tuple:
    """UTC (day, MonAbbr, year) from a unix-seconds timestamp (int or float)."""
    dt = datetime.fromtimestamp(int(ts), tz=timezone.utc)
    return dt.day, _MONTHS[f"{dt.month:02d}"], dt.year


def _cov_range(ts1, ts2) -> str:
    """Date-only Coverage cell from two unix-seconds timestamps, read live from each
    series' own file (never estimated): 'D--D Mon YYYY' within a month, 'D Mon--D Mon YYYY'
    across a month boundary. Width pass: replaces the old point-count / 'pts' / block-number
    coverage cells, which is what the reviewer's misread rows were built from."""
    d1, mo1, y1 = _date_parts(ts1)
    d2, mo2, y2 = _date_parts(ts2)
    assert y1 == y2, "coverage range crosses a year boundary -- needs a per-side year"
    if mo1 == mo2:
        return f"{d1}--{d2} {mo1} {y1}"
    return f"{d1} {mo1}--{d2} {mo2} {y1}"


def _d_only_iso(ts_iso: str) -> str:
    """'2023-03-11T12:00:00Z' -> '11 Mar 2023', the date-only counterpart of _dm_hm_utc,
    for Coverage cells that cite a single results/*.json timestamp rather than a range."""
    date_part = ts_iso.replace("Z", "").split("T")[0]
    y, m, d = date_part.split("-")
    return f"{int(d)} {_MONTHS[m]} {y}"


def _r3(x: float) -> str:
    """Fixed 3-decimal display rounding for phi/floor/q*/margin table cells, matches the
    precision the main text already uses when it quotes these same Tether-row figures.
    Source-of-truth values stay at full JSON precision in results/specificity_panel.json;
    this only trims what is DISPLAYED, the same convention _bps() applies to bps figures.

    Rounds half-UP on the exact decimal, NOT via f-string/round() on the float. A prior
    revision used f"{x:.3f}" and printed the Tether silent_margin as 0.287 when three
    decimals of 0.2875 are 0.288. The value is a decimal-exact halfway case
    (0.9551 - 0.6676 = 0.2875), but 0.2875 has no binary representation and the nearest
    double sits just BELOW it, so the tie rule is never reached and the value rounds down.
    Decimal(repr(x)) recovers the intended decimal; Decimal(x) would NOT, it takes the
    exact binary expansion (0.28749999...) and reproduces the same wrong answer."""
    return str(Decimal(repr(x)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))


SHORT_TABLES_DIR = TABLES_DIR.parent.parent / "fc27short" / "tables"

# The full paper is no longer tracked or shipped; only the short paper is submitted.
# Where paper/fc27/ is absent, its fragments are not regenerated -- and, importantly,
# not RE-CREATED: _write() below does mkdir(parents=True), so running the regular
# writers in a tree without the full paper would silently rebuild paper/fc27/tables/
# and put it back into the artifact this removal exists to keep it out of.
REGULAR_PAPER_PRESENT = TABLES_DIR.parent.exists()
# The short paper used to receive byte-identical mirrors of two regular fragments. G10's
# cold reviews broke that: the short needs a third "no verdict" state in the panel's Fires
# column, wider Episode widths, and the placebo's Coinbase row ahead of the composite echo
# -- and the regular version is frozen and may not change. So the short now gets its OWN
# fragments, written by the same generators under `short=True`, from the same results
# files. One generator, two renderings: the numbers cannot drift apart, and the regular
# fragments stay byte-identical.
SHORT_TABLE_STEMS: set[str] = set()


def _write(stem: str, body: str) -> Path:
    # Never CREATE the full paper's tree. mkdir(parents=True) here used to resurrect
    # paper/fc27/tables/ in any checkout that did not have the full paper -- including,
    # fatally, inside the artifact export, whose reproduction run calls these writers
    # and whose whole point is not to ship that paper. Writing into it when it exists is
    # fine; conjuring it is not.
    if not TABLES_DIR.parent.exists():
        raise FileNotFoundError(
            f"{TABLES_DIR.parent} is absent: the full paper is not in this tree, so its "
            f"fragments are not generated here. Guard the caller with "
            f"tables.REGULAR_PAPER_PRESENT.")
    TABLES_DIR.mkdir(parents=True, exist_ok=True)
    out = TABLES_DIR / f"{stem}.tex"
    out.write_text(body, encoding="utf-8")
    if stem in SHORT_TABLE_STEMS:
        SHORT_TABLES_DIR.mkdir(parents=True, exist_ok=True)
        (SHORT_TABLES_DIR / f"{stem}.tex").write_text(body, encoding="utf-8")
    return out


def _write_short(stem: str, body: str) -> Path:
    """A fragment that exists only in the short paper."""
    SHORT_TABLES_DIR.mkdir(parents=True, exist_ok=True)
    out = SHORT_TABLES_DIR / f"{stem}.tex"
    out.write_text(body, encoding="utf-8")
    return out


# ======================================================================================
# T1, tab_sources.tex: the frozen-snapshot table (fc27.tex tab:data), extended with
# the N1 series and a Rows column. Every count below is read from the file itself (or,
# where the file is a JSON list of records rather than a CSV, from len() of that list) --
# none is copied from prose or estimated.
# ======================================================================================

def write_tab_sources() -> Path:
    """T1, tab_sources.tex: the frozen-snapshot table (fc27.tex tab:data).

    Width pass 2: a reviewer found cells whose content, wrapped across two lines by a
    narrow p{} column, read as leaking into the next column when skimmed or extracted
    (e.g. Source "DeFiLlama coins API" wrapping into what looked like the next row's
    Rows value). Fix is content-only, no column-width change: Series names shortened,
    Source cut to one or two words, and Coverage cut to a date-only range so every cell
    fits on one line. Coverage ranges are read live from each series' own timestamp
    column (never estimated), except: Redemptions, which has no timestamp column of its
    own, uses constants.WINDOW_START_S/WINDOW_END_S -- exactly the bounds
    redemptions.py's own block_by_time() calls pass to derive the block range this cell
    used to show; DAI backing, which reads run_date_utc from
    results/number4_dai_contagion.json (already the table's own source for that row);
    and the two GitHub label snapshots, which carry no retrieval date anywhere in the
    frozen record, so their Coverage cell is "--" rather than a fabricated date.
    """
    # --- original 9-file snapshot (data_fetch.py), row count + date range both read
    # directly from each file's own column --------------------------------------
    df_price = pd.read_csv(DATA_DIR / "price_hourly.csv")
    n_price = len(df_price)                                            # 639 = 212+213+214
    df_binance = pd.read_csv(DATA_DIR / "binance_usdcusdt_1h.csv")
    n_binance = len(df_binance)                                        # 131
    df_supply = pd.read_csv(DATA_DIR / "usdc_supply_daily.csv")
    n_supply = len(df_supply)                                          # 12
    n_redemptions = _csv_rows(DATA_DIR / "redemptions_by_wallet.csv")  # 776
    df_terra = pd.read_csv(DATA_DIR / "terra_price_hourly.csv")
    n_terra = len(df_terra)                                            # 384 = 192*2
    df_usdt = pd.read_csv(DATA_DIR / "usdt_usd_hourly.csv")
    n_usdt = len(df_usdt)                                              # 217

    with open(RESULTS_DIR / "number4_dai_contagion.json", encoding="utf-8") as f:
        dai_run_date_utc = json.load(f)["run_date_utc"]

    # --- N1 series (data_fetch_n1.py) ---------------------------------------------
    n_kraken_usdc = _csv_rows(N1_DIR / "kraken_usdcusd" / "hourly.csv")
    n_kraken_usdt = _csv_rows(N1_DIR / "kraken_usdtusd" / "hourly.csv")
    n_kraken_usdt_2019 = _csv_rows(N1_DIR / "kraken_usdtusd_2019" / "hourly.csv")
    n_block_anchors = _csv_rows(N1_DIR / "onchain" / "hourly_block_anchors.csv")
    n_usdp_supply = _csv_rows(N1_DIR / "paxos2023" / "usdp_supply_daily.csv")

    with open(N1_DIR / "tether2019" / "disclosure_passages_nyag.json", encoding="utf-8") as f:
        n_nyag_passages = len(json.load(f)["passages"])
    with open(N1_DIR / "paxos2023" / "disclosure_passages_paxos.json", encoding="utf-8") as f:
        n_paxos_passages = len(json.load(f))

    n_dawsbot = len(pd.read_csv(N1_DIR / "labels" / "dawsbot_eth-labels" / "accounts.csv"))
    with open(N1_DIR / "labels" / "brianleect_etherscan-labels" / "combinedAccountLabels.json",
              encoding="utf-8") as f:
        n_brianleect = len(json.load(f))

    # Each tuple is (Series, Source, Rows, Coverage).
    original_rows = [
        ("USDC/USDT/DAI price", "DeFiLlama", n_price,
         _cov_range(df_price["timestamp"].min(), df_price["timestamp"].max())),
        ("USDC/USDT cross", "Binance", n_binance,
         _cov_range(df_binance["open_time"].min() // 1000, df_binance["open_time"].max() // 1000)),
        ("USDC supply", "DeFiLlama", n_supply,
         _cov_range(df_supply["date"].min(), df_supply["date"].max())),
        ("Redemptions", "Etherscan", _texnum(n_redemptions),
         _cov_range(WINDOW_START_S, WINDOW_END_S)),
        ("DAI backing", "Etherscan", "1", _d_only_iso(dai_run_date_utc)),
        ("USDT/USD price+vol", "Coinbase", n_usdt,
         _cov_range(df_usdt["open_time"].min(), df_usdt["open_time"].max())),
        ("Terra price", "DeFiLlama", n_terra,
         _cov_range(df_terra["timestamp"].min(), df_terra["timestamp"].max())),
    ]
    n1_rows = [
        ("Kraken USDC/USD", "Kraken", n_kraken_usdc, "8--17 Mar 2023"),
        ("Kraken USDT/USD", "Kraken", n_kraken_usdt, "8--17 Mar 2023"),
        ("Kraken USDT/USD 2019", "Kraken", n_kraken_usdt_2019, "20 Apr--15 May"),
        ("NYAG press release", "NYAG", n_nyag_passages, "25 Apr 2019"),
        ("Paxos/USDP statement", "Paxos", f"{n_paxos_passages}+{n_usdp_supply}+1",
         "8--14 Mar 2023"),
        ("Labels (dawsbot)", "GitHub", _texnum(n_dawsbot), "--"),
        ("Labels (brianleect)", "GitHub", _texnum(n_brianleect), "--"),
        ("Block anchors", "Ethereum RPC", n_block_anchors, "8--17 Mar 2023"),
    ]

    lines = [
        "% Source: data/MANIFEST.json (files{} + sources[] entries) -- row counts read",
        "%   directly from each listed file, not from MANIFEST prose or estimated:",
        "%   data/price_hourly.csv, data/binance_usdcusdt_1h.csv, data/usdc_supply_daily.csv,",
        "%   data/redemptions_by_wallet.csv, data/usdt_usd_hourly.csv, data/terra_price_hourly.csv,",
        "%   data/n1/kraken_usdcusd/hourly.csv, data/n1/kraken_usdtusd/hourly.csv,",
        "%   data/n1/kraken_usdtusd_2019/hourly.csv, data/n1/onchain/hourly_block_anchors.csv,",
        "%   data/n1/tether2019/disclosure_passages_nyag.json (passages[]),",
        "%   data/n1/paxos2023/disclosure_passages_paxos.json (list),",
        "%   data/n1/paxos2023/usdp_supply_daily.csv,",
        "%   data/n1/labels/dawsbot_eth-labels/accounts.csv,",
        "%   data/n1/labels/brianleect_etherscan-labels/combinedAccountLabels.json (dict keys);",
        "%   results/number4_dai_contagion.json (run_date_utc, DAI backing row's Coverage).",
        "% Width pass 2: see write_tab_sources docstring. Coverage is date-only, read from",
        "%   each file's own timestamp column via _cov_range/_d_only_iso, except Redemptions",
        "%   (constants.WINDOW_START_S/WINDOW_END_S, matching redemptions.py's block_by_time",
        "%   calls) and the two GitHub label rows (no frozen retrieval date exists, so \"--\"",
        "%   rather than a fabricated one). Series shortened, Source cut to one or two words.",
        r"\begin{tabular}{@{}p{2.9cm}p{2.9cm}p{1.5cm}p{2.7cm}@{}}",
        r"\toprule",
        r"Series & Source & Rows & Coverage \\",
        r"\midrule",
    ]
    for series, source, rows_val, coverage in original_rows:
        lines.append(f"{series} & {source} & {rows_val} & {coverage} \\\\")
    lines.append(r"\midrule")  # visually separate the two freeze sessions (data_fetch.py / _n1.py)
    for series, source, rows_val, coverage in n1_rows:
        lines.append(f"{series} & {source} & {rows_val} & {coverage} \\\\")
    lines.append(r"\bottomrule")
    lines.append(r"\end{tabular}")
    return _write("tab_sources", "\n".join(lines) + "\n")


# ======================================================================================
# T3, tab_placebo.tex: cross-stablecoin placebo table (fc27.tex tab:placebo), with a
# Feed column added and the USDT premium row split into a composite row (unchanged
# number, now feed-labelled) and a new Coinbase row. Every USDT number is read live from
# results/usdt_premium_timing.json and results/usdt_premium.json, none hardcoded.
# ======================================================================================

def write_tab_placebo(short: bool = False) -> Path:
    with open(RESULTS_DIR / "number3_placebo.json", encoding="utf-8") as f:
        placebo = json.load(f)
    with open(RESULTS_DIR / "usdt_premium_timing.json", encoding="utf-8") as f:
        timing = json.load(f)
    with open(RESULTS_DIR / "usdt_premium.json", encoding="utf-8") as f:
        cb = json.load(f)

    threshold = placebo["break_threshold"]

    def _row(coin, exposure, feed, trough, max_premium_bps, premium_ts=None, dagger=False):
        # Plain "N bps", no +/math mode, matches the existing tab:placebo convention
        # (fc27.tex:583-585: "1233 bps", "70 bps", ... no sign, no $...$).
        depeg = _bps((1 - trough) * 10000)
        broke = "yes" if trough < threshold else "no"
        premium_cell = f"{_bps(max_premium_bps)} bps"
        if premium_ts is not None:
            premium_cell += f" ({_dm_hm_utc(premium_ts)})"
        if dagger:
            # Not raised (no "^"): a math superscript is auto-shrunk by TeX's scriptstyle
            # to well under 8pt regardless of the surrounding \small (verified empirically
            #, $^\dagger$ measures 5.98pt in the built PDF vs 8.97pt for bare $\dagger$),
            # which would breach the 8pt table-text floor. A bare dagger glyph
            # straight after the value keeps the footnote-marker convention at full size.
            premium_cell += r" $\dagger$"
        return (f"{coin} & {exposure} & {feed} & \\${trough:.3f} & {depeg} bps & "
                f"{premium_cell} & {broke} \\\\")

    usdc, dai, usdt = placebo["USDC"], placebo["DAI"], placebo["USDT"]
    composite_peak = timing["peaks"]["composite_usdt"]
    coinbase_peak = timing["peaks"]["coinbase_usdt_usd"]

    usdt_composite = _row("USDT", "none", "composite", usdt["trough"],
                           composite_peak["premium_bps"],
                           premium_ts=composite_peak["timestamp_utc"], dagger=True)
    usdt_coinbase = _row("USDT", "none", "Coinbase", cb["min_price"],
                          coinbase_peak["premium_bps"],
                          premium_ts=coinbase_peak["timestamp_utc"])
    # Row order. The regular keeps composite-then-Coinbase. The short swaps them, because
    # three of four G10 cold reviewers stumbled on it: the composite row leads with the
    # +270 bps 13 March peak, which the argument explicitly REJECTS as post-backstop and
    # disqualifies only with a dagger, while the Coinbase +266 bps 11 March print the
    # identification actually rests on sits underneath it. A reader meets the rejected
    # number first. Putting the used number first costs nothing and removes the trap.
    rows = [
        _row("USDC", "direct", "composite", usdc["trough"], usdc["max_premium_bps"]),
        _row("DAI", "contagion", "composite", dai["trough"], dai["max_premium_bps"]),
    ] + ([usdt_coinbase, usdt_composite] if short else [usdt_composite, usdt_coinbase])

    delta_h = composite_peak["relative_to"]["joint_statement_treasury_fed_fdic"]["delta_minutes"] / 60
    lines = [
        "% Source: results/number3_placebo.json (USDC.{trough,max_premium_bps},",
        "%   DAI.{trough,max_premium_bps}, USDT.trough, break_threshold);",
        "%   results/usdt_premium_timing.json (peaks.composite_usdt.{premium_bps,",
        "%   timestamp_utc, relative_to.joint_statement_treasury_fed_fdic.delta_minutes},",
        "%   peaks.coinbase_usdt_usd.{premium_bps, timestamp_utc});",
        "%   results/usdt_premium.json (min_price, Coinbase feed).",
        r"\begin{tabular}{@{}lllcccc@{}}",
        r"\toprule",
        r"Coin & Exposure & Feed & Trough & De-peg & Premium (peak, UTC) & Break? \\",
        r"\midrule",
    ] + rows + [
        r"\bottomrule",
        r"\multicolumn{7}{p{0.93\linewidth}}{$\dagger$ post-backstop: "
        f"{delta_h:.1f}" r"~h after the 2023-03-12 22:15 UTC joint Treasury/Fed/FDIC "
        r"statement.} \\",
        r"\end{tabular}",
    ]
    body = "\n".join(lines) + "\n"
    return _write_short("tab_placebo_short", body) if short else _write("tab_placebo", body)


# ======================================================================================
# T4 RETIRED (G6, cut ladder rung R1). tab_usdt_premium carried six rows, four of which
# Table 1's Coinbase row already printed (peak premium, its timestamp, the $0.999
# minimum, and that it stayed inside the break band). The two it alone carried, the
# $1.52B window volume and the $1.24B above par, are stated in Section 5's body, as is
# the Kraken +600 bps print. A whole float for two facts already in the prose could not
# be justified against a hard page limit.
# ======================================================================================
# ======================================================================================
# TABLE BUILDER GROUP 2, tab_panel, tab_hhi, tab_dai, tab2_sentence,
# share_range. These five fragments already existed on disk from an earlier QA loop (with
# no generator behind them, report.py's only table-writing path was Group 1 above), so
# each function here reproduces its fragment byte-identically rather than authoring one
# fresh. The committed fragment is the specification: every NUMBER is read live from its
# results/*.json (never hardcoded independent of the source), but the fixed connective
# prose (episode names, disclosure paraphrases, feed labels, the Paxos/Terra Category
# wording) is carried over as-is, tab_panel.tex's own leading comment says as much
# ("not a bare JSON copy" / "exact wording specified in the table conventions").
# ======================================================================================

def write_tab_panel(short: bool = False) -> Path:
    """T2, tab_panel.tex: the 3-episode specificity panel (fc27.tex tab:panel).

    Ruling D17 moved the Paxos/USDP row out of the panel entirely (to a one-sentence
    timing control in the body prose) -- the panel is now USDC 2023, Tether 2019, Terra
    2022. Its footnote letter [b] is retired with it; Terra's footnote (formerly [c])
    is relabelled [b] so the panel's remaining two marks stay sequential.

    Width pass: the prose Category column is dropped from the tabular and
    replaced by footnote marks a/b on the episode name (defined in a footnote row
    inside the tabular itself, not in fc27.tex's \\caption, this generator may only
    change what's inside \\begin{tabular}...\\end{tabular}; caption prose is out of
    scope here). Disclosure cells are trimmed to a citation + a compact UTC
    timestamp. Tether/NYAG has no bib entry for its disclosure in refs.bib (grepped,
    absent) and this generator does not own refs.bib, so that cell cites the institution
    by short name instead of \\cite{}; only the USDC row has a matching key
    (circle2023tweet) and uses it."""
    with open(RESULTS_DIR / "specificity_panel.json", encoding="utf-8") as f:
        panel = json.load(f)

    row1, row2 = panel["row1_usdc_march_2023"], panel["row2_tether_2019"]
    row4 = panel["row4_terra_2022"]

    # Row 1, USDC March 2023: the headline (phi=0.08, composite, headline print) combo.
    c1 = next(c for c in row1["combinations"]
              if c["phi_label"] == "static_phi_svb_0.08" and c["trough_label"] == "headline_print"
              and c["trough_feed"] == "composite")
    r1_trough = (f"{c1['trough_feed']}, headline: "
                 f"\\${c1['trough_price']:.3f} ({_dm_hm_utc(c1['trough_hour_utc'])})")

    # Row 2, Tether Apr-May 2019: trough convention fixed at "low", phi ranges over the
    # three counsel-numerator readings (700M/850M/900M).
    low = [c for c in row2["combinations"] if c["trough_label"] == "low"]
    trough_price_2 = low[0]["trough_price"]
    assert all(c["trough_price"] == trough_price_2 for c in low), "row2 'low' troughs disagree"
    phi_lo, phi_hi = min(c["phi"] for c in low), max(c["phi"] for c in low)
    floor_lo, floor_hi = min(c["floor"] for c in low), max(c["floor"] for c in low)
    q_lo, q_hi = min(c["q_star_rho0"] for c in low), max(c["q_star_rho0"] for c in low)
    m_lo, m_hi = min(c["silent_margin"] for c in low), max(c["silent_margin"] for c in low)

    lines = [
        "% Source: results/specificity_panel.json --",
        '%   row1_usdc_march_2023.combinations[phi=0.08, trough_label="headline_print", trough_feed="composite"]',
        '%   row2_tether_2019.{combinations[trough_label="low", phi in {0.2585,0.3139,0.3324}]}',
        "%   row4_terra_2022.USDT.{trough, silent_margin, fires}",
        "% + results/second_event_terra.json (USDT.implied_failure_prob = null -- q* undefined at phi=0; trough feed = DeFiLlama composite aggregate).",
        "% D17: row5_paxos2023_candidate (USDP Mar 2023) is no longer read here -- moved out",
        "%   of the panel to a one-sentence timing control in the body prose.",
        "% Width pass: the Category prose column is replaced by footnote marks [a]/[b]",
        "% (defined in the footnote row below, not in fc27.tex's caption -- see docstring).",
        "% Plain bracketed letters, not raised math superscripts ($^a$ etc.): a superscript",
        "% is auto-shrunk by TeX's scriptstyle well under 8pt regardless of the surrounding",
        "% \\small (verified empirically), breaching the table-text legibility floor.",
        "% phi/floor/q*/margin are displayed at 3 decimals (_r3), matching the precision the",
        "% main text already uses for this same Tether range (fc27.tex:673); full-precision",
        "% values remain in the source JSON. Tether's q*/margin range holds the trough",
        '%   convention fixed at Kraken USDT/USD "low" (the deepest single print) and varies only',
        "% phi -- narrower than the cross-convention quote of q* in [0.103, 0.174],",
        "% which additionally spans the close/vwap conventions.",
        (r"\begin{tabular}{@{}p{1.75cm} p{1.85cm} p{0.9cm} p{0.9cm} p{1.95cm} p{1.0cm} p{1.3cm} p{1.35cm}@{}}" if short else
         r"\begin{tabular}{@{}p{1.2cm} p{2.1cm} p{1.05cm} p{1.05cm} p{2.3cm} p{1.05cm} p{0.75cm} p{1.1cm}@{}}"),
        r"\toprule",
        # \star, not *, matching the body everywhere (D24(m)).
        r"Episode & Disclosure & $\phi$ & Floor & Trough (feed) & $q^\star$ & Fires & Margin \\",
        r"\midrule",
        "USDC Mar 2023 &",
        rf"  \cite{{circle2023tweet}}, {_dm_hm_utc(row1['disclosure_ts_utc'])} &",
        f"  {c1['phi']} & {c1['floor']} &",
        rf"  {r1_trough} &",
        rf"  {round(c1['q_star_rho0'], 2)} & {'Yes' if c1['fires'] else 'No'} & ${_r3(c1['silent_margin'])}$ \\",
        r"Tether 2019 [a] &",
        "  NYAG press release, 25 Apr 2019 &",
        rf"  $[{_r3(phi_lo)},\allowbreak {_r3(phi_hi)}]$ & $[{_r3(floor_lo)},\allowbreak {_r3(floor_hi)}]$ &",
        rf"  Kraken, low: \${trough_price_2:.3f} ({_dm_hm_utc(low[0]['trough_hour_utc'], year=True)}) &",
        rf"  $[{_r3(q_lo)},\allowbreak {_r3(q_hi)}]$ & {'No verdict' if short else 'No'} & $[{_r3(m_lo)},\allowbreak {_r3(m_hi)}]$ \\",
        # "USDT 2022" collided with the screened candidate Tether (2022) and disagreed
        # with the body, which called the row Terra 2022. It is the USDT leg of the May
        # 2022 Terra episode, and D24(c) names it that way here and at the three body
        # sites. Fires stays Yes, with "not counted" next to it rather than only in the
        # footnote, so the degenerate case cannot be read as a second true positive.
        # "not counted" rides the Episode column, which wraps. The Fires column is a
        # narrow c and ran 10.3pt past it when the phrase sat there.
        (r"USDT, Terra episode (May 2022) [b] &" if short else
         r"USDT, Terra episode (May 2022), not counted [b] &"),
        r"  no disclosure &",
        "  0.00 & 1.00 &",
        rf"  composite: \${round(row4['USDT']['trough'], 3)} &",
        rf"  -- & {('Yes (not counted)' if short else 'Yes') if row4['USDT']['fires'] else 'No'} "
        rf"& ${round(row4['USDT']['silent_margin'], 3)}$ \\",
        r"\bottomrule",
        r"\multicolumn{8}{p{10.6cm}}{[a] regulator-quantified, issuer-contested. "
        r"[b] degenerate by construction, with $\phi=0$, and not counted as a panel "
        r"member.} \\",
        r"\end{tabular}",
    ]
    body = "\n".join(lines) + "\n"
    return _write_short("tab_panel_short", body) if short else _write("tab_panel", body)


def write_tab_hhi() -> Path:
    """T-HHI, tab_hhi.tex: redemption-concentration table (fc27.tex tab:hhi), extended
    with the entity-resolved block (the public-label join, entity_resolved key)."""
    with open(RESULTS_DIR / "number2_redemption_hhi.json", encoding="utf-8") as f:
        hhi = json.load(f)
    ub, ex, er = hhi["upper_bound"], hhi["ex_custodian"], hhi["entity_resolved"]
    ce = er["custodian_exclusion"]

    lines = [
        "% Source: results/number2_redemption_hhi.json --",
        "%   upper block (unchanged from the current manuscript tab:hhi):",
        "%     n_direct_senders; coverage.coverage_pct; upper_bound.{hhi_raw, hhi_normalized,",
        "%     top10_share, largest_share, effective_n, bootstrap.{lo,hi}}; ex_custodian.{hhi_raw,",
        "%     largest_share, effective_n}.",
        "%   entity-resolved block (entity_resolved key):",
        "%     labelled.union.{n_labelled, address_share} (coverage 2/776); externally_corroborated_share;",
        "%     own_tracing_share; no_sybil_status; entity_hhi.{hhi_raw, bootstrap.{lo,hi}};",
        "%     custodian_exclusion.all_labelled_exchange_custodian_excluded.hhi_raw;",
        "%     custodian_exclusion.coinbase_conduits_excluded_existing_figure.hhi_raw (reproduces the",
        "%     ex-custodian row above under entity resolution, for continuity -- same underlying figure,",
        "%     not a second independent number); unlabelled_tail.{label_consistent_supremum_hhi,",
        "%     label_consistent_supremum_assumption}.",
        '% CONFIRMED: the old worst-case "0.577" figure does not appear anywhere in the live JSON',
        "% (grep across usdc_depeg/results/ and paper/ returns no match) -- correctly",
        "% identifies it as false, and it is absent, not merely uncited. Not reintroduced here.",
        "% Width pass: Statistic/Value are both p{} (was p{8.4cm}r -- the old 'r' column",
        "% could not wrap and let the 'No-sybil condition' prose row set the table's true",
        "% width). Row labels trimmed to what the JSON itself supports; the longer",
        "% constructions they drop (the entity-resolved section's own label-set names, the",
        "% supremum row's 'entire unlabelled tail merged with...' clause) are unchanged in",
        "% fc27.tex's existing tab:hhi caption, so no information is lost, only de-duplicated.",
        r"\begin{tabular}{@{}p{6.5cm}p{2.6cm}@{}}",
        r"\toprule",
        r"Statistic & Value \\",
        r"\midrule",
        f"Wallets (redeemers) & {hhi['n_direct_senders']} \\\\",
        f"Coverage of gross burns & {hhi['coverage']['coverage_pct']:.1f}\\% \\\\",
        f"HHI (raw) & {round(ub['hhi_raw'], 4)} \\\\",
        f"HHI (normalized) & {round(ub['hhi_normalized'], 4)} \\\\",
        f"Top-10 share & {ub['top10_share'] * 100:.1f}\\% \\\\",
        f"Largest share & {ub['largest_share'] * 100:.1f}\\% \\\\",
        rf"Effective \# redeemers ($1/\mathrm{{HHI}}$) & {ub['effective_n']:.1f} \\",
        rf"Bootstrap 95\% CI (HHI) & $[{ub['bootstrap']['lo']:.4f},\,{ub['bootstrap']['hi']:.4f}]$ \\",
        r"\midrule",
        f"HHI, ex-custodian & {round(ex['hhi_raw'], 4)} \\\\",
        f"Largest share, ex-custodian & {ex['largest_share'] * 100:.1f}\\% \\\\",
        rf"Effective \# redeemers, ex-custodian & {ex['effective_n']:.1f} \\",
        r"\midrule",
        r"\multicolumn{2}{@{}l}{\textit{Entity-resolved (public label join)}} \\",
        f"Labelled coverage & {er['labelled']['union']['n_labelled']}/{hhi['n_direct_senders']} \\\\",
        f"Externally corroborated share & {er['externally_corroborated_share']:.2f}\\% \\\\",
        f"Own-tracing share & {er['own_tracing_share']:.2f}\\% \\\\",
        rf"Entity HHI ({er['sybil_test']['n_entities_with_2plus_addresses']} multi-address entities) & {round(er['entity_hhi']['hhi_raw'], 4)} \\",
        # G5b (audit 16.13): the entity-HHI bootstrap row is gone. With 0 multi-address
        # entities the entity partition IS the address partition, so its point estimate
        # equals the address HHI to 15 digits and a second interval over the same
        # distribution differed only by where the two draws sat in the RNG stream
        # ([0.0517, 0.4184] against [0.0521, 0.4255], same seed). Printing both invited
        # the question why one distribution has two intervals. One CI, stated once.
        f"No-sybil condition & {er['no_sybil_status'].split('(')[0].strip()} \\\\",
        rf"HHI, labelled custodians excluded & {round(ce['all_labelled_exchange_custodian_excluded']['hhi_raw'], 4)} \\",
        rf"HHI, both Coinbase conduits excluded & {round(ce['coinbase_conduits_excluded_existing_figure']['hhi_raw'], 4)} \\",
        rf"Supremum worst case & {round(er['unlabelled_tail']['label_consistent_supremum_hhi'], 4)} \\",
        r"\bottomrule",
        r"\end{tabular}",
    ]
    return _write("tab_hhi", "\n".join(lines) + "\n")


def write_tab_dai() -> Path:
    """T-DAI, tab_dai.tex: DAI's de-peg and its documented USDC exposure (fc27.tex tab:dai).

    Reports the exposure and BOTH ratios the single contemporaneous source gives; deliberately
    reports NO mechanical/panic decomposition. An adversarial pass established that the two
    ratios have different denominators and imply pass-throughs straddling the observed de-peg,
    so any residual would be an artifact of which ratio is chosen rather than a measurement --
    and that the USDC->DAI mint leg was capped out at the moment analysed, removing the
    arbitrage argument for a near-100% mechanical channel.

    Ruling D19 retracted the on-chain PSM-USDC-A balance row entirely: the query hit the
    PSM contract rather than the GemJoin adapter, and no re-query is possible, so that
    row is no longer emitted. The $3.1B reported cap and the 74%/36% shares are
    unaffected (they come from the contemporaneous source, not the on-chain read)."""
    with open(RESULTS_DIR / "number4_dai_contagion.json", encoding="utf-8") as f:
        dai = json.load(f)

    psm_share = dai["psm_share_of_dai_supply"]
    coll_share = dai["source_collateral_share"]
    supply_b = dai["dai_total_supply_usd"] / 1e9
    psm_b = dai["reported_psm_usdc"] / 1e9
    lines = [
        "% Source: results/number4_dai_contagion.json --",
        f"%   usdc_depeg_bps ({dai['usdc_depeg_bps']} -> {_bps(dai['usdc_depeg_bps'])});"
        f" observed_dai_depeg_bps ({dai['observed_dai_depeg_bps']} ->"
        f" {_bps(dai['observed_dai_depeg_bps'])});",
        f"%   dai_total_supply_usd ({dai['dai_total_supply_usd']!r} -> ${supply_b:.2f}B);"
        f" reported_psm_usdc ({dai['reported_psm_usdc']!r} -> ${psm_b:.1f}B);",
        f"%   psm_share_of_dai_supply ({psm_share!r} -> {round(psm_share * 100)}%);"
        f" source_collateral_share ({coll_share!r} -> {round(coll_share * 100)}%).",
        "%   NO decomposition row: see the no_decomposition key in the same results file.",
        "%   D19: the on_chain_psm_usdc_total row is retracted, not read here -- see",
        "%   write_tab_dai docstring.",
        r"\begin{tabular}{@{}p{6.4cm}p{4.0cm}@{}}",
        r"\toprule",
        r"Quantity & Value \\",
        r"\midrule",
        rf"USDC de-peg (trough, composite) & ${_bps(dai['usdc_depeg_bps'])}$~bps \\",
        rf"Observed DAI de-peg (trough, composite) & ${_bps(dai['observed_dai_depeg_bps'])}$~bps \\",
        rf"On-chain DAI supply, run-date block & \${supply_b:.2f}B \\",
        rf"Reported PSM-USDC-A cap, 11 Mar 2023 & \${psm_b:.1f}B \\",
        rf"\quad as a share of DAI supply & ${round(psm_share * 100)}\%$ \\",
        rf"Reported USDC share of DAI collateral & ${round(coll_share * 100)}\%$ \\",
        r"\bottomrule",
        r"\end{tabular}",
    ]
    return _write("tab_dai", "\n".join(lines) + "\n")


def write_tab2_sentence() -> Path:
    """T2-sentence, tab2_sentence.tex: the Prop. 1 rho-sweep sentence (fc27.tex, near
    tab:panel). Prose is kept formal per the documented ruling; the three
    q* values at the composite trough are read live from phi_denominator_sensitivity.json."""
    with open(RESULTS_DIR / "phi_denominator_sensitivity.json", encoding="utf-8") as f:
        sens = json.load(f)
    composite = {g["phi_label"]: g for g in sens["grid"] if g["trough_label"] == "composite_min_headline"}
    q_alt = composite["static_phi_altreserve_42.1B_0.0784"]
    q_svb = composite["static_phi_svb_0.08"]
    q_325 = composite["static_phi_3.3_over_40.0_0.0825"]

    lines = [
        "% Source: results/phi_denominator_sensitivity.json grid[] -- entries with",
        '%   trough_label="composite_min_headline" at phi_label in {static_phi_altreserve_42.1B_0.0784,',
        "%   static_phi_svb_0.08, static_phi_3.3_over_40.0_0.0825}: q_star_rho0 = 1.573 / 1.5416 / 1.4948",
        "%   respectively. The rho=1 identity is rational_bound.py's own stated pricing relation",
        "%   P_fund(q,rho) = 1 - q*phi*(1-rho) (module docstring + implied_failure_prob's denominator",
        "%   phi*(1-rho_fail), which vanishes at rho=1 for any phi>0, making P_fund identically 1 for",
        "%   every admissible q). This sentence",
        "%   supplements Proposition 1 / Corollary 1 rather than replacing their formal statements.",
        r"Under the worst-case recovery assumption $\rho=0$, the implied unremediated-loss probability at",
        r"the composite trough exceeds the admissible $[0,1]$ range at every $\phi$ in the sensitivity",
        # Two decimals, matching the 1.54 headline. The stored values carry four
        # (1.573 / 1.5416 / 1.4948), which overstates a quantity computed from a
        # two-figure phi and printed four different ways across the paper (audit 16.31).
        # \star, not *, so the symbol matches the body everywhere (D24(m)).
        rf"grid: $q^{{\star}}={q_alt['q_star_rho0']:.2f}$ at $\phi={q_alt['phi']}$, "
        rf"$q^{{\star}}={q_svb['q_star_rho0']:.2f}$ at $\phi={q_svb['phi']}$, and "
        rf"$q^{{\star}}={q_325['q_star_rho0']:.2f}$ at",
        rf"$\phi={q_325['phi']}$. At the opposite extreme, $\rho=1$ collapses the pricing relation to the identity",
        r"$P_{\mathrm{fund}}(q,1)=1-q\phi(1-1)\equiv 1$ for every $q\in[0,1]$, so full assumed recovery",
        r"makes the floor coincide with par and the bound uninformative. This is why",
        r"Proposition~\ref{prop:floor} and",
        r"Corollary~\ref{cor:fosd} are evaluated at $\rho=0$ throughout.",
    ]
    return _write("tab2_sentence", "\n".join(lines) + "\n")


def write_share_range() -> Path:
    """Share-range, share_range.tex: the two non-fundamental-share sentences (fc27.tex,
    supplements Number 1). Sentence 1 reads the feed_trough_only_at_phi_0.08 block;
    sentence 2 reads min/max across the full grid[] (3 phi conventions x 5 troughs)."""
    with open(RESULTS_DIR / "phi_denominator_sensitivity.json", encoding="utf-8") as f:
        sens = json.load(f)
    feed = sens["feed_trough_only_at_phi_0.08"]
    grid = sens["grid"]
    grid_min = min(g["s_nf0"] for g in grid)
    grid_max = max(g["s_nf0"] for g in grid)
    phis = sorted({g["phi"] for g in grid})
    second_lowest = next(t for t in sens["trough_readings"]
                          if t["trough_label"] == "composite_second_lowest")

    lines = [
        "% Source: results/phi_denominator_sensitivity.json --",
        "%   Sentence 1: feed_trough_only_at_phi_0.08 (added to",
        "%     phi_denominator_sensitivity.py and regenerated; restricted to the four genuinely",
        "%     distinct price feeds, excluding composite_second_lowest which is a second print",
        "%     within the composite feed, not a fifth feed): s_nf0_min=0.1919 (kraken_close_min),",
        "%     s_nf0_max=0.3513 (composite_min_headline).",
        "%   Sentence 2: full grid[] range across all 3 phi conventions x 5 trough readings",
        "%     (min/max of grid[].s_nf0): 0.1667 (phi=0.0825, kraken_close_min) to 0.3643",
        "%     (phi=0.0784, composite_min_headline). Pinned by",
        "%     tests/test_phi_denominator_sensitivity.py::test_feed_trough_only_at_phi_08_excludes_second_composite_print",
        "%     and ::test_broader_grid_range_includes_non_feed_cells.",
        # This fragment is typeset inside Section 6, so it is held to the manuscript's
        # writing standard. The spaced en dashes that used to bracket the "including the
        # composite feed's second-lowest print" aside were doing an em dash's job, which
        # the standard bans, and the aside split the subject from its verb across 30
        # words. Three sentences now, and \mathrm{nf} to match the body's subscript.
        r"At $\phi=0.08$, restricted to the four price feeds' own troughs (composite, Kraken close,",
        r"Kraken VWAP, and Binance/USDT-deflated), the non-fundamental share $s_{\mathrm{nf}}(0)$ ranges from",
        rf"${feed['s_nf0_min'] * 100:.1f}\%$ (Kraken close) to ${feed['s_nf0_max'] * 100:.1f}\%$ (composite).",
        r"A broader sensitivity range should not be conflated with it. That range runs over the",
        rf"full grid of three $\phi$ conventions ({', '.join(str(p) for p in phis)}) and all five",
        r"trough readings, which add the composite feed's second-lowest print",
        rf"($\${round(second_lowest['trough_price'], 4)}$) to the four feed troughs above.",
        rf"It spans ${grid_min * 100:.1f}\%$ to ${grid_max * 100:.1f}\%$.",
    ]
    return _write("share_range", "\n".join(lines) + "\n")


# ======================================================================================
# TABLE BUILDER GROUP 3, tab_firing. New fragment: the old Figure 1 lower strip (per-feed
# breach counts) is deleted along with that figure, and its content survives as this small
# table instead.
# ======================================================================================

def write_tab_firing() -> Path:
    """T-FIRING, tab_firing.tex: per-feed breach-count table replacing the deleted
    Figure 1 lower strip. Compact Feed/Breaches table, every value read live: the four
    USDC price-convention counts from results/trough_corroboration.json
    (firing_report.series), DAI/USDT from results/impossibility_window.json (coins),
    which is where the per-coin counts live -- trough_corroboration.json only carries
    USDC's own feed conventions. The Kraken-low row's single-trade caveat
    (trough_corroboration.json's kraken_low_convention_note) is deliberately not
    reproduced here; that caveat now lives in body prose."""
    with open(RESULTS_DIR / "trough_corroboration.json", encoding="utf-8") as f:
        trough = json.load(f)
    with open(RESULTS_DIR / "impossibility_window.json", encoding="utf-8") as f:
        window = json.load(f)

    series = trough["firing_report"]["series"]
    coins = window["coins"]

    rows = [
        ("Composite (USDC)", series["composite"]),
        ("Kraken close (USDC)", series["kraken_usdc_close"]),
        ("Kraken VWAP (USDC)", series["kraken_usdc_vwap"]),
        ("Kraken low (USDC)", series["kraken_usdc_low"]),
        ("DAI (composite)", {"firing_count": coins["DAI"]["breaching_hours"],
                              "n_hours_observed": coins["DAI"]["hours_observed"]}),
        ("USDT (composite)", {"firing_count": coins["USDT"]["breaching_hours"],
                               "n_hours_observed": coins["USDT"]["hours_observed"]}),
    ]

    lines = [
        "% Source: results/trough_corroboration.json --",
        "%   firing_report.series.{composite, kraken_usdc_close, kraken_usdc_vwap,",
        "%   kraken_usdc_low}.{firing_count, n_hours_observed} (9/212, 9/240, 10/240, 12/240);",
        "%   results/impossibility_window.json coins.{DAI,USDT}.{breaching_hours,",
        "%   hours_observed} (2/214, 0/213). Replaces the old Figure 1 lower strip,",
        "%   deleted along with that figure.",
        "%   The Kraken-low single-trade caveat (trough_corroboration.json's",
        "%   kraken_low_convention_note) is not reproduced here -- see write_tab_firing",
        "%   docstring; that caveat now lives in body prose.",
        r"\begin{tabular}{@{}lc@{}}",
        r"\toprule",
        r"Feed & Breaches \\",
        r"\midrule",
    ]
    for feed, d in rows:
        lines.append(f"{feed} & {d['firing_count']}/{d['n_hours_observed']} \\\\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    return _write("tab_firing", "\n".join(lines) + "\n")


def write_tab_claimmap() -> tuple[Path, Path]:
    """Claim map, tab_claimmap.tex + tab_claimmap_b.tex: every reported number to the
    results file and key it regenerates from, read from claim_map.yaml. This table
    replaced the inline "(Source: ...)" markers that used to sit in the manuscript's
    running prose, so the provenance is stated once in Appendix C instead of ~20 times
    in the body. Two fragments, for the reason given at the split below."""
    import yaml

    with open(PKG_DIR / "claim_map.yaml", encoding="utf-8") as f:
        cm = yaml.safe_load(f)
    rows = cm["claims"]

    # Column widths sum to 10.7cm. The remaining budget against a 12.2cm \textwidth goes
    # to the narrow Sec. column and the \tabcolsep gutters, which is why the p{} widths
    # cannot simply be scaled up to fill 12.2cm: an earlier revision summing to 11.5cm
    # tripped \checkwidth once those two were added. The Key column carries the longest
    # content (several underscore-joined JSON keys) so it takes the widest p{}.
    #
    # Two G5b changes:
    #
    # The Sec. column emits \ref{label}, not a hand-typed number. Twelve of these
    # twenty-two numbers had drifted away from the sections they name. A \ref cannot
    # drift, because LaTeX resolves it against the real section every build.
    #
    # Filenames and keys are set with \path, not \texttt. \texttt could not break an
    # underscore-joined name, so nine rows ran past their column and the build reported
    # 18 overfull boxes (nine rows seen on two pdflatex passes). \path breaks at the
    # characters named in \UrlBreaks below, which is what lets a long name wrap inside
    # a p{} column instead of hanging over it. \path also takes _ literally, so the
    # generator no longer escapes it. The redefinition sits inside \begingroup so the
    # bibliography's real URLs keep their own break rules.
    # The map is emitted as TWO fragments, not one.
    #
    # As a single 22-row float it was 141pt taller than \textheight, so LaTeX set it
    # anyway and the last row ("Hour-by-hour panel series for 11 March") printed at
    # y 779.6-799.6 on a 792pt page -- off the bottom edge, invisible in any renderer
    # even though pdftotext still found it in the content stream. A float cannot break
    # across pages, so no amount of placement tuning fixes this; the table has to be
    # two floats. The warning sat in the build log for a long time (58.5pt, then 141.0pt
    # as the table grew) and nothing read it, because "Float too large" is a warning and
    # the build gate counted only errors, undefined refs, and overfull hboxes.
    #
    # Splitting here rather than in the manuscript keeps the standing rule that table
    # fragments change only through tables.py and claim_map.yaml.
    half = (len(rows) + 1) // 2
    return (_write("tab_claimmap", _claimmap_tabular(rows[:half])),
            _write("tab_claimmap_b", _claimmap_tabular(rows[half:])))


def _claimmap_tabular(rows: list) -> str:
    """One complete tabular over `rows`, used for both halves of the claim map."""
    body = [
        "% Source: claim_map.yaml, via tables.py::write_tab_claimmap.",
        "% Do not hand-edit: regenerate with `python tables.py`.",
        r"\begingroup",
        r"\footnotesize",
        r"\setlength{\tabcolsep}{2pt}",
        # 0mu, against the preamble's 0mu plus 1mu. These cells carry no interword
        # space, so in a justified column \Urlmuskip is the only stretch available and
        # takes all of it, printing number1b _ rational _ bound . json. Ragged right
        # plus zero muskip means the name is set solid and breaks only where it must.
        r"\Urlmuskip=0mu\relax",
        r"\def\UrlBreaks{\do\_\do\.\do\,\do\-}",
        r"\urlstyle{tt}",
        r"\begin{tabular}{@{}>{\raggedright\arraybackslash}p{3.4cm}c"
        r">{\raggedright\arraybackslash}p{2.9cm}"
        r">{\raggedright\arraybackslash}p{4.0cm}@{}}",
        r"\toprule",
        r"Claim & Sec. & Results file & Key \\",
        r"\midrule",
    ]
    for r in rows:
        claim = str(r["claim"]).replace("&", r"\&")
        fname = str(r["file"]).strip()
        keys = ", ".join(rf"\path{{{k.strip()}}}" for k in str(r["key"]).split(","))
        body.append(rf"{claim} & \ref{{{r['label']}}} & \path{{{fname}}} & {keys} \\")
    body += [r"\bottomrule", r"\end{tabular}", r"\endgroup"]
    return "\n".join(body) + "\n"


def write_tab_sens_short() -> Path:
    """S-15, tab_sens_short.tex: the phi-by-trough grid, short paper only.

    Rows are the three readings of Circle's disclosure, columns the five frozen trough
    readings, cells the lower edge of the non-fundamental share at rho=0. The regular
    paper states this grid as prose and as share_range.tex; the short paper has neither
    the lines for the prose nor an appendix for the fragment, and its reviewers asked for
    exactly this object.
    """
    with open(RESULTS_DIR / "phi_denominator_sensitivity.json", encoding="utf-8") as f:
        sens = json.load(f)

    # Transposed against the first attempt, which \checkwidth rejected: five feed
    # headings plus a label column overran \textwidth. Denominators as columns and
    # troughs as rows gives four columns instead of six, and reads better besides -- a
    # reader asks what a given feed gives under each denominator, not the reverse.
    COL = {
        "static_phi_altreserve_42.1B_0.0784": r"\$42.1B",
        "static_phi_svb_0.08": r"\$40B",
        "static_phi_3.3_over_40.0_0.0825": r"\$3.3B/\$40.0B",
    }
    ROW = {
        "composite_min_headline": "Composite",
        "composite_second_lowest": "Composite, 2nd",
        "kraken_close_min": "Kraken close",
        "kraken_vwap_min": "Kraken VWAP",
        "binance_usdcusdt_low_min": "Binance low",
    }
    col_order = ["static_phi_altreserve_42.1B_0.0784", "static_phi_svb_0.08",
                 "static_phi_3.3_over_40.0_0.0825"]
    row_order = ["composite_min_headline", "composite_second_lowest", "kraken_close_min",
                 "kraken_vwap_min", "binance_usdcusdt_low_min"]

    cell = {(g["phi_label"], g["trough_label"]): g for g in sens["grid"]}
    assert all((c, r) in cell for r in row_order for c in col_order), "grid is not complete"
    assert all(cell[(c, r)]["fires"] for r in row_order for c in col_order), \
        "a grid cell does not fire; the sentence accompanying this table would be false"

    phi_of = {c: cell[(c, row_order[0])]["phi"] for c in col_order}
    lines = [
        "% Source: results/phi_denominator_sensitivity.json (grid[].s_nf0, by",
        "%   trough_label x phi_label). Cells are the lower edge of the non-fundamental",
        "%   share at rho=0, in percent. Every cell fires; the assertion is checked in",
        "%   the generator, not stated here by hand.",
        r"\begin{tabular}{@{}l" + "c" * len(col_order) + r"@{}}",
        r"\toprule",
        # Two header rows. One row carrying both the reserve total and its phi made
        # each column as wide as its widest header string, and \checkwidth rejected the
        # result; splitting halves the column width and loses nothing.
        "Trough reading & " + " & ".join(COL[c] for c in col_order) + r" \\",
        " & " + " & ".join(rf"($\phi={phi_of[c]}$)" for c in col_order) + r" \\",
        r"\midrule",
    ]
    for r in row_order:
        cells = [f"{cell[(c, r)]['s_nf0'] * 100:.1f}" for c in col_order]
        lines.append(f"{ROW[r]} & " + " & ".join(cells) + r" \\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    return _write_short("tab_sens_short", "\n".join(lines) + "\n")


def main():
    if REGULAR_PAPER_PRESENT:
        for name in ("write_tab_sources", "write_tab_placebo",
                     "write_tab_panel", "write_tab_hhi", "write_tab_dai",
                     "write_tab2_sentence", "write_share_range", "write_tab_firing",
                     "write_tab_claimmap"):
            fn = globals()[name]
            out = fn()
            for p in (out if isinstance(out, tuple) else (out,)):
                print(f"{name}: wrote {p}")
    else:
        print(f"skipping the full paper's fragments: {TABLES_DIR.parent} is absent")
    # Short-paper renderings. Same generators, same results files, different layout --
    # see the SHORT_TABLES_DIR comment for why these are separate fragments rather than
    # mirrors of the regular ones.
    for name, kwargs in (("write_tab_placebo", {"short": True}),
                         ("write_tab_panel", {"short": True}),
                         ("write_tab_sens_short", {})):
        fn = globals()[name]
        out = fn(**kwargs)
        for p in (out if isinstance(out, tuple) else (out,)):
            print(f"{name}: wrote {p}")


if __name__ == "__main__":
    main()
