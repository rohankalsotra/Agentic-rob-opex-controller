"""Tests for comms_agent.py using a FAKE Claude, so no key and no cost. We test our own loop:
does it accept good drafts, ask for corrections, and refuse drafts that keep failing?"""
import ast
import asyncio
import json
from pathlib import Path

import pytest

pytest.importorskip("claude_agent_sdk")
import comms_agent as ca
import handoff
from variance_state import load_state
from test_comms_checks import GOOD_HEADLINE, GOOD_POINTS

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
pytestmark = pytest.mark.skipif(
    not ((DATA / "sample_opex_2026.xlsx").exists() and (DATA / "sample_review_decisions.csv").exists()),
    reason="Generate the sample data first.")

GOOD_REPLY = json.dumps({"headline": GOOD_HEADLINE, "discussion_points": GOOD_POINTS})
BAD_REPLY = json.dumps({"headline": GOOD_HEADLINE, "discussion_points":
                        [GOOD_POINTS[0].replace("+$61.7K", "+$61.9K")] + GOOD_POINTS[1:]})


@pytest.fixture(scope="module")
def package():
    return handoff.build_handoff(load_state())


def fake_claude(*replies):
    """A pretend Claude that answers with the given replies, one per call, and records the prompts."""
    prompts = []
    remaining = list(replies)

    async def model_call(prompt):
        prompts.append(prompt)
        return remaining.pop(0), 0.01
    model_call.prompts = prompts
    return model_call


def run(package, *replies, max_attempts=2):
    fake = fake_claude(*replies)
    return asyncio.run(ca.draft_narrative(package, fake, max_attempts)), fake


# ---- the loop --------------------------------------------------------------------------------
def test_a_good_first_draft_is_accepted(package):
    result, fake = run(package, GOOD_REPLY)
    assert result["accepted"] and result["headline"] == GOOD_HEADLINE
    assert len(fake.prompts) == 1 and result["cost"] == pytest.approx(0.01)


def test_a_bad_draft_gets_a_second_chance_that_shows_the_problem(package):
    result, fake = run(package, BAD_REPLY, GOOD_REPLY)
    assert result["accepted"] and len(result["attempts"]) == 2
    retry_prompt = fake.prompts[1]
    assert "REJECTED" in retry_prompt and "+$61.9K" in retry_prompt      # it was told exactly what was wrong
    assert result["cost"] == pytest.approx(0.02)


def test_a_draft_that_keeps_failing_is_never_used(package):
    result, fake = run(package, BAD_REPLY, BAD_REPLY)
    assert not result["accepted"]
    assert result["headline"] is None and result["discussion_points"] is None
    assert len(fake.prompts) == 2                                         # it stopped: no endless loop


def test_a_reply_that_is_not_json_counts_as_a_failed_attempt(package):
    result, _ = run(package, "Sure! Here is a headline: all good.", GOOD_REPLY)
    assert result["accepted"]
    assert "not a JSON object" in result["attempts"][0]["problems"][0]


def test_the_package_is_what_claude_is_shown(package):
    _, fake = run(package, GOOD_REPLY)
    assert "+$43.6K" in fake.prompts[0] and "TXN-000121 itself was kept" in fake.prompts[0]


def test_a_failed_draft_leaves_placeholders_in_the_pre_read(package):
    import preread
    result, _ = run(package, BAD_REPLY, BAD_REPLY)
    text = preread.render_preread(package, result["headline"], result["discussion_points"])
    assert preread.HEADLINE_PLACEHOLDER in text and preread.DISCUSSION_PLACEHOLDER in text


# ---- the guardrails --------------------------------------------------------------------------
def test_claude_is_locked_inside_its_box():
    options = ca.build_options()
    assert options.tools == [] and options.allowed_tools == []
    assert options.permission_mode == "dontAsk" and options.setting_sources == []
    assert options.max_turns == 1 and options.max_budget_usd <= 0.25
    assert options.model == "haiku"


def test_the_rules_are_in_the_prompt():
    for phrase in ("NEVER CALCULATE", "Do NOT explain why", "EVERY flagged line",
                   "budgeted_months_with_no_transactions", "ONLY a JSON object"):
        assert phrase in ca.SYSTEM_PROMPT


def test_the_comms_side_only_imports_what_it_should():
    """Neither Comms file may import the calculator, pandas or the hand-off builder."""
    allowed = {
        "comms_checks.py": {"json", "re", "verifier"},
        "comms_agent.py": {"asyncio", "json", "sys", "pathlib", "dotenv", "claude_agent_sdk",
                           "comms_checks", "preread"},
    }
    for name, expected in allowed.items():
        tree = ast.parse((ROOT / "agents" / name).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                imported.add((node.module or "").split(".")[0])
        assert imported == expected, (name, imported ^ expected)
