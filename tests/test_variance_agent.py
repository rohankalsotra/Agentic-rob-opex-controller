"""Tests for variance_agent.py. These do NOT call Claude (no key, no cost). They check the
parts WE control: what the tools return, and the settings that keep Claude inside its box."""
import asyncio
import json
from pathlib import Path

import pytest

pytest.importorskip("claude_agent_sdk")
import variance_agent as va

DATA = Path(__file__).resolve().parent.parent / "data"
pytestmark = pytest.mark.skipif(
    not ((DATA / "sample_opex_2026.xlsx").exists() and (DATA / "sample_review_decisions.csv").exists()),
    reason="Generate the sample data first.")


@pytest.fixture(scope="module")
def state():
    return va.load_state()


def call(state, tool_name, args):
    """Run one of our tools directly, the way the SDK would, and return (is_error, parsed JSON)."""
    tool_obj = {t.name: t for t in va.make_tools(state)}[tool_name]
    result = asyncio.run(tool_obj.handler(args))
    return result["is_error"], json.loads(result["content"][0]["text"])


# ---- the tools -------------------------------------------------------------------------------
def test_there_are_exactly_two_tools(state):
    assert sorted(t.name for t in va.make_tools(state)) == sorted(va.TOOL_NAMES)


def test_quarter_tool_returns_the_q3_card(state):
    is_error, card = call(state, "get_variance_report", {"period": "quarter"})
    assert not is_error
    assert card["report"] == "Q3 2026"
    assert card["total"]["variance"]["exact"] == "+$43,600"
    assert card["control_total"]["passed"] is True
    assert any("TXN-000121 itself was kept" in x
               for x in card["data_quality_summary"]["resolved_by_a_reviewer"])


def test_ytd_tool_returns_the_year_to_date_card(state):
    _, card = call(state, "get_variance_report", {"period": "ytd"})
    assert card["period"] == "Year to date" and card["months"] == "Jan 2026 to Sep 2026"


def test_a_bad_period_comes_back_as_an_error_not_a_crash(state):
    is_error, payload = call(state, "get_variance_report", {"period": "next year"})
    assert is_error and "period" in payload["error"]


def test_data_quality_tool_shows_names_and_decisions(state):
    is_error, card = call(state, "get_data_quality", {})
    assert not is_error
    assert card["open_needs_a_human"] == []
    assert card["resolved_automatically"][0]["cost_center"] == "Customer Success"
    assert len(card["reviewer_decisions_applied"]) == 2


# ---- the guardrails --------------------------------------------------------------------------
def test_claude_is_locked_inside_its_box(state):
    options = va.build_options(state)
    assert options.tools == []                                   # no built-in tools at all
    assert options.allowed_tools == ["mcp__variance__get_variance_report",
                                     "mcp__variance__get_data_quality"]
    assert options.permission_mode == "dontAsk"
    assert options.setting_sources == []
    assert options.max_budget_usd is not None and options.max_budget_usd <= 0.5
    assert options.max_turns is not None and options.max_turns <= 10


def test_dev_runs_use_the_cheap_model(state):
    assert va.build_options(state).model == "haiku"


def test_the_rules_are_in_the_prompt():
    for phrase in ("NEVER CALCULATE", "character for character", "\"exact\"", "On track", "Do NOT invent",
                   "budgeted_months_with_no_transactions", "Never say the report is", "which was kept",
                   "data_quality_summary",
                   "word for word", "entirely"):
        assert phrase in va.SYSTEM_PROMPT


def test_no_tool_ever_hands_claude_a_bare_cost_center_id(state):
    """Claude guessed the wrong team name from an ID once. IDs are not shown at all now."""
    for tool_name, args in [("get_variance_report", {"period": "quarter"}),
                            ("get_variance_report", {"period": "ytd"}),
                            ("get_data_quality", {})]:
        _, payload = call(state, tool_name, args)
        assert "CC-" not in json.dumps(payload)
