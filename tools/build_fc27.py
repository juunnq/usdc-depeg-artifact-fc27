#!/usr/bin/env python3
"""The exact fc27 build recipe, as a script rather than hand-maintained Makefile
recipe text -- no fc27 build script existed before this one, the submission
artifact was produced ad hoc from a hand-maintained Makefile recipe.

Steps, in order:
  1. Regenerate every figure (usdc_depeg/figures.py's main()) and every table
     fragment (usdc_depeg/tables.py's main()) -- tables.py already writes its
     fragments straight to paper/fc27/tables/ (figures.TABLES_DIR), so no copy
     step is needed for those; figures.py writes to usdc_depeg/figs/, which DOES
     need copying into paper/fc27/figs/ (LaTeX only sees the latter).
  2. Read every `\\includegraphics{figs/NAME.pdf}` target out of fc27.tex and cross-
     check it against what step 1 actually produced -- FAILS LOUDLY (raises,
     nonzero exit) if fc27.tex cites a figure this run didn't produce, rather than
     silently building from whatever stale copy happens to already sit in
     paper/fc27/figs/. This is the fix for a real defect found in G3b: the OLD
     Makefile's fc27-figs target named two figure files by hand, and when the
     figure set changed, neither the Makefile nor the build itself noticed that
     paper/fc27/figs/ still held pre-rename copies -- `make fc27` kept succeeding,
     silently shipping figures nobody was generating any more.
  3. Copy exactly the cross-checked set into paper/fc27/figs/, and DELETE any
     other *.pdf already sitting there (a stale figure an earlier build left
     behind, that no current \\includegraphics target names) -- the other half of
     the same fix: a stale file surviving alongside a correct one is just as
     capable of shipping silently as a stale file being the only one present.
  4. pdflatex -> bibtex -> pdflatex x3, with SOURCE_DATE_EPOCH pinned (the fixed
     constant this project already uses, not derived from `git log` -- see this
     script's own SOURCE_DATE_EPOCH constant for why) and \\pdftrailerid{} already
     in fc27.tex's own preamble pinning the trailer /ID -- together these are what
     make repeated builds of the same source byte-identical.

Usage:
    python tools/build_fc27.py             # full build
    python tools/build_fc27.py --no-figs   # skip step 1 (figures.py/tables.py),
                                            # use whatever's already on disk --
                                            # for iterating on the .tex alone
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PKG_DIR = REPO_ROOT / "usdc_depeg"


class Target:
    """One buildable paper. Two exist: the regular 15-page submission and the 8-page
    short-paper variant, which are separate submissions judged under different CFP
    criteria and so are built from separate source trees rather than from one source
    with a flag. Both use the identical deterministic recipe below, which is the point
    of routing them through one script: a second hand-maintained recipe is exactly the
    drift this script was written to end."""

    def __init__(self, name: str, dirname: str, stem: str):
        self.name = name
        self.dir = REPO_ROOT / "paper" / dirname
        self.stem = stem

    @property
    def tex(self) -> Path:
        return self.dir / f"{self.stem}.tex"

    @property
    def figs(self) -> Path:
        return self.dir / "figs"

    @property
    def pdf(self) -> Path:
        return self.dir / f"{self.stem}.pdf"

    @property
    def log(self) -> Path:
        return self.dir / f"{self.stem}.log"


TARGETS = {
    "regular": Target("regular", "fc27", "fc27"),
    "short": Target("short", "fc27short", "fc27short"),
}
REGULAR = TARGETS["regular"]

# Kept as names because this module's docstring and several review documents refer to
# them; they are the regular target's paths.
FC27_DIR = REGULAR.dir
FC27_TEX = REGULAR.tex
FC27_FIGS_DIR = REGULAR.figs

sys.path.insert(0, str(PKG_DIR))

# Fixed constant, deliberately not derived from `git log` -- keying it to the last
# commit would make the PDF hash change on every commit, which defeats recording a
# submission hash. 1789603200 = 2026-09-17T00:00:00Z. Matches the value this
# project's paper/Makefile fc27 target has used throughout (kept in sync by hand
# until this script existed; this script is now the one place it needs to change).
SOURCE_DATE_EPOCH = "1789603200"

INCLUDEGRAPHICS_RE = re.compile(r"\\includegraphics(?:\[[^\]]*\])?\{figs/([^}]+?)\.pdf\}")


def regenerate_figures_and_tables() -> dict[str, str]:
    """Runs figures.py's and tables.py's own main() IN-PROCESS (imported, not
    subprocessed) so this script sees each figure builder's real return value
    rather than re-parsing stdout. Returns {stem: sha256} for every figure
    produced this run -- tables.py's fragments need no equivalent return, they
    already land at their final destination (figures.TABLES_DIR)."""
    import figures
    import tables

    print("[1/4] regenerating figures and tables ...")
    produced = figures.main()
    tables.main()

    stems: dict[str, str] = {}
    for name, result in produced:
        stem = Path(result["path"]).stem
        stems[stem] = result["sha256"]
    return stems


INPUT_RE = re.compile(r"\\input\{([^}]+)\}")


def expanded_source(tgt: Target = REGULAR) -> str:
    """fc27.tex with every \\input resolved, depth-first, in document order.

    G5b split the manuscript into paper/fc27/sections/*.tex, so fc27.tex is now a
    skeleton of \\input lines and cites no figure of its own. Scanning fc27.tex
    alone therefore found zero \\includegraphics targets and this script refused to
    build -- correctly, but for the wrong reason. Everything downstream that asks
    "what does the manuscript actually say" has to read the expansion, not the
    skeleton. Table fragments under tables/ are \\input too and are resolved by the
    same pass, which costs nothing and keeps one notion of "the source"."""
    seen: set[Path] = set()

    def expand(path: Path) -> str:
        path = path.resolve()
        if path in seen or not path.exists():
            # A missing target is LaTeX's error to report, not this script's, and a
            # cycle would otherwise hang the build.
            return ""
        seen.add(path)
        out = []
        for line in path.read_text(encoding="utf-8").splitlines():
            out.append(line)
            for m in INPUT_RE.finditer(re.sub(r"(?<!\\)%.*$", "", line)):
                rel = m.group(1)
                if not rel.endswith(".tex"):
                    rel += ".tex"
                out.append(expand(tgt.dir / rel))
        return "\n".join(out)

    return expand(tgt.tex)


def includegraphics_targets(tgt: Target = REGULAR) -> list[str]:
    """Every figure stem the manuscript actually cites, in document order,
    de-duplicated but order-preserving (a figure could in principle be included
    twice). Reads the \\input-expanded source, not the skeleton alone."""
    seen: list[str] = []
    for m in INCLUDEGRAPHICS_RE.finditer(expanded_source(tgt)):
        stem = m.group(1)
        if stem not in seen:
            seen.append(stem)
    return seen


def sync_figs(produced_stems: dict[str, str], tgt: Target = REGULAR) -> None:
    """Copy exactly the cited, this-run-produced figures into the target's figs/;
    delete every other *.pdf already there. Raises loudly (does not warn-and-continue)
    if a cited stem was not produced this run -- see this module's own docstring for
    why that used to fail silently instead."""
    print("[2/4] cross-checking \\includegraphics targets against this run's figures ...")
    targets = includegraphics_targets(tgt)
    if not targets:
        raise RuntimeError(f"{tgt.tex} has no \\includegraphics{{figs/...}} targets at "
                            f"all -- that itself is almost certainly wrong, refusing to "
                            f"build a paper with no figures without an explicit override")

    missing = [t for t in targets if t not in produced_stems]
    if missing:
        raise RuntimeError(
            f"{tgt.tex.name} cites figure(s) this run of figures.py did NOT produce: "
            f"{missing!r}. Produced this run: {sorted(produced_stems)!r}. This is "
            f"exactly the defect this script exists to catch loudly rather than "
            f"silently build from a stale {tgt.figs.name}/ copy -- fix the "
            f"\\includegraphics target, or add the missing figure to figures.py's "
            f"main() list, before building."
        )

    FC27_FIGS_DIR = tgt.figs
    FC27_FIGS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"      {len(targets)} target(s): {targets}")
    for stem in targets:
        src = PKG_DIR / "figs" / f"{stem}.pdf"
        dst = FC27_FIGS_DIR / f"{stem}.pdf"
        shutil.copy2(src, dst)
        print(f"      copied {src.relative_to(REPO_ROOT)} -> {dst.relative_to(REPO_ROOT)}")

    stale = [p for p in FC27_FIGS_DIR.glob("*.pdf") if p.stem not in targets]
    for p in stale:
        p.unlink()
        print(f"      deleted stale {p.relative_to(REPO_ROOT)} (no current "
              f"\\includegraphics target names it)")


def run_pdflatex_sequence(tgt: Target = REGULAR) -> None:
    print("[3/4] pdflatex -> bibtex -> pdflatex x3 (SOURCE_DATE_EPOCH pinned) ...")
    import os
    env = dict(os.environ)
    env["SOURCE_DATE_EPOCH"] = SOURCE_DATE_EPOCH

    def run(cmd: list[str]) -> None:
        print(f"      $ {' '.join(cmd)}  (cwd={tgt.dir})")
        r = subprocess.run(cmd, cwd=tgt.dir, env=env, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"{' '.join(cmd)} failed (exit {r.returncode}):\n"
                                f"STDOUT (tail):\n{r.stdout[-3000:]}\n"
                                f"STDERR (tail):\n{r.stderr[-3000:]}")

    run(["pdflatex", "-interaction=nonstopmode", tgt.stem])
    run(["bibtex", tgt.stem])
    run(["pdflatex", "-interaction=nonstopmode", tgt.stem])
    run(["pdflatex", "-interaction=nonstopmode", tgt.stem])
    run(["pdflatex", "-interaction=nonstopmode", tgt.stem])


def check_log(tgt: Target = REGULAR) -> dict:
    """Precise greps against fc27.log, not a bare 'build succeeded' assumption --
    pdflatex exits 0 even with undefined references or overfull boxes."""
    log = tgt.log.read_text(encoding="utf-8", errors="replace")
    import fitz
    doc = fitz.open(tgt.pdf)
    n_pages = len(doc)
    # Text set outside the page box, measured from the PDF rather than inferred from
    # the log. A float taller than \textheight is set anyway, so its overflow runs past
    # the bottom edge and is invisible in any renderer while still being present in the
    # content stream -- pdftotext finds it, a reader never sees it. That happened to the
    # last row of Appendix C's claim map, and survived three sessions: "Float too large
    # for page" is a warning, and this gate counted only errors, undefined references,
    # and overfull hboxes. The log line is also counted below, but this is the real
    # check, because it catches text leaving the page for any reason.
    offpage = 0
    for page in doc:
        H, W = page.rect.height, page.rect.width
        for b in page.get_text("blocks"):
            if b[3] > H + 0.5 or b[1] < -0.5 or b[2] > W + 0.5 or b[0] < -0.5:
                offpage += 1
    doc.close()
    return {
        "pages": n_pages,
        "undefined_refs": len(re.findall(
            r"There were undefined references|Citation .* undefined|"
            r"Reference .* undefined", log)),
        "multiply_defined": log.count("multiply defined"),
        "table_too_wide": log.count("TABLE TOO WIDE"),
        "overfull_hbox": len(re.findall(r"Overfull \\hbox", log)),
        # Badness, not just a count. A sub-point overfull is invisible on the page; a
        # 15pt one runs into the margin and a reviewer sees it. Gating on the count
        # alone would either fail every build (this document has four 0.6pt table cells)
        # or, if tuned around them, fail none. The threshold is what makes it a gate.
        "overfull_hbox_over_2pt": len([
            x for x in re.findall(r"Overfull \\hbox \(([\d.]+)pt too wide\)", log)
            if float(x) > 2.0]),
        "overfull_hbox_worst_pt": max(
            [float(x) for x in
             re.findall(r"Overfull \\hbox \(([\d.]+)pt too wide\)", log)] or [0.0]),
        # Vertical overflow has no tolerance: any overfull vbox means material is
        # running past the bottom of its box, which is the class of defect that put a
        # claim-map row off the physical page for three sessions.
        "overfull_vbox": len(re.findall(
            r"Overfull \\vbox \(([\d.]+)pt too (?:high|deep)\)", log)),
        "float_too_large": len(re.findall(r"Float too large for page", log)),
        "offpage_text": offpage,
    }


def build_one(tgt: Target, produced_stems: dict[str, str]) -> int:
    """Steps 2 to 4 for one target. Step 1 is shared: figures.py and tables.py are run
    once per invocation no matter how many targets are built, because both write one
    canonical set that every target draws from."""
    if not tgt.tex.exists():
        # The full paper is no longer tracked -- only the short paper is submitted -- so
        # a clean clone has paper/fc27short/ and nothing else. Say that, rather than
        # failing several steps later inside pdflatex with a missing-file error.
        raise SystemExit(
            f"{tgt.tex.relative_to(REPO_ROOT)} is not in this tree, so the "
            f"'{tgt.name}' target cannot be built. Only the short paper is tracked: "
            f"build it with `python tools/build_fc27.py --target short`.")
    print(f"=== target: {tgt.name} ({tgt.tex.relative_to(REPO_ROOT)}) ===")
    sync_figs(produced_stems, tgt)
    run_pdflatex_sequence(tgt)

    print("[4/4] checking the build log ...")
    report = check_log(tgt)
    for k, v in report.items():
        print(f"      {k}: {v}")

    ok = (report["undefined_refs"] == 0 and report["multiply_defined"] == 0
          and report["table_too_wide"] == 0 and report["offpage_text"] == 0
          and report["float_too_large"] == 0
          and report["overfull_hbox_over_2pt"] == 0
          and report["overfull_vbox"] == 0)
    print()
    if ok:
        print(f"Build OK: {tgt.pdf} ({report['pages']} pages)")
        return 0
    print(f"Build of {tgt.name} completed but the log has problems -- see the counts "
          f"above.", file=sys.stderr)
    return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-figs", action="store_true",
                     help="skip regenerating figures/tables; use what's on disk")
    ap.add_argument("--target", choices=["regular", "short", "both"], default="regular",
                     help="which paper to build (default: regular). 'short' is the "
                          "8-page short-paper variant under paper/fc27short/, a separate "
                          "submission judged under a different CFP criterion. Both use "
                          "the identical deterministic recipe.")
    args = ap.parse_args()

    if args.no_figs:
        print("[1/4] SKIPPED (--no-figs) -- using whatever figures already exist "
              "under usdc_depeg/figs/")
        produced_stems = {p.stem: None for p in (PKG_DIR / "figs").glob("*.pdf")}
    else:
        produced_stems = regenerate_figures_and_tables()

    names = ["regular", "short"] if args.target == "both" else [args.target]
    rc = 0
    for name in names:
        rc |= build_one(TARGETS[name], produced_stems)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
