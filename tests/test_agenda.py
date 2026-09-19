"""Tests for agenda.py: a timed agenda made only by fixed rules, so we can test it exactly."""
import ast
import copy
import json
from pathlib import Path

import pytest

import agenda
import handoff
import verifier
from variance_state import load_state

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

needs_sample = pytest.mark.skipif(
    not ((DATA / "sample_opex_2026.xlsx").exists() and (DATA / "sample_review_decisions.csv").exists()),
    reason="Generate the sample data first.")


@pytest.fixture(scope="module")
def package():
    return handoff.build_handoff(load_state())


def with_flagged_count(package, n):
    """A copy of the package with exactly n flagged lines (copies of the first one, renamed)."""
    changed = copy.deepcopy(package)
    template = changed["quarter"]["flagged_lines"][0]
    changed["quarter"]["flagged_lines"] = [dict(template, category=f"Category {i}") for i in range(n)]
    if n == 0:
        changed["quarter"]["flagged_teams_and_drivers"] = []
    return changed


# ---- the trust boundary ----------------------------------------------------------------------
def test_the_agenda_never_imports_the_calculator_or_any_ai():
    tree = ast.parse((ROOT / "agents" / "agenda.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported == {"json", "pathlib", "preread"}


# ---- the time arithmetic ---------------------------------------------------------------------
@pytest.mark.parametrize("total, parts, expected", [
    (40, 4, [10, 10, 10, 10]), (40, 3, [14, 13, 13]), (10, 3, [4, 3, 3]), (7, 1, [7]),
])
def test_split_minutes_shares_evenly_and_gives_leftovers_to_the_first(total, parts, expected):
    assert agenda.split_minutes(total, parts) == expected


@needs_sample
@pytest.mark.parametrize("meeting", [30, 31, 45, 47, 60, 90, 120, 180])
@pytest.mark.parametrize("flagged_count", [0, 1, 2, 4, 7, 12])
def test_the_minutes_always_add_up_and_nothing_is_too_short(package, meeting, flagged_count):
    items = agenda.build_agenda(with_flagged_count(package, flagged_count), meeting)
    assert sum(i["minutes"] for i in items) == meeting
    fixed_titles = {"Opening and overview", "Missing transactions check",
                    "Data quality and open items", "Decisions and follow-ups", "Open discussion"}
    for item in items:
        floor = 1 if item["title"] in fixed_titles else agenda.MIN_FLAGGED_MINUTES
        assert item["minutes"] >= floor, item["title"]


@pytest.mark.parametrize("bad", [29, 181, 0, -60, 60.5, "60", None, True])
def test_a_meeting_length_outside_the_rules_is_refused(package, bad):
    with pytest.raises(ValueError):
        agenda.build_agenda(package, bad)


# ---- the content -----------------------------------------------------------------------------
@needs_sample
def test_the_sample_agenda_is_exactly_what_we_expect(package):
    items = agenda.build_agenda(package, 60)
    assert [(i["title"], i["minutes"]) for i in items] == [
        ("Opening and overview", 5),
        ("Field Sales / Contractors (OVER)", 10),
        ("Engineering Ops / Software & Subscriptions (UNDER)", 10),   # ranked by dollar swing, not alphabet
        ("Marketing / Travel (OVER)", 10),
        ("Marketing / Events & Marketing (UNDER)", 10),
        ("Missing transactions check", 5),
        ("Data quality and open items", 5),
        ("Decisions and follow-ups", 5),
    ]


@needs_sample
def test_a_short_meeting_batches_the_smallest_lines_into_a_read_out(package):
    items = agenda.build_agenda(package, 30)              # 30 minutes: 10 left for 4 flagged lines
    titles = [i["title"] for i in items]
    assert "Other flagged lines (read-out only)" in titles
    assert titles[1] == "Field Sales / Contractors (OVER)"                # the biggest keeps its own slot
    batch = next(i for i in items if i["title"].startswith("Other flagged"))
    assert len(batch["details"]) == 2 and batch["minutes"] >= agenda.MIN_FLAGGED_MINUTES


@needs_sample
def test_with_nothing_flagged_the_time_goes_to_open_discussion(package):
    quiet = copy.deepcopy(package)
    quiet["quarter"]["flagged_lines"] = []
    quiet["quarter"]["budgeted_months_with_no_transactions"] = []
    quiet["year_to_date"]["budgeted_months_with_no_transactions"] = []
    items = agenda.build_agenda(quiet, 60)
    assert [i["title"] for i in items] == ["Opening and overview", "Open discussion",
                                           "Data quality and open items", "Decisions and follow-ups"]
    assert sum(i["minutes"] for i in items) == 60


@needs_sample
def test_the_missing_transactions_note_is_in_the_agenda(package):
    text = agenda.render_agenda(package)
    assert "Missing transactions check" in text
    assert "Someone should confirm before reading this line as real underspend" in text
    assert "TXN-000121 itself was kept" in text


@needs_sample
def test_times_are_shown_as_clock_ranges_that_run_from_zero_to_the_meeting_length(package):
    text = agenda.render_agenda(package, 90)
    assert "| 0:00-0:05 | Opening and overview | 5 |" in text
    assert "Decisions and follow-ups | 5 |" in text and "1:25-1:30" in text
    assert "# Q3 2026 QBR Agenda (90 minutes)" in text


@needs_sample
def test_every_money_percent_and_id_in_the_agenda_comes_from_the_package(package):
    """Minutes are plain numbers we add ourselves, so we only check money, percentages and IDs."""
    text = agenda.render_agenda(package)
    allowed = verifier.extract(json.dumps(package))
    found = verifier.extract(text)
    for kind in ("money", "percent", "id"):
        missing = [t for t in found[kind] if t not in allowed[kind]]
        assert missing == [], (kind, missing)
