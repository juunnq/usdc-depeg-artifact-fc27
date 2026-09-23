"""Manifest preservation, a keyless data_fetch re-run must never drop provenance
for files owned by other modules (redemptions, DAI backing, Terra, USDT premium)."""
import json

from constants import ONCHAIN_PRESENT, RESULTS_DIR
from data_fetch import OWNED_FILES as OWNED
from data_fetch import ORIGINAL_NINE_FILES as _ORIGINAL_NINE
from data_fetch import preserve_foreign_entries

MANIFEST = RESULTS_DIR.parent / "data" / "MANIFEST.json"

# R2 fix: OWNED and _ORIGINAL_NINE used to be literal sets re-declared here,
# duplicating (and able to silently desync from) the actual filenames the
# producing modules use. Both are now imported from data_fetch.py, which
# derives _ORIGINAL_NINE from each producing module's own output-filename
# constant (redemptions.REDEMPTIONS_FILE, dai_contagion.BACKING_FILE,
# second_event.TERRA_FILE, usdt_premium.USDT_USD_FILE) rather than
# re-declaring the nine names as a fresh literal.


def _fresh_manifest():
    return {"frozen_utc": "test", "window_utc": [], "sources": [], "files": {}}


def test_all_existing_file_entries_survive_a_refresh():
    existing = json.load(open(MANIFEST))
    manifest = _fresh_manifest()
    # simulate the refresh having just written its own five files
    for name in OWNED:
        manifest["files"][name] = {"sha256": "new", "bytes": 1}
    preserve_foreign_entries(manifest, existing, OWNED)
    # every file the prior manifest knew about is still present
    assert set(existing["files"]) <= set(manifest["files"])
    # The invariant this test exists to protect is that a refresh must not DROP a
    # foreign entry, i.e. monotonicity, asserted on the line above. It previously
    # also pinned an exact count of 9, which was correct when written (7 originals +
    # 2 excluded-coin evidence files) and became FALSE the moment the N1 data freeze
    # legitimately added n1/ entries: the manifest now tracks 13, so a pristine clone
    # ran this suite RED at the exact step Appendix C names as the reproduction path
    # (found by the N7 adversarial pass, ruling C6; tracked as R2). An exact-count
    # assertion on a collection the design expects to GROW tests the wrong thing --
    # it cannot distinguish a legitimate freeze from a regression. Assert the real
    # invariant instead: the originals survive, and nothing is lost.
    # redemptions_by_wallet.csv is one of the original nine, and it is deliberately
    # absent from the short paper's artifact along with the rest of the on-chain
    # attribution package. Expect the nine minus what this tree does not ship.
    expected_nine = set(_ORIGINAL_NINE)
    if not ONCHAIN_PRESENT:
        expected_nine -= {"redemptions_by_wallet.csv"}
    assert set(existing["files"]) >= expected_nine
    assert set(manifest["files"]) >= expected_nine
    assert len(manifest["files"]) >= len(existing["files"])


def test_foreign_sources_carried_and_owned_sources_not_duplicated():
    existing = json.load(open(MANIFEST))
    manifest = _fresh_manifest()
    preserve_foreign_entries(manifest, existing, OWNED)
    carried = {s.get("file") for s in manifest["sources"]}
    foreign = {s.get("file") for s in existing.get("sources", [])} - OWNED
    assert foreign <= carried          # everything foreign survives
    assert not (carried & OWNED)       # nothing owned is duplicated from the old run


def test_foreign_hashes_unchanged_by_refresh():
    existing = json.load(open(MANIFEST))
    manifest = _fresh_manifest()
    preserve_foreign_entries(manifest, existing, OWNED)
    for name, meta in existing["files"].items():
        if name not in OWNED:
            assert manifest["files"][name] == meta
