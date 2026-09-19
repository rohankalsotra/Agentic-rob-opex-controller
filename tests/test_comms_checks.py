"""Tests for comms_checks.py: the gate every AI-written part of the pre-read must pass."""
import copy
import json
from pathlib import Path

import pytest

import comms_checks as cc
import handoff
from variance_state import load_state

DATA = Path(__file__).resolve().parent.parent / "data"
pytestmark = pytest.mark.skipif(
    not ((DATA / "sample_opex_2026.xlsx").exists() and (DATA / "sample_review_decisions.csv").exists()),
    reason="Generate the sample data first.")

GOOD_HEADLINE = "Q3 2026 is on track overall at +$43.6K (+3.2%), with four lines flagged for discussion."
GOOD_POINTS = [
    "What is behind the Field Sales Contractors overspend of +$61.7K (+45.7%)?",
    "Marketing Travel is OVER at +$20.3K (+33.8%). Can someone walk through the spend?",
    "Marketing Events & Marketing is UNDER at -$11.5K (-31.9%) and has no transactions recorded for "
    "Sep 2026. Can someone confirm whether a posting is missing?",
    "Engineering Ops Software & Subscriptions is UNDER at -$32.3K (-59.8%). What is behind the underspend?",
]


@pytest.fixture(scope="module")
def package():
    return handoff.build_handoff(load_state())


def problems(package, headline=GOOD_HEADLINE, points=None):
    return cc.check_draft(package, headline, GOOD_POINTS if points is None else points)


def has(problem_list, text):
    return any(text in p for p in problem_list)


# ---- a good draft passes ---------------------------------------------------------------------
def test_a_good_draft_passes(package):
    assert problems(package) == []


# ---- each kind of bad draft is caught --------------------------------------------------------
def test_an_invented_number_is_caught(package):
    bad = [GOOD_POINTS[0].replace("+$61.7K", "+$61.9K")] + GOOD_POINTS[1:]
    assert has(problems(package, points=bad), "+$61.9K")


def test_a_number_claude_calculated_itself_is_caught(package):
    bad = GOOD_POINTS + ["Together the two OVER lines add up to $82.0K."]
    assert has(problems(package, points=bad), "$82.0K")


@pytest.mark.parametrize("word", ["significantly", "entirely", "reliable", "offsetting"])
def test_judgment_words_are_caught(package, word):
    bad = [GOOD_POINTS[0] + f" It is {word} high."] + GOOD_POINTS[1:]
    assert has(problems(package, points=bad), f'"{word}"')


@pytest.mark.parametrize("phrase", ["because", "due to", "postponed", "probably"])
def test_made_up_causes_are_caught(package, phrase):
    bad = [GOOD_POINTS[0] + f" That is {phrase} something."] + GOOD_POINTS[1:]
    assert has(problems(package, points=bad), f'"{phrase}"')


def test_a_headline_without_the_total_is_caught(package):
    found = problems(package, headline="Q3 2026 went fine overall.")
    assert has(found, "+$43.6K") and has(found, "+3.2%") and has(found, "On track")


def test_a_flagged_line_left_out_is_caught(package):
    """The Round-5 lesson: leaving something out is a failure too."""
    bad = [p for p in GOOD_POINTS if "Marketing Travel" not in p]
    assert has(problems(package, points=bad), "flagged line Marketing / Travel")


def test_the_missing_transactions_caveat_left_out_is_caught(package):
    bad = GOOD_POINTS.copy()
    bad[2] = "Marketing Events & Marketing is UNDER at -$11.5K (-31.9%). What is behind the underspend?"
    found = problems(package, points=bad)
    assert has(found, "missing transactions")


def test_wrong_shape_is_caught(package):
    assert has(problems(package, points=["Only one point."]), "between 2 and 6")
    assert has(problems(package, headline="word " * 60), "longer than 50 words")


# ---- parsing the model's reply ---------------------------------------------------------------
def test_parse_accepts_plain_and_fenced_json():
    raw = '{"headline": "H", "discussion_points": ["a", "b"]}'
    assert cc.parse_draft(raw) == ("H", ["a", "b"])
    assert cc.parse_draft("```json\n" + raw + "\n```") == ("H", ["a", "b"])


@pytest.mark.parametrize("raw", ["no json here", '{"headline": "H"}', '{"headline": "", "discussion_points": []}',
                                 '{"headline": "H", "discussion_points": [1, 2]}', "{not json}"])
def test_parse_rejects_bad_replies(raw):
    with pytest.raises(ValueError):
        cc.parse_draft(raw)


# ---- the checks adapt to the data (nothing hard-coded to our sample) -------------------------
def test_with_nothing_flagged_no_coverage_is_demanded(package):
    quiet = copy.deepcopy(package)
    quiet["quarter"]["flagged_lines"] = []
    quiet["quarter"]["budgeted_months_with_no_transactions"] = []
    assert cc.check_draft(quiet, GOOD_HEADLINE, ["Nothing to add here.", "Everything is on track."]) == []
