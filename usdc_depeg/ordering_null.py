"""Null probability of the realized cross-coin fragility ordering, by explicit
enumeration of the admissible arrangements (never a formula plugged in from memory).

fc27short.tex claims a FULL rank order, not merely "USDC is most fragile." The cross-coin
section's ex-ante paragraph states it directly: "USDC (direct SVB exposure) most fragile,
DAI (mechanically pinned to USDC through the PSM) next, and USDT (no SVB exposure) least"
(ex-ante, from the model). The "Realized ordering" paragraph then reports the observed
troughs, $0.877 (USDC), $0.886 (DAI), $0.992 (USDT).

THE NULL IS 1/2, NOT 1/6 (ruling D16). DAI is mechanically linked to USDC through the
PSM, which is what the DAI-contagion result establishes when it declines to read DAI's break as an
independent run. A null that enumerated all 3! = 6 permutations would treat DAI as an
exchangeable draw, contradicting the paper's own contagion argument. USDC and DAI move as
one block and the single free comparison is USDT's placement relative to that block, so
there are 2 admissible arrangements and the realized one has probability 1/2. This module
enumerates those 2 explicitly rather than asserting the fraction.

Offline / keyless. Reads only the frozen manuscript text; writes only
results/ordering_null.json.

CLAIM EXTRACTION IS ANCHOR-BASED, NOT LINE-NUMBER-BASED. An earlier revision of this
module hardcoded lines[521:524] / lines[589:591], pinned to the fc27short.tex line numbers
current at the time it was written. Every later manuscript edit that added or removed a
line above those offsets shifted what the indices pointed at, silently: no exception,
just a different, unrelated passage quoted as if it were the claim. Found when report.py
was extended to actually call this module on every run (previously it was never wired in,
so the drift accumulated across several sessions of caption/paragraph edits without
anyone re-running it): the committed results/ordering_null.json quoted two sentences from
a table caption, not from either claim. Searching for a stable anchor substring in
whitespace-normalized text, bounded by a second anchor marking the claim's end, is immune
to the line shifting; if the manuscript prose changes enough that an anchor no longer
appears, extraction raises loudly instead of silently grabbing the wrong text.
"""

import json
import re
from pathlib import Path

from constants import RESULTS_DIR, DATA_DIR

# Quotes the SHORT paper: it is the submitted document, and the full paper is neither
# tracked nor present in a clean clone. The anchors below are its Section 5 wording.
MANUSCRIPT = (Path(__file__).resolve().parent.parent
              / "paper" / "fc27short" / "fc27short.tex")
COINS = ["USDC", "DAI", "USDT"]  # ordered most-fragile-first in the claimed ranking
CLAIMED_ORDER = ("USDC", "DAI", "USDT")


def _write_result(path: Path, obj: dict) -> None:
    resolved = path.resolve()
    if DATA_DIR.resolve() in resolved.parents or resolved == DATA_DIR.resolve():
        raise PermissionError(f"refusing to write under frozen data/: {resolved}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)


def enumerate_permutations() -> list:
    """The admissible arrangements under the ruled null (D16), most-fragile-first.

    DAI is NOT a free draw. It is mechanically linked to USDC through the PSM, which is
    what the DAI-contagion result establishes, so the manuscript cannot treat DAI as exchangeable here
    while treating it as mechanically determined there. USDC and DAI therefore move as
    one block, and the single free comparison is where USDT falls relative to that block:
    outside it (least fragile, the realized outcome) or inside it. Two admissible
    arrangements, one of which matches, so the null probability is 1/2.

    An earlier revision enumerated all 3! = 6 permutations of the three coins and
    reported 1/6. That treated DAI as an independent draw and understated the null."""
    pair = [c for c in CLAIMED_ORDER if c != "USDT"]  # USDC, DAI: the linked block
    return [
        {"order_most_fragile_first": pair + ["USDT"],
         "description": "USDT least fragile, outside the linked USDC-DAI block",
         "matches_claimed_order": True},
        {"order_most_fragile_first": ["USDT"] + pair,
         "description": "USDT more fragile than the linked USDC-DAI block",
         "matches_claimed_order": False},
    ]


def _extract_between(norm: str, start_anchor: str, end_anchor: str) -> str:
    """Return the substring from `start_anchor` through the end of `end_anchor`,
    inclusive, in whitespace-normalized text `norm`. Raises ValueError (loud, not a
    silent wrong-text return) if either anchor is missing, the manuscript changed
    enough that this extraction needs a human to re-pick the anchors, not a computer to
    guess at sentence boundaries in LaTeX prose full of macros and decimal points."""
    i = norm.find(start_anchor)
    if i < 0:
        raise ValueError(f"ordering_null.py: start anchor not found in fc27short.tex: "
                          f"{start_anchor!r}")
    j = norm.find(end_anchor, i)
    if j < 0:
        raise ValueError(f"ordering_null.py: end anchor not found after its start "
                          f"anchor in fc27short.tex: {end_anchor!r}")
    return norm[i:j + len(end_anchor)]


def _line_of(anchor: str, raw_text: str) -> int:
    """1-indexed line number where `anchor` starts in the RAW (non-normalized) source,
    for a human-readable pointer in the output, informational only, never used to
    locate the claim itself (that happens in whitespace-normalized text via
    `_extract_between`, which is what actually matters for correctness).

    Searches raw_text with a whitespace-TOLERANT regex built from `anchor` (its own
    literal characters escaped, internal whitespace runs replaced by `\\s+`), because if
    the anchor happens to wrap across a line break in the raw .tex source, a plain
    substring .find() would miss it entirely, and a naive .find() of just the anchor's
    first word is worse: a single short word like "A" matches the first occurrence
    anywhere in the file, nowhere near the real sentence, and returns a confidently wrong
    line number instead of failing. Use enough of the anchor to be unambiguous."""
    pattern = re.escape(anchor)
    pattern = re.sub(r"(\\ )+", r"\\s+", pattern)
    m = re.search(pattern, raw_text)
    return raw_text.count("\n", 0, m.start()) + 1 if m else -1


def _manuscript_claim_lines() -> dict:
    """Pulls the exact claim text from the frozen manuscript source, anchor-based (see
    module docstring), so the JSON carries what the paper actually says rather than a
    paraphrase, and so a later manuscript edit that shifts line numbers cannot make
    this silently quote the wrong passage.

    Reads the \\input-expanded manuscript, not fc27short.tex alone. G5b split the body into
    sections/*.tex and fc27short.tex became a skeleton, at which point both anchors below
    stopped being present in it and this raised. The line numbers reported alongside the
    text are offsets into the expansion, not into any one file, and were already
    documented as informational."""
    from constants import manuscript_source

    raw = manuscript_source(MANUSCRIPT)
    norm = re.sub(r"\s+", " ", raw)

    exante_start = "Reserve exposure orders the three coins ex ante"
    realized_start = "Troughs are"
    exante = _extract_between(
        norm, exante_start, "the ex-ante fragility ordering USDC, DAI, USDT.")
    # End anchor was "as noted above.", a back-reference the prose audit marked for
    # deletion (it was a repair sentence for a claim already made three times). Anchored
    # instead on the ordering claim itself, which is the content this module checks and
    # cannot be cut without changing what the paper asserts.
    realized = _extract_between(norm, realized_start, "in that order.")
    return {
        "ex_ante_claim": exante,
        "ex_ante_claim_tex_line": _line_of(exante_start, raw),
        "realized_ordering_claim": realized,
        "realized_ordering_claim_tex_line": _line_of(realized_start, raw),
    }


def report() -> dict:
    perms = enumerate_permutations()
    n_matching = sum(1 for p in perms if p["matches_claimed_order"])
    n_total = len(perms)
    claims = _manuscript_claim_lines()
    return {
        "coins": COINS,
        "manuscript_claim_lines": claims,
        "claim_type_determination": (
            "FULL RANK ORDER over a linked pair and one free coin. The ex-ante paragraph "
            f"(fc27short.tex:{claims['ex_ante_claim_tex_line']}) states an explicit 3-way "
            "ranking (USDC most fragile, DAI next, USDT least) and the realized-ordering "
            f"paragraph (fc27short:{claims['realized_ordering_claim_tex_line']}) reports "
            "the realized troughs as consistent with that ex-ante exposure ranking. DAI "
            "is mechanically linked to USDC through the PSM (the DAI-contagion result), so it "
            "is not an "
            "exchangeable draw and the USDC-DAI pair moves as one block. The null space "
            "is the 2 placements of USDT relative to that block, not the 6 permutations "
            "of 3 free items."
        ),
        "claimed_order_most_fragile_first": list(CLAIMED_ORDER),
        "permutations": perms,
        "n_permutations": n_total,
        "n_matching_claimed_order": n_matching,
        "probability_null_uniform": n_matching / n_total,
        "probability_as_fraction": f"{n_matching}/{n_total}",
        "note": (
            "1/2 under the ruled null (D16). The free comparison is USDT against the "
            "linked USDC-DAI pair, because the DAI-contagion result establishes DAI's break runs "
            "through its USDC exposure rather than an independent run. Scoring the match "
            "against all 6 permutations would treat DAI as exchangeable, which the "
            "paper's own contagion argument denies."
        ),
    }


if __name__ == "__main__":
    r = report()
    _write_result(RESULTS_DIR / "ordering_null.json", r)
    print(json.dumps(r, indent=2))
