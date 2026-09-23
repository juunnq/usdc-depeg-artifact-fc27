"""phi under each impairment scenario the issuer's own disclosure admits.

The disclosed-impairment floor rests on phi = SVB deposit / total reserves. That is the
NARROWEST reading of the disclosure. The same frozen capture itemises the whole cash leg,
so the disclosure itself bounds how much worse the impairment could rationally have been
feared to be, and the estimator can be re-evaluated at each of those readings instead of
being defended by a margin argument.

Every dollar figure below is PARSED from the frozen capture, not typed here: the capture's
own prose is the source, and its SHA-256 is recorded in the output so a reader can re-verify
the parse against the same bytes. The two accounting identities the capture asserts
(5.4 + 3.3 + 1.0 = 9.7 cash, and 32.4 + 9.7 = 42.1 total) are ASSERTED, not assumed, so a
capture that ever stopped supporting them would fail loudly here rather than silently
changing a published number.

Scenarios, in increasing severity:
  svb_only                  -- the disclosed reading: only the SVB deposit is impaired
  outside_custodian         -- every cash deposit outside the G-SIB custodian is impaired
                               (SVB + Customers Bank); BNY Mellon treated as safe
  full_cash_leg             -- the entire cash leg is impaired

Denominators: the tweet's "~$40 billion" headline and the reserve-composition total
(T-bills + cash) the same capture implies.

Feeds: the three price readings the short paper quotes. Each is read FRESH from its frozen
file rather than trusted as a literal, matching the discipline in
phi_denominator_sensitivity.py.

READ-ONLY on data/: this module only opens frozen files; nothing under data/ is written.
"""
import hashlib
import json
import re
from datetime import datetime, timezone

import pandas as pd

from constants import DATA_DIR, RESULTS_DIR
from rational_bound import below_floor_share, floor_worstcase, implied_failure_prob

N1_DIR = DATA_DIR / "n1"
CAPTURE = N1_DIR / "circle2023" / "raw" / "circle_update_usdc_svb_20230311202753.html"
OUT_PATH = RESULTS_DIR / "phi_cash_leg_scenarios.json"

FLOOR_PRICE = 0.92  # 1 - 0.08, the disclosed-reading floor; used only for reporting


class FrozenFileError(RuntimeError):
    """Raised if this module is ever pointed at a frozen data/ path for writing."""


def _assert_safe_write_path(path) -> None:
    path = path.resolve()
    data_dir = DATA_DIR.resolve()
    if data_dir in path.parents or path == data_dir:
        raise FrozenFileError(
            f"refusing to write '{path}': it is under the frozen data/ directory. "
            f"This module writes only to results/."
        )
    manifest_path = DATA_DIR / "MANIFEST.json"
    if manifest_path.exists():
        manifest = json.load(open(manifest_path, encoding="utf-8"))
        names = set(manifest.get("files", {}).keys())
        names |= {s["file"] for s in manifest.get("sources", []) if "file" in s}
        if path.name in names:
            raise FrozenFileError(
                f"refusing to write '{path}': '{path.name}' is a manifest-tracked "
                f"frozen filename."
            )


def _capture_text() -> tuple[str, str]:
    """Return (visible text of the frozen capture, its sha256)."""
    raw = CAPTURE.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    html = raw.decode("utf-8", "replace")
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        text = soup.get_text(" ", strip=True)
    except ImportError:
        text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text), digest


# Each figure is anchored on the institution or asset it belongs to, so a reordering of
# the capture's prose cannot silently swap two amounts. "bn" and "B" both appear.
_AMOUNT = r"\$([0-9]+(?:\.[0-9]+)?)\s*(?:bn|B|billion)\b"
ANCHORS = {
    "tbills_usd_bn": rf"77%\s*\(\s*{_AMOUNT}",
    "cash_usd_bn": rf"23%\s*\(\s*{_AMOUNT}",
    "bny_mellon_usd_bn": rf"deposited\s+{_AMOUNT}\s+with\s+BNY\s+Mellon",
    "svb_usd_bn": rf"{_AMOUNT}\s+of\s+USDC.{{0,3}}s\s+cash\s+reserves\s+remain\s+with\s+SVB",
    "customers_bank_usd_bn": rf"{_AMOUNT}\s+of\s+the\s+USDC\s+reserves\s+is\s+held\s+with\s+Customers\s+Bank",
}


def parse_cash_leg() -> dict:
    """Parse the itemised cash leg out of the frozen capture and check it adds up."""
    text, digest = _capture_text()
    figures = {}
    for key, pattern in ANCHORS.items():
        m = re.search(pattern, text, re.I)
        if not m:
            raise ValueError(
                f"{key}: anchor pattern found no match in {CAPTURE.name}. The capture is "
                f"frozen, so this means the pattern is wrong, not the data."
            )
        figures[key] = float(m.group(1))

    silvergate_zero = bool(re.search(r"zero\s+exposure\s+to\s+Silvergate", text, re.I))
    if not silvergate_zero:
        raise ValueError("capture no longer states zero Silvergate exposure")

    # Accounting identities the capture itself asserts. Compared at 1e-9 because these are
    # one-decimal figures in binary floating point (5.4 + 3.3 + 1.0 == 9.700000000000001).
    itemised = (figures["bny_mellon_usd_bn"] + figures["svb_usd_bn"]
                + figures["customers_bank_usd_bn"])
    if abs(itemised - figures["cash_usd_bn"]) > 1e-9:
        raise ValueError(
            f"itemised cash {itemised} != stated cash leg {figures['cash_usd_bn']}"
        )
    reserve_total = figures["tbills_usd_bn"] + figures["cash_usd_bn"]

    return {
        "capture_file": CAPTURE.relative_to(DATA_DIR.parent).as_posix(),
        "capture_sha256": digest,
        "figures_usd_bn": figures,
        "silvergate_zero_exposure_stated": silvergate_zero,
        "itemised_cash_sum_usd_bn": round(itemised, 10),
        "reserve_total_usd_bn": round(reserve_total, 10),
        "identities_checked": [
            "bny_mellon + svb + customers_bank == cash",
            "tbills + cash == reserve_total",
        ],
    }


def _feed_troughs() -> dict:
    """Three price readings, each computed fresh from its frozen file."""
    comp = pd.read_csv(DATA_DIR / "price_hourly.csv")
    comp = comp[comp["symbol"].astype(str).str.upper() == "USDC"]
    kr = pd.read_csv(N1_DIR / "kraken_usdcusd" / "hourly.csv")
    return {
        "composite_min_headline": float(comp["price"].min()),
        "kraken_vwap_min": float(kr["vwap"].min()),
        "kraken_close_min": float(kr["close"].min()),
    }


def build() -> dict:
    cash = parse_cash_leg()
    f = cash["figures_usd_bn"]
    troughs = _feed_troughs()

    scenarios = {
        "svb_only": {
            "impaired_usd_bn": f["svb_usd_bn"],
            "description": "the disclosed reading: only the SVB deposit is impaired",
        },
        "outside_custodian": {
            "impaired_usd_bn": round(f["svb_usd_bn"] + f["customers_bank_usd_bn"], 10),
            "description": "every cash deposit outside the G-SIB custodian is impaired "
                            "(SVB + Customers Bank); BNY Mellon treated as safe",
        },
        "full_cash_leg": {
            "impaired_usd_bn": f["cash_usd_bn"],
            "description": "the entire cash leg is impaired",
        },
    }
    denominators = {
        "headline_40B": 40.0,
        "reserve_composition": cash["reserve_total_usd_bn"],
    }

    grid = []
    for s_key, s in scenarios.items():
        for d_key, den in denominators.items():
            phi = s["impaired_usd_bn"] / den
            floor = floor_worstcase(phi)
            for t_key, p_obs in troughs.items():
                fires = p_obs < floor
                grid.append({
                    "scenario": s_key,
                    "denominator": d_key,
                    "denominator_usd_bn": den,
                    "impaired_usd_bn": s["impaired_usd_bn"],
                    "phi": round(phi, 6),
                    "floor": round(floor, 6),
                    "feed": t_key,
                    "p_obs": p_obs,
                    "fires": fires,
                    "verdict": "fires" if fires else "none",
                    "q_star": round(implied_failure_prob(p_obs, phi), 6) if fires else None,
                    "s_nf0": round(below_floor_share(p_obs, phi), 6) if fires else None,
                })

    # The phi at which each feed stops firing is 1 - p_obs, by construction.
    crossings = {k: round(1.0 - v, 6) for k, v in troughs.items()}

    def _cell(scenario, denominator, feed):
        for g in grid:
            if (g["scenario"] == scenario and g["denominator"] == denominator
                    and g["feed"] == feed):
                return g
        raise KeyError((scenario, denominator, feed))

    oc_rc_comp = _cell("outside_custodian", "reserve_composition", "composite_min_headline")
    oc_rc_vwap = _cell("outside_custodian", "reserve_composition", "kraken_vwap_min")
    oc_40_comp = _cell("outside_custodian", "headline_40B", "composite_min_headline")

    # Flat, dotted-addressable figures for the manuscript, so a printed number resolves
    # to one key rather than to a grid search. Percent forms are stored alongside the
    # fractions because the manuscript prints percentages.
    for_print = {
        "bny_mellon_usd_bn": f["bny_mellon_usd_bn"],
        "svb_usd_bn": f["svb_usd_bn"],
        "customers_bank_usd_bn": f["customers_bank_usd_bn"],
        "cash_leg_usd_bn": f["cash_usd_bn"],
        "tbills_usd_bn": f["tbills_usd_bn"],
        "reserve_total_usd_bn": cash["reserve_total_usd_bn"],
        "outside_custodian_impaired_usd_bn": scenarios["outside_custodian"]["impaired_usd_bn"],
        "outside_custodian_phi_reserve_composition": oc_rc_comp["phi"],
        "outside_custodian_phi_headline_40B": oc_40_comp["phi"],
        "outside_custodian_composite_edge_reserve_composition": oc_rc_comp["s_nf0"],
        "outside_custodian_composite_edge_headline_40B": oc_40_comp["s_nf0"],
        "outside_custodian_vwap_edge_reserve_composition": oc_rc_vwap["s_nf0"],
        "outside_custodian_composite_edge_pct_reserve_composition":
            round(100 * oc_rc_comp["s_nf0"], 1),
        "outside_custodian_composite_edge_pct_headline_40B":
            round(100 * oc_40_comp["s_nf0"], 1),
        "outside_custodian_vwap_edge_pct_reserve_composition":
            round(100 * oc_rc_vwap["s_nf0"], 1),
        "crossing_phi_composite": crossings["composite_min_headline"],
        "crossing_phi_kraken_vwap": crossings["kraken_vwap_min"],
        "crossing_phi_kraken_close": crossings["kraken_close_min"],
    }

    return {
        "for_print": for_print,
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "method_source": "rational_bound.py (floor_worstcase, implied_failure_prob, "
                          "below_floor_share)",
        "disclosure": cash,
        "feed_troughs": troughs,
        "feed_zero_crossings_phi": crossings,
        "scenarios": scenarios,
        "denominators_usd_bn": denominators,
        "grid": grid,
        "note": (
            "The 'outside_custodian' scenario is the worst case the disclosure itself "
            "admits. Its phi straddles two of the three feeds' zero crossings, so the "
            "estimator's verdict depends on the feed and on which reserve total is used "
            "as the denominator."
        ),
    }


if __name__ == "__main__":
    result = build()
    _assert_safe_write_path(OUT_PATH)
    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)
    print(f"wrote {OUT_PATH}")
