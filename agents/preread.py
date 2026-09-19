"""
preread.py -- builds the SLT pre-read (a one-page briefing for senior leaders) from the hand-off package.

THE RULE FOR THIS FILE: it only READS the hand-off file and arranges finished text.
    * It never calculates: every figure is copied from the package exactly as written.
    * It never imports the calculator (no pandas, no variance_math). A test enforces this.
    * Everything that MUST always be in the pre-read (the numbers, what is flagged, the data
      caveats, the follow-ups) is written here by plain code, so it can never be forgotten
      or reworded by an AI model.
    * Only two slots are left for an AI model to write: the headline and the discussion points.
      They are clearly labelled "AI-drafted" in the document.

Try it (after python agents/handoff.py):    python agents/preread.py
"""

import json
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
HANDOFF_PATH = PROJECT_DIR / "outputs" / "variance_handoff.json"
PREREAD_PATH = PROJECT_DIR / "outputs" / "preread_draft.md"

HEADLINE_PLACEHOLDER = "_[Headline not drafted yet]_"
DISCUSSION_PLACEHOLDER = "_[Discussion points not drafted yet]_"


# --- small building blocks -------------------------------------------------------------------
def _table(card, exact=False):
    """A table of the total and every cost center. `exact` switches to full-dollar figures."""
    size = "exact" if exact else "short"
    lines = ["| Cost center | Budget | Actual | Variance | Status |", "|---|---:|---:|---:|---|"]
    rows = [("**All cost centers**", card["total"])] + [(c["cost_center"], c) for c in card["cost_centers"]]
    for name, c in rows:
        lines.append(f"| {name} | {c['budget'][size]} | {c['actual'][size]} | "
                     f"{c['variance'][size]} ({c['variance_pct']}) | {c['status']} |")
    return "\n".join(lines)


def _who(item):
    """How a data-quality item is named: its Transaction ID (if it has one) and where it sits."""
    ident = item["transaction_id"] or "Row with no Transaction ID"
    return f"{ident} ({item['cost_center']}, {item['category']}, {item['month']})"


def _empty_months(package):
    """Budgeted months with no transactions, from both periods, each listed once."""
    seen, out = set(), []
    for card in (package["quarter"], package["year_to_date"]):
        for e in card["budgeted_months_with_no_transactions"]:
            key = (e["cost_center"], e["category"], e["month"])
            if key not in seen:
                seen.add(key)
                out.append(e)
    return out


def _flagged(card):
    """Flagged lines, then what drives each flagged team."""
    if not card["flagged_lines"]:
        return "No line is flagged."
    lines = [f"- **{f['cost_center']} / {f['category']}: {f['status']}** at {f['variance']['short']} "
             f"({f['variance_pct']}) against a budget of {f['budget']['short']}"
             for f in card["flagged_lines"]]
    drivers = []
    for d in card["flagged_teams_and_drivers"]:
        biggest = ", ".join(f"{b['category']} {b['variance']['short']} ({b['variance_pct']})"
                            for b in d["biggest_lines"])
        other = d["all_other_lines"]
        drivers.append(f"- **{d['cost_center']}** is {d['team_status']} at {d['team_variance']['short']} "
                       f"({d['team_variance_pct']}). Biggest lines: {biggest}. "
                       f"The other {other['count']} lines together net {other['net_variance']['short']}.")
    text = "\n".join(lines)
    if drivers:
        text += "\n\n**What is driving the flagged teams**\n\n" + "\n".join(drivers)
    return text


def _data_quality(package):
    """Control totals, every data issue and what was decided, and every empty budgeted month."""
    dq = package["data_quality"]
    lines = []
    for label, card in (("quarter", package["quarter"]), ("year to date", package["year_to_date"])):
        c = card["control_total"]
        verdict = "passed" if c["passed"] else "FAILED. Do not rely on these figures"
        lines.append(f"- **Control total ({label}): {verdict}.** Transactions "
                     f"{c['transactions_total']['short']}, report {c['report_total']['short']}.")
    lines += [f"- **Open, needs a human:** {_who(i)}: {i['what_was_found']}" for i in dq["open_needs_a_human"]]
    lines += [f"- **Reviewer decision:** {_who(i)}: {i['what_was_decided']}" for i in dq["resolved_by_a_reviewer"]]
    lines += [f"- **Handled automatically:** {_who(i)}: {i['what_was_decided']}" for i in dq["resolved_automatically"]]
    deciders = sorted({d["decided_by"] for d in dq["reviewer_decisions_applied"]})
    if deciders:
        lines.append(f"- Reviewer decisions were made by {', '.join(deciders)}.")
    for e in _empty_months(package):
        lines.append(f"- **No transactions recorded:** {e['cost_center']} / {e['category']}, {e['month']} "
                     f"(budget {e['budget']['short']}). {e['note']}")
    return "\n".join(lines)


def _follow_ups(package):
    """Suggested follow-ups, generated from the flags and the open items. Never names an owner or a date."""
    q, y = package["quarter"], package["year_to_date"]
    items, seen = [], set()
    for suffix, card in (("", q), (" (year to date)", y)):
        for f in card["flagged_lines"]:
            if (f["cost_center"], f["category"]) not in seen:
                seen.add((f["cost_center"], f["category"]))
                items.append(f"Review what is behind {f['cost_center']} / {f['category']} "
                             f"({f['status']}, {f['variance']['short']}, {f['variance_pct']}){suffix}.")
    for e in _empty_months(package):
        items.append(f"Confirm whether {e['cost_center']} / {e['category']} spending for {e['month']} "
                     f"is missing from the ledger (budget {e['budget']['short']}).")
    for i in package["data_quality"]["open_needs_a_human"]:
        items.append(f"Resolve the open data issue on {_who(i)}: {i['what_was_found']}")
    if not items:
        return "No follow-ups are generated from this report."
    return "\n".join(f"- [ ] {text}  \n  Owner: to be assigned · Due: to be assigned" for text in items)


# --- the whole document ----------------------------------------------------------------------
def render_preread(package, headline=None, discussion_points=None):
    """Assemble the pre-read. `headline` (text) and `discussion_points` (list of text) are the two
    slots an AI model may fill. Leave them out and clearly marked placeholders appear instead."""
    q, y = package["quarter"], package["year_to_date"]
    if discussion_points:
        points = "\n".join(f"- {p}" for p in discussion_points)
    else:
        points = DISCUSSION_PLACEHOLDER

    sections = [
        f"# {package['report']} OPEX Pre-Read\n\n_As of {package['as_of']}. DRAFT for human review._",
        "> Sections marked *calculated by code* copy their figures directly from the calculation "
        "engine. Sections marked *AI-drafted* were written by an AI model using only those figures "
        "and must be reviewed before this is sent.",
        f"## Headline (AI-drafted)\n\n{headline or HEADLINE_PLACEHOLDER}",
        f"## The numbers (calculated by code)\n\n### {package['report']} ({q['months']})\n\n{_table(q)}\n\n"
        f"### Year to date ({y['months']})\n\n{_table(y)}\n\n_{q['how_to_read']}_",
        f"## What is flagged (calculated by code)\n\n### {package['report']}\n\n{_flagged(q)}\n\n"
        f"### Year to date\n\n{_flagged(y)}",
        f"## Data quality and caveats (calculated by code)\n\n{_data_quality(package)}",
        f"## Discussion points (AI-drafted)\n\n{points}",
        f"## Suggested follow-ups (generated by code from the flags)\n\n{_follow_ups(package)}",
        f"## Appendix: exact figures (calculated by code)\n\n### {package['report']}\n\n"
        f"{_table(q, exact=True)}\n\n### Year to date\n\n{_table(y, exact=True)}",
        "---\n\n- [ ] A human has reviewed this pre-read before it was sent.",
    ]
    return "\n\n".join(sections) + "\n"


if __name__ == "__main__":
    package = json.loads(HANDOFF_PATH.read_text(encoding="utf-8"))
    text = render_preread(package)
    PREREAD_PATH.write_text(text, encoding="utf-8")
    print(text)
    print(f"(Saved to {PREREAD_PATH.relative_to(PROJECT_DIR)})")
