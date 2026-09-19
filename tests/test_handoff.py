"""Tests for handoff.py: the one file that passes information from the calculator to the Comms side."""
import json
from pathlib import Path

import pytest

import handoff
from variance_state import load_state

DATA = Path(__file__).resolve().parent.parent / "data"
pytestmark = pytest.mark.skipif(
    not ((DATA / "sample_opex_2026.xlsx").exists() and (DATA / "sample_review_decisions.csv").exists()),
    reason="Generate the sample data first.")


@pytest.fixture(scope="module")
def package():
    return handoff.build_handoff(load_state())


def leaves(thing):
    if isinstance(thing, dict):
        for v in thing.values():
            yield from leaves(v)
    elif isinstance(thing, list):
        for v in thing:
            yield from leaves(v)
    else:
        yield thing


def test_package_contains_both_periods_and_the_data_quality_card(package):
    assert package["report"] == "Q3 2026" and package["as_of"] == "30 Sep 2026"
    assert package["quarter"]["period"] == "Quarter"
    assert package["year_to_date"]["period"] == "Year to date"
    assert package["data_quality"]["open_needs_a_human"] == []


def test_package_holds_only_finished_text_never_raw_numbers(package):
    for value in leaves(package):
        assert value is None or (isinstance(value, (str, bool, int)) and not isinstance(value, float))


def test_package_never_contains_a_bare_cost_center_id(package):
    assert "CC-" not in json.dumps(package)


def test_saving_and_loading_gives_back_the_same_package(package, tmp_path):
    path = handoff.save_handoff(package, tmp_path / "nested" / "handoff.json")
    assert handoff.load_handoff(path) == package
