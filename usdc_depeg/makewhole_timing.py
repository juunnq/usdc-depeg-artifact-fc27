"""Timing check for fc27.tex's claim that "the trough predates both Circle's make-whole
pledge and the federal action by more than a day."

Two independent sub-claims, checked separately (they can have different truth values):
  (a) trough -> Circle's make-whole pledge, more than a day
  (b) trough -> the federal (Treasury/Fed/FDIC) backstop action, more than a day

PREMISE CHECK, historical: at one point the only frozen artifact for the bib key
`circle2023update` ("An Update on USDC and Silicon Valley Bank", the source fc27.tex's
prose implicitly leans on for the make-whole-pledge sentence, the sentence itself carries
NO \\cite at all) was the bare entry in paper/fc27/refs.bib, with no raw HTML/PDF capture
of that blog post, or of any other circle.com page, anywhere under data/ (verified via
`grep -ri circle usdc_depeg/data/ usdc_depeg/data/n1/` and a scan of every `sources[]` entry
in data/MANIFEST.json: zero hits). Offline, sub-claim (a) could not then be computed from a
real intraday timestamp, it was booked as an explicit gap, not fabricated. Sub-claim (b)
did not depend on the missing source and WAS computed, from data already frozen separately.

RESOLVED: the Circle blog post is now frozen at
data/n1/circle2023/raw/circle_update_usdc_svb_20230311202753.html, its first Wayback
capture (MANIFEST.json series circle2023_update_usdc_svb). The page HTML carries no
article:published_time/datePublished meta tag, so the Wayback first-capture UTC timestamp
(2023-03-11T20:27:53Z) is used as an upper bound on the true publish time (a capture
cannot predate publication), sub-claim (a) is therefore computed against that bound,
not fabricated: since even the upper-bound gap is well under 24h, "more than a day" is
false regardless of exactly how much earlier the true publish time was.

Offline / keyless. Reads only the frozen snapshot; writes only results/makewhole_timing.json.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from constants import RESULTS_DIR, DATA_DIR

TROUGH_CORROBORATION_FILE = RESULTS_DIR / "trough_corroboration.json"
JOINT_STATEMENT_FILE = DATA_DIR / "n1" / "paxos2023" / "joint_statement_treasury_fed_fdic.json"
REFS_BIB_FILE = Path(__file__).resolve().parent.parent / "paper" / "fc27short" / "refs.bib"
CIRCLE_MANIFEST_SERIES = "circle2023_update_usdc_svb"
CIRCLE_FIRST_CAPTURE_TS_UTC = "2023-03-11T20:27:53Z"  # Wayback ts 20230311202753

A_DAY_S = 86400.0


def _write_result(path: Path, obj: dict) -> None:
    """Refuses to write anywhere under the frozen data/ tree, which is read-only
    by contract; guard the property in code rather than relying on discipline."""
    resolved = path.resolve()
    if DATA_DIR.resolve() in resolved.parents or resolved == DATA_DIR.resolve():
        raise PermissionError(f"refusing to write under frozen data/: {resolved}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)


def _trough_ts() -> dict:
    """The composite headline trough timestamp, read from the already-frozen N7 result
    file (not re-derived, N7 section 2, C1, settled this number)."""
    d = json.loads(TROUGH_CORROBORATION_FILE.read_text(encoding="utf-8"))
    comp = d["firing_report"]["series"]["composite"]
    kraken_low = d["firing_report"]["series"]["kraken_usdc_low"]
    return {
        "composite_headline_ts_utc": comp["min_print_utc"],
        "kraken_usdc_low_ts_utc": kraken_low["min_print_utc"],
        "source": "results/trough_corroboration.json:firing_report.series.{composite,kraken_usdc_low}",
    }


def _federal_action_ts() -> dict:
    d = json.loads(JOINT_STATEMENT_FILE.read_text(encoding="utf-8"))
    return {
        "ts_utc": d["joint_statement_ts_utc"],
        "source": "data/n1/paxos2023/joint_statement_treasury_fed_fdic.json:joint_statement_ts_utc",
    }


def _hours_between(earlier_iso: str, later_iso: str) -> float:
    t0 = datetime.fromisoformat(earlier_iso.replace("Z", "+00:00"))
    t1 = datetime.fromisoformat(later_iso.replace("Z", "+00:00"))
    return (t1 - t0).total_seconds() / 3600.0


def federal_action_timing() -> dict:
    """Sub-claim (b): trough -> federal backstop action. Fully frozen, computed both
    against the composite headline trough and the Kraken low (they agree on the truth
    value; reported separately rather than averaged, since they are different feeds)."""
    trough = _trough_ts()
    fed = _federal_action_ts()
    out = {}
    for label, key in (("composite_headline", "composite_headline_ts_utc"),
                        ("kraken_usdc_low", "kraken_usdc_low_ts_utc")):
        hours = _hours_between(trough[key], fed["ts_utc"])
        out[label] = {
            "trough_ts_utc": trough[key],
            "federal_action_ts_utc": fed["ts_utc"],
            "hours_after_trough": round(hours, 4),
            "more_than_a_day": bool(hours * 3600.0 > A_DAY_S),
        }
    return {
        "trough_source": trough["source"],
        "federal_action_source": fed["source"],
        "by_trough_reference": out,
        "more_than_a_day": out["composite_headline"]["more_than_a_day"],
        "hours_after_trough": out["composite_headline"]["hours_after_trough"],
    }


def circle_makewhole_timing() -> dict:
    """Sub-claim (a), RESOLVED: computed against the Circle blog
    post's first Wayback capture, now frozen at
    data/n1/circle2023/raw/circle_update_usdc_svb_20230311202753.html (MANIFEST.json
    series circle2023_update_usdc_svb). The saved page HTML has no
    article:published_time/datePublished meta tag, so the Wayback first-capture UTC
    timestamp is used as an upper bound on the true publish time (a capture cannot
    predate publication of the page it captures), the reported hours-after-trough is
    therefore itself an upper bound, and 'more than a day' is sound as a definitive FALSE
    even though the exact true publish instant is not pinned down: the upper-bound gap
    is well under 24h, so no earlier true publish time could push it over.

    Page-content check (verified directly, not assumed): the saved HTML's body (line 2002)
    contains "Circle, as required by law under stored-value money transmission
    regulation, will stand behind USDC and cover any shortfall using corporate
    resources, involving external capital if necessary", the make-whole pledge
    language fc27.tex:397-399 is describing. This confirms circle2023update is the
    right source for that sentence (the prior gap version of this function flagged this
    as unverified)."""
    trough = _trough_ts()
    out = {}
    for label, key in (("composite_headline", "composite_headline_ts_utc"),
                        ("kraken_usdc_low", "kraken_usdc_low_ts_utc")):
        hours = _hours_between(trough[key], CIRCLE_FIRST_CAPTURE_TS_UTC)
        out[label] = {
            "trough_ts_utc": trough[key],
            "circle_first_capture_ts_utc": CIRCLE_FIRST_CAPTURE_TS_UTC,
            "hours_after_trough_upper_bound": round(hours, 4),
            "more_than_a_day": bool(hours * 3600.0 > A_DAY_S),
        }
    return {
        "status": "COMPUTED",
        "sub_claim": "trough predates Circle's make-whole pledge by more than a day",
        "manuscript_location": "paper/fc27/fc27.tex:397-399 (the sentence itself carries "
                                "no \\cite -- refs.bib's circle2023update is the only "
                                "candidate source in the bibliography for a Circle "
                                "make-whole statement in this vicinity)",
        "bib_key": "circle2023update",
        "bib_title": "An Update on USDC and Silicon Valley Bank",
        "source": "data/MANIFEST.json sources[] series=" + CIRCLE_MANIFEST_SERIES
                  + " -- data/n1/circle2023/raw/circle_update_usdc_svb_20230311202753.html "
                    "(Wayback capture 20230311202753, the FIRST capture of this URL across "
                    "111 total captures spanning 2023-03-11..2026-06-27, verified via a "
                    "full-history CDX query and confirmed identical across http/https and "
                    "www/non-www URL variants -- see data/n1/circle2023/cdx_search_log.json)",
        "timestamp_basis": "no article:published_time/datePublished meta tag exists in "
                            "the captured page HTML, so the Wayback first-capture "
                            "timestamp (2023-03-11T20:27:53Z) stands in as an UPPER BOUND "
                            "on the true publish time, not the publish time itself.",
        "content_verification": (
            "The saved HTML (data/n1/circle2023/raw/circle_update_usdc_svb_20230311202753"
            ".html, line 2002) contains the make-whole pledge sentence: \"Circle, as "
            "required by law under stored-value money transmission regulation, will "
            "stand behind USDC and cover any shortfall using corporate resources, "
            "involving external capital if necessary.\" This confirms circle2023update "
            "is the right source for fc27.tex:397-399's uncited claim -- resolving the "
            "content_caveat the prior (gap) version of this function flagged."
        ),
        "by_trough_reference": out,
        "more_than_a_day": out["composite_headline"]["more_than_a_day"],
        "hours_after_trough_upper_bound": out["composite_headline"]["hours_after_trough_upper_bound"],
    }


def report() -> dict:
    fed = federal_action_timing()
    circle = circle_makewhole_timing()
    return {
        "manuscript_claim": "fc27.tex -- \"the trough predates both Circle's "
                            "make-whole pledge and the federal action by more than a day\"",
        "circle_makewhole_pledge": circle,
        "federal_backstop_action": fed,
    }


if __name__ == "__main__":
    r = report()
    _write_result(RESULTS_DIR / "makewhole_timing.json", r)
    print(json.dumps(r, indent=2))
