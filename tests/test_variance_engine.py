"""Tests for variance_engine.py: the data checks and the reviewer's decisions.

Each test builds a tiny workbook containing ONE situation, then checks what the
code does with it. The situations come straight from real PO data."""
import pandas as pd
import pytest

import variance_engine as ve
from helpers import txn_row, budget_row, budget_months, write_workbook


def load(tmp_path, txn_rows, budget_rows=None):
    """Write a tiny workbook and read it back through the real engine."""
    path = write_workbook(tmp_path / "wb.xlsx", budget_rows or budget_months([7, 8, 9]), txn_rows)
    return ve.load_workbook(path)


def types(issues):
    return sorted(i["type"] for i in issues)


# ================= 1. what counts as a duplicate =========================================
def test_same_transaction_id_twice_is_a_true_duplicate_and_is_removed(tmp_path):
    _, txn, issues = load(tmp_path, [txn_row(), txn_row()])          # identical, same ID
    assert len(txn) == 1 and txn["Amount"].sum() == 1000              # counted once
    assert types(issues) == ["duplicate_removed"]
    assert issues[0]["status"] == "auto_resolved"                     # reported, nobody needs to act


def test_a_po_extension_is_not_a_duplicate(tmp_path):
    """Same PO number, NEW Transaction ID, new amount, explanatory description."""
    extension = txn_row(**{"Transaction ID": "TXN-2", "Amount": 500,
                           "Project Description": "Extension: more scope approved."})
    _, txn, issues = load(tmp_path, [txn_row(), extension])
    assert len(txn) == 2 and txn["Amount"].sum() == 1500              # both kept
    assert issues == []                                               # and nothing flagged


def test_same_amount_on_different_pos_is_not_a_duplicate(tmp_path):
    other = txn_row(**{"Transaction ID": "TXN-2", "PO Number": "PO-2", "PO Owner": "Bo Chen",
                       "Project Name": "Project B", "Project Description": "Different work."})
    _, txn, issues = load(tmp_path, [txn_row(), other])               # both $1,000
    assert len(txn) == 2 and issues == []


def test_same_id_with_different_details_is_flagged_and_nothing_is_removed(tmp_path):
    clash = txn_row(Amount=999)                                       # same ID, different amount
    _, txn, issues = load(tmp_path, [txn_row(), clash])
    assert len(txn) == 2
    assert types(issues) == ["conflicting_transaction_id", "conflicting_transaction_id"]


# ================= 2. rows with no Transaction ID ==========================================
def test_no_id_row_matching_another_row_is_a_possible_duplicate_and_is_kept(tmp_path):
    _, txn, issues = load(tmp_path, [txn_row(), txn_row(**{"Transaction ID": None})])
    assert len(txn) == 2                                              # NEVER removed automatically
    assert types(issues) == ["possible_duplicate"]
    assert issues[0]["related_ids"] == ["TXN-1"] and issues[0]["status"] == "open"


def test_no_id_row_with_no_lookalike_is_flagged_as_missing_id(tmp_path):
    _, txn, issues = load(tmp_path, [txn_row(), txn_row(**{"Transaction ID": None, "Amount": 777})])
    assert len(txn) == 2 and types(issues) == ["missing_transaction_id"]


def test_two_identical_rows_that_both_lack_ids_are_never_removed(tmp_path):
    blank = txn_row(**{"Transaction ID": None})
    _, txn, issues = load(tmp_path, [blank, blank])
    assert len(txn) == 2                                              # we can't be sure, so keep both
    assert types(issues) == ["missing_transaction_id", "missing_transaction_id"]


# ================= 3. other data problems ====================================================
def test_blank_amount_is_flagged_kept_and_left_out_of_totals(tmp_path):
    _, txn, issues = load(tmp_path, [txn_row(), txn_row(**{"Transaction ID": "TXN-2", "Amount": None})])
    assert len(txn) == 2 and txn["Amount"].sum() == 1000
    assert types(issues) == ["missing_amount"]


def test_transaction_with_no_budget_line_is_flagged(tmp_path):
    _, _, issues = load(tmp_path, [txn_row(Category="Software")])     # budget only has Travel
    assert types(issues) == ["no_budget_line"]


def test_budget_line_listed_twice_is_flagged(tmp_path):
    _, _, issues = load(tmp_path, [txn_row()], budget_months([7, 7, 8, 9]))
    assert types(issues) == ["duplicate_budget_line"]


def test_missing_required_column_stops_with_a_clear_error(tmp_path):
    bad = txn_row(); del bad["Amount"]
    with pytest.raises(ValueError, match="Amount"):
        load(tmp_path, [bad])


# ================= 4. the reviewer's decisions ==================================================
def decisions(*rows):
    return pd.DataFrame(list(rows), columns=ve.DECISION_COLUMNS)


def decide(target, action, value=None):
    return [target, action, value, "test reason", "Tester", "2026-01-01"]


NO_ID = txn_row(**{"Transaction ID": None})


def test_remove_no_id_copy_removes_only_the_blank_id_row(tmp_path):
    _, txn, issues = load(tmp_path, [txn_row(), NO_ID])
    txn2, issues2, log = ve.apply_decisions(txn, issues, decisions(decide("TXN-1", "remove_no_id_copy")))
    assert txn2["Transaction ID"].tolist() == ["TXN-1"]               # the row WITH the ID stays
    assert log[0]["result"] == "applied" and issues2[0]["status"] == "resolved"


def test_keep_row_confirms_it_is_not_a_duplicate(tmp_path):
    _, txn, issues = load(tmp_path, [txn_row(), NO_ID])
    txn2, issues2, log = ve.apply_decisions(txn, issues, decisions(decide("TXN-1", "keep_row")))
    assert len(txn2) == 2 and issues2[0]["status"] == "resolved"


def test_set_amount_fills_a_blank_amount(tmp_path):
    _, txn, issues = load(tmp_path, [txn_row(Amount=None)])
    txn2, issues2, log = ve.apply_decisions(txn, issues, decisions(decide("TXN-1", "set_amount", 2500)))
    assert txn2.loc[0, "Amount"] == 2500 and issues2[0]["status"] == "resolved"


def test_set_amount_refuses_to_overwrite_an_existing_amount(tmp_path):
    _, txn, issues = load(tmp_path, [txn_row()])                      # Amount is 1000
    txn2, _, log = ve.apply_decisions(txn, issues, decisions(decide("TXN-1", "set_amount", 999)))
    assert log[0]["result"] == "rejected" and txn2.loc[0, "Amount"] == 1000


@pytest.mark.parametrize("bad_decision", [
    decide("TXN-1", "set_amount", "abc"),             # not a number
    decide("TXN-999", "remove_no_id_copy"),           # no such transaction
    decide("TXN-1", "delete_everything"),             # not an allowed action
    decide("TXN-1", "remove_no_id_copy"),             # allowed, but there is no blank-ID copy to remove
])
def test_bad_decisions_are_rejected_and_change_nothing(tmp_path, bad_decision):
    _, txn, issues = load(tmp_path, [txn_row(Amount=None)])
    txn2, _, log = ve.apply_decisions(txn, issues, decisions(bad_decision))
    assert log[0]["result"] == "rejected"
    assert txn2["Amount"].isna().sum() == 1 and len(txn2) == 1


def test_a_look_alike_that_has_its_own_id_is_never_removed_by_a_decision(tmp_path):
    twin = txn_row(**{"Transaction ID": "TXN-2"})                     # same details, own real ID
    _, txn, issues = load(tmp_path, [txn_row(), twin])
    txn2, _, log = ve.apply_decisions(txn, issues, decisions(decide("TXN-1", "remove_no_id_copy")))
    assert len(txn2) == 2 and log[0]["result"] == "rejected"


def test_applying_decisions_does_not_alter_the_original_table(tmp_path):
    _, txn, issues = load(tmp_path, [txn_row(Amount=None)])
    ve.apply_decisions(txn, issues, decisions(decide("TXN-1", "set_amount", 2500)))
    assert txn["Amount"].isna().sum() == 1                             # the input is untouched
