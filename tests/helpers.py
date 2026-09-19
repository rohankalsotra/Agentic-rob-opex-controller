"""
helpers.py -- small tools for building tiny, hand-made test data.

Each helper creates a normal-looking row with sensible defaults. A test then
changes only the ONE detail it cares about, so the test's point is easy to see:

    txn_row(Amount=500)      -> an ordinary transaction, except its amount is 500
"""
import pandas as pd


def txn_row(**overrides):
    """One transaction row. Pass details to override, e.g. txn_row(Amount=500)."""
    row = {
        "Transaction ID": "TXN-1", "PO Number": "PO-1", "PO Owner": "Ana Lopez",
        "Cost Center ID": "CC-1", "Cost Center": "Team One", "Category": "Travel",
        "Vendor": "Vendor Co", "Project Name": "Project A",
        "Project Description": "Original scope.",
        "Posting Date": pd.Timestamp(2026, 7, 10), "Amount": 1000,
    }
    row.update(overrides)
    return row


def budget_row(**overrides):
    """One budget row (July, $1,000 by default)."""
    row = {"Cost Center ID": "CC-1", "Cost Center": "Team One", "Category": "Travel",
           "Month": pd.Timestamp(2026, 7, 1), "Budget": 1000}
    row.update(overrides)
    return row


def budget_months(months, **overrides):
    """Budget rows for several months, e.g. budget_months([7, 8, 9], Budget=2000)."""
    return [budget_row(Month=pd.Timestamp(2026, m, 1), **overrides) for m in months]


def with_month(rows):
    """Turn transaction rows into a table with the 'Month' column that the math expects
    (the engine normally adds this; here we do it by hand for isolated tests)."""
    df = pd.DataFrame(rows)
    df["Month"] = df["Posting Date"].dt.to_period("M").dt.to_timestamp()
    return df


def write_workbook(path, budget_rows, txn_rows):
    """Save hand-made rows as a real Excel workbook, so the engine reads it like a real file."""
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        pd.DataFrame(budget_rows).to_excel(writer, sheet_name="Budget", index=False)
        pd.DataFrame(txn_rows).to_excel(writer, sheet_name="Transactions", index=False)
    return path
