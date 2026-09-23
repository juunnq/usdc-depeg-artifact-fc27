"""The manuscript carries no em dash and no prose semicolon.

That is the writing standard in review/prose_audit_prompt.md, categories 10 and 11. Before
G5b the source had 97 em dashes and 65 semicolons, so this is a real constraint that was
not being met rather than a property that happened to hold.

Two test groups:

  * The scanner's own masking is tested on controlled input FIRST. A scanner that counts
    wrongly is worse than no scanner, and this one has to be right about three specific
    things: `\\;` is a math thin space and not punctuation, `--` between numbers is a
    correct en dash and not a defect, and `\\$` is a literal dollar sign in a paper full
    of prices and does not open math mode. Each of those, counted naively, produces a
    confident wrong answer rather than an obvious failure.

  * Then the manuscript itself.
"""
import sys
from pathlib import Path

import pytest

from constants import PKG_DIR

sys.path.insert(0, str(PKG_DIR.parent / "tools"))
import punct_pass as pp  # noqa: E402


# (source line, {kind: expected count})
MASKING_CASES = [
    ("A plain sentence with no defects.", {}),
    ("An em dash---right here.", {"em dash": 1}),
    ("Two em dashes---like---this.", {"em dash": 2}),
    (r"A range of $19$--$35\%$ is fine.", {}),
    (r"Yields ($4.81$--$5.01\%$) are a range.", {}),
    ("A prose semicolon; here.", {"semicolon": 1}),
    (r"Math thin space $a \; b$ is not a semicolon.", {}),
    (r"Displayed: \begin{equation} x \; y; z \end{equation}", {}),
    (r"Prices \$0.92 and \$0.88 with a semicolon; between.", {"semicolon": 1}),
    ("% a comment with --- and ; in it", {}),
    ("Text % trailing comment with --- and ;", {}),
    (r"Escaped \% percent then a real semicolon; here.", {"semicolon": 1}),
    ("A spaced en dash -- doing an em dash's job.", {"spaced en dash": 1}),
    (r"\path{a_b}; after a path.", {"semicolon": 1}),
    ("Inline $x; y$ math semicolon is masked.", {}),
]


@pytest.mark.parametrize("src,expected", MASKING_CASES, ids=range(len(MASKING_CASES)))
def test_scanner_masking(src, expected, tmp_path):
    f = tmp_path / "case.tex"
    f.write_text(src + "\n", encoding="utf-8")
    got: dict[str, int] = {}
    for _n, kind, _s in pp.findings(f):
        got[kind] = got.get(kind, 0) + 1
    assert got == expected, f"{src!r}: expected {expected}, got {got}"


def _manuscript_hits() -> list[str]:
    out = []
    for path in pp.scan_files():
        rel = path.relative_to(pp.REPO_ROOT).as_posix()
        for n, kind, src in pp.findings(path):
            out.append(f"{rel}:{n}  {kind}: {src[:90]}")
    return out


def test_manuscript_has_no_em_dash_or_prose_semicolon():
    hits = _manuscript_hits()
    assert not hits, (
        f"{len(hits)} punctuation defect(s) against the writing standard "
        f"(no em dash, no prose semicolon, no spaced en dash doing an em dash's job).\n"
        + "\n".join(hits[:40])
        + ("\n  ... and more" if len(hits) > 40 else "")
    )


# Control characters written by a mangled backslash. A LaTeX command whose backslash is
# lost to an escape becomes a control byte plus a plain word: \ref -> CR + "ef{...}",
# \texttt -> TAB + "exttt{...}". G6 shipped three of these into the rendered PDF.
#
# The build cannot catch it, and in one direction the build gets QUIETER: destroying a
# \ref removes the reference entirely, so the undefined-reference count goes DOWN. The
# only things that see it are a byte scan and reading the rendered text.
CONTROL_CHARS = {
    "\t": r"TAB (a lost \t, as in \texttt)",
    "\x0b": r"VT (a lost \v, as in \vspace)",
    "\x0c": r"FF (a lost \f, as in \footnote)",
    "\x08": r"BS (a lost \b, as in \begin)",
    "\x00": "NUL",
    "\x07": r"BEL (a lost \a)",
}


def test_no_control_characters_in_tex_sources():
    r"""No stray control byte in any .tex file or fragment.

    CR is handled separately from the set above: these files carry CRLF line endings on
    Windows, so a bare "no \r" assertion fires on every line. A CR followed by LF is an
    ordinary line ending; any other CR is a lost backslash.
    """
    bad = []
    for path in pp.all_tex_files():
        rel = path.relative_to(pp.REPO_ROOT).as_posix()
        text = path.read_bytes().decode("utf-8", errors="replace")
        for i, ch in enumerate(text):
            if ch == "\r":
                if i + 1 < len(text) and text[i + 1] == "\n":
                    continue
                name = r"CR (a lost \r, as in \ref)"
            elif ch in CONTROL_CHARS:
                name = CONTROL_CHARS[ch]
            else:
                continue
            ctx = text[max(0, i - 45):i + 45].replace("\r", "<CR>").replace("\t", "<TAB>")
            bad.append(f"{rel} offset {i}: {name}\n    ...{ctx}...")
    assert not bad, (
        f"{len(bad)} control character(s) in the LaTeX sources. A backslash was lost "
        f"crossing a tool boundary (shell heredoc, re.sub template, sed).\n"
        + "\n".join(bad[:12])
    )


def test_no_macro_fragment_reaches_the_rendered_pdf():
    """The rendered-text half of the same check.

    A byte scan catches the control character. It says nothing about whether the damaged
    macro's tail is now printing as ordinary words, which is what a reader actually sees.
    """
    pdf = pp.REPO_ROOT / "paper" / "fc27" / "fc27.pdf"
    if not pdf.exists():
        pytest.skip("fc27.pdf absent; build first (python tools/build_fc27.py)")
    try:
        import fitz
    except ImportError:  # pragma: no cover
        pytest.skip("PyMuPDF not available")
    text = "".join(page.get_text() for page in fitz.open(pdf))
    stray = ["exttt{", "extbf{", "extit{", "extsc{", "mph{", "ef{", "ite{", "abel{",
             "egin{", "nd{", "aption{"]
    found = sorted({s for s in stray if s in text})
    assert not found, (
        f"macro fragment(s) visible in the rendered PDF: {found}. A LaTeX command lost "
        f"its backslash and is printing as words."
    )
