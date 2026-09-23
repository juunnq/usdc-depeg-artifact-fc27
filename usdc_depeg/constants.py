"""Shared constants for the USDC de-peg study.

Every magic number, address, the analysis window, and the global seed live here so
that all modules and tests are deterministic and trace to a single source of truth.
"""
from pathlib import Path

# March 11, 2023 — the de-peg trough day. Seeds EVERY stochastic routine.
SEED = 20230311

# --- Fundamentals input (Derivation 1) -------------------------------------
# phi = fraction of USDC reserves impaired by the SVB collapse.
# Circle disclosed $3.3B of ~$40B reserves trapped at SVB == ~8%.
# Source: Circle disclosure 2023-03-10; USDC Wikipedia; Anadu et al. (Boston Fed WP 23-11).
PHI_SVB = 0.08

# --- Analysis window (UTC) -------------------------------------------------
# Mar 8 00:00 -> Mar 17 00:00 UTC brackets: SVB distress (Mar 9), Circle
# disclosure (~Mar 10 18:00), FDIC backstop (Mar 12), full re-peg (Mar 13-14).
WINDOW_START_MS = 1678233600000  # 2023-03-08T00:00:00Z
WINDOW_END_MS = 1679011200000    # 2023-03-17T00:00:00Z
WINDOW_START_S = WINDOW_START_MS // 1000
WINDOW_END_S = WINDOW_END_MS // 1000

# --- On-chain identifiers --------------------------------------------------
USDC_CONTRACT = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"  # burn sink

# Verified on-chain entity mapping (self-derived; see README "Number 2").
# Coinbase dominated USDC redemption during the run: the "Coinbase 10" hot wallet
# (Etherscan/Blockscan-labeled) fed two single-source routing conduits into the
# Circle minter; the hot wallet itself aggregates 14,111 distinct upstream senders.
COINBASE_HOTWALLET = "0xa9d1e08c7793af67e9d92fe308d5697fb81d3e43"
COINBASE_CONDUITS = [
    "0x2cc5146929a893d1d73bc34fb37815cc1a44ae33",
    "0xad4ac4eb660d2b2d78a062bf136610af15defd80",
]
COINBASE_UPSTREAM_SENDERS = 14111

# --- DAI contagion (MakerDAO PSM) ------------------------------------------
# DAI was heavily USDC-linked via the Maker Peg Stability Module, so a USDC de-peg
# passes through to DAI. The PSM held substantial USDC on-chain through 2021-2022, but by
# March 2023 much had been moved to off-chain custody (Coinbase Custody ~$500M + US
# Treasuries via RWA ~$1B), so the on-chain balance at the documented PSM-USDC-A contracts
# on the run date is ~$0 and a clean on-chain backing fraction is NOT available.
# DAI_REPORTED_PSM_USDC is therefore a SOURCED REPORTING figure, not an on-chain pull;
# dai_contagion.py freezes what IS on-chain (DAI supply; run-date PSM custody ~0).
# on_chain_backing_clean stays false.
#   Re-frozen 2026-09-10 to a contemporaneous 2023-03-11 figure (The Block,
#   Wayback-captured 2023-03-11T10:54:27Z, see data/n1/dai_psm_2023/), superseding an
#   earlier ~$2.4B Jan-2023 figure. NOTE, UNRESOLVED: the source reports the PSM-USDC-A
#   debt ceiling as "reached" ($3.1B), which is a statement about `line`, not a custody
#   reading. It is NOT reconcilable with the ~$99 on-chain join balance at the same block by
#   any "refilled during the panic" story: PSM `sellGem` physically transfers USDC INTO the
#   join, so billions cannot arrive on the morning of 11 March and leave $99 in the join at
#   12:00 UTC the same day. An earlier revision of this comment asserted that reconciliation;
#   it was wrong and is retracted. Whether $3.1B was `line`, `Art`, or both is settleable
#   with one archival call, vat.ilks(bytes32("PSM-USDC-A")) at block 16804701, which
#   needs ETHERSCAN_API_KEY and has not been run. Until then this figure is a reported
#   ceiling only, and no pass-through coefficient is derived from it.
DAI_CONTRACT = "0x6b175474e89094c44da98b954eedeac495271d0f"
PSM_USDC_JOIN = "0x0a59649758aa4d66e25f08dd01271e891fe52199"  # PSM-USDC-A join (on-chain ~$0)
PSM_USDC_CONTRACT = "0x89b78cfa322f6c5de0abceecab66aee45393cc5a"  # MCD_PSM_USDC_A (on-chain ~$0)
DAI_RUN_DATE_TS = 1678536000  # 2023-03-11 12:00 UTC (de-peg trough day)
DAI_REPORTED_PSM_USDC = 3.1e9  # PSM-USDC-A reached its $3.1B cap, 2023-03-11 (The Block; contemporaneous)

# --- Second event: Terra/LUNA collapse (May 2022), no-fundamental-shock control ---
# USDT (and USDC) had NO Terra/UST/LUNA reserve exposure, so the Terra-shock phi ~= 0 and
# the worst-case fundamental floor is ~1.00: Proposition 1 then attributes ~100% of any
# dip to non-fundamental. A clean control that the method does NOT manufacture a
# fundamental story where none exists. Frozen to data/terra_price_hourly.csv.
TERRA_WINDOW_START_S = 1651968000  # 2022-05-08 00:00 UTC
TERRA_SPAN_HOURS = 192             # 8 days; captures the May 9-13 UST collapse
TERRA_USDT_PHI = 0.0               # no UST/LUNA in USDT reserves (Terra-shock exposure)

# --- Specificity panel: Tether 2019 disclosure anchor -----------------------
# The New York Attorney General's press release (data/n1/tether2019/
# disclosure_passages_nyag.json, passages[0].date = "2019-04-25") is the disclosure
# this project's ruling on the Tether specificity row uses for its phi. Only a
# DATE is sourced, not a time of day -- an AG press release carries no frozen intraday
# timestamp the way Circle's tweet does, and none was found across three prior
# sessions' searches (see D14's bearing evidence). Anchored at 00:00 UTC on that
# date rather than left unanchored, so any figure needing "hours since disclosure"
# for this row has one canonical value instead of each caller picking its own.
# State this as date-level precision explicitly wherever it is used -- it is not
# claiming to know the hour the market actually learned of the filing.
TETHER_2019_DISCLOSURE_S = 1556150400  # 2019-04-25T00:00:00Z

# --- Data sources ----------------------------------------------------------
# DeFiLlama coins API identifiers (hourly USD price).
DLL_COINS = {
    "USDC": "coingecko:usd-coin",
    "USDT": "coingecko:tether",
    "DAI": "coingecko:dai",
}
# DeFiLlama stablecoins API ids (daily circulating supply). USDC=2 verified live.
DLL_STABLECOIN_IDS = {"USDC": 2}

# --- Excluded placebo coins (USDP / GUSD / BUSD) ----------------------------
# The cross-stablecoin placebo (Number 3) uses USDC/DAI/USDT only. USDP, GUSD, and
# BUSD are EXCLUDED; data_fetch.fetch_excluded_coins freezes the evidence for why
# (crisis troughs + a calm-window false-positive check + keyless venue volumes) and
# placebo.excluded_coins() reads only that frozen snapshot.
EXCLUDED_COINS = {
    "USDP": "coingecko:paxos-standard",
    "GUSD": "coingecko:gemini-dollar",
    "BUSD": "coingecko:binance-usd",
}
# Calm baseline window (UTC): Feb 16 00:00 -> Mar 8 00:00 2023 (480h, pre-SVB,
# abuts the crisis window start). Measures whether the 1%-break test would fire on
# these coins in a no-shock period (a false positive), i.e. whether the test is
# diagnostic for them at all. 480h verified within DeFiLlama's span limit.
CALM_START_S = 1676505600  # 2023-02-16T00:00:00Z
CALM_END_S = WINDOW_START_S  # 2023-03-08T00:00:00Z

# --- Paths -----------------------------------------------------------------
PKG_DIR = Path(__file__).resolve().parent
DATA_DIR = PKG_DIR / "data"
RESULTS_DIR = PKG_DIR / "results"
FC27_DIR = PKG_DIR.parent / "paper" / "fc27"
FC27_TEX = FC27_DIR / "fc27.tex"
# The short paper is the submitted document and the only manuscript this repository
# tracks; the full paper stays on the author's disk and is not in a clean clone.
# THE ON-CHAIN ATTRIBUTION PACKAGE IS OPTIONAL IN THE ARTIFACT.
# Numbers 2 (redemption concentration) and 4 (DAI contagion), and the FIFO rule they
# rest on, work from wallet-level Etherscan data. They belong to the long paper. The
# submitted short paper uses none of them, so its artifact ships neither the code nor
# data/redemptions_by_wallet.csv, and redemptions.py is the sentinel for whether it is
# present. Absence is a shipping decision; nothing should report it as a failure.
REDEMPTIONS_FILE = DATA_DIR / "redemptions_by_wallet.csv"
DAI_BACKING_FILE = DATA_DIR / "dai_backing.json"
ONCHAIN_PRESENT = (PKG_DIR / "redemptions.py").exists()
ONCHAIN_RESULTS = frozenset({
    "number2_redemption_hhi.json",
    "number4_dai_contagion.json",
    "fifo_rule.json",
})

FC27SHORT_DIR = PKG_DIR.parent / "paper" / "fc27short"
FC27SHORT_TEX = FC27SHORT_DIR / "fc27short.tex"


def manuscript_source(tex=None) -> str:
    """The manuscript's full text, with every \\input resolved depth-first in document
    order.

    G5b split the body into paper/fc27/sections/*.tex, leaving fc27.tex a skeleton of
    \\input lines. Anything that asks "what does the paper actually say" has to read the
    expansion. Reading fc27.tex directly used to be the same thing and silently stopped
    being so: the split broke build_fc27.py's figure cross-check (loudly, it refused to
    build) and ordering_null.py's claim extraction (loudly, a missing anchor). One
    function so a third consumer cannot quietly get a skeleton and conclude the
    manuscript says nothing.

    Table fragments under tables/ are \\input too and resolve through the same pass,
    which is what callers checking rendered prose want.

    Defaults to the SHORT paper, which is the submitted document. Pass FC27_TEX to read
    the full paper where it is still on disk; it is not tracked and a clean clone does
    not have it, so a caller that needs it must handle its absence.
    """
    tex = Path(tex) if tex is not None else FC27SHORT_TEX
    base = tex.parent
    import re as _re

    seen: set[Path] = set()
    input_re = _re.compile(r"\\input\{([^}]+)\}")

    def expand(path: Path) -> str:
        path = path.resolve()
        if path in seen or not path.exists():
            # A missing target is LaTeX's error to report, not ours; a cycle would hang.
            return ""
        seen.add(path)
        out = []
        for line in path.read_text(encoding="utf-8").splitlines():
            out.append(line)
            for m in input_re.finditer(_re.sub(r"(?<!\\)%.*$", "", line)):
                rel = m.group(1)
                out.append(expand(base / (rel if rel.endswith(".tex") else rel + ".tex")))
        return "\n".join(out)

    return expand(tex)
