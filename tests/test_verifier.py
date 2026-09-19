"""Tests for verifier.py: does the number checker catch what it should, and pass what it should?

Several tests use REAL answers Claude gave during our own testing (pasted below as text),
checked against the REAL tool output from the sample data."""
import asyncio
import json
from pathlib import Path

import pytest

pytest.importorskip("claude_agent_sdk")            # the tool output comes from the agent's tools
import variance_agent as va
import verifier as vf

DATA = Path(__file__).resolve().parent.parent / "data"
pytestmark = pytest.mark.skipif(
    not ((DATA / "sample_opex_2026.xlsx").exists() and (DATA / "sample_review_decisions.csv").exists()),
    reason="Generate the sample data first.")

# Claude's real answer from round 4 (all correct).
GOOD_ANSWER = """**Q3 2026 is on track overall at +$43.6K / +3.2%, but Field Sales is OVER budget.**

**Flagged items:**
- **Field Sales OVER** by +$61.2K / +15.1%, driven by Contractors at +$61.7K / +45.7%
- **Marketing Travel OVER** by +$20.3K / +33.8%
- **Marketing Events & Marketing UNDER** by -$11.5K / -31.9%
- **Engineering Ops Software & Subscriptions UNDER** by -$32.3K / -59.8%

**Data quality:**
The control total passed. A reviewer removed 1 duplicate row in Field Sales (Apr 2026) that copied TXN-000121, which was kept. A missing amount on TXN-000269 (Finance & Admin, Jun 2026) was set to $2,500. One identical duplicate (TXN-000056, Customer Success, Feb 2026) was removed automatically.

**Note:** Marketing Events & Marketing had no transactions recorded in Sep 2026 against a $12.0K budget."""


@pytest.fixture(scope="module")
def tool_texts():
    """Exactly what Claude receives when it asks for the Q3 report."""
    state = va.load_state()
    for t in va.make_tools(state):
        if t.name == "get_variance_report":
            asyncio.run(t.handler({"period": "quarter"}))
    return list(state["tool_outputs"])


def check(answer, tool_texts):
    return vf.verify_numbers(answer, tool_texts)


# ---- passes when it should -------------------------------------------------------------------
def test_a_correct_real_answer_passes(tool_texts):
    verdict = check(GOOD_ANSWER, tool_texts)
    assert verdict["passed"], verdict["failures"]
    assert verdict["failures"] == [] and verdict["warnings"] == []
    assert verdict["checked"] >= 20                    # it really did look at plenty of numbers


# ---- fails when it should --------------------------------------------------------------------
@pytest.mark.parametrize("wrong, right", [
    ("+$61.9K", "+$61.2K"),                            # one digit off
    ("+45.9%", "+45.7%"),                              # wrong percent
    ("TXN-000999", "TXN-000121"),                      # invented transaction
    ("$3,000", "$2,500"),                              # wrong reviewer amount
])
def test_an_altered_number_is_caught(tool_texts, wrong, right):
    assert right in GOOD_ANSWER
    verdict = check(GOOD_ANSWER.replace(right, wrong), tool_texts)
    assert not verdict["passed"]
    assert any(wrong in f for f in verdict["failures"])


def test_a_flipped_sign_is_a_failure(tool_texts):
    verdict = check(GOOD_ANSWER.replace("-$11.5K", "+$11.5K"), tool_texts)
    assert not verdict["passed"]
    assert "opposite" in verdict["failures"][0]


def test_a_dropped_sign_is_only_a_warning(tool_texts):
    verdict = check(GOOD_ANSWER.replace("+$61.2K", "$61.2K"), tool_texts)
    assert verdict["passed"]
    assert any("$61.2K" in w for w in verdict["warnings"])


def test_numbers_claude_calculated_itself_are_caught(tool_texts):
    answer = GOOD_ANSWER + "\n\nTogether the two OVER lines add up to $82.0K."
    verdict = check(answer, tool_texts)
    assert not verdict["passed"]
    assert any("$82.0K" in f for f in verdict["failures"])


def test_a_number_from_another_period_is_caught(tool_texts):
    """+16.0% is a real year-to-date number, but the tool output here is Q3 only."""
    verdict = check(GOOD_ANSWER + "\nYear to date Field Sales Contractors are +16.0%.", tool_texts)
    assert not verdict["passed"]


def test_an_answer_with_numbers_but_no_tool_output_fails():
    verdict = check("Q3 is +$43.6K over.", [])
    assert not verdict["passed"]


def test_an_answer_with_no_numbers_passes_trivially():
    assert check("Nothing to report.", [])["passed"]


# ---- what it deliberately cannot catch (so nobody over-trusts it) ---------------------------
def test_known_limit_a_wrong_team_name_is_NOT_caught(tool_texts):
    """Round 3: Claude said 'Field Sales' for the Sep 2026 gap; it belongs to Marketing.
    Every number in that sentence exists in the tool output, so this check passes it.
    That is why a human still reads the answer, and why IDs are never given without names."""
    wrong_team = GOOD_ANSWER.replace("Marketing Events & Marketing had no transactions",
                                     "Field Sales Events & Marketing had no transactions")
    assert check(wrong_team, tool_texts)["passed"]


# ---- the pieces ------------------------------------------------------------------------------
def test_list_numbering_and_quarter_labels_are_not_counted_as_numbers():
    found = vf.extract("Q3 results:\n1. first\n2. second")
    assert found["plain"] == [] and found["money"] == []


def test_describe_prints_a_clear_verdict(tool_texts):
    ok = vf.describe(check(GOOD_ANSWER, tool_texts))
    bad = vf.describe(check(GOOD_ANSWER.replace("+$61.2K", "+$61.9K"), tool_texts))
    assert ok[0].startswith("PASSED") and bad[0].startswith("FAILED")


# ---- punctuation is not part of a number (bug found while building the pre-read) -------------
def test_a_comma_after_a_number_is_punctuation_not_part_of_it():
    found = vf.extract("Set to $2,500, then 3, then $1,234,567.")
    assert found["money"] == ["$2,500", "$1,234,567"]
    assert found["plain"] == ["3"]


def test_a_number_followed_by_a_comma_in_json_can_be_found():
    tool_output = '{"count": 3, "next": 4}'
    assert vf.verify_numbers("The other 3 lines net nothing.", [tool_output])["passed"]
