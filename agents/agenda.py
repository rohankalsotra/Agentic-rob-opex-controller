"""
agenda.py -- builds a timed QBR meeting agenda from the hand-off package. NO AI, no calculator.

Everything here is decided by fixed rules, so the same package always gives the same agenda:

    Fixed items (5 minutes each):   Opening and overview
                                    Missing transactions check   (only if there are any)
                                    Data quality and open items
                                    Decisions and follow-ups
    Everything left over is shared equally between the flagged lines, BIGGEST variance first
    (the calculator side already ranked them). Each flagged line gets at least 3 minutes; if the
    meeting is too short for that, the smallest ones are batched into one "read-out" item.
    Any minute that does not divide evenly goes to the first (biggest) items.

The agenda never invents anything: every figure is copied from the package, and the minutes are
plain whole-number arithmetic. The meeting length is checked: 30 to 180 minutes.

Try it (after python agents/handoff.py):    python agents/agenda.py
"""

import json
from pathlib import Path

import preread

PROJECT_DIR = Path(__file__).resolve().parent.parent
HANDOFF_PATH = PROJECT_DIR / "outputs" / "variance_handoff.json"
AGENDA_PATH = PROJECT_DIR / "outputs" / "qbr_agenda.md"

DEFAULT_MINUTES = 60
MIN_MEETING, MAX_MEETING = 30, 180
FIXED_MINUTES = 5
MIN_FLAGGED_MINUTES = 3
DISCUSS_PROMPT = "Discuss: what is behind this, and who follows up?"


def split_minutes(total, parts):
    """Share `total` minutes between `parts` items as evenly as whole minutes allow.
    Leftover minutes go one each to the first items. 40 into 3 -> [14, 13, 13]."""
    base, extra = divmod(total, parts)
    return [base + (1 if i < extra else 0) for i in range(parts)]


def _clock(minutes):
    return f"{minutes // 60}:{minutes % 60:02d}"


def _line_detail(f):
    return (f"{f['cost_center']} / {f['category']}: {f['status']} at {f['variance']['short']} "
            f"({f['variance_pct']}); budget {f['budget']['short']}, actual {f['actual']['short']}")


def build_agenda(package, total_minutes=DEFAULT_MINUTES):
    """Return a list of agenda items, each {"title", "minutes", "details"}, adding up to total_minutes."""
    if (not isinstance(total_minutes, int) or isinstance(total_minutes, bool)
            or not MIN_MEETING <= total_minutes <= MAX_MEETING):
        raise ValueError(f"The meeting length must be a whole number of minutes from "
                         f"{MIN_MEETING} to {MAX_MEETING}.")
    q, y, dq = package["quarter"], package["year_to_date"], package["data_quality"]
    flagged = q["flagged_lines"]
    empty = preread.empty_months(package)

    def total_line(label, card):
        t = card["total"]
        return (f"{label}: {t['actual']['short']} actual against {t['budget']['short']} budget, "
                f"variance {t['variance']['short']} ({t['variance_pct']}), status {t['status']}.")

    opening = {"title": "Opening and overview", "minutes": FIXED_MINUTES,
               "details": [total_line(package["report"], q), total_line("Year to date", y), q["how_to_read"]]}

    missing = None
    if empty:
        missing = {"title": "Missing transactions check", "minutes": FIXED_MINUTES,
                   "details": [f"{e['cost_center']} / {e['category']}, {e['month']}: budget {e['budget']['short']}. "
                               f"{e['note']}" for e in empty]}

    quality_details = []
    for label, card in (("quarter", q), ("year to date", y)):
        c = card["control_total"]
        verdict = "passed" if c["passed"] else "FAILED. Do not rely on these figures"
        quality_details.append(f"Control total ({label}): {verdict}.")
    quality_details += [f"Open, needs a human: {preread.who(i)}: {i['what_was_found']}"
                        for i in dq["open_needs_a_human"]]
    quality_details += [f"Reviewer decision: {preread.who(i)}: {i['what_was_decided']}"
                        for i in dq["resolved_by_a_reviewer"]]
    quality_details += [f"Handled automatically: {preread.who(i)}: {i['what_was_decided']}"
                        for i in dq["resolved_automatically"]]
    quality = {"title": "Data quality and open items", "minutes": FIXED_MINUTES, "details": quality_details}

    follow_ups = preread.follow_up_items(package)
    decisions = {"title": "Decisions and follow-ups", "minutes": FIXED_MINUTES,
                 "details": (["Assign an owner and a due date to each follow-up:"] + follow_ups)
                 if follow_ups else ["No follow-ups were generated from this report."]}

    fixed = [opening] + ([missing] if missing else []) + [quality, decisions]
    remaining = total_minutes - sum(item["minutes"] for item in fixed)

    # --- the flagged-line items: share what is left ---
    if not flagged:
        flagged_items = [{"title": "Open discussion", "minutes": remaining,
                          "details": ["No line is flagged this quarter."]}]
    else:
        slots = remaining // MIN_FLAGGED_MINUTES            # how many items the time can hold
        own = len(flagged) if len(flagged) <= slots else slots - 1
        batch = flagged[own:]
        count = own + (1 if batch else 0)
        minutes = split_minutes(remaining, count)
        flagged_items = [{"title": f"{f['cost_center']} / {f['category']} ({f['status']})",
                          "minutes": m, "details": [_line_detail(f), DISCUSS_PROMPT]}
                         for f, m in zip(flagged[:own], minutes)]
        if batch:
            flagged_items.append({"title": "Other flagged lines (read-out only)", "minutes": minutes[-1],
                                  "details": [_line_detail(f) for f in batch]})

    items = [opening] + flagged_items + ([missing] if missing else []) + [quality, decisions]
    assert sum(item["minutes"] for item in items) == total_minutes      # a safety net, never expected to fire
    return items


def render_agenda(package, total_minutes=DEFAULT_MINUTES):
    """The agenda as markdown."""
    items = build_agenda(package, total_minutes)
    rows, sections, clock = [], [], 0
    for item in items:
        span = f"{_clock(clock)}-{_clock(clock + item['minutes'])}"
        rows.append(f"| {span} | {item['title']} | {item['minutes']} |")
        sections.append(f"### {span} {item['title']} ({item['minutes']} min)\n\n"
                        + "\n".join(f"- {d}" for d in item["details"]))
        clock += item["minutes"]
    return "\n".join([
        f"# {package['report']} QBR Agenda ({total_minutes} minutes)",
        "",
        f"_As of {package['as_of']}. DRAFT for human review. The order and the times come from fixed rules "
        "in code; no AI model wrote this agenda._",
        "",
        "| Time | Item | Minutes |", "|---|---|---:|", *rows,
        "",
        "## Detail",
        "",
        "\n\n".join(sections),
        "",
        "## How the times were set",
        "",
        f"- Opening, missing-transactions check (only if there is one), data quality and decisions get "
        f"{FIXED_MINUTES} minutes each.",
        f"- The remaining time is shared equally across the flagged lines, biggest dollar variance first, "
        f"with at least {MIN_FLAGGED_MINUTES} minutes each. If the meeting is too short for that, the "
        "smallest lines are batched into one read-out item.",
        "- A minute that does not divide evenly goes to the first (biggest) items.",
        "",
        "---",
        "",
        "- [ ] A human has reviewed this agenda before it was sent.",
        "",
    ])


if __name__ == "__main__":
    package = json.loads(HANDOFF_PATH.read_text(encoding="utf-8"))
    text = render_agenda(package)
    AGENDA_PATH.write_text(text, encoding="utf-8")
    print(text)
    print(f"(Saved to {AGENDA_PATH.relative_to(PROJECT_DIR)})")
