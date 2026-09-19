"""
variance_math.py -- the variance calculations. Plain pandas arithmetic, no AI involved.

For a chosen "as of" date (say 30 Sep 2026) it reports two periods:
    * the quarter   (Q3 = Jul-Sep)
    * year to date  (Jan-Sep)
at three levels: all cost centers together, each cost center, and each cost center
+ category. For each it shows Budget, Actual, Variance in dollars, Variance in percent,
and a status flag.

Try it:   python agents/variance_math.py
"""

from pathlib import Path
import pandas as pd

import variance_engine as engine                       # our "read and check" file
from formatting import format_money, format_pct        # our display helpers

# --- SETTINGS: the flag rule ---------------------------------------------------------------
# ASSUMPTION (a starting point, please change to match your team's convention):
# a line is flagged only when it is off by AT LEAST this percent AND at least this many dollars.
# Requiring both stops tiny lines with big percentages from crying wolf.
FLAG_PERCENT = 0.10          # 10%
FLAG_DOLLARS = 10_000        # $10,000


def period_bounds(as_of):
    """Work out the quarter and the year for an 'as of' date.
    Calendar quarters: Q1 = Jan-Mar, Q2 = Apr-Jun, Q3 = Jul-Sep, Q4 = Oct-Dec."""
    as_of = pd.Timestamp(as_of)
    if as_of != as_of + pd.offsets.MonthEnd(0):
        raise ValueError("as_of must be the last day of a month (for example 2026-09-30), "
                         "because budgets and actuals are tracked by whole months.")
    quarter = (as_of.month - 1) // 3 + 1
    quarter_start = pd.Timestamp(as_of.year, 3 * (quarter - 1) + 1, 1)
    year_start = pd.Timestamp(as_of.year, 1, 1)
    is_quarter_end = as_of.month % 3 == 0 and as_of == as_of + pd.offsets.MonthEnd(0)
    label = f"Q{quarter} {as_of.year}" + ("" if is_quarter_end else " (quarter to date)")
    return {"label": label, "quarter": (quarter_start, as_of), "ytd": (year_start, as_of)}


def _status(row):
    """OVER / UNDER / On track, using the flag rule above."""
    if pd.isna(row["Variance %"]):
        return "n/a"                                   # budget was zero: % is undefined
    if row["Variance %"] >= FLAG_PERCENT and row["Variance $"] >= FLAG_DOLLARS:
        return "OVER"
    if row["Variance %"] <= -FLAG_PERCENT and row["Variance $"] <= -FLAG_DOLLARS:
        return "UNDER"
    return "On track"


def _add_variance(df):
    """Given a table with Budget and Actual, add the variance columns.
    Variance $ = Actual - Budget.  Positive = spent MORE than budgeted.
    Variance % = Variance $ / Budget."""
    df["Variance $"] = df["Actual"] - df["Budget"]
    df["Variance %"] = (df["Variance $"] / df["Budget"]).where(df["Budget"] != 0)
    df["Status"] = df.apply(_status, axis=1)
    return df


def _summarize(budget, txn, start, end, by):
    """Add up budget and actual for the months between start and end, grouped by `by`."""
    b = budget[(budget["Month"] >= start) & (budget["Month"] <= end)]
    t = txn[(txn["Month"] >= start) & (txn["Month"] <= end)]

    budget_sum = b.groupby(by, as_index=False)["Budget"].sum()
    actual_sum = t.groupby(by, as_index=False)["Amount"].sum().rename(columns={"Amount": "Actual"})

    # how="left" keeps every budget line, even ones with no spending. Those get NaN, then 0.
    out = budget_sum.merge(actual_sum, on=by, how="left")
    out["Actual"] = out["Actual"].fillna(0)
    return _add_variance(out)


def _team_drivers(by_line, by_team, top_n=3):
    """For each FLAGGED team, find the lines driving its variance: the biggest dollar
    swings first. Also add up all the remaining lines, so nothing is hidden."""
    drivers = {}
    flagged_teams = by_team[by_team["Status"].isin(["OVER", "UNDER"])]
    for _, team in flagged_teams.iterrows():
        lines = by_line[by_line["Cost Center ID"] == team["Cost Center ID"]]
        order = lines["Variance $"].abs().sort_values(ascending=False).index   # biggest swing first
        lines = lines.loc[order]
        drivers[team["Cost Center ID"]] = {
            "top": lines.head(top_n),
            "other_count": len(lines) - min(top_n, len(lines)),
            "other_net": lines.iloc[top_n:]["Variance $"].sum(),
        }
    return drivers


def build_variance_report(budget, txn, as_of):
    """Build the full report. `txn` must already be cleaned (see variance_engine.py)."""
    bounds = period_bounds(as_of)
    report = {"as_of": pd.Timestamp(as_of), "label": bounds["label"], "periods": {}}

    for name, key in [("Quarter", "quarter"), ("Year to date", "ytd")]:
        start, end = bounds[key]
        in_period_b = budget[(budget["Month"] >= start) & (budget["Month"] <= end)]
        in_period_t = txn[(txn["Month"] >= start) & (txn["Month"] <= end)]

        by_line = _summarize(budget, txn, start, end,
                             ["Cost Center ID", "Cost Center", "Category"])
        by_team = _summarize(budget, txn, start, end, ["Cost Center ID", "Cost Center"])

        total = pd.DataFrame([{"Scope": "All cost centers",
                               "Budget": by_line["Budget"].sum(),
                               "Actual": by_line["Actual"].sum()}])
        total = _add_variance(total)

        # CONTROL TOTAL: the report's actuals must equal the raw transactions in the period.
        # If they differ, some transactions were dropped on the way (for example, no budget line).
        diff = in_period_t["Amount"].sum() - by_line["Actual"].sum()
        control = {"transactions_total": float(in_period_t["Amount"].sum()),
                   "report_total": float(by_line["Actual"].sum()),
                   "passed": bool(abs(diff) < 0.005)}   # plain Python types: safe to send as JSON later

        # Budgeted months with NO transactions at all: could be true, or a missing posting.
        counts = txn.groupby(engine.BUDGET_KEY).size().rename("n").reset_index()
        lines = in_period_b.merge(counts, on=engine.BUDGET_KEY, how="left")
        empty = lines[lines["n"].isna()][["Cost Center ID", "Category", "Month", "Budget"]]

        report["periods"][name] = {
            "start": start, "end": end, "total": total, "by_cost_center": by_team,
            "by_line": by_line, "control": control,
            "team_drivers": _team_drivers(by_line, by_team),
            "no_transaction_lines": empty.to_dict("records"),
        }
    return report


def to_display(df):
    """A text-only copy of a results table, using K / M / B and percentages."""
    out = df.drop(columns=["Budget", "Actual", "Variance $", "Variance %", "Status"]).copy()
    out["Budget"] = df["Budget"].map(format_money)
    out["Actual"] = df["Actual"].map(format_money)
    out["Variance $"] = df["Variance $"].map(lambda v: format_money(v, signed=True))
    out["Variance %"] = df["Variance %"].map(format_pct)
    out["Status"] = df["Status"]
    return out


# --- Runs only when you start THIS file directly: python agents/variance_math.py ---------------
if __name__ == "__main__":
    pd.set_option("display.width", 220)
    pd.set_option("display.max_columns", 20)
    data_dir = Path(__file__).resolve().parent.parent / "data"

    # 1) read + check, 2) apply the reviewer's decisions, 3) calculate
    budget, txn, issues = engine.load_workbook(data_dir / "sample_opex_2026.xlsx")
    decisions = engine.read_decisions(data_dir / "sample_review_decisions.csv")
    txn, issues, log = engine.apply_decisions(txn, issues, decisions)
    report = build_variance_report(budget, txn, as_of="2026-09-30")

    print(f"VARIANCE REPORT: {report['label']}, as of {report['as_of']:%d %b %Y}")
    print(f"Flag rule: OVER/UNDER when off by at least {FLAG_PERCENT:.0%} AND "
          f"{format_money(FLAG_DOLLARS)}. Variance = Actual - Budget (+ means overspent).")

    for name, p in report["periods"].items():
        print(f"\n{'=' * 100}\n{name.upper()}: {p['start']:%b} to {p['end']:%b %Y}\n{'=' * 100}")
        print("\nAll cost centers")
        print(to_display(p["total"]).to_string(index=False))
        print("\nBy cost center")
        print(to_display(p["by_cost_center"].drop(columns="Cost Center ID")).to_string(index=False))
        team_rows = p["by_cost_center"].set_index("Cost Center ID")
        if not p["team_drivers"]:
            print("\nNo cost center is flagged.")
        for cc_id, info in p["team_drivers"].items():
            t = team_rows.loc[cc_id]
            print(f"\n{t['Cost Center']} is {t['Status']} ({format_money(t['Variance $'], True)}, "
                  f"{format_pct(t['Variance %'])}). What is driving it:")
            print(to_display(info["top"].drop(columns=["Cost Center ID", "Cost Center"]))
                  .to_string(index=False))
            print(f"   The other {info['other_count']} lines together net "
                  f"{format_money(info['other_net'], True)}.")
        flagged = p["by_line"][p["by_line"]["Status"].isin(["OVER", "UNDER"])]
        print(f"\nFlagged lines (cost center + category): {len(flagged)}")
        print(to_display(flagged.drop(columns="Cost Center ID")).to_string(index=False))
        c = p["control"]
        print(f"\nControl total: transactions {format_money(c['transactions_total'])} vs "
              f"report {format_money(c['report_total'])} -> {'PASS' if c['passed'] else 'FAIL'}")
        for line in p["no_transaction_lines"]:
            print(f"Note: no transactions for {line['Cost Center ID']} / {line['Category']} in "
                  f"{line['Month']:%b %Y} (budget {format_money(line['Budget'])}). "
                  "Could be true or a missing posting.")
