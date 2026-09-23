"""figstyle.py: module imports cleanly; the palette is Okabe-Ito; the coin map is
fixed at the values G1's cohesion spec pins; save_figure is deterministic on a demo
figure. Synthetic data only -- this file never reads a real results/*.json."""
import hashlib
import tempfile
from pathlib import Path

import matplotlib
import pytest

import figstyle as fs


@pytest.fixture(autouse=True)
def _restore_global_rcparams():
    """apply_rcparams() mutates matplotlib's GLOBAL rcParams (plt.rcParams.update),
    which persists across test FILES, not just test functions, because pytest runs
    the whole suite in one process. FOUND BY RUNNING THE FULL SUITE, not just this
    file: without this fixture, a test here that calls apply_rcparams() (usetex=True)
    leaked into tests/test_figures.py's tests running afterward in the same pytest
    process, and figures.py's own captions contain literal unescaped '$' characters
    that are harmless under ITS OWN default rcParams (mathtext) but crash a real
    `latex` subprocess once usetex=True is active -- turning a style-module test into
    a cross-file regression in a sibling test file this one does not own. Snapshot
    rcParams before each test in this file and restore them after, regardless of
    pass/fail."""
    snapshot = dict(matplotlib.rcParams)
    yield
    matplotlib.rcParams.update(snapshot)

# Okabe-Ito, the full 8-colour set (Okabe & Ito 2008 / Wong 2011, the standard
# reference every figstyle.PALETTE / PALETTE_RESERVED_UNUSED value must come from).
OKABE_ITO = {
    "#000000", "#E69F00", "#56B4E9", "#009E73",
    "#F0E442", "#0072B2", "#D55E00", "#CC79A7",
}


def test_module_imports_and_exposes_the_documented_api():
    for name in ("apply_rcparams", "PALETTE", "PALETTE_RESERVED_UNUSED", "COIN_STYLE",
                 "COIN_ORDER", "FEED_STYLE", "REFERENCE_STYLE", "EVENT_STYLE",
                 "EVENT_LETTERS", "MARGIN_CONNECTOR_STYLE", "FLOOR_RANGE_BAR_STYLE",
                 "PANEL_LABEL_STYLE", "SIZES", "new_full_two_panel", "new_half_height",
                 "new_full_single", "plot_coin_series", "add_floor_line", "add_par_line",
                 "add_breach_shading", "add_event_markers", "add_panel_label",
                 "add_legend_above", "add_shared_legend", "compute_price_ylim",
                 "set_price_axis", "save_figure", "to_grayscale",
                 "simulate_deuteranopia", "simulate_protanopia"):
        assert hasattr(fs, name), f"figstyle.py is missing documented API: {name}"


def test_palette_is_okabe_ito():
    # Every ROLE colour actually used (not the placeholder greys reserved for
    # de-emphasis/chrome, which are deliberately NOT part of the 8-colour set) must
    # be a real Okabe-Ito value.
    hue_roles = {"usdc", "dai", "usdt"}
    for role in hue_roles:
        assert fs.PALETTE[role].upper() in OKABE_ITO, (
            f"PALETTE[{role!r}] = {fs.PALETTE[role]!r} is not an Okabe-Ito colour")
    # The four reserved-unused colours are also real Okabe-Ito values, and together
    # with the three hue roles account for exactly the non-black six chromatic hues.
    for hex_code in fs.PALETTE_RESERVED_UNUSED:
        assert hex_code.upper() in OKABE_ITO, (
            f"PALETTE_RESERVED_UNUSED contains a non-Okabe-Ito colour: {hex_code!r}")
    used = {fs.PALETTE[r].upper() for r in hue_roles}
    reserved = {c.upper() for c in fs.PALETTE_RESERVED_UNUSED}
    assert used.isdisjoint(reserved), "a colour is both assigned to a role AND reserved-unused"
    assert used | reserved | {"#000000"} == OKABE_ITO, (
        "used + reserved + black should account for the full 8-colour Okabe-Ito set "
        "exactly once each -- if this fails, a colour was silently dropped or duplicated")
    # F0E442 (yellow) is explicitly never used for a line at any width (G1 C1) --
    # confirm it landed in the reserved set, not assigned to a role.
    assert "#F0E442" in reserved, "yellow must be reserved, never assigned to a coin/role"


def test_coin_style_map_is_fixed():
    """Pins the exact G1 C1 cohesion-spec values. A change here is a change to the
    paper's visual identity and must not happen silently."""
    assert set(fs.COIN_STYLE) == {"USDC", "DAI", "USDT"}
    assert fs.COIN_ORDER == ("USDC", "DAI", "USDT")

    usdc = fs.COIN_STYLE["USDC"]
    assert usdc["linestyle"] == "-"
    assert usdc["linewidth"] == 1.2
    assert usdc["color"] == "#0072B2"

    dai = fs.COIN_STYLE["DAI"]
    assert dai["linestyle"] == (0, (4, 1.5))
    assert dai["linewidth"] == 1.0
    assert dai["color"] == "#D55E00"

    usdt = fs.COIN_STYLE["USDT"]
    assert usdt["linestyle"] == (0, (1, 1.5))
    assert usdt["linewidth"] == 1.0
    assert usdt["color"] == "#009E73"

    # No two coins may share a colour or a linestyle -- redundant-channel separation
    # (G1 C1) requires both to distinguish every pair.
    colors = [v["color"] for v in fs.COIN_STYLE.values()]
    styles = [v["linestyle"] for v in fs.COIN_STYLE.values()]
    assert len(set(colors)) == 3, "two coins share a colour"
    assert len(set(styles)) == 3, "two coins share a linestyle"


def test_event_letters_are_fixed_and_uppercase():
    """G1 C4: event letters are uppercase, assigned once, and distinct from the
    lowercase panel-letter scheme."""
    assert set(fs.EVENT_LETTERS) == {"A", "B", "C", "D"}
    for letter in fs.EVENT_LETTERS:
        assert letter.isupper() and len(letter) == 1


def test_add_event_markers_rejects_an_unknown_letter():
    """A figure may never invent its own event letter (G1 C4) -- this must fail
    loudly, not silently draw an unlabelled or mislabelled line."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots()
    try:
        raised = False
        try:
            fs.add_event_markers(ax, [("Z", 0.5)])
        except KeyError:
            raised = True
        assert raised, "add_event_markers silently accepted an unknown letter 'Z'"
    finally:
        plt.close(fig)


def test_save_figure_is_deterministic_on_a_demo_figure(tmp_path=None):
    """Builds a trivial SYNTHETIC figure (no real results/*.json read) twice, in two
    independent calls, and asserts both produce byte-identical PDFs -- the same
    property save_figure's own internal double-render probe checks, verified here
    from the outside as a regression guard on that mechanism itself."""
    import matplotlib
    matplotlib.use("Agg")
    import numpy as np

    def _build_and_save(out_dir: Path) -> dict:
        fs.apply_rcparams()
        fig, ax = fs.new_half_height()
        x = np.linspace(0, 1, 20)
        fs.plot_coin_series(ax, x, {
            "USDC": 1.0 - 0.05 * x, "DAI": 1.0 - 0.04 * x, "USDT": np.ones_like(x)})
        fs.add_floor_line(ax, 0.95)
        fs.add_par_line(ax)
        return fs.save_figure(fig, "test_demo_determinism", out_dir=out_dir)

    with tempfile.TemporaryDirectory() as td1, tempfile.TemporaryDirectory() as td2:
        res1 = _build_and_save(Path(td1))
        res2 = _build_and_save(Path(td2))
        assert res1["sha256"] == res2["sha256"], (
            "two independent builds of an identical synthetic figure produced "
            "different PDF bytes")
        # Sanity: the hash matches what's actually on disk, not just what
        # save_figure's return value claims.
        on_disk_hash = hashlib.sha256(Path(res1["path"]).read_bytes()).hexdigest()
        assert on_disk_hash == res1["sha256"]


def test_sizes_match_current_figure_ruling():
    """Pins the exact graphic heights the current figure ruling sets -- a silent
    drift here would desync the style module from the ruling it implements.
    full_two_panel's height moved 6.4 -> 8.6cm under the ruling that superseded
    the figure-design plan's original number; this test tracks the ruling, not
    the superseded plan."""
    assert fs.SIZES["full_two_panel"]["height_cm"] == 8.6
    assert fs.SIZES["half_height"]["height_cm"] == 4.0
    assert abs(fs.LNCS_WIDTH_CM - 12.2) < 1e-9
    assert abs(fs.LNCS_WIDTH_IN - 12.2 / 2.54) < 1e-9
