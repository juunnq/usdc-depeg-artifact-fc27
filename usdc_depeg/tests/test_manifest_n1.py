"""N1 data freeze, pin every MANIFEST entry added since the N1 freeze began against
the actual file on disk: existence, SHA-256, byte size where recorded, row count where
recorded, and UTC coverage where recorded. Does not touch or re-verify the pre-existing
entries (that is a git-diff check at the reconciliation gate, not a pytest concern) and
never fetches anything, offline, read-only against data/ already frozen."""
import csv
import hashlib
import json

from constants import ONCHAIN_PRESENT, DATA_DIR

MANIFEST = DATA_DIR / "MANIFEST.json"

# Discriminates entries added during the N1 freeze from the pre-existing
# (2026-06/2026-07) freeze. Both timestamp field names appear in the manifest
# depending on which fetch wrote the entry, some used "retrieval_utc", others (matching the pre-existing
# convention) used "retrieved_utc".
_N1_STAMP_PREFIX = "2026-09"


def _load_manifest():
    with open(MANIFEST, encoding="utf-8") as f:
        return json.load(f)


def _stamp(entry):
    return entry.get("retrieval_utc") or entry.get("retrieved_utc") or ""


def _new_entries():
    return [s for s in _load_manifest()["sources"] if _stamp(s).startswith(_N1_STAMP_PREFIX)]


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _row_count(path):
    """CSV -> data rows excluding header. JSON object -> top-level key count.
    JSON array -> element count."""
    if path.suffix == ".csv":
        with open(path, encoding="utf-8", newline="") as f:
            return sum(1 for _ in csv.reader(f)) - 1
    if path.suffix == ".json":
        data = json.load(open(path, encoding="utf-8"))
        return len(data)
    raise AssertionError(f"no row-counting rule for extension {path.suffix} ({path})")


def _hour_utc_bounds(path):
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows, f"{path} has a header but zero data rows -- cannot check coverage"
    hours = [r["hour_utc"] for r in rows]
    return min(hours), max(hours)


def test_at_least_one_new_entry_present():
    # Sanity floor: if this is ever 0, the discriminator prefix or the freeze itself
    # is broken, fail loudly rather than have every other test in this file
    # vacuously pass over an empty list.
    assert len(_new_entries()) > 0


def test_new_entry_count_pinned():
    # Regression pin, entries added since the N1 freeze began, in order:
    # 19 original sources
    # + 22 in the initial N1 freeze pass (2: a superseded partial Kraken USDT/USD pull
    #   whose sha256 no longer matched the file after the resumed complete pull
    #   overwrote it in place, was removed during reconciliation, leaving 22 net)
    # + 1 in a follow-up freeze pass (the appellate opinion in the same NYAG matter,
    #   froze context but not the target affirmation, that gap stayed open)
    # + 24 in a later follow-up pass (the NYAG press release, Tether's response post,
    #   and 22 daily Wayback captures of the transparency page, a dollar-amount
    #   angle on the same still-open Tether-affirmation gap)
    # + 1 in a subsequent pass (hourly block anchors, 240 rows, fetched via public
    #   JSON-RPC without any API key, the only tidy CSV that route could produce,
    #   since historical eth_getLogs and archival eth_call are both gated behind paid
    #   tiers on every endpoint tried).
    # + 7 in an adversarial-review-driven pass (Paxos/USDP March 2023: the CDX search
    #   log, the frozen disclosure passages, the joint-statement timestamp record, the
    #   USDP daily supply series, and the raw captures). That candidate row does NOT
    #   qualify as a panel member, its only quantified disclosure post-dates the
    #   federal backstop by 80.4 minutes, but the evidence is frozen either way,
    #   which is the point.
    # + 1 in a bibliography pass (the Circle blog post "An Update on USDC and Silicon
    #   Valley Bank", frozen at its first Wayback capture, 2023-03-11T20:27:53Z --
    #   closes the makewhole_timing.py gap on the Circle-pledge sub-claim; resolves to
    #   "not more than a day", contradicting the manuscript's joint "more than a day"
    #   claim for that specific clause).
    # + 2 in a PSMSOURCE contemporaneity fix: 1 genuinely new entry (the Wayback-captured
    #   The Block article pinning the PSM-USDC-A $3.1B-cap figure for 2023-03-11,
    #   n1/dai_psm_2023/raw/...) PLUS the pre-existing dai_backing.json entry, whose
    #   retrieved_utc was refreshed when dai_contagion.refreeze_reported() rewrote its
    #   reported_psm_usdc/reported_source, that single entry (unchanged file count,
    #   dai_contagion._freeze_manifest replaces rather than appends it) simply moved into
    #   the current discriminator window.
    # + 2 in a residual fix (R-7): n1/paxos2023/cdx_search_log.json and
    #   n1/paxos2023/joint_statement_treasury_fed_fdic.json were retrieval logs with no
    #   prior MANIFEST entry at all, each had an absolute local filesystem path baked
    #   into a raw_path field, sanitised to a repo-relative path in place, then given a
    #   first-time MANIFEST entry (sha256 + bytes + a
    #   "log sanitised: absolute path removed; content otherwise unchanged" notes field)
    #   so the sanitisation is itself auditable against disk like every other frozen
    #   artifact.
    # + 3 sourcing previously uncited manuscript numbers:
    #   the U.S. Treasury daily par yield curve for 2023-03-10 (backing the gate-rate and
    #   time-value-discount figures), the TUSD hourly price series for the 2023-03-08..17
    #   window (backing the TrueUSD scope-condition deviation), and the Paxos BUSD press
    #   release capture (backing the redemption total in the excluded-coins appendix).
    # The two address-label corpora are not shipped in the short paper's artifact
    # (see constants.ONCHAIN_PRESENT), so there the pinned total is 2 lower. Derive it
    # rather than hardcoding a second number that can rot independently.
    expected = 63 if ONCHAIN_PRESENT else 63 - 2
    assert len(_new_entries()) == expected


def test_every_new_entry_file_exists():
    missing = [s["file"] for s in _new_entries() if not (DATA_DIR / s["file"]).is_file()]
    assert not missing, f"MANIFEST entries with no file on disk: {missing}"


def test_every_new_entry_sha256_matches():
    mismatches = []
    for s in _new_entries():
        if "sha256" not in s:
            # dai_backing.json's sources[] entry (written by dai_contagion._freeze_manifest)
            # never carries a sha256, that file's hash is tracked separately in the
            # top-level files{} dict instead (see test_manifest_files_dict_entries_match_disk,
            # which checks it regardless of the new/old-entry split this file discriminates).
            continue
        path = DATA_DIR / s["file"]
        actual = _sha256(path)
        if actual != s["sha256"]:
            mismatches.append((s["file"], s.get("series"), s["sha256"], actual))
    assert not mismatches, f"sha256 mismatches (file, series, recorded, actual): {mismatches}"


def test_every_new_entry_with_bytes_matches_filesize():
    mismatches = []
    for s in _new_entries():
        if "bytes" not in s:
            continue
        actual = (DATA_DIR / s["file"]).stat().st_size
        if actual != s["bytes"]:
            mismatches.append((s["file"], s["bytes"], actual))
    assert not mismatches, f"byte-size mismatches (file, recorded, actual): {mismatches}"


def test_every_new_entry_with_rows_matches_row_count():
    mismatches = []
    for s in _new_entries():
        if "rows" not in s:
            continue
        path = DATA_DIR / s["file"]
        actual = _row_count(path)
        if actual != s["rows"]:
            mismatches.append((s["file"], s.get("series"), s["rows"], actual))
    assert not mismatches, f"row-count mismatches (file, series, recorded, actual): {mismatches}"


def test_every_new_entry_with_coverage_matches_hour_bounds():
    mismatches = []
    for s in _new_entries():
        if "coverage_start_utc" not in s:
            continue
        path = DATA_DIR / s["file"]
        if s.get("rows") == 0:
            # A booked instrument gap (e.g. the Bitstamp connect-timeout): the file
            # is header-only by design, so there is nothing to bound. The manifest
            # entry itself must say so explicitly rather than claim a real range.
            assert s["coverage_start_utc"] is None and s["coverage_end_utc"] is None, (
                f"{s['file']} has rows=0 but claims a non-null coverage range "
                f"({s['coverage_start_utc']} .. {s['coverage_end_utc']}) -- an "
                "instrument gap must not assert a range it didn't measure"
            )
            continue
        lo, hi = _hour_utc_bounds(path)
        if (lo, hi) != (s["coverage_start_utc"], s["coverage_end_utc"]):
            mismatches.append((s["file"], s.get("series"), (s["coverage_start_utc"], s["coverage_end_utc"]), (lo, hi)))
    assert not mismatches, f"coverage mismatches (file, series, recorded, actual): {mismatches}"


def test_no_stale_entry_shares_a_file_with_a_different_sha256():
    # The exact defect fixed during N1.3 reconciliation: two sources entries naming
    # the same physical file must not disagree about that file's hash, since a file
    # has exactly one sha256 at any given time. Guards against a repeat of the
    # superseded-partial-pull bug across ALL entries (new and pre-existing), not just
    # the one instance already fixed. Pre-existing (pre-N1) entries legitimately have
    # no "sha256" key at all, that convention keeps sha256 only in the top-level
    # files{} dict, so entries without one are skipped here, not treated as a hit.
    by_file = {}
    for s in _load_manifest()["sources"]:
        if "sha256" not in s:
            continue
        by_file.setdefault(s["file"], set()).add(s["sha256"])
    conflicts = {f: hashes for f, hashes in by_file.items() if len(hashes) > 1}
    assert not conflicts, f"one file, multiple disagreeing sha256 entries: {conflicts}"


def test_g8_four_previously_unhashed_files_are_in_files_dict():
    """G8.0 Part 2: these four files are read by trough_corroboration.py (line 404)
    and specificity_panel.py/tables.py but had no SHA-256 recorded anywhere in
    MANIFEST.json before this pass. test_manifest_files_dict_entries_match_disk
    below re-verifies every files{} entry against disk, but only for whatever is
    THERE -- it would not by itself catch one of these four going missing again.
    Pin their presence, and that of the new circle2023 Part-1 artifacts, by name."""
    files = _load_manifest()["files"]
    for rel in (
        "n1/kraken_usdcusd/trades_raw.jsonl",
        "n1/paxos2023/disclosure_passages_paxos.json",
        "n1/tether2019/disclosure_passages_nyag.json",
        "n1/tether2019/usdt_liabilities_wayback.json",
        "n1/circle2023/raw/circle_tweet_usdc_svb_20230311032209.html",
        "n1/circle2023/raw/circle_pressrelease_usdc_svb_20230313031348.html",
        "n1/circle2023/disclosure_passages_circle.json",
    ):
        assert rel in files, f"{rel} missing from MANIFEST.json files{{}}"
        assert "sha256" in files[rel] and "bytes" in files[rel], f"{rel} entry incomplete"


def test_manifest_files_dict_entries_match_disk():
    # The top-level files{} dict (distinct from sources[]) is keyed by path relative
    # to DATA_DIR and is meant to always reflect CURRENT disk state, not history --
    # unlike sources[], which is an append-only log. Check every entry, not just the
    # new ones, since this dict has no historical/append-only exemption.
    manifest = _load_manifest()
    mismatches = []
    for rel_path, meta in manifest["files"].items():
        path = DATA_DIR / rel_path
        if not path.is_file():
            mismatches.append((rel_path, "MISSING", meta.get("sha256")))
            continue
        actual = _sha256(path)
        if actual != meta["sha256"]:
            mismatches.append((rel_path, actual, meta["sha256"]))
    assert not mismatches, f"files{{}} entries out of sync with disk (path, actual, recorded): {mismatches}"
