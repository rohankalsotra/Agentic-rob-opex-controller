"""
variance_agent.py -- the Variance Agent: Claude explains the numbers, Python calculates them.

How it fits together (read this top to bottom):

    your question --> Claude --asks--> TOOL (our Python code) --answers--> Claude --> plain-English answer
                                        |
                                        +-- reads the workbook, applies reviewer decisions,
                                            calculates with pandas, formats as text cards

Claude never sees the raw spreadsheet and never does arithmetic. It can only call the
two tools below and copy the text they return. That is the "trust boundary".

Try it (from the project folder, with your venv on):
    python agents/variance_agent.py "Give me the Q3 variance report"
    python agents/variance_agent.py "What is the exact variance for Field Sales in Q3?"
"""

import asyncio
import json
import sys

from dotenv import load_dotenv
from claude_agent_sdk import (
    query, ClaudeAgentOptions, tool, create_sdk_mcp_server,
    AssistantMessage, ResultMessage, TextBlock, ToolUseBlock, ClaudeSDKError,
)

import variance_tools as vt
import verifier
from variance_state import PROJECT_DIR, DATA_DIR, AS_OF, load_state    # the calculator side (no AI)

MODEL = "haiku"                         # cheap model for development; "sonnet" for polished runs
SERVER_NAME = "variance"                # the name of our tool "bundle"
TOOL_NAMES = ["get_variance_report", "get_data_quality"]
# The SDK names each tool  mcp__<bundle>__<tool>  and we must list exactly which ones Claude may use.
ALLOWED_TOOLS = [f"mcp__{SERVER_NAME}__{name}" for name in TOOL_NAMES]

# --- THE RULES CLAUDE MUST FOLLOW ------------------------------------------------------------
SYSTEM_PROMPT = """You are the Variance Agent on a finance operations team. You explain budget-versus-actual \
(operating expense) results to senior leaders, in plain English.

HARD RULES - these matter more than being helpful:
1. NEVER CALCULATE. Do not add, subtract, average, round, convert units or compare sizes of numbers. \
Every number in your answer must be copied character for character from a tool result.
2. Every dollar amount comes in two forms: "short" (like $43.6K) and "exact" (like $43,600). Use the \
"short" form by default. Use the "exact" form only when the person asks for exact or full figures.
3. Whenever you give a dollar variance, give its percentage next to it. Leaders want both.
4. Only call something OVER or UNDER if its "status" says OVER or UNDER. If the status is "On track", \
say it is on track. You may quote its variance, but do not describe it as a problem.
5. Do NOT invent reasons. The data shows what changed, not why. If asked why, say the data does not \
say, and point to the line that would need a human follow-up.
6. If a number you need is not in the tool results, say you do not have it. Never estimate.
7. If the control total did not pass, say the report cannot be trusted yet. If it passed, state that \
as a plain fact ("the control total passed") and NOTHING stronger. Never say the report is "reliable", \
"accurate" or "trustworthy": that is a human's judgment, not yours.
8. If "budgeted_months_with_no_transactions" is not empty, you MUST mention each entry, with the cost \
center name exactly as given, and copy its note word for word. Add nothing to the note, especially no reasons \
or guesses about what happened.
9. Report data-quality items exactly as "what_was_decided" says. Say precisely which row was removed and \
which was kept. Never say a row was removed unless the text says so. If the data quality tool lists open \
issues, say so.
10. Do not judge size or importance. Avoid words like "significantly", "major", "offsetting", "minor", \
"entirely", "mostly" or "mainly". Say what the status field says and give the numbers. To explain what drives \
a team, name its "biggest_lines" and copy the "all_other_lines" figures instead of summarizing them in words.
11. Only two periods exist: the quarter (Q3 2026) and year to date, both as of 30 Sep 2026.

HOW TO WORK: call get_variance_report for the period(s) the question needs. It already includes a \
"data_quality_summary". In a full report, give that summary one or two plain lines (what was removed or \
changed, and by whom). Call get_data_quality only when the person wants detail about the data issues.

STYLE: lead with the headline in one sentence, then a short bullet list of what is flagged. Keep it brief."""


# --- STEP 1: the two tools Claude is allowed to call -----------------------------------------
def _text_result(payload, log, is_error=False):
    """Wrap a dictionary as the reply format the SDK expects, and keep a copy in `log`."""
    text = json.dumps(payload, indent=1)
    log.append(text)
    return {"content": [{"type": "text", "text": text}], "is_error": is_error}


def make_tools(state):
    """Build the two tools. `state` is the loaded data from load_state()."""

    @tool(
        "get_variance_report",
        "Get the budget-versus-actual variance report for ONE period: totals, every cost center, "
        "flagged lines, what drives each flagged team, a control-total check, and a summary of "
        "data quality issues. All numbers are already calculated and formatted as text.",
        {"type": "object",
         "properties": {"period": {"type": "string", "enum": ["quarter", "ytd"],
                                   "description": "'quarter' = Q3 2026 (Jul-Sep). 'ytd' = year to date (Jan-Sep)."}},
         "required": ["period"]},
    )
    async def get_variance_report(args):
        try:
            dq = vt.build_data_quality_card(state["issues"], state["log"], state["names"])
            return _text_result(vt.build_report_card(state["report"], args["period"], dq),
                                state["tool_outputs"])
        except ValueError as err:
            return _text_result({"error": str(err)}, state["tool_outputs"], is_error=True)

    @tool(
        "get_data_quality",
        "Get what was found in the raw data (duplicates, missing amounts) and what was decided about each. "
        "Use it for a full report or whenever the reliability of the numbers matters.",
        {"type": "object", "properties": {}},
    )
    async def get_data_quality(args):
        return _text_result(vt.build_data_quality_card(state["issues"], state["log"], state["names"]),
                            state["tool_outputs"])

    return [get_variance_report, get_data_quality]


# --- STEP 2: the settings that keep Claude inside its box ------------------------------------
def build_options(state, model=MODEL):
    return ClaudeAgentOptions(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        tools=[],                                   # NO built-in tools: no files, no web, no shell
        mcp_servers={SERVER_NAME: create_sdk_mcp_server(SERVER_NAME, tools=make_tools(state))},
        allowed_tools=ALLOWED_TOOLS,                # only our two tools may run
        permission_mode="dontAsk",                  # never stop to ask; anything not allowed is refused
        setting_sources=[],                         # ignore any settings files on this computer
        max_turns=8,                                # safety net: no endless loops
        max_budget_usd=0.25,                        # safety net: stop if a run passes 25 cents
    )


# --- STEP 3: ask a question and show what happens ---------------------------------------------
async def ask(question, state=None, model=MODEL):
    state = state or load_state()
    state["tool_outputs"].clear()                  # forget anything from an earlier question
    answer_parts = []
    verdict = None
    print(f"Question: {question}\n")
    try:
        async for message in query(prompt=question, options=build_options(state, model)):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, ToolUseBlock):
                        print(f"[Claude called a tool: {block.name.split('__')[-1]} {block.input}]")
                    elif isinstance(block, TextBlock):
                        answer_parts.append(block.text)
            elif isinstance(message, ResultMessage):
                answer = "\n".join(answer_parts)
                print("\n----- ANSWER -----")
                print(answer)
                # The number check: plain Python, no AI. Every number in the answer must
                # appear in what the tools handed to Claude.
                verdict = verifier.verify_numbers(answer, state["tool_outputs"])
                print("\n----- NUMBER CHECK (plain Python, no AI) -----")
                print("\n".join(verifier.describe(verdict)))
                print("\n----- Receipt -----")
                print("Succeeded:", not message.is_error)
                cost = message.total_cost_usd
                print("Cost (USD):", "unknown" if cost is None else f"${cost:.4f}")
                print("Time (seconds):", round(message.duration_ms / 1000, 1))
                if message.is_error:
                    print("Details:", message.errors or message.result)
    except ClaudeSDKError as err:
        print("\nThe SDK reported a problem:", err)
    return {"answer": "\n".join(answer_parts), "verdict": verdict}


if __name__ == "__main__":
    load_dotenv(PROJECT_DIR / ".env")
    question = " ".join(sys.argv[1:]) or "Give me the Q3 variance report."
    asyncio.run(ask(question))
