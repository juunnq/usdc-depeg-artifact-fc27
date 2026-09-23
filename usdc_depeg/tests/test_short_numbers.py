"""Every number the short paper prints must also appear in the regular paper.

D28 builds the short version as a rewrite to budget, and requires that every number and
citation be carried VERBATIM from the regular text. The regular text is the one whose
numbers are claim-mapped, gated, and traced to results files, so "carried verbatim from
the regular text" is what makes the short version's numbers provenanced at all. It
replaces the printed claim map with an artifact URL, so nothing inside the short paper
records where its numbers came from.

That makes containment the invariant worth testing: a numeric literal in the short paper
that does NOT occur in the regular paper was either invented, re-rounded, or mistyped
during the rewrite, and no other check in this repository would see it. The eight
sections were written in parallel by separate writers working from the regular sources,
which is exactly the arrangement in which a silently re-rounded figure is most likely.

Direction matters: containment is one-way. The regular paper prints many numbers the
short version drops, which is the whole point of an 8-page variant, so the reverse
inclusion is not asserted.

WHAT CHANGED WHEN THE FULL PAPER LEFT THE REPOSITORY (J5). Only the short paper is
submitted, so the full paper is no longer tracked, shipped in the artifact, or present in
a clean clone -- and a number that appears only in it is of no interest. The containment
tests below therefore SKIP when paper/fc27/sections is absent, rather than failing: they
are a cross-check for whoever still has both trees, not the provenance guarantee.

The guarantee that survives, and it is the stronger one, is per-number rather than
per-document: every figure the short paper prints that is NOT carried from the regular
text sits in AUTHORIZED_DIVERGENCE with the results file, dotted key and transform that
produce it, and test_each_authorized_divergence_is_still_printed_and_still_resolves
re-derives each one from that key on every run. That test needs no second manuscript and
does not skip. Alongside it, claim_map.yaml maps each results file to the section that
cites it.

Two numbers are authorized exceptions. G9's short-track reviewers raised both as Tier-1
defects in the regular text that the short version was told to fix, so for these two the
short version is deliberately NOT a copy. They are listed in AUTHORIZED_DIVERGENCE below,
each with the results key it resolves through, and the second test re-derives each one from
that key -- so the exception is gated on provenance rather than simply excused.

WHAT THIS TEST ACTUALLY COMPARES, and the trap that follows from it (H2b, 2026-09-21).
It compares EXTRACTED LITERALS, not values. _numbers() spans a token across a comma and
then DELETES the comma rather than splitting on it, so a range written `[0.668,0.742]`
collapses to the single literal `0.6680.742`. The regular paper's table fragment writes
the same range as `$[0.668,\\allowbreak 0.742]$`; macro-stripping turns that `\\allowbreak`
into a SPACE, the token pattern cannot span a space, and it tokenises as TWO literals,
`0.668` and `0.742`. Same numbers, same rendered output, different extracted literals --
and this test correctly reported the short paper's version as an invention.

So the rule is stronger than "carry the number verbatim": A RANGE'S SPELLING, INCLUDING
`\\allowbreak`, MUST MATCH ACROSS THE TWO PAPERS. Copy the markup character for character
from the regular source, not just the digits. Nothing marks which ranges are safe --
`[0.259,0.332]` and `[0.135,0.174]` pass without `\\allowbreak` only because the regular
paper happens to print those two in the same un-broken form.

Two consequences worth keeping in mind before changing _numbers():
  - Splitting on separators (`re.split(r"[,\\s]", tok)`) would make this class of failure
    impossible, where deleting them creates it. That change is deliberately NOT made here,
    because the fused token is also what makes the failure legible.
  - A failure from this test is ambiguous by construction: "invented number" and "same
    number, different markup" look identical in its output. The assertion therefore prints
    the RAW extracted token, and it should keep doing so -- seeing `0.6680.742` is what
    makes the cause obvious in seconds.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from constants import PKG_DIR

RESULTS = PKG_DIR / "results"

# number printed in the short version -> (results file, key, why it is not in the regular)
AUTHORIZED_DIVERGENCE = {
    # S-2. A reviewer back-solved Kraken's close trough to ~$0.901 and concluded the paper
    # contradicts itself, because Section 3 calls Kraken a cross-check on the $0.877 trough
    # while Section 4 reads 19.2% off Kraken's close. Both are true of DIFFERENT Kraken
    # statistics. Printing the close minimum once is what makes them visibly consistent.
    "0.901": ("trough_corroboration.json",
              "firing_report.series.kraken_usdc_close.min_print", None),
    # S-10. The regular text prints lag-1 autocorrelation 0.96 and an effective sample size
    # of 4.0. Those two do not reconcile: 212*(1-0.96)/(1+0.96) = 4.33. The results file
    # holds 0.9631, which gives 3.985 -> 4.0. The short version prints the precision that
    # reproduces its own printed figure; the regular version is frozen and keeps 0.96.
    "0.963": ("impossibility_window.json", "coins.USDC.independence.lag1_autocorr", None),
    # H2 T4.1. Section 3's cross-check sentence was rewritten to read the cross-check off
    # Kraken's close and VWAP rather than off its hourly low, because the low's minimum
    # rests on one trade. That put the VWAP minimum in print for the first time.
    "0.893": ("trough_corroboration.json",
              "firing_report.series.kraken_usdc_vwap.min_print", None),
    # H2 T4.1, same sentence: the size of the single trade the $0.874 low rests on. The
    # regular paper prints the same trade as its NOTIONAL ($2,948); the short paper prints
    # it as a coin count, so the literal differs while the trade does not. Printed as a
    # whole number of coins, so this one TRUNCATES (3373.52 -> 3,373) rather than rounds.
    "3373": ("trough_corroboration.json",
             "below_floor_depth.min_print_trade_volume_usdc", "trunc"),
    # H2 T6. Figure 3's Kraken-close zero crossing: the impairment at which that feed's
    # trough stops being unrationalizable, i.e. 1 - min_print as a percentage. The
    # composite's twin of this number (12.3%) is already in the regular paper; this one
    # is new because the regular paper has no share-vs-phi figure to need it.
    "9.9": ("trough_corroboration.json",
            "firing_report.series.kraken_usdc_close.min_print", "pct_below_par"),
    # J2. Section 4's cash-leg paragraph. The regular paper says the update does not state
    # how the cash remainder splits across banks; the frozen capture of that same update
    # states the split in full, so the short paper prints the itemisation instead. These
    # four are the figures that itemisation puts in print, and the regular paper has none
    # of them because it never makes the claim.
    #
    # The BNY Mellon leg, as the capture words it.
    "5.4": ("phi_cash_leg_scenarios.json", "for_print.bny_mellon_usd_bn", None),
    # Every deposit outside the custodian treated as impaired: (3.3 + 1.0) / 42.1.
    "0.102": ("phi_cash_leg_scenarios.json",
              "for_print.outside_custodian_phi_reserve_composition", None),
    # The lower edge still standing at that impairment, composite and Kraken VWAP. The
    # third feed, Kraken's close, returns no verdict there, which is the point of the
    # sentence and the reason no third number appears.
    "17.2": ("phi_cash_leg_scenarios.json",
             "for_print.outside_custodian_composite_edge_pct_reserve_composition", None),
    "4.7": ("phi_cash_leg_scenarios.json",
            "for_print.outside_custodian_vwap_edge_pct_reserve_composition", None),
    # J3. The same scenario read against the OTHER denominator, the $40B the disclosure
    # itself used. It matters because it is the harsher of the two: there only the
    # composite still returns a verdict, so the sentence has to name both bases or it
    # overstates how many feeds survive the worst case.
    "0.1075": ("phi_cash_leg_scenarios.json",
               "for_print.outside_custodian_phi_headline_40B", None),
    "12.8": ("phi_cash_leg_scenarios.json",
             "for_print.outside_custodian_composite_edge_pct_headline_40B", None),
    # J2. Section 4 used to explain the three hours in which only Kraken's hourly low
    # breaches as resting on one thin trade. They do not: those hours carry thousands of
    # sub-floor trades, and the thin print is in a different hour that breaches on every
    # convention. The executed depth replaces the claim, which is also what the frozen
    # record instructs -- the minimum print is one trade and is not the headline.
    "37303": ("subfloor_depth_live.json", "live.n_trades", None),
    # The notional those trades cleared. Printed in millions to one decimal, because a
    # nine-digit figure in prose reads as false precision next to a trade count.
    "154.0": ("subfloor_depth_live.json", "live.notional_usd", "usd_millions"),
    # J3. Tether 2019's impairment range, at FOUR decimals. At three it contradicts the
    # floor range printed in the same sentence: 1 - 0.259 = 0.741, but the floor prints
    # 0.742, because it rounds from 0.7415. Four decimals make the identity check out on
    # the page. Both resolve through the flat block specificity_panel.py now emits,
    # because the underlying readings sit in a LIST that a dotted key cannot address.
    "0.2585": ("specificity_panel.json", "for_print.tether2019_phi_lo", None),
    "0.3324": ("specificity_panel.json", "for_print.tether2019_phi_hi", None),
    # J3, from the cold read. The cent gaps printed beside this range are computed from
    # the unrounded floors: 0.9551 - 0.742 is 21.3, not the 21.4 in print. At the same
    # precision as the impairment range the subtraction checks out on the page.
    "0.6676": ("specificity_panel.json", "for_print.tether2019_floor_lo", None),
    "0.7415": ("specificity_panel.json", "for_print.tether2019_floor_hi", None),
}

# Numbers a LATER session will print in the short paper that do not occur in the regular
# paper, pre-verified here so that session only has to move an entry across rather than
# re-establish its provenance.
#
# These are NOT in AUTHORIZED_DIVERGENCE, and must not be added to it until they are
# actually printed: that allow-list is gated at both ends, and an entry for an unprinted
# number fails test_each_authorized_divergence_is_still_printed_and_still_resolves. What is
# checked here instead is that each key RESOLVES to the stated value in its results file --
# the provenance half of the gate, available early. Delete an entry when it graduates.
#
# printed literal -> (results file, dotted key, expected value)
PENDING_DIVERGENCE = {
    "0.1071": ("phi_cash_leg_scenarios.json", "for_print.crossing_phi_kraken_vwap", 0.10714),
    "169898078": ("subfloor_depth_live.json", "live.volume_usdc", 169898078.33),
}

PAPER = PKG_DIR.parent / "paper"
REG_SECTIONS = PAPER / "fc27" / "sections"
SHORT_SECTIONS = PAPER / "fc27short" / "sections"
SHORT_TEX = PAPER / "fc27short" / "fc27short.tex"

# Arguments that carry digits which are names, not quantities.
_OPAQUE = re.compile(
    r"\\(?:cite|ref|eqref|label|input|includegraphics|url|path|bibliography"
    r"|bibliographystyle|documentclass|usepackage|newlength|newcommand"
    r"|PassOptionsToPackage|setlength|settowidth|pdftrailerid)\s*(?:\[[^\]]*\])?\{[^}]*\}")
# Typesetting dimensions and column specs: 1.5em, 0mu, p{2.1cm}, 0.93\linewidth, 12.2cm.
_DIMEN = re.compile(r"-?\d*\.?\d+\s*(?:em|ex|pt|mu|cm|mm|in|\\linewidth|\\textwidth)")
_COLSPEC = re.compile(r"[pmb]\{[^}]*\}")
_COMMENT = re.compile(r"(?<!\\)%.*$", re.M)

# A numeric literal as the manuscript writes one: 0.877, 1233, 12.3, 1{,}233, 8.
_NUMBER = re.compile(r"\d[\d{},.]*\d|\d")


def _numbers(text: str) -> set[str]:
    text = _COMMENT.sub(" ", text)
    text = _OPAQUE.sub(" ", text)
    text = _COLSPEC.sub(" ", text)
    text = _DIMEN.sub(" ", text)
    out = set()
    for tok in _NUMBER.findall(text):
        tok = tok.replace("{,}", "").replace(",", "")
        tok = tok.rstrip(".")
        if tok:
            out.add(tok)
    return out


def _read(paths) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in paths)


def test_every_number_in_the_short_paper_occurs_in_the_regular_paper():
    if not SHORT_SECTIONS.exists():
        pytest.skip("short version not present on this branch")
    if not REG_SECTIONS.exists():
        pytest.skip("regular paper not in this tree; containment is a cross-check "
                    "against it, and AUTHORIZED_DIVERGENCE carries the provenance")
    short_files = sorted(SHORT_SECTIONS.glob("*.tex"))
    assert short_files, "paper/fc27short/sections/ has no .tex files"

    short_src = _read(short_files)
    if SHORT_TEX.exists():
        # The skeleton carries the figure and table captions, which are prose the
        # assembly wrote rather than a writer, and are just as able to carry an
        # invented number.
        short_src += "\n" + SHORT_TEX.read_text(encoding="utf-8")

    # The regular paper's generated table fragments are part of the regular paper, and
    # several figures the short version legitimately carries are printed only there --
    # share_range.tex, for instance, is where 19.2% and 35.1% appear. Reading only
    # sections/ made the corpus too narrow and reported a verbatim carry as an invention.
    regular_src = _read(sorted(REG_SECTIONS.glob("*.tex"))
                         + sorted((REG_SECTIONS.parent / "tables").glob("*.tex")))

    short_nums = _numbers(short_src)
    regular_nums = _numbers(regular_src)

    invented = sorted(short_nums - regular_nums - set(AUTHORIZED_DIVERGENCE),
                      key=lambda s: (len(s), s))
    assert not invented, (
        f"{len(invented)} number(s) printed in the short paper do not occur anywhere in "
        f"the regular paper, so they are not carried verbatim and have no provenance: "
        f"{invented}"
    )


def test_each_authorized_divergence_is_still_printed_and_still_resolves():
    """The allow-list is the one hole in containment, so it is gated at both ends.

    A number stops being an authorized divergence the moment it stops being printed (the
    entry is then stale and hides a future invention) or stops matching the results key it
    names (the entry is then a licence to print anything).
    """
    if not SHORT_SECTIONS.exists():
        pytest.skip("short version not present on this branch")
    short_nums = _numbers(_read(sorted(SHORT_SECTIONS.glob("*.tex"))))

    for printed, (filename, key, transform) in sorted(AUTHORIZED_DIVERGENCE.items()):
        assert printed in short_nums, (
            f"{printed} is allow-listed as an authorized divergence but the short paper no "
            f"longer prints it; delete the entry rather than leaving the hole open"
        )
        path = RESULTS / filename
        assert path.exists(), f"{printed}: results file {filename} is missing"
        found = _resolve(json.loads(path.read_text(encoding="utf-8")), key)
        assert found is not None, f"{printed}: {filename} has no key {key!r}"

        # Some printed figures are a stated transform of the stored value rather than the
        # value itself. The transform is named in the entry so it is reviewable, and is
        # applied here rather than being left implicit in a comment.
        value = float(found)
        if transform == "pct_below_par":
            value = (1.0 - value) * 100.0
        elif transform == "usd_millions":
            value = value / 1e6
        elif transform == "trunc":
            assert str(int(value)) == printed, (
                f"{printed} no longer truncates from {filename}:{key} = {found}"
            )
            continue
        elif transform is not None:
            raise AssertionError(f"{printed}: unknown transform {transform!r}")

        # The printed figure is the (transformed) stored value rounded to the precision
        # printed. An integer literal is rounded to zero decimals.
        decimals = len(printed.split(".")[1]) if "." in printed else 0
        assert round(value, decimals) == float(printed), (
            f"{printed} no longer rounds from {filename}:{key} = {found}"
            + (f" (transform {transform})" if transform else "")
        )


def _resolve(doc, key):
    """Walk a dotted key path through a results document.

    A path, not a search: `min_print` occurs under six different series in
    trough_corroboration.json, and a search would have bound Kraken's close to the
    composite's trough without complaining.
    """
    node = doc
    for segment in key.split("."):
        if not isinstance(node, dict) or segment not in node:
            return None
        node = node[segment]
    return node


# Keys the SHORT paper cites and the regular paper does not, each with the reason it is
# allowed to be one-sided. The invariant the test below enforces is that a short-paper
# citation has been through the regular paper's bibliography verification; an entry here
# is a statement that this particular key was verified some other way, named.
SHORT_ONLY_CITE_KEYS = {
    "Eichengreen_2025":
        "J3. Nearest neighbour to q*, added at the short paper's contribution boundary. "
        "Record content-negotiated from its DOI (10.1080/1351847X.2025.2505757), not typed.",
    "defillama2026api":
        "J3. The price aggregator the frozen snapshot was built from. Title is the "
        "documentation page's own <title>, URL the address the fetch resolved to.",
    "kraken2026api":
        "J3. Same, for the fiat-settled cross-check feed.",
    "coinbase2026exchangeapi":
        "J3. Same, for the USDT-USD premium series.",
    "nyag2019tether":
        "J3. The 2019 release the Tether episode is assessed on. Every field is read off "
        "the frozen capture in data/n1/tether2019/, which the manifest hashes.",
}


def test_short_paper_cites_only_keys_the_regular_paper_cites():
    """Same argument, for citations. A key the regular paper never cites has not been
    through its bibliography verification."""
    if not SHORT_SECTIONS.exists():
        pytest.skip("short version not present on this branch")
    if not REG_SECTIONS.exists():
        pytest.skip("regular paper not in this tree; this is a cross-check against it")
    cite_re = re.compile(r"\\cite\{([^}]*)\}")

    def keys(text):
        out = set()
        for group in cite_re.findall(_COMMENT.sub(" ", text)):
            out.update(k.strip() for k in group.split(",") if k.strip())
        return out

    short_keys = keys(_read(sorted(SHORT_SECTIONS.glob("*.tex"))))
    regular_keys = keys(_read(sorted(REG_SECTIONS.glob("*.tex"))))
    unknown = sorted(short_keys - regular_keys - set(SHORT_ONLY_CITE_KEYS))
    assert not unknown, (
        f"short paper cites key(s) the regular paper does not: {unknown}"
    )


def test_short_only_cite_keys_are_all_still_cited_and_still_one_sided():
    """The allowlist is gated at both ends, like AUTHORIZED_DIVERGENCE.

    An entry that stops being cited is stale and hides the next unverified citation; an
    entry the regular paper has since picked up needs no exemption and should go.
    """
    if not SHORT_SECTIONS.exists():
        pytest.skip("short version not present on this branch")
    cite_re = re.compile(r"\\cite\{([^}]*)\}")

    def keys(text):
        out = set()
        for group in cite_re.findall(_COMMENT.sub(" ", text)):
            out.update(k.strip() for k in group.split(",") if k.strip())
        return out

    short_keys = keys(_read(sorted(SHORT_SECTIONS.glob("*.tex"))))
    # The "still cited by the short paper" half needs no second manuscript and stays
    # enforced everywhere; only the one-sidedness check reads the regular paper.
    regular_keys = (keys(_read(sorted(REG_SECTIONS.glob("*.tex"))))
                    if REG_SECTIONS.exists() else None)
    for key, reason in sorted(SHORT_ONLY_CITE_KEYS.items()):
        assert reason.strip(), f"{key}: allowlisted with no reason"
        assert key in short_keys, (
            f"{key} is allowlisted as short-paper-only but the short paper no longer "
            f"cites it; delete the entry rather than leaving the hole open"
        )
        if regular_keys is not None:
            assert key not in regular_keys, (
                f"{key} is now cited by the regular paper too, so its exemption is "
                f"obsolete"
            )


def test_pending_divergences_resolve_to_their_results_keys():
    """Provenance for numbers a later session will print, verified before it prints them.

    This is the half of the allow-list gate that can be checked early. The other half --
    that the number is actually printed -- is deliberately NOT asserted here, because
    these are not printed yet.
    """
    for printed, (filename, key, expected) in sorted(PENDING_DIVERGENCE.items()):
        path = RESULTS / filename
        assert path.exists(), f"{printed}: results file {filename} is missing"
        found = _resolve(json.loads(path.read_text(encoding="utf-8")), key)
        assert found is not None, f"{printed}: {filename} has no key {key!r}"
        assert float(found) == pytest.approx(expected, rel=1e-9), (
            f"{printed}: {filename}:{key} is {found}, expected {expected}"
        )


def test_pending_divergences_are_not_already_allow_listed():
    """An entry that graduates must MOVE, not be duplicated in both registries."""
    overlap = set(PENDING_DIVERGENCE) & set(AUTHORIZED_DIVERGENCE)
    assert not overlap, (
        f"{sorted(overlap)} appear in both PENDING_DIVERGENCE and AUTHORIZED_DIVERGENCE; "
        f"delete the pending entry when it graduates"
    )


def test_pending_divergences_are_still_absent_from_the_regular_paper():
    """If a pending number turns up in the regular paper, it needs no allow-listing at
    all and the entry should simply be deleted."""
    if not REG_SECTIONS.exists():
        pytest.skip("regular paper not in this tree; this is a cross-check against it")
    regular_nums = _numbers(_read(sorted(REG_SECTIONS.glob("*.tex"))
                                  + sorted((REG_SECTIONS.parent / "tables").glob("*.tex"))))
    for printed in sorted(PENDING_DIVERGENCE):
        assert printed not in regular_nums, (
            f"{printed} now occurs in the regular paper, so it is free to print and its "
            f"PENDING_DIVERGENCE entry is obsolete"
        )
