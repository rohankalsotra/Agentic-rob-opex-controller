"""
variance_tools.py -- turns the calculated report into TEXT CARDS for Claude to read.

Why this file exists (the trust boundary):
    Claude must NEVER do arithmetic. So by the time anything reaches Claude, every
    number has already been calculated by variance_math.py AND already turned into a
    finished piece of text like "$1.35M" or "+45.7%". Claude only copies that text.
    There is nothing for Claude to add up, round or convert.

    Each dollar amount is given two ways:  "short" ($43.6K)  and  "exact" ($43,612).
    Claude uses "short" by default and "exact" only when someone asks for exact numbers.

No AI in this file. Plain Python: report in -> plain dictionaries of text out.
"""

import pandas as pd

from formatting import format_money, format_money_exact, format_pct
from variance_math import FLAG_PERCENT, FLAG_DOLLARS

# The words a person (or Claude) can use for the period -> the names used inside the report.
PERIOD_NAMES = {"quarter": "Quarter", "ytd": "Year to date"}


def _money(amount, signed=False):
    """One dollar amount, written two ways."""
    return {"short": format_money(amount, signed), "exact": format_money_exact(amount, signed)}


def _card(row):
    """One row of a results table -> one card. Every value is finished text."""
    return {
        "budget": _money(row["Budget"]),
        "actual": _money(row["Actual"]),
        "variance": _money(row["Variance $"], signed=True),
        "variance_pct": format_pct(row["Variance %"]),
        "status": row["Status"],
    }


def _month_text(month):
    return pd.Timestamp(month).strftime("%b %Y")


def build_report_card(report, period="quarter", data_quality_card=None):
    """The variance report for ONE period, as a JSON-friendly dictionary of text.
    `period` is "quarter" or "ytd". If a data quality card is given, a short summary of it
    is placed INSIDE this card, so the caveats always travel with the numbers."""
    if period not in PERIOD_NAMES:
        raise ValueError(f"period must be one of {sorted(PERIOD_NAMES)}, not {period!r}")
    name = PERIOD_NAMES[period]
    p = report["periods"][name]

    total = _card(p["total"].iloc[0])
    total["scope"] = "All cost centers"

    teams = p["by_cost_center"]
    team_cards = [{"cost_center": r["Cost Center"], **_card(r)} for _, r in teams.iterrows()]

    flagged = p["by_line"][p["by_line"]["Status"].isin(["OVER", "UNDER"])]
    flagged_cards = [{"cost_center": r["Cost Center"], "category": r["Category"], **_card(r)}
                     for _, r in flagged.iterrows()]

    team_by_id = teams.set_index("Cost Center ID")
    name_of = team_by_id["Cost Center"].to_dict()      # "CC-200" -> "Marketing": Claude must never guess this
    drivers = []
    for cc_id, info in p["team_drivers"].items():
        t = team_by_id.loc[cc_id]
        drivers.append({
            "cost_center": t["Cost Center"],
            "team_status": t["Status"],
            "team_variance": _money(t["Variance $"], signed=True),
            "team_variance_pct": format_pct(t["Variance %"]),
            "biggest_lines": [{"category": r["Category"], **_card(r)}
                              for _, r in info["top"].iterrows()],
            "all_other_lines": {"count": int(info["other_count"]),
                                "net_variance": _money(info["other_net"], signed=True)},
        })

    c = p["control"]
    card = {
        "report": report["label"],
        "period": name,
        "months": f"{p['start']:%b %Y} to {p['end']:%b %Y}",
        "as_of": f"{report['as_of']:%d %b %Y}",
        "how_to_read": ("Variance = Actual minus Budget. A plus sign means MORE was spent than "
                        "budgeted; a minus sign means LESS. A line or team is flagged OVER or UNDER "
                        f"only when it is off by at least {FLAG_PERCENT:.0%} AND at least "
                        f"{format_money(FLAG_DOLLARS)}."),
        "total": total,
        "cost_centers": team_cards,
        "flagged_lines": flagged_cards,
        "flagged_teams_and_drivers": drivers,
        "control_total": {
            "passed": bool(c["passed"]),
            "transactions_total": _money(c["transactions_total"]),
            "report_total": _money(c["report_total"]),
        },
        "budgeted_months_with_no_transactions": [
            {"cost_center": name_of.get(x["Cost Center ID"], x["Cost Center ID"]),
             "category": x["Category"],
             "month": _month_text(x["Month"]), "budget": _money(x["Budget"]),
             "note": "No transactions were recorded. This could be true, or a posting could be "
                     "missing. Someone should confirm before reading this line as real underspend."}
            for x in p["no_transaction_lines"]
        ],
    }
    if data_quality_card is not None:
        card["data_quality_summary"] = _summarize_data_quality(data_quality_card)
    return card


def _summarize_data_quality(dq):
    """A short, finished-text summary of the data quality card, one sentence per item."""
    def line(item):
        who = item["transaction_id"] or "a row with no Transaction ID"
        return f"{item['type']} on {who} ({item['cost_center']}, {item['month']}): {item['what_was_decided']}"

    return {
        "open_issues_needing_a_human": [line(i) for i in dq["open_needs_a_human"]],
        "resolved_by_a_reviewer": [line(i) for i in dq["resolved_by_a_reviewer"]],
        "resolved_automatically": [line(i) for i in dq["resolved_automatically"]],
    }


def build_data_quality_card(issues, log=None, cost_center_names=None):
    """What was found in the raw data, and what was decided about it."""
    names = cost_center_names or {}

    def describe(issue):
        return {
            "type": issue["type"],
            "status": issue["status"],
            "transaction_id": issue["transaction_id"],
            "po_number": issue["po_number"],
            "cost_center": names.get(issue["cost_center"], issue["cost_center"]),
            "category": issue["category"],
            "month": issue["month"],
            "what_was_found": issue["message"],
            "what_was_decided": issue["resolution"],
        }

    cards = [describe(i) for i in issues]
    return {
        "open_needs_a_human": [c for c in cards if c["status"] == "open"],
        "resolved_by_a_reviewer": [c for c in cards if c["status"] == "resolved"],
        "resolved_automatically": [c for c in cards if c["status"] == "auto_resolved"],
        "reviewer_decisions_applied": [
            {"transaction_id": d["target"], "action": d["action"], "result": d["result"],
             "decided_by": d["decided_by"], "reason": d["reason"]}
            for d in (log or [])
        ],
    }
