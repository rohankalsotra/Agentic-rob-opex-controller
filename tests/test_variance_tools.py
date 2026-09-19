"""Tests for variance_tools.py: the text cards that Claude reads.

Two things matter most here:
  1. every value handed to Claude is FINISHED TEXT (no raw decimal numbers to do math on);
  2. the text says the right thing.
"""
import json
from pathlib import Path

import pandas as pd
import pytest

import variance_engine as ve
import variance_math as vm
import variance_tools as vt
from helpers import budget_months, txn_row, with_month

DATA = Path(__file__).resolve().parent.parent / "data"
WORKBOOK, REVIEW = DATA / "sample_opex_2026.xlsx", DATA / "sample_review_decisions.csv"


def leaves(thing):
    """Every individual value (the 'leaves') inside nested dictionaries and lists."""
    if isinstance(thing, dict):
        for v in thing.values():
            yield from leaves(v)
    elif isinstance(thing, list):
        for v in thing:
            yield from leaves(v)
    else:
        yield thing


@pytest.fixture
def tiny_report():
    """One team, one category. Q3 budget = 3 x $100,000. Actual = 3 x $120,000."""
    budget = pd.DataFrame(budget_months([7, 8, 9], Budget=100_000))
    txn = with_month([txn_row(Amount=120_000, **{"Transaction ID": f"T{m}",
                                                 "Posting Date": pd.Timestamp(2026, m, 5)})
                      for m in (7, 8, 9)])
    return vm.build_variance_report(budget, txn, "2026-09-30")


# ---- the hand-made example: we know the answers in advance -----------------------------------
def test_total_says_the_right_things(tiny_report):
    total = vt.build_report_card(tiny_report, "quarter")["total"]
    assert total["budget"] == {"short": "$300.0K", "exact": "$300,000"}
    assert total["actual"] == {"short": "$360.0K", "exact": "$360,000"}
    assert total["variance"] == {"short": "+$60.0K", "exact": "+$60,000"}
    assert total["variance_pct"] == "+20.0%"
    assert total["status"] == "OVER"


def test_the_flagged_line_and_its_team_driver_are_included(tiny_report):
    card = vt.build_report_card(tiny_report, "quarter")
    assert [(f["cost_center"], f["category"], f["status"]) for f in card["flagged_lines"]] == \
           [("Team One", "Travel", "OVER")]
    driver = card["flagged_teams_and_drivers"][0]
    assert driver["cost_center"] == "Team One"
    assert driver["biggest_lines"][0]["category"] == "Travel"
    assert driver["all_other_lines"]["count"] == 0


def test_no_raw_numbers_reach_claude(tiny_report):
    """Text, True/False and whole-number COUNTS only. Never a decimal number."""
    card = vt.build_report_card(tiny_report, "quarter")
    for value in leaves(card):
        assert isinstance(value, (str, bool, int)), f"raw number leaked: {value!r}"
        assert not isinstance(value, float)


def test_card_can_be_turned_into_json(tiny_report):
    json.dumps(vt.build_report_card(tiny_report, "quarter"))       # would raise if it could not


def test_labels_and_control_total(tiny_report):
    card = vt.build_report_card(tiny_report, "ytd")
    assert card["report"] == "Q3 2026" and card["period"] == "Year to date"
    assert card["months"] == "Jan 2026 to Sep 2026"
    assert card["control_total"]["passed"] is True


def test_unknown_period_is_refused(tiny_report):
    with pytest.raises(ValueError):
        vt.build_report_card(tiny_report, "next year")


# ---- the data quality card ---------------------------------------------------------------------
def test_data_quality_card_sorts_issues_by_status():
    issues = [
        ve._make_issue("missing_amount", "Amount is blank."),
        ve._make_issue("duplicate_removed", "Copy removed."),
        ve._make_issue("possible_duplicate", "Looks like a copy."),
    ]
    issues[1]["status"], issues[1]["resolution"] = "auto_resolved", "Removed automatically."
    issues[2]["status"], issues[2]["resolution"] = "resolved", "Reviewer removed it."
    card = vt.build_data_quality_card(issues, log=[])
    assert [c["type"] for c in card["open_needs_a_human"]] == ["missing_amount"]
    assert [c["type"] for c in card["resolved_automatically"]] == ["duplicate_removed"]
    assert [c["type"] for c in card["resolved_by_a_reviewer"]] == ["possible_duplicate"]


# ---- the real sample workbook -----------------------------------------------------------------
needs_sample = pytest.mark.skipif(not (WORKBOOK.exists() and REVIEW.exists()),
                                  reason="Generate the sample data first.")


@pytest.fixture(scope="module")
def sample():
    budget, txn, issues = ve.load_workbook(WORKBOOK)
    txn, issues, log = ve.apply_decisions(txn, issues, ve.read_decisions(REVIEW))
    return vm.build_variance_report(budget, txn, "2026-09-30"), issues, log


@needs_sample
def test_sample_q3_card_matches_the_known_answers(sample):
    card = vt.build_report_card(sample[0], "quarter")
    assert card["total"]["budget"]["short"] == "$1.35M"
    assert card["total"]["actual"]["short"] == "$1.39M"
    assert card["total"]["variance"]["exact"] == "+$43,600"
    flagged = {(f["cost_center"], f["category"], f["status"]) for f in card["flagged_lines"]}
    assert flagged == {("Field Sales", "Contractors", "OVER"),
                       ("Marketing", "Travel", "OVER"),
                       ("Marketing", "Events & Marketing", "UNDER"),
                       ("Engineering Ops", "Software & Subscriptions", "UNDER")}
    driver = card["flagged_teams_and_drivers"][0]
    assert driver["cost_center"] == "Field Sales"
    assert driver["biggest_lines"][0]["category"] == "Contractors"
    empty = card["budgeted_months_with_no_transactions"][0]
    assert empty["month"] == "Sep 2026"
    assert "missing" in empty["note"]          # the caveat is written by code, not left to Claude
    assert empty["cost_center"] == "Marketing"  # a NAME, never a bare ID that Claude might mis-guess


@needs_sample
def test_sample_cards_contain_only_text(sample):
    for period in ("quarter", "ytd"):
        for value in leaves(vt.build_report_card(sample[0], period)):
            assert isinstance(value, (str, bool, int))


@needs_sample
def test_sample_data_quality_card(sample):
    _, issues, log = sample
    card = vt.build_data_quality_card(issues, log, {"CC-300": "Customer Success"})
    assert card["open_needs_a_human"] == []
    assert len(card["resolved_by_a_reviewer"]) == 2
    assert len(card["resolved_automatically"]) == 1
    assert card["resolved_automatically"][0]["cost_center"] == "Customer Success"
    assert {d["transaction_id"] for d in card["reviewer_decisions_applied"]} == \
           {"TXN-000121", "TXN-000269"}


@needs_sample
def test_data_quality_summary_travels_inside_the_report_card(sample):
    report, issues, log = sample
    dq = vt.build_data_quality_card(issues, log, {"CC-100": "Field Sales"})
    card = vt.build_report_card(report, "quarter", dq)
    summary = card["data_quality_summary"]
    assert summary["open_issues_needing_a_human"] == []
    assert any("TXN-000121 itself was kept" in line for line in summary["resolved_by_a_reviewer"])
    assert any("a row with no Transaction ID" in line for line in summary["resolved_by_a_reviewer"])
    assert len(summary["resolved_automatically"]) == 1
    for value in leaves(card):                       # still only text
        assert isinstance(value, (str, bool, int))


def test_without_a_data_quality_card_there_is_no_summary(tiny_report):
    assert "data_quality_summary" not in vt.build_report_card(tiny_report, "quarter")


def keys_of(thing):
    """Every dictionary key used anywhere inside nested dictionaries and lists."""
    if isinstance(thing, dict):
        for k, v in thing.items():
            yield k
            yield from keys_of(v)
    elif isinstance(thing, list):
        for v in thing:
            yield from keys_of(v)


@needs_sample
def test_cost_centers_are_always_given_by_name_never_as_a_bare_id(sample):
    """Round 3 lesson: given only 'CC-200', Claude guessed 'Field Sales' (wrong: it is Marketing).
    So a report card may not contain any cost-center ID field at all."""
    for period in ("quarter", "ytd"):
        card = vt.build_report_card(sample[0], period)
        assert not any(k.endswith("_id") for k in keys_of(card))
        assert "CC-" not in json.dumps(card)


@needs_sample
def test_flagged_lines_are_ranked_by_size_of_the_dollar_variance(sample):
    """Biggest swing first, whether over or under. The agenda relies on this order."""
    card = vt.build_report_card(sample[0], "quarter")
    order = [(f["cost_center"], f["category"]) for f in card["flagged_lines"]]
    assert order == [("Field Sales", "Contractors"),                  # +$61.7K
                     ("Engineering Ops", "Software & Subscriptions"),  # -$32.3K
                     ("Marketing", "Travel"),                          # +$20.3K
                     ("Marketing", "Events & Marketing")]              # -$11.5K
