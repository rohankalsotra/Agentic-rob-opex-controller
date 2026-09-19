"""The big test: run the whole pipeline on the sample workbook and check the ANSWER KEY,
the situations we planted on purpose in scripts/make_sample_data.py.

Skipped automatically if the sample workbook has not been generated yet."""
from pathlib import Path

import pytest

import variance_engine as ve
import variance_math as vm

DATA = Path(__file__).resolve().parent.parent / "data"
WORKBOOK, REVIEW = DATA / "sample_opex_2026.xlsx", DATA / "sample_review_decisions.csv"

pytestmark = pytest.mark.skipif(
    not (WORKBOOK.exists() and REVIEW.exists()),
    reason="Generate the sample data first: python scripts/make_sample_data.py")


@pytest.fixture(scope="module")
def run():
    budget, txn, raw_issues = ve.load_workbook(WORKBOOK)
    txn_final, issues, log = ve.apply_decisions(txn, [dict(i) for i in raw_issues],
                                                ve.read_decisions(REVIEW))
    report = vm.build_variance_report(budget, txn_final, "2026-09-30")
    return {"raw": raw_issues, "txn_before": txn, "txn": txn_final, "issues": issues,
            "log": log, "report": report}


def flagged(period):
    lines = period["by_line"]
    lines = lines[lines["Status"].isin(["OVER", "UNDER"])]
    return {(r["Cost Center ID"], r["Category"], r["Status"]) for _, r in lines.iterrows()}


# ---- the data situations -------------------------------------------------------------------
def test_the_raw_data_produces_exactly_the_three_expected_issues(run):
    assert ve_types(run["raw"]) == ["duplicate_removed", "missing_amount", "possible_duplicate"]


def ve_types(issues):
    return sorted(i["type"] for i in issues)


def test_true_duplicate_is_txn_000056(run):
    dup = next(i for i in run["raw"] if i["type"] == "duplicate_removed")
    assert dup["transaction_id"] == "TXN-000056"


def test_no_id_lookalike_points_at_txn_000121(run):
    poss = next(i for i in run["raw"] if i["type"] == "possible_duplicate")
    assert poss["related_ids"] == ["TXN-000121"]


def test_blank_amount_is_txn_000269(run):
    blank = next(i for i in run["raw"] if i["type"] == "missing_amount")
    assert blank["transaction_id"] == "TXN-000269"


def test_legitimate_look_alikes_are_never_flagged(run):
    """The PO extension (TXN-000424) and the same-amount pair (TXN-000285, TXN-000301)."""
    touched = {i["transaction_id"] for i in run["raw"]} | {r for i in run["raw"] for r in i["related_ids"]}
    assert not touched & {"TXN-000424", "TXN-000285", "TXN-000301"}


# ---- after the reviewer's decisions ---------------------------------------------------------
def test_both_decisions_were_applied_and_nothing_is_left_open(run):
    assert [e["result"] for e in run["log"]] == ["applied", "applied"]
    assert not [i for i in run["issues"] if i["status"] == "open"]


def test_transactions_and_totals_after_decisions(run):
    assert len(run["txn_before"]) == 425 and len(run["txn"]) == 424
    assert run["txn"]["Amount"].sum() == 4_084_558      # 4,094,609 - 12,551 (copy) + 2,500 (filled in)


# ---- the variance report --------------------------------------------------------------------
def test_q3_flagged_lines_match_the_answer_key(run):
    assert flagged(run["report"]["periods"]["Quarter"]) == {
        ("CC-100", "Contractors", "OVER"), ("CC-200", "Travel", "OVER"),
        ("CC-200", "Events & Marketing", "UNDER"), ("CC-400", "Software & Subscriptions", "UNDER")}


def test_year_to_date_flagged_lines_match_the_answer_key(run):
    assert flagged(run["report"]["periods"]["Year to date"]) == {
        ("CC-100", "Contractors", "OVER"), ("CC-200", "Travel", "OVER"),
        ("CC-400", "Software & Subscriptions", "UNDER")}


def test_only_field_sales_is_flagged_as_a_team_in_q3_and_none_year_to_date(run):
    assert set(run["report"]["periods"]["Quarter"]["team_drivers"]) == {"CC-100"}
    assert run["report"]["periods"]["Year to date"]["team_drivers"] == {}


def test_budget_totals(run):
    q = run["report"]["periods"]["Quarter"]["total"].iloc[0]
    y = run["report"]["periods"]["Year to date"]["total"].iloc[0]
    assert (q["Budget"], y["Budget"]) == (1_350_000, 4_050_000)
    assert y["Actual"] == 4_084_558                      # every transaction falls in Jan-Sep


def test_control_totals_pass(run):
    assert all(p["control"]["passed"] for p in run["report"]["periods"].values())


def test_the_empty_september_events_line_is_noted(run):
    for period in run["report"]["periods"].values():
        notes = period["no_transaction_lines"]
        assert len(notes) == 1
        assert (notes[0]["Cost Center ID"], notes[0]["Category"]) == ("CC-200", "Events & Marketing")
