"""Tests for preread.py: the pre-read is assembled by plain code, so we can test it exactly."""
import ast
import copy
import json
from pathlib import Path

import pytest

import handoff
import preread
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


# ---- THE TRUST BOUNDARY, ENFORCED ------------------------------------------------------------
def test_the_comms_side_never_imports_the_calculator():
    """preread.py may only import json and pathlib. If someone adds pandas, the engine, or the
    math, this test fails: the Comms side must not be able to calculate."""
    tree = ast.parse((ROOT / "agents" / "preread.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported == {"json", "pathlib"}


# ---- everything that must always be there ----------------------------------------------------
@needs_sample
def test_every_required_section_and_caveat_is_present(package):
    text = preread.render_preread(package)
    for must_have in ["# Q3 2026 OPEX Pre-Read", "As of 30 Sep 2026", "Headline (AI-drafted)",
                      "Discussion points (AI-drafted)", "Appendix: exact figures",
                      "Control total (quarter): passed", "Control total (year to date): passed",
                      "TXN-000121 itself was kept", "$2,500",
                      "Marketing / Events & Marketing, Sep 2026",
                      "Someone should confirm before reading this line as real underspend",
                      "Owner: to be assigned", "A human has reviewed this pre-read"]:
        assert must_have in text, must_have


@needs_sample
def test_the_known_answers_appear_exactly(package):
    text = preread.render_preread(package)
    assert "| **All cost centers** | $1.35M | $1.39M | +$43.6K (+3.2%) | On track |" in text
    assert "| Field Sales | $405.0K | $466.2K | +$61.2K (+15.1%) | OVER |" in text
    assert "| Engineering Ops | $324.0K | $295.0K | -$29.0K (-8.9%) | On track |" in text
    assert "| **All cost centers** | $1,350,000 | $1,393,600 | +$43,600 (+3.2%) | On track |" in text
    assert "**Field Sales / Contractors: OVER** at +$61.7K (+45.7%)" in text


@needs_sample
def test_the_document_never_rewrites_a_number(package):
    """The number checker from the Variance Agent, pointed at the pre-read. Every number in the
    finished document must appear in the hand-off package. Nothing was reformatted or invented."""
    text = preread.render_preread(package)
    verdict = verifier.verify_numbers(text, [json.dumps(package)])
    assert verdict["passed"], verdict["failures"]
    assert verdict["warnings"] == []


@needs_sample
def test_placeholders_appear_until_the_ai_slots_are_filled(package):
    text = preread.render_preread(package)
    assert preread.HEADLINE_PLACEHOLDER in text and preread.DISCUSSION_PLACEHOLDER in text


@needs_sample
def test_ai_slots_fill_in_where_expected(package):
    text = preread.render_preread(package, headline="Q3 is on track overall.",
                                  discussion_points=["Point one.", "Point two."])
    assert "Q3 is on track overall." in text
    assert "- Point one.\n- Point two." in text
    assert preread.HEADLINE_PLACEHOLDER not in text and preread.DISCUSSION_PLACEHOLDER not in text


# ---- what changes when the data changes ------------------------------------------------------
@needs_sample
def test_a_failed_control_total_is_shouted_not_hidden(package):
    bad = copy.deepcopy(package)
    bad["quarter"]["control_total"]["passed"] = False
    assert "FAILED. Do not rely on these figures" in preread.render_preread(bad)


@needs_sample
def test_open_issues_become_open_items_and_follow_ups(package):
    changed = copy.deepcopy(package)
    changed["data_quality"]["open_needs_a_human"] = [{
        "type": "missing_amount", "status": "open", "transaction_id": "TXN-000777", "po_number": "PO-1",
        "cost_center": "Marketing", "category": "Travel", "month": "Jul 2026",
        "what_was_found": "Amount is blank.", "what_was_decided": None}]
    text = preread.render_preread(changed)
    assert "**Open, needs a human:** TXN-000777 (Marketing, Travel, Jul 2026): Amount is blank." in text
    assert "Resolve the open data issue on TXN-000777" in text


@needs_sample
def test_with_nothing_flagged_the_document_says_so(package):
    quiet = copy.deepcopy(package)
    for key in ("quarter", "year_to_date"):
        quiet[key]["flagged_lines"] = []
        quiet[key]["flagged_teams_and_drivers"] = []
        quiet[key]["budgeted_months_with_no_transactions"] = []
    quiet["data_quality"]["open_needs_a_human"] = []
    text = preread.render_preread(quiet)
    assert "No line is flagged." in text
    assert "No follow-ups are generated from this report." in text
    assert "No transactions recorded" not in text


@needs_sample
def test_follow_ups_never_name_a_person_or_a_date(package):
    text = preread.render_preread(package)
    follow_ups = text.split("## Suggested follow-ups")[1].split("## Appendix")[0]
    assert follow_ups.count("Owner: to be assigned · Due: to be assigned") == \
           follow_ups.count("- [ ]")
