"""G8.0: pins the verbatim Circle disclosure passages frozen in
data/n1/circle2023/disclosure_passages_circle.json against two kinds of drift --
(1) the extracted text itself silently changing, and (2) the raw capture file a
passage was extracted from no longer matching what MANIFEST.json's files{} dict
(which data_fetch.verify() checks) records for it. Offline, read-only, no network,
mirrors the conventions of test_manifest_n1.py."""
import hashlib
import json

from constants import DATA_DIR

CIRCLE_DIR = DATA_DIR / "n1" / "circle2023"
PASSAGES_PATH = CIRCLE_DIR / "disclosure_passages_circle.json"
MANIFEST_PATH = DATA_DIR / "MANIFEST.json"


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _doc():
    with open(PASSAGES_PATH, encoding="utf-8") as f:
        return json.load(f)


def test_three_passages_and_one_booked_gap():
    doc = _doc()
    assert len(doc["passages"]) == 3
    assert len(doc["gaps"]) == 1
    assert doc["gaps"][0]["bib_key"] == "circle2023attestation"
    assert doc["gaps"][0]["status"].startswith("NOT CAPTURED")


def test_tweet_passage_verbatim_pin():
    tweet = next(p for p in _doc()["passages"] if p["bib_key"] == "circle2023tweet")
    assert tweet["verbatim_passage"] == (
        "1/ Following the confirmation at the end of today that the wires initiated "
        "on Thursday to remove balances were not yet processed, $3.3 billion of the "
        "~$40 billion of USDC reserves remain at SVB."
    )
    assert tweet["capture_timestamp"] == "20230311032209"
    assert tweet["raw_file"] == "data/n1/circle2023/raw/circle_tweet_usdc_svb_20230311032209.html"


def test_pressrelease_passages_verbatim_pin():
    pr = [p for p in _doc()["passages"] if p["bib_key"] == "circle2023pressrelease"]
    assert len(pr) == 2

    eight_pct = next(p for p in pr if "about 8%" in p["verbatim_passage"])
    assert eight_pct["verbatim_passage"] == (
        "BOSTON – March 12, 2023 – Following the joint announcement by U.S. Treasury "
        "Secretary Janet Yellen and U.S. prudential regulators, all depositors with "
        "Silicon Valley Bank and Signature Bank, which was closed by regulators today, "
        "will be made whole. The $3.3B USDC reserve deposit held at Silicon Valley Bank, "
        "about 8% of the USDC total reserve, will be fully available when U.S. banks "
        "open tomorrow morning. No USDC cash reserves were held at Signature Bank. As a "
        "regulated payment token, USDC remains redeemable 1:1 with the U.S. Dollar."
    )

    denom = next(p for p in pr if "77%" in p["verbatim_passage"])
    assert denom["verbatim_passage"] == (
        "On March 11, 2023, Circle shared details about the USDC reserve, stating it is "
        "currently collateralized 77% ($32.4B) with short-dated U.S. Treasury Bills. U.S. "
        "Treasury Bills are the most liquid assets in the world and are direct obligations "
        "of the U.S. government. These reserves are held in custody by BNY Mellon and "
        "active liquidity and asset management is done by BlackRock. The cash portion of "
        "the USDC reserve, 23% ($9.7B), is now held primarily at BNY Mellon. Anyone can "
        "view the entire liquidity ladder down to the CUSIP number on T-Bills via the "
        "USDXX ticker, and monthly USDC attestation reports, including the latest report "
        "from January, 2023, are available in the Trust & Transparency section of "
        "Circle’s website."
    )
    assert eight_pct["capture_timestamp"] == denom["capture_timestamp"] == "20230313031348"
    assert (eight_pct["raw_file"] == denom["raw_file"]
            == "data/n1/circle2023/raw/circle_pressrelease_usdc_svb_20230313031348.html")


def test_every_passage_raw_file_hash_matches_manifest():
    """Each passage names the raw capture it was extracted from; that file's
    SHA-256 must match MANIFEST.json's files{} entry (the dict verify() checks)
    AND the file on disk, catching either a stale manifest entry or a silently
    edited/re-fetched capture out from under the frozen passage."""
    manifest_files = json.load(open(MANIFEST_PATH, encoding="utf-8"))["files"]
    checked = set()
    for p in _doc()["passages"]:
        rel = p["raw_file"]
        assert rel.startswith("data/"), rel
        rel_to_data = rel[len("data/"):]
        assert rel_to_data in manifest_files, f"{rel} has no MANIFEST.json files{{}} entry"
        path = DATA_DIR / rel_to_data
        assert path.is_file(), f"{rel} is not on disk"
        assert _sha256(path) == manifest_files[rel_to_data]["sha256"], f"{rel} sha256 drift"
        checked.add(rel_to_data)
    assert checked == {
        "n1/circle2023/raw/circle_tweet_usdc_svb_20230311032209.html",
        "n1/circle2023/raw/circle_pressrelease_usdc_svb_20230313031348.html",
    }


def test_disclosure_passages_circle_json_itself_is_manifested():
    manifest_files = json.load(open(MANIFEST_PATH, encoding="utf-8"))["files"]
    rel = "n1/circle2023/disclosure_passages_circle.json"
    assert rel in manifest_files
    assert _sha256(DATA_DIR / rel) == manifest_files[rel]["sha256"]
