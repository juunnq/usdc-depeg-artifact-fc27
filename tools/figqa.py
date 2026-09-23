#!/usr/bin/env python3
"""QA harness for figures against the D22 rules figstyle.py implements. Every check
here is mechanical -- it inspects the SAVED ARTIFACT (the PDF's own bytes, or the live
matplotlib Figure object when the caller has one), never the plotting code's intent,
matching this project's standing rule that a style/config layer you did not fully
control can silently violate a constraint your call-site arguments look like they set
(established during the style-candidate evaluation; the research-brain note this
project follows is "a styling package can set a default your own call-site arguments
do not reach").

TWO MODES, by how much you can give it:
  - PDF-only (`--pdf path/to/fig.pdf`): works on ANY already-saved PDF, including
    figures this project's older figures.py built before figstyle.py existed. Runs
    every check it can from the bytes alone. In-axes-text detection and style
    conformance are HEURISTIC in this mode (see detect_axes_boxes / check_style_
    conformance_pdf_only below) -- good enough to catch the known violations in the
    current shipped figures (this is how the G2.3 baseline run was produced), not a
    substitute for the exact check.
  - PDF + live Figure object (import this module from the SAME process that built
    the figure, call run_qa(pdf_path, fig=fig, ...)): in-axes-text and style
    conformance become EXACT, checked against the actual matplotlib artists rather
    than reconstructed from PDF vector geometry.

Checks, each PASS/FAIL/SKIP with one reason:
  1. Rasterize at print width; produce grayscale + CVD-simulated (deuteranopia,
     protanopia) versions beside the raster (figstyle's own LMS simulation, reused
     here so there are not two independently-approximated implementations).
  2. Font-size floor: every text span >= MIN_FONT_PT (7pt) at the PDF's own declared
     size (not the screen-preview rcParam).
  3. In-axes text: any non-tick-label text artist whose position falls inside a
     detected/known axes data region.
  4. Style conformance: line styles/colours/spines/legend match figstyle's maps
     (exact with a Figure object; heuristic color-palette check PDF-only).
  5. Determinism: two independent renders (from a builder callable, or two given PDF
     paths) hash-identical.
  6. Height vs. a budget (cm) passed by the caller -- this module does not know the
     D21 budget itself, the caller states it.
  7. One PASS/FAIL report, printed and returned as a dict, with a one-line reason
     per check.

Usage:
    python tools/figqa.py --pdf usdc_depeg/figs/fig_11mar_panel.pdf --height-budget-cm 6.4
    python tools/figqa.py --pdf usdc_depeg/figs/demo_T1.pdf --height-budget-cm 6.4
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

import fitz  # PyMuPDF
import numpy as np
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "usdc_depeg"))
import figstyle as fs  # noqa: E402  (path insert must precede this import)

MIN_FONT_PT = fs.MIN_FONT_PT  # 7.0, single source of truth stays in figstyle
PT_PER_CM = 72.0 / 2.54


# ======================================================================================
# 1. Rasterize + grayscale + CVD
# ======================================================================================
def rasterize_and_simulate(pdf_path: Path, out_dir: Path, dpi: int = 300) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = pdf_path.stem
    doc = fitz.open(pdf_path)
    page = doc[0]
    pix = page.get_pixmap(dpi=dpi)
    raster_path = out_dir / f"{stem}_raster.png"
    pix.save(raster_path)

    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    rgb = arr[..., :3].astype(np.float64) / 255.0

    gray = fs.to_grayscale(rgb)
    gray_path = out_dir / f"{stem}_grayscale.png"
    Image.fromarray((gray * 255).clip(0, 255).astype(np.uint8), mode="L").save(gray_path)

    deut = fs.simulate_deuteranopia(rgb)
    deut_path = out_dir / f"{stem}_deuteranopia.png"
    Image.fromarray((deut * 255).clip(0, 255).astype(np.uint8)).save(deut_path)

    prot = fs.simulate_protanopia(rgb)
    prot_path = out_dir / f"{stem}_protanopia.png"
    Image.fromarray((prot * 255).clip(0, 255).astype(np.uint8)).save(prot_path)

    page_size_pt = [page.rect.width, page.rect.height]
    doc.close()
    return {"raster": str(raster_path), "grayscale": str(gray_path),
            "deuteranopia": str(deut_path), "protanopia": str(prot_path),
            "dpi": dpi, "page_size_pt": page_size_pt}


# ======================================================================================
# 2. Font-size floor
# ======================================================================================
def extract_text_spans(pdf_path: Path) -> list[dict]:
    """Every text span in the PDF with its declared font size (pt, the PDF's own
    number, not a screen-preview rcParam) and bbox in PDF point coordinates
    (origin top-left, y increases downward -- PyMuPDF convention)."""
    doc = fitz.open(pdf_path)
    spans = []
    for page_num, page in enumerate(doc):
        d = page.get_text("dict")
        for block in d.get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    spans.append({
                        "page": page_num,
                        "text": span["text"],
                        "size_pt": round(span["size"], 3),
                        "bbox": list(span["bbox"]),  # (x0, y0, x1, y1)
                    })
    doc.close()
    return spans


def check_min_font_size(spans: list[dict], min_pt: float = MIN_FONT_PT) -> dict:
    failing = [s for s in spans if s["text"].strip() and s["size_pt"] < min_pt - 1e-6]
    return {"check": "min_font_size", "pass": len(failing) == 0,
            "min_pt_required": min_pt,
            "min_pt_found": min(([s["size_pt"] for s in spans if s["text"].strip()]) or [None]),
            "failing_spans": failing[:20],  # cap: a report, not a dump
            "n_failing": len(failing)}


# ======================================================================================
# 3. In-axes text -- PDF-only heuristic (axes-box detection via spine-pairing)
# ======================================================================================
def detect_axes_boxes(pdf_path: Path, min_frac_w: float = 0.15,
                       min_frac_h: float = 0.10) -> list[dict]:
    """Best-effort: find axes data regions from vector line geometry, by pairing a
    long vertical segment (candidate left spine) with a long horizontal segment
    (candidate bottom spine) that share a near corner. Works well for matplotlib
    output (spines ARE literal straight line paths) and handles multiple panels by
    finding multiple disjoint corner pairs. Returns a list of
    {x0,y0,x1,y1} in PDF point coords (top-left origin), one per detected panel.
    Conservative by design: only pairs that share a corner within 2pt count, so a
    spurious long grid line alone does not fabricate a phantom axes box."""
    doc = fitz.open(pdf_path)
    page = doc[0]
    pw, ph = page.rect.width, page.rect.height
    draws = page.get_drawings()

    h_segs, v_segs = [], []
    for dr in draws:
        for item in dr["items"]:
            if item[0] != "l":
                continue
            p0, p1 = item[1], item[2]
            dx, dy = abs(p1.x - p0.x), abs(p1.y - p0.y)
            if dy < 0.5 and dx >= min_frac_w * pw:
                h_segs.append((round(min(p0.y, p1.y), 1),
                                round(min(p0.x, p1.x), 1), round(max(p0.x, p1.x), 1)))
            elif dx < 0.5 and dy >= min_frac_h * ph:
                v_segs.append((round(min(p0.x, p1.x), 1),
                                round(min(p0.y, p1.y), 1), round(max(p0.y, p1.y), 1)))
    doc.close()

    h_segs = sorted(set(h_segs))
    v_segs = sorted(set(v_segs))

    boxes = []
    seen = set()
    for vx, vy0, vy1 in v_segs:
        for hy, hx0, hx1 in h_segs:
            # corner match: vertical segment's bottom (vy1, larger y = lower on page,
            # PDF/PyMuPDF y grows downward) meets horizontal segment's left end (hx0)
            if abs(vx - hx0) <= 2.0 and abs(vy1 - hy) <= 2.0:
                box = (round(vx, 1), round(vy0, 1), round(hx1, 1), round(vy1, 1))
                if box not in seen and (box[2] - box[0]) > 0 and (box[3] - box[1]) > 0:
                    seen.add(box)
                    boxes.append({"x0": box[0], "y0": box[1], "x1": box[2], "y1": box[3]})
    return boxes


def classify_in_axes_text(spans: list[dict], axes_boxes: list[dict],
                           edge_margin_pt: float = 3.0) -> list[dict]:
    """A span is 'in-axes' if its bbox CENTER falls strictly inside a detected axes
    box, shrunk inward by edge_margin_pt on every side -- the margin is what excludes
    tick labels sitting right at the frame boundary. Axis labels sit further outside
    the frame than this margin and are also excluded by construction."""
    violations = []
    for span in spans:
        if not span["text"].strip():
            continue
        x0, y0, x1, y1 = span["bbox"]
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        for box in axes_boxes:
            bx0, by0 = box["x0"] + edge_margin_pt, box["y0"] + edge_margin_pt
            bx1, by1 = box["x1"] - edge_margin_pt, box["y1"] - edge_margin_pt
            if bx0 < cx < bx1 and by0 < cy < by1:
                violations.append(span)
                break
    return violations


def check_in_axes_text_pdf_only(pdf_path: Path) -> dict:
    spans = extract_text_spans(pdf_path)
    boxes = detect_axes_boxes(pdf_path)
    violations = classify_in_axes_text(spans, boxes)
    return {"check": "in_axes_text", "mode": "pdf_only_heuristic",
            "pass": len(violations) == 0,
            "axes_boxes_detected": boxes,
            "violations": [{"text": v["text"], "bbox": v["bbox"]} for v in violations],
            "n_violations": len(violations)}


def check_in_axes_text_live(fig) -> dict:
    """EXACT check against a live matplotlib Figure: for every ax in fig.axes, any
    Text artist in ax.texts (or ax.title) whose position (axes-fraction or data,
    converted to display coords) falls inside the axes' own window extent, EXCLUDING
    tick labels (ax.get_x/yticklabels are separate objects, never in ax.texts) and
    EXCLUDING panel labels placed via figstyle.add_panel_label at (0.0, 1.02) --
    which is axes-fraction y=1.02, i.e. OUTSIDE the [0,1] data region by construction,
    so it correctly passes without a special case."""
    violations = []
    fig.canvas.draw()
    for ax in fig.axes:
        bbox = ax.get_window_extent()
        for t in list(ax.texts) + ([ax.title] if ax.title.get_text() else []):
            if not t.get_text().strip():
                continue
            tx, ty = t.get_transform().transform(t.get_position())
            if bbox.x0 < tx < bbox.x1 and bbox.y0 < ty < bbox.y1:
                violations.append({"text": t.get_text(), "axes": str(ax)})
    return {"check": "in_axes_text", "mode": "live_figure", "pass": len(violations) == 0,
            "violations": violations, "n_violations": len(violations)}


# ======================================================================================
# 4. Style conformance
# ======================================================================================
def _line_signature(line) -> tuple:
    """(color, linewidth, dash_pattern) with dash_pattern read from the UNSCALED
    private attribute, not get_linestyle(). FOUND WHILE BUILDING THIS CHECK:
    get_linestyle() normalizes EVERY custom dash tuple -- (4,1.5), (1,1.5), and even
    the 4-element dash-dot (6,2,1,2) -- to the same string '--', so comparing against
    it cannot distinguish DAI's dashes from USDT's dots from the floor line's
    dash-dot; all three would read back identically. `_unscaled_dash_pattern`
    preserves the literal input tuple. This is a private matplotlib attribute (used
    here in QA tooling only, never in figstyle.py's own figure-building code, which
    is the module that actually has to keep working if a future matplotlib version
    removes it) with a defensive fallback to get_linestyle() if it's ever gone."""
    color = line.get_color()
    lw = round(line.get_linewidth(), 3)
    dash = getattr(line, "_unscaled_dash_pattern", None)
    if dash is None or dash == (0.0, None):
        dash = line.get_linestyle()  # fallback: still distinguishes solid from dashed
    elif isinstance(dash, tuple) and isinstance(dash[1], (list, tuple)):
        dash = (round(dash[0], 3), tuple(round(x, 3) for x in dash[1]))
    return (color, lw, dash)


def _known_style_signatures() -> dict:
    """Derive the 'known good' (color, linewidth, dash_pattern) signature for every
    NAMED style category figstyle.py defines (COIN_STYLE, REFERENCE_STYLE,
    EVENT_STYLE, MARGIN_CONNECTOR_STYLE, FEED_STYLE) by actually plotting each on a
    throwaway axes and reading its signature back the SAME way a candidate line is
    read -- rather than comparing against a hand-normalized guess of what
    matplotlib will report, which is exactly the mismatch that broke this check on
    its first run."""
    import matplotlib.pyplot as plt
    scratch_fig, scratch_ax = plt.subplots()
    sigs = {}
    for name, style in fs.COIN_STYLE.items():
        (line,) = scratch_ax.plot([0, 1], [0, 1], **{k: v for k, v in style.items()
                                                        if k != "marker" or v is not None})
        sigs[_line_signature(line)] = f"coin:{name}"
    for name, style in fs.REFERENCE_STYLE.items():
        line = scratch_ax.axhline(0.5, **{k: v for k, v in style.items() if k != "zorder"})
        sigs[_line_signature(line)] = f"reference:{name}"
    event_line = scratch_ax.axvline(0.5, **{k: v for k, v in fs.EVENT_STYLE.items()
                                             if k != "zorder"})
    sigs[_line_signature(event_line)] = "event_marker"
    for name, style in fs.MARGIN_CONNECTOR_STYLE.items():
        (line,) = scratch_ax.plot([0, 1], [0, 1], **style)
        sigs[_line_signature(line)] = f"margin_connector:{name}"
    (floor_range_line,) = scratch_ax.plot([0, 1], [0.5, 0.5], **fs.FLOOR_RANGE_BAR_STYLE)
    sigs[_line_signature(floor_range_line)] = "floor_range_bar"
    # De-emphasized variant: a floor-range bar recoloured to PALETTE['degenerate']
    # for a row whose connector is ALSO the de-emphasized "does_not_breach" grey --
    # the same role MARGIN_CONNECTOR_STYLE already names twice (breaches/
    # does_not_breach), applied to the range-bar style so a row's floor marker
    # never mismatches its own row's connector
    # colour. Every other FLOOR_RANGE_BAR_STYLE field (width, marker, linestyle)
    # is unchanged; only the PALETTE-role colour differs.
    degenerate_bar_style = dict(fs.FLOOR_RANGE_BAR_STYLE, color=fs.PALETTE["degenerate"])
    (degenerate_bar_line,) = scratch_ax.plot([0, 1], [0.5, 0.5], **degenerate_bar_style)
    sigs[_line_signature(degenerate_bar_line)] = "floor_range_bar:degenerate"
    # FEED_STYLE (G1 C2): a feed inherits its coin's colour with per-feed modifiers
    # layered on top (fs.coin_feed_style), not used by any figure when this
    # registry was first written -- now drawn by fig_event's panel (b) (Kraken
    # close/VWAP), so it needs its own signatures the same way every other named
    # category above does. "composite" is the coin's own unmodified style, already
    # covered by the COIN_STYLE loop above, so it is skipped here.
    for coin in fs.COIN_ORDER:
        for feed in fs.FEED_STYLE:
            if feed == "composite":
                continue
            style = fs.coin_feed_style(coin, feed)
            style = {k: v for k, v in style.items() if k != "marker_every_hours"}
            (line,) = scratch_ax.plot([0, 1], [0, 1], **{k: v for k, v in style.items()
                                                            if k != "marker" or v is not None})
            sigs[_line_signature(line)] = f"feed:{coin}:{feed}"
    # FEED_CURVE_STYLE: four readings of one derived quantity, separated by dash pattern
    # alone (all PALETTE["usdc"], because all four ARE USDC). Registered here for the
    # same reason every category above is -- the live check matches a drawn line's full
    # signature against this table, and an unregistered style matches nothing and is
    # reported as a violation even when it is the correct one.
    for name, style in fs.FEED_CURVE_STYLE.items():
        (line,) = scratch_ax.plot([0, 1], [0, 1], **style)
        sigs[_line_signature(line)] = f"feed_curve:{name}"
    plt.close(scratch_fig)
    return sigs


def check_style_conformance_live(fig) -> dict:
    """EXACT: walk every Line2D in every axes, compare its (color, linewidth,
    dash-pattern) signature against figstyle.COIN_STYLE / REFERENCE_STYLE's OWN
    signatures (derived by replaying those style dicts through matplotlib once --
    see _known_style_signatures -- so both sides go through identical normalization);
    check spines and legend frame. FAILS WITH THE FIRST MISMATCH, per the brief --
    returns immediately on the first violation found, in a fixed deterministic scan
    order (axes order, then line order, then spine order), rather than collecting
    every mismatch, so two runs against the same figure always report the same first
    failure."""
    known_styles = _known_style_signatures()

    for ax in fig.axes:
        for spine_name in ("top", "right"):
            sp = ax.spines.get(spine_name)
            if sp is not None and sp.get_visible() and spine_name != "top":
                # top spine may be legitimately visible when a secondary event axis
                # is attached (figstyle.add_event_markers) -- only right is a hard
                # FAIL unconditionally.
                return {"check": "style_conformance", "mode": "live_figure", "pass": False,
                        "first_mismatch": f"{ax}: spine '{spine_name}' is visible; "
                                           f"G1 C8 requires left+bottom only"}
        leg = ax.get_legend()
        if leg is not None and leg.get_frame_on():
            return {"check": "style_conformance", "mode": "live_figure", "pass": False,
                    "first_mismatch": f"{ax}: legend frame is on; figstyle sets "
                                       f"legend.frameon=False"}
        for line in ax.get_lines():
            if line.get_linestyle() in ("None", "none"):
                continue  # marker-only lines (e.g. Figure 2's floor/trough points)
                          # carry no dash pattern to check
            sig = _line_signature(line)
            if sig not in known_styles:
                return {"check": "style_conformance", "mode": "live_figure", "pass": False,
                        "first_mismatch": f"{ax}: line with signature {sig!r} matches "
                                           f"no entry in COIN_STYLE or REFERENCE_STYLE"}
    return {"check": "style_conformance", "mode": "live_figure", "pass": True,
            "first_mismatch": None}


def check_style_conformance_pdf_only(pdf_path: Path) -> dict:
    """BEST-EFFORT ONLY (no Figure object): extract distinct stroke colours used in
    vector line drawings and check each is within a small tolerance of an allowed
    colour (figstyle.PALETTE's values, plus pure black/white/mid-grey for
    chrome/grid). Cannot check line DASH PATTERN this way (PyMuPDF's get_drawings
    does not reliably expose the dash array for every backend path type), so this is
    a WEAKER check than the live-figure version -- report it as such, never as a
    clean PASS standing in for the exact check."""
    allowed = set(fs.PALETTE.values()) | {"#000000", "#ffffff", "#999999"}
    allowed_rgb = np.array([[int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)]
                             for h in allowed]) / 255.0

    doc = fitz.open(pdf_path)
    found_colors = set()
    for page in doc:
        for dr in page.get_drawings():
            c = dr.get("color")
            if c:
                found_colors.add(tuple(round(x, 3) for x in c))
    doc.close()

    unrecognized = []
    for c in found_colors:
        c_arr = np.array(c[:3]) if len(c) >= 3 else np.array([c[0]] * 3)
        dists = np.linalg.norm(allowed_rgb - c_arr, axis=1)
        if dists.min() > 0.08:  # loose tolerance: anti-aliasing / alpha blending
            unrecognized.append(list(c))

    return {"check": "style_conformance", "mode": "pdf_only_heuristic",
            "pass": len(unrecognized) == 0,
            "note": "PDF-only mode cannot check dash pattern, only stroke colour; "
                    "this is weaker evidence than the live-figure check",
            "colors_found": [list(c) for c in sorted(found_colors)],
            "unrecognized_colors": unrecognized}


# ======================================================================================
# 5. Determinism
# ======================================================================================
def check_determinism_two_paths(pdf_a: Path, pdf_b: Path) -> dict:
    ha = hashlib.sha256(Path(pdf_a).read_bytes()).hexdigest()
    hb = hashlib.sha256(Path(pdf_b).read_bytes()).hexdigest()
    return {"check": "determinism", "pass": ha == hb, "sha256_a": ha, "sha256_b": hb}


def check_determinism_builder(build_fn) -> dict:
    """build_fn() -> a fresh (fig, name) each call; used when this is run from the
    same process that can rebuild the figure. Delegates to figstyle.save_figure's own
    assert-based check (raises on mismatch), catching it into a report entry instead
    of propagating, so this harness always finishes and reports rather than crashing
    mid-QA-run."""
    import tempfile
    try:
        fig1, name = build_fn()
        with tempfile.TemporaryDirectory() as td:
            res = fs.save_figure(fig1, name, out_dir=Path(td))
        return {"check": "determinism", "mode": "builder", "pass": True,
                "sha256": res["sha256"]}
    except AssertionError as e:
        return {"check": "determinism", "mode": "builder", "pass": False, "error": str(e)}


# ======================================================================================
# 6. Height vs. budget
# ======================================================================================
def check_height(pdf_path: Path, budget_cm: float | None) -> dict:
    doc = fitz.open(pdf_path)
    h_pt = doc[0].rect.height
    w_pt = doc[0].rect.width
    doc.close()
    h_cm = h_pt / PT_PER_CM
    w_cm = w_pt / PT_PER_CM
    result = {"check": "height_budget", "height_cm": round(h_cm, 3),
              "width_cm": round(w_cm, 3)}
    if budget_cm is None:
        result["pass"] = None
        result["note"] = "no --height-budget-cm given; not checked"
    else:
        result["budget_cm"] = budget_cm
        result["pass"] = h_cm <= budget_cm + 1e-6
    return result


# ======================================================================================
# 7. Orchestrator
# ======================================================================================
def run_qa(pdf_path: Path, fig=None, height_budget_cm: float | None = None,
           out_dir: Path | None = None, compare_pdf: Path | None = None) -> dict:
    pdf_path = Path(pdf_path)
    out_dir = Path(out_dir) if out_dir else pdf_path.parent / "figqa_out"

    report = {"pdf": str(pdf_path), "checks": []}

    report["checks"].append({"check": "rasterize", "pass": True,
                              **rasterize_and_simulate(pdf_path, out_dir)})

    spans = extract_text_spans(pdf_path)
    report["checks"].append(check_min_font_size(spans))

    if fig is not None:
        report["checks"].append(check_in_axes_text_live(fig))
        report["checks"].append(check_style_conformance_live(fig))
    else:
        report["checks"].append(check_in_axes_text_pdf_only(pdf_path))
        report["checks"].append(check_style_conformance_pdf_only(pdf_path))

    if compare_pdf is not None:
        report["checks"].append(check_determinism_two_paths(pdf_path, compare_pdf))
    else:
        report["checks"].append({"check": "determinism", "pass": None,
                                  "note": "no --compare-pdf given; not checked in "
                                          "this invocation"})

    report["checks"].append(check_height(pdf_path, height_budget_cm))

    report["overall_pass"] = all(c.get("pass") is not False for c in report["checks"])
    return report


# ======================================================================================
# 8. TikZ lane
# ======================================================================================
# A TikZ figure is drawn by the manuscript's own compiler, not by matplotlib, so it never
# passes through figures.py and was invisible to every check above -- it would have been
# the only figure in the paper outside the QA loop (palette conformance, grayscale
# safety, height budget). This lane closes that by compiling the fragment on its own,
# cropping the page to the drawing, and running the checks that do not depend on a
# matplotlib Figure object: colour against the palette, grayscale legibility, the font
# floor, and the size budget.
TIKZ_WRAPPER = r"""\documentclass[runningheads]{llncs}
\usepackage{amsmath,amssymb}
\usepackage{tikz}
\usetikzlibrary{positioning,shapes.geometric,arrows.meta}
\pagestyle{empty}
\pdftrailerid{}
\begin{document}
\input{FRAGMENT}
\end{document}
"""


def _compile_tikz(fragment: Path, work: Path,
                  stem: str = "tikzprobe") -> tuple[Path, Path]:
    """Compile a bare tikzpicture fragment in an llncs document and crop to the drawing.

    Cropping matters: the check is on the GRAPHIC's dimensions, and an uncropped page is
    always 21.59 x 27.94 cm, which would make any height budget pass.
    """
    import os
    import subprocess

    work.mkdir(parents=True, exist_ok=True)
    (work / "frag.tex").write_text(fragment.read_text(encoding="utf-8"), encoding="utf-8")
    (work / f"{stem}.tex").write_text(
        TIKZ_WRAPPER.replace("FRAGMENT", "frag"), encoding="utf-8")

    env = dict(os.environ)
    env["SOURCE_DATE_EPOCH"] = "1789603200"   # same pin the manuscript build uses
    r = subprocess.run(["pdflatex", "-interaction=nonstopmode", stem],
                       cwd=work, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    pdf = work / f"{stem}.pdf"
    if not pdf.exists():
        raise RuntimeError(f"TikZ fragment did not compile:\n{r.stdout[-2000:]}")

    doc = fitz.open(pdf)
    page = doc[0]
    xs, ys = [], []
    for dr in page.get_drawings():
        rect = dr["rect"]
        xs += [rect.x0, rect.x1]
        ys += [rect.y0, rect.y1]
    for blk in page.get_text("dict")["blocks"]:
        if blk.get("type") == 0:
            x0, y0, x1, y1 = blk["bbox"]
            xs += [x0, x1]
            ys += [y0, y1]
    if not xs:
        raise RuntimeError("no drawing or text found in the compiled fragment")
    page.set_cropbox(fitz.Rect(min(xs), min(ys), max(xs), max(ys)))
    cropped = work / f"{stem}_cropped.pdf"
    doc.save(str(cropped))
    doc.close()
    # Both paths are returned because they answer different questions. The CROPPED file
    # is the artifact to measure and rasterise. The RAW file is the one to hash for
    # determinism: \pdftrailerid{} pins pdflatex's trailer /ID, but PyMuPDF mints a
    # fresh /ID whenever it re-saves, so hashing the cropped copy would report this
    # harness's own non-determinism as the figure's.
    return cropped, pdf


def check_size(pdf_path: Path, budget_cm: float | None,
               max_width_cm: float = 12.20) -> dict:
    doc = fitz.open(pdf_path)
    r = doc[0].rect
    doc.close()
    w_cm, h_cm = r.width / PT_PER_CM, r.height / PT_PER_CM
    ok_w = w_cm <= max_width_cm + 0.02
    ok_h = True if budget_cm is None else h_cm <= budget_cm + 0.02
    return {"check": "size_budget", "pass": ok_w and ok_h,
            "width_cm": round(w_cm, 2), "height_cm": round(h_cm, 2),
            "max_width_cm": max_width_cm, "budget_cm": budget_cm,
            "note": f"{w_cm:.2f} x {h_cm:.2f} cm against "
                    f"{max_width_cm} x {budget_cm if budget_cm else '-'} cm"}


def check_grayscale_separation(pdf_path: Path, min_delta: float = 0.10) -> dict:
    """Every distinct stroke colour must stay distinguishable once flattened to gray.

    The matplotlib lane leans on the line-style map for this; a schematic has no line
    styles to lean on, so colour separation carries it alone. Two hues that collapse to
    the same gray are a grayscale-print defect even when both are on-palette.
    """
    doc = fitz.open(pdf_path)
    colors = set()
    for page in doc:
        for dr in page.get_drawings():
            for key in ("color", "fill"):
                c = dr.get(key)
                if c and len(c) >= 3:
                    colors.add(tuple(round(x, 3) for x in c[:3]))
    doc.close()

    grays = {}
    for c in colors:
        g = float(fs.to_grayscale(np.array(c, dtype=np.float64).reshape(1, 1, 3))[0, 0])
        grays[c] = round(g, 4)

    collisions = []
    items = sorted(grays.items(), key=lambda kv: kv[1])
    for (c1, g1), (c2, g2) in zip(items, items[1:]):
        if abs(g1 - g2) < min_delta:
            collisions.append({"a": list(c1), "b": list(c2),
                               "gray_a": g1, "gray_b": g2,
                               "delta": round(abs(g1 - g2), 4)})
    return {"check": "grayscale_separation", "pass": not collisions,
            "min_delta_required": min_delta,
            "n_colors": len(colors),
            "grays": {str(k): v for k, v in grays.items()},
            "collisions": collisions,
            "note": f"{len(colors)} distinct colour(s), "
                    f"{len(collisions)} pair(s) closer than {min_delta} in gray"}


def check_min_font_size_tikz(spans: list[dict], primary_floor: float = MIN_FONT_PT,
                             script_floor: float = 5.9) -> dict:
    """The font floor, split by who chose the size.

    The matplotlib lane's flat 7pt floor does not transfer to LaTeX. In a tikzpicture the
    author picks ONE base size; TeX then derives math script sizes from it and snaps them
    to the font family's discrete design sizes. At \\small (8.97pt here) a subscript lands
    on Computer Modern's 6pt face, measuring 5.98pt, and the only way to lift it is to
    raise the base -- which at this width is not available.

    Applying 7pt flatly would also condemn the manuscript itself: its body sets
    $P_{\\mathrm{fund}}$ subscripts at 6.97pt and the $q^{\\star}$ superscript at 5.98pt.
    A figure check that fails the surrounding prose is measuring the wrong thing.

    So: enforce the real floor on PRIMARY (author-chosen) text, and hold derived script
    text to the smallest size the manuscript already prints. Both are reported.
    """
    spans = [s for s in spans if s["text"].strip()]
    if not spans:
        return {"check": "min_font_size", "pass": False, "note": "no text spans found"}
    sizes = [round(s["size_pt"], 2) for s in spans]
    primary_size = Counter(sizes).most_common(1)[0][0]
    # A span is script-derived if it is materially below the primary size; TeX's
    # scriptstyle ratio is ~0.7, so anything under 0.85x cannot be the base face.
    primary = [s for s in spans if round(s["size_pt"], 2) >= 0.85 * primary_size]
    script = [s for s in spans if round(s["size_pt"], 2) < 0.85 * primary_size]

    bad_primary = [s for s in primary if s["size_pt"] < primary_floor - 0.05]
    bad_script = [s for s in script if s["size_pt"] < script_floor - 0.05]
    return {"check": "min_font_size", "mode": "tikz_split",
            "pass": not bad_primary and not bad_script,
            "primary_size_pt": primary_size,
            "primary_floor_pt": primary_floor,
            "script_floor_pt": script_floor,
            "n_primary": len(primary), "n_script": len(script),
            "min_primary_pt": round(min((s["size_pt"] for s in primary), default=0.0), 3),
            "min_script_pt": round(min((s["size_pt"] for s in script), default=0.0), 3),
            "n_failing": len(bad_primary) + len(bad_script),
            "note": f"primary {primary_size}pt (floor {primary_floor}), "
                    f"{len(script)} TeX-derived script span(s) at "
                    f"{round(min((s['size_pt'] for s in script), default=0.0), 2)}pt "
                    f"(floor {script_floor}, the manuscript's own minimum)"}


def run_qa_tikz(fragment_path: Path, height_budget_cm: float | None = None,
                out_dir: Path | None = None, work_dir: Path | None = None) -> dict:
    """The TikZ lane. Same contract as run_qa: a dict with per-check PASS/FAIL."""
    fragment_path = Path(fragment_path)
    out_dir = Path(out_dir) if out_dir else fragment_path.parent / "figqa_out"
    work_dir = Path(work_dir) if work_dir else out_dir / "_tikzwork"

    cropped, raw = _compile_tikz(fragment_path, work_dir)
    report = {"pdf": str(fragment_path), "lane": "tikz",
              "compiled": str(cropped), "checks": []}

    report["checks"].append({"check": "rasterize", "pass": True,
                             **rasterize_and_simulate(cropped, out_dir)})
    report["checks"].append(check_min_font_size_tikz(extract_text_spans(cropped)))
    report["checks"].append(check_style_conformance_pdf_only(cropped))
    report["checks"].append(check_grayscale_separation(cropped))
    report["checks"].append(check_size(cropped, height_budget_cm))

    # Determinism: recompile into a second workspace and compare the RAW compiler output
    # (see _compile_tikz's closing note on why not the cropped copy).
    _, raw2 = _compile_tikz(fragment_path, work_dir.with_name(work_dir.name + "2"))
    report["checks"].append(check_determinism_two_paths(raw, raw2))

    report["overall_pass"] = all(c.get("pass") is not False for c in report["checks"])
    return report


def print_report(report: dict) -> None:
    print(f"\n=== figqa: {report['pdf']} ===")
    for c in report["checks"]:
        status = "PASS" if c.get("pass") is True else (
            "FAIL" if c.get("pass") is False else "SKIP")
        reason = c.get("first_mismatch") or c.get("note") or ""
        if c["check"] == "min_font_size" and not c["pass"]:
            reason = (f"{c['n_failing']} span(s) below {c['min_pt_required']}pt "
                       f"(min found {c['min_pt_found']}pt)")
        if c["check"] == "in_axes_text" and not c["pass"]:
            reason = f"{c['n_violations']} text span(s) inside the axes data region"
        if c["check"] == "style_conformance" and c.get("unrecognized_colors"):
            reason = f"{len(c['unrecognized_colors'])} unrecognized colour(s)"
        if c["check"] == "height_budget":
            reason = f"{c['height_cm']}cm" + (
                f" vs budget {c['budget_cm']}cm" if "budget_cm" in c else "")
        print(f"  [{status}] {c['check']:20s} {reason}")
    print(f"  OVERALL: {'PASS' if report['overall_pass'] else 'FAIL'}\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", type=Path)
    ap.add_argument("--tikz", type=Path,
                    help="a bare tikzpicture fragment; runs the TikZ lane instead")
    ap.add_argument("--height-budget-cm", type=float, default=None)
    ap.add_argument("--compare-pdf", type=Path, default=None)
    ap.add_argument("--out-dir", type=Path, default=None)
    ap.add_argument("--json-out", type=Path, default=None)
    args = ap.parse_args()

    if not args.pdf and not args.tikz:
        ap.error("one of --pdf or --tikz is required")
    if args.tikz:
        report = run_qa_tikz(args.tikz, height_budget_cm=args.height_budget_cm,
                             out_dir=args.out_dir)
    else:
        report = run_qa(args.pdf, height_budget_cm=args.height_budget_cm,
                        out_dir=args.out_dir, compare_pdf=args.compare_pdf)
    print_report(report)
    if args.json_out:
        args.json_out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return 0 if report["overall_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
