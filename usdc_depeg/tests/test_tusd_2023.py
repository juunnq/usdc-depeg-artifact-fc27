"""TUSD's 11 March 2023 deviation from par, pins the computed value
against the frozen DeFiLlama series so fc27.tex's TUSD figure cannot silently drift."""
import json

import pytest

import tusd_2023
from constants import RESULTS_DIR


def test_march11_minimum_traces_to_frozen_file():
    m = tusd_2023.march11_minimum()
    assert m["date"] == "2023-03-11"
    assert m["min_price"] == pytest.approx(0.988439)
    assert m["min_price_timestamp_utc"] == "2023-03-11T10:57:53+00:00"


def test_deviation_from_par_bps():
    m = tusd_2023.march11_minimum()
    assert m["deviation_from_par_bps"] == pytest.approx(115.61, abs=0.01)
    # the manuscript's retired "83 bps" figure had no source in data/ or results/;
    # the frozen series does not reproduce it, guard against silently drifting back.
    assert m["deviation_from_par_bps"] != pytest.approx(83, abs=1)


def test_results_artifact_persisted_and_matches():
    path = RESULTS_DIR / "tusd_2023.json"
    assert path.exists(), "run `python tusd_2023.py` to produce results/tusd_2023.json"
    frozen = json.load(open(path))
    fresh = tusd_2023.report()
    assert frozen["min_price"] == pytest.approx(fresh["min_price"])
    assert frozen["deviation_from_par_bps"] == pytest.approx(fresh["deviation_from_par_bps"])
