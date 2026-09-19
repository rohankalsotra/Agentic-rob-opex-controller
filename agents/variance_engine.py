"""
variance_engine.py -- the "calculator" half of the Variance Agent.

THE DESIGN RULE for this whole project:
    All arithmetic happens HERE, in plain Python/pandas code.
    The AI model never does math. It only talks about numbers that this file
    already calculated.

This piece covers one job: READ the workbook (Budget + Transactions sheets) and
REPORT any data problems. It never quietly changes a number without telling you.

Try it:   python agents/variance_engine.py
"""

from pathlib import Path
import pandas as pd

# Columns each sheet must have. If one is missing we stop right away with a clear
# message instead of failing later in some confusing way.
BUDGET_COLUMNS = ["Cost Center ID", "Cost Center", "Category", "Month", "Budget"]
TRANSACTION_COLUMNS = [
    "Transaction ID", "PO Number", "PO Owner", "Cost Center ID", "Cost Center",
    "Category", "Vendor", "Project Name", "Project Description", "Posting Date", "Amount",
]

# The fields that must ALL match for a row with no Transaction ID to count as a
# "possible duplicate" of another row. Why so many? Every line of one project shares
# the same owner, name and description -- so without Amount and Posting Date, every
# monthly line of a project would look like a copy of the others.
LOOKALIKE_FIELDS = ["PO Number", "PO Owner", "Project Name", "Project Description",
                    "Amount", "Posting Date"]

# The three fields that identify one budget line.
BUDGET_KEY = ["Cost Center ID", "Category", "Month"]


def _make_issue(kind, message, row=None, related_ids=None):
    """Build one 'issue' record: a small dictionary of labelled fields.
    (The leading underscore is a Python habit meaning 'used inside this file only'.)
    Every issue starts as "open". A human decision can later mark it "resolved"."""
    issue = {"type": kind, "message": message, "transaction_id": None,
             "po_number": None, "cost_center": None, "category": None, "month": None,
             "related_ids": related_ids or [], "status": "open", "resolution": None}
    if row is not None:
        tid = row.get("Transaction ID")
        issue["transaction_id"] = None if pd.isna(tid) else tid
        issue["po_number"] = row.get("PO Number")
        issue["cost_center"] = row.get("Cost Center ID")
        issue["category"] = row.get("Category")
        month = row.get("Month", row.get("Posting Date"))
        issue["month"] = None if pd.isna(month) else month.strftime("%b %Y")
    return issue


def _check_columns(df, required, sheet_name):
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f"The '{sheet_name}' sheet is missing required columns: {missing}")


def load_workbook(path):
    """Read the workbook. Return THREE things:
         1. the budget table
         2. the transactions table (cleaned)
         3. a list of issues we found along the way
    """
    budget = pd.read_excel(path, sheet_name="Budget")
    txn = pd.read_excel(path, sheet_name="Transactions")
    _check_columns(budget, BUDGET_COLUMNS, "Budget")
    _check_columns(txn, TRANSACTION_COLUMNS, "Transactions")

    budget["Month"] = pd.to_datetime(budget["Month"])
    txn["Posting Date"] = pd.to_datetime(txn["Posting Date"])

    issues = []                                       # start with an empty list

    # --- Check 1: TRUE duplicates (the same Transaction ID loaded twice) ------------------
    # RULE: the Transaction ID is what tells records apart. If a row with a Transaction ID
    # is a complete copy of an earlier row, it is the same record loaded twice: we remove
    # the extra copy, and we report it. Rows WITHOUT an ID are never removed here.
    has_id = txn["Transaction ID"].notna()
    extra_copy = has_id & txn.duplicated(keep="first")    # & means "and"
    for _, row in txn[extra_copy].iterrows():
        issue = _make_issue(
            "duplicate_removed",
            "The same Transaction ID appeared twice with identical details. "
            "The extra copy was removed from the calculations.", row)
        issue["status"] = "auto_resolved"            # handled by the rule; nobody needs to act
        issue["resolution"] = "Removed automatically: identical Transaction ID."
        issues.append(issue)
    txn = txn[~extra_copy].reset_index(drop=True)         # ~ means NOT

    # --- Check 2: the same Transaction ID used for DIFFERENT records ------------------------
    # This should never happen. We can't tell which is right, so we remove nothing and flag both.
    id_clash = txn["Transaction ID"].notna() & txn.duplicated(subset=["Transaction ID"], keep=False)
    for _, row in txn[id_clash].iterrows():
        issues.append(_make_issue(
            "conflicting_transaction_id",
            "This Transaction ID is used by more than one record with different details. "
            "Nothing was removed; a human needs to check.", row))

    # --- Check 3: rows with NO Transaction ID --------------------------------------------------
    # We cannot be sure whether these are new or copies. We never remove them. We look for
    # a look-alike (all LOOKALIKE_FIELDS identical) and flag it for a human either way.
    with_id = txn[txn["Transaction ID"].notna()]
    for _, row in txn[txn["Transaction ID"].isna()].iterrows():
        same = (with_id[LOOKALIKE_FIELDS] == row[LOOKALIKE_FIELDS]).all(axis=1)
        matches = with_id[same]
        if len(matches) > 0:
            ids = ", ".join(matches["Transaction ID"])
            issues.append(_make_issue(
                "possible_duplicate",
                f"No Transaction ID, and every other detail matches {ids}. It is left in the "
                "totals. A human needs to decide whether it is a copy.", row,
                related_ids=list(matches["Transaction ID"])))
        else:
            issues.append(_make_issue(
                "missing_transaction_id",
                "No Transaction ID, so we cannot confirm this record is unique. "
                "It is left in the totals.", row))

    # --- Check 4: a transaction with no Amount -------------------------------------------------------
    # A blank could mean "zero" or "not entered". We do not guess: we leave it blank and flag it.
    for _, row in txn[txn["Amount"].isna()].iterrows():
        issues.append(_make_issue(
            "missing_amount",
            "Amount is blank. Left out of the totals until someone confirms it.", row))

    # Work out which month each transaction belongs to, so we can match it to a budget line.
    # .dt.to_period("M") turns 2026-04-17 into "April 2026"; .to_timestamp() makes it 2026-04-01.
    txn["Month"] = txn["Posting Date"].dt.to_period("M").dt.to_timestamp()

    # --- Check 5: does every transaction belong to a budget line? -------------------------------------
    budget_lines = budget[BUDGET_KEY].drop_duplicates()
    joined = txn.merge(budget_lines, on=BUDGET_KEY, how="left", indicator=True)
    for _, row in joined[joined["_merge"] == "left_only"].iterrows():
        issues.append(_make_issue(
            "no_budget_line",
            "There is no budget line for this cost center, category and month.", row))

    # --- Check 6: is any budget line listed twice? -----------------------------------------------------------
    for _, row in budget[budget.duplicated(subset=BUDGET_KEY, keep="first")].iterrows():
        issues.append(_make_issue(
            "duplicate_budget_line",
            f"Budget line for {row['Cost Center ID']} / {row['Category']} is listed twice. "
            "Totals may be overstated.", row))

    return budget, txn, issues


# =====================================================================================
# THE REVIEW STEP: a human looks at the flagged issues and records decisions in a small
# file. The code applies those decisions ON TOP of the raw data (the workbook itself is
# never edited) and keeps a log of every decision: applied or rejected, and why.
# =====================================================================================

DECISION_COLUMNS = ["Target Transaction ID", "Action", "New Value", "Reason",
                    "Decided By", "Decided On"]

# The only actions a reviewer may use. Anything else is rejected, not guessed at.
#   set_amount         fill in a blank Amount (Target = the transaction; New Value = amount)
#   remove_no_id_copy  delete the no-ID row that copies the Target transaction
#   keep_row           confirm the no-ID row is NOT a duplicate, so it stays


def read_decisions(path):
    """Read the review file (a CSV) into a table."""
    decisions = pd.read_csv(path)
    missing = [col for col in DECISION_COLUMNS if col not in decisions.columns]
    if missing:
        raise ValueError(f"The review file is missing columns: {missing}")
    return decisions


def apply_decisions(txn, issues, decisions):
    """Apply each decision. Return the updated transactions, the updated issues, and a log."""
    txn = txn.copy()          # work on a copy, so the caller's table is never changed
    log = []

    def record(d, result, detail):
        log.append({"target": d["Target Transaction ID"], "action": d["Action"],
                    "result": result, "detail": detail,
                    "decided_by": d["Decided By"], "reason": d["Reason"]})

    def resolve(issue, note):
        issue["status"], issue["resolution"] = "resolved", note

    for _, d in decisions.iterrows():
        target, action = d["Target Transaction ID"], d["Action"]

        if action == "set_amount":
            rows = txn[(txn["Transaction ID"] == target) & txn["Amount"].isna()]
            if rows.empty:                       # safety: only ever fills a BLANK amount
                record(d, "rejected", "No row with that Transaction ID has a blank Amount.")
                continue
            try:
                value = float(d["New Value"])
            except (TypeError, ValueError):
                record(d, "rejected", "New Value is not a number.")
                continue
            txn.loc[rows.index, "Amount"] = value
            note = f"Amount for {target} set from blank to ${value:,.0f}."
            record(d, "applied", note)
            for i in issues:
                if i["type"] == "missing_amount" and i["transaction_id"] == target:
                    resolve(i, note)

        elif action in ("remove_no_id_copy", "keep_row"):
            original = txn[txn["Transaction ID"] == target]
            if original.empty:
                record(d, "rejected", "No transaction has that Transaction ID.")
                continue
            # the no-ID rows that match the original on every look-alike field
            copies = (txn[LOOKALIKE_FIELDS] == original.iloc[0][LOOKALIKE_FIELDS]).all(axis=1) \
                     & txn["Transaction ID"].isna()
            if not copies.any():
                record(d, "rejected", "No row without a Transaction ID matches that transaction.")
                continue
            if action == "remove_no_id_copy":
                txn = txn[~copies]
                note = (f"Removed {int(copies.sum())} row(s) with no Transaction ID that copied {target}. "
                        f"{target} itself was kept.")
            else:
                note = f"Reviewer confirmed the no-ID row matching {target} is not a duplicate. Kept."
            record(d, "applied", note)
            for i in issues:
                if i["type"] == "possible_duplicate" and target in i["related_ids"]:
                    resolve(i, note)

        else:
            record(d, "rejected", f"Unknown action '{action}'.")

    return txn.reset_index(drop=True), issues, log


# --- This block runs only when you start THIS file directly (python agents/variance_engine.py).
# It does not run when another file imports this one. It's our quick "try it out" area.
if __name__ == "__main__":
    data_dir = Path(__file__).resolve().parent.parent / "data"

    budget, txn, issues = load_workbook(data_dir / "sample_opex_2026.xlsx")
    print("STEP 1: read and check the raw data")
    print(f"  Budget rows: {len(budget)}   Transactions after cleaning: {len(txn)}")
    print(f"  Data issues found: {len(issues)}")
    for number, issue in enumerate(issues, start=1):
        print(f"  {number}. [{issue['type']}] {issue['transaction_id'] or '(no ID)'} "
              f"| {issue['po_number']} | {issue['status']}")
    total_before = txn["Amount"].sum()

    print("\nSTEP 2: apply the reviewer's decisions")
    decisions = read_decisions(data_dir / "sample_review_decisions.csv")
    txn, issues, log = apply_decisions(txn, issues, decisions)
    for entry in log:
        print(f"  [{entry['result']}] {entry['action']} on {entry['target']}: {entry['detail']}")
        print(f"      decided by {entry['decided_by']}. Reason: {entry['reason']}")

    def count(status):
        return sum(1 for i in issues if i["status"] == status)

    print("\nSTEP 3: where things stand")
    print(f"  Open (waiting for a human): {count('open')}")
    print(f"  Resolved by a reviewer:     {count('resolved')}")
    print(f"  Handled automatically:      {count('auto_resolved')}")
    print(f"  Transactions now: {len(txn)}")
    print(f"  Total transaction amount: ${total_before:,.0f} before  ->  ${txn['Amount'].sum():,.0f} after")
