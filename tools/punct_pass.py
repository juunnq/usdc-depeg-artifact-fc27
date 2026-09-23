#!/usr/bin/env python3
"""Find every em dash and every prose semicolon in the manuscript.

The writing standard this paper is held to (review/prose_audit_prompt.md, categories 10
and 11) allows neither. Counting them by eye does not work, and counting them with a naive
search fails in three specific ways this scanner exists to avoid:

  1. An em dash in LaTeX source is `---`, not the Unicode character. Searching a source
     file for U+2014 returns ZERO on a file containing 97 of them.

  2. An EN dash is `--` and is CORRECT in a numeric range (19--35, 4.81--5.01). A scanner
     that looks for `--` flags every range in the paper as a defect. Em dashes are found
     first and masked before anything else is considered.

  3. `\\;` in math is a thin space, not punctuation. This manuscript has four, inside
     displayed equations in the bound section. Counting them as semicolons produces four
     defects that cannot be fixed, because fixing them would break the math.

Math is masked before scanning, so a semicolon inside $...$ or an equation environment is
not reported. `\\$` is a literal dollar sign (this paper is full of prices) and does NOT
open math mode, which a regex-based masker gets wrong.

Also reported: a SPACED en dash between non-digits, which is an em dash wearing a
disguise. Four of those live in the generated table fragments.

Usage:
    python tools/punct_pass.py            # report, exit 1 if anything is found
    python tools/punct_pass.py --quiet    # counts only
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FC27_DIR = REPO_ROOT / "paper" / "fc27"

MATH_ENVS = ("equation", "align", "eqnarray", "gather", "multline", "displaymath")

# Content commands whose braces carry literal text, not prose punctuation.
VERBATIMISH = re.compile(r"\\(?:path|url|texttt|verb)\b\s*\{[^{}]*\}")


def scan_files() -> list[Path]:
    """fc27.tex, every section file and every table fragment, in document order.

    The regular paper only. The punctuation baseline this feeds is a count of the
    regular manuscript's own em and en dashes, so widening it would silently move a
    number that exists to be compared against a recorded figure.
    """
    out = [FC27_DIR / "fc27.tex"]
    out += sorted((FC27_DIR / "sections").glob("*.tex"))
    out += sorted((FC27_DIR / "tables").glob("*.tex"))
    return [p for p in out if p.exists()]


def all_tex_files() -> list[Path]:
    """Every .tex file in every paper, for checks that are about bytes rather than
    about one manuscript's style -- the control-character scan above all.

    A lost backslash is a build-corrupting defect wherever it lands, and the short
    paper's sections were written by separate agents, so they need the same scan the
    regular tree gets. Kept apart from scan_files() so the punctuation baseline stays
    a statement about one manuscript.
    """
    out = list(scan_files())
    short_dir = FC27_DIR.parent / "fc27short"
    if short_dir.exists():
        out.append(short_dir / "fc27short.tex")
        out += sorted((short_dir / "sections").glob("*.tex"))
        out += sorted((short_dir / "tables").glob("*.tex"))
    return [p for p in out if p.exists()]


def strip_comment(line: str) -> str:
    r"""Drop an unescaped % and everything after it. `\%` is a literal percent sign and
    keeps its tail, which matters because almost every number in this paper is one."""
    out = []
    i = 0
    while i < len(line):
        c = line[i]
        if c == "\\" and i + 1 < len(line):
            out.append(line[i:i + 2])
            i += 2
            continue
        if c == "%":
            break
        out.append(c)
        i += 1
    return "".join(out)


def mask_math(text: str) -> str:
    r"""Replace every math region with spaces, preserving length and newlines so that
    reported line and column numbers still point at the real source.

    Walks the string rather than pattern-matching, because `\$` is a literal dollar and a
    regex for `\$[^$]*\$` treats `\$0.92` ... `\$0.88` as a math span and masks the prose
    between two prices.
    """
    out = list(text)
    i, n = 0, len(text)
    in_math = False

    def blank(a: int, b: int) -> None:
        for k in range(a, b):
            if out[k] != "\n":
                out[k] = " "

    while i < n:
        if text[i] == "\\" and i + 1 < n:
            nxt = text[i + 1]
            if nxt in "$%&_#{}":          # escaped literal, never a delimiter
                i += 2
                continue
            if not in_math:
                m = re.match(r"\\begin\{(" + "|".join(MATH_ENVS) + r")\*?\}", text[i:])
                if m:
                    env = m.group(1)
                    end = re.search(r"\\end\{" + env + r"\*?\}", text[i:])
                    stop = i + (end.end() if end else n - i)
                    blank(i, stop)
                    i = stop
                    continue
            if nxt in "([":               # \( ... \)  and  \[ ... \]
                close = "\\)" if nxt == "(" else "\\]"
                j = text.find(close, i + 2)
                stop = (j + 2) if j != -1 else n
                blank(i, stop)
                i = stop
                continue
            i += 2
            continue
        if text[i] == "$":
            if not in_math:
                in_math = True
                start = i
                i += 1
                continue
            in_math = False
            blank(start, i + 1)
            i += 1
            continue
        i += 1
    return "".join(out)


#: characters that sit between a number and a dash without changing that it is a range:
#: math delimiters, braces, parens, a percent escape, whitespace.
_RANGE_NOISE = re.compile(r"[$\\%{}() ]")


def _is_numeric_range(plain: str, start: int, end: int) -> bool:
    r"""True if the `--` at [start, end) joins two numbers in the ORIGINAL line.

    Must be asked of the unmasked text. This paper writes ranges as `$19$--$35\%$` and
    `($4.81$--$5.01\%$)`, so by the time math is blanked the digits that prove it is a
    range are gone and every such range looks like a spaced en dash.
    """
    before = _RANGE_NOISE.sub("", plain[max(0, start - 14):start])
    after = _RANGE_NOISE.sub("", plain[end:end + 14])
    return bool(before[-1:].isdigit() and after[:1].isdigit())


def _is_empty_table_cell(line: str, start: int, end: int) -> bool:
    r"""True if the `--` IS a table cell, rather than punctuation inside one.

    `& -- \\` and `& -- &` are the standard way to write "not applicable" in a LaTeX
    table, and this manuscript uses it in four fragments. Reporting those as em dashes in
    disguise would be a false positive with a cost: the only way to "fix" them is to
    damage correct tables. A dash is a placeholder when the whole cell is just the dash.
    """
    before = line[:start].rstrip()
    after = line[end:].lstrip()
    opens = before.endswith("&") or before == "" or before.endswith(r"\\")
    closes = after.startswith("&") or after == "" or after.startswith(r"\\")
    return opens and closes


def findings(path: Path) -> list[tuple[int, str, str]]:
    """(line number, kind, the source line) for every defect in one file."""
    raw = path.read_text(encoding="utf-8")
    lines = raw.splitlines()
    plain_lines = [strip_comment(l) for l in lines]
    masked_doc = mask_math("\n".join(plain_lines))
    hits: list[tuple[int, str, str]] = []

    for n, masked in enumerate(masked_doc.splitlines(), 1):
        src = lines[n - 1].strip()
        plain = plain_lines[n - 1]
        masked = VERBATIMISH.sub(lambda m: " " * len(m.group(0)), masked)

        for _ in re.finditer(r"---", masked):
            hits.append((n, "em dash", src))

        # Mask em dashes before looking at en dashes, or every --- reports twice.
        no_em = masked.replace("---", "\x00\x00\x00")

        # A spaced en dash between non-digits is an em dash in disguise. A numeric range
        # (19--35) is correct and is not reported.
        for m in re.finditer(r"(?<!-)--(?!-)", no_em):
            if _is_numeric_range(plain, m.start(), m.end()):
                continue
            if _is_empty_table_cell(no_em, m.start(), m.end()):
                continue
            spaced = (m.start() > 0 and no_em[m.start() - 1] == " ") or \
                     (m.end() < len(no_em) and no_em[m.end()] == " ")
            if spaced:
                hits.append((n, "spaced en dash", src))

        for m in re.finditer(r";", no_em):
            if no_em[max(0, m.start() - 1):m.start()] == "\\":
                continue                    # \; is a math thin space
            hits.append((n, "semicolon", src))

    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true", help="counts only")
    args = ap.parse_args()

    total: dict[str, int] = {"em dash": 0, "semicolon": 0, "spaced en dash": 0}
    any_file = False

    for path in scan_files():
        hits = findings(path)
        if not hits:
            continue
        any_file = True
        rel = path.relative_to(REPO_ROOT).as_posix()
        if not args.quiet:
            print(f"\n{rel}")
            for n, kind, src in hits:
                print(f"  {n:>4}  {kind:<15} {src[:96]}")
        for _n, kind, _s in hits:
            total[kind] += 1

    print(f"\n{'=' * 70}")
    for kind in ("em dash", "semicolon", "spaced en dash"):
        print(f"  {kind:<16} {total[kind]}")
    grand = sum(total.values())
    print(f"  {'TOTAL':<16} {grand}")
    if not any_file:
        print("\nclean: no em dash, no prose semicolon, no spaced en dash")
    return 1 if grand else 0


if __name__ == "__main__":
    sys.exit(main())
