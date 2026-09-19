"""
comms_agent.py -- the Executive Comms Agent: drafts the two AI-written parts of the SLT pre-read.

What it may do:     write a headline and a few discussion points.
What it may NOT do: calculate, change a number, invent a cause, or see anything except the
                    hand-off package (outputs/variance_handoff.json).

How a run goes:
    1. Read the hand-off package (finished text only).
    2. Ask Claude for a headline + discussion points, giving it the whole package in the prompt.
       (No tools: nothing for Claude to forget to call.)
    3. Run our checks (comms_checks.py). If any fail, show Claude the problems and let it try
       once more.
    4. If the draft passes, put it in the pre-read. If not, the pre-read keeps its clearly marked
       placeholders. A draft that fails the checks is NEVER used.

Try it (after python agents/handoff.py):    python agents/comms_agent.py
"""

import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv
from claude_agent_sdk import (
    query, ClaudeAgentOptions, AssistantMessage, ResultMessage, TextBlock, ClaudeSDKError,
)

import comms_checks
import preread

PROJECT_DIR = Path(__file__).resolve().parent.parent
MODEL = "haiku"                        # cheap for development; pass "sonnet" for a polished run
MAX_ATTEMPTS = 2                       # first try, plus one correction round

SYSTEM_PROMPT = """You are the Executive Comms Agent on a finance operations team. You write two short \
parts of a pre-read for senior leaders, using ONLY the hand-off package you are given.

You write:
  "headline": one or two sentences (at most 40 words) stating how the quarter looks overall.
  "discussion_points": 3 to 6 short points, each one sentence, for the leaders to discuss.

HARD RULES - these matter more than being helpful:
1. NEVER CALCULATE. Do not add, subtract, round, convert or compare numbers. Copy every number \
character for character from the package, and use the "short" forms (like +$61.7K), never the "exact" ones.
2. Whenever you give a dollar variance, put its percentage next to it.
3. The headline MUST state the total variance, its percentage and its status exactly as the package gives \
them for the quarter ("total").
4. Say OVER or UNDER only for items whose status says OVER or UNDER. Say "on track" for items that are on track.
5. Do NOT explain why anything happened. The data never says why. Phrase discussion points as questions or \
neutral asks (for example "What is behind ...?" or "Can someone confirm ...?").
6. Cover EVERY flagged line in "flagged_lines" for the quarter by naming both its cost center and its \
category. Also raise every entry in "budgeted_months_with_no_transactions", saying no transactions are \
recorded or that a posting may be missing. You may combine related items into one point.
7. Do not judge size or importance: never use words like significant, major, minor, offsetting, entirely, \
mostly, mainly, reliable, accurate, concerning, or modest. Never speculate: no because, due to, likely, \
probably, perhaps, seems, or suggests.
8. Use cost center names exactly as the package gives them.

REPLY FORMAT: reply with ONLY a JSON object and nothing else, exactly like this:
{"headline": "...", "discussion_points": ["...", "..."]}"""


def build_user_prompt(package, previous_reply=None, problems=None):
    """The message sent to Claude: the whole package, and (on a retry) what went wrong."""
    prompt = ("Here is the hand-off package as JSON. Write the headline and discussion points.\n\n"
              + json.dumps(package, indent=1))
    if problems:
        prompt += ("\n\nYour previous reply was REJECTED by automated checks.\n\nPrevious reply:\n"
                   f"{previous_reply}\n\nProblems found:\n" + "\n".join(f"- {p}" for p in problems)
                   + "\n\nWrite a corrected reply that fixes every problem. Same JSON format, nothing else.")
    return prompt


def build_options(model=MODEL):
    """The settings that keep Claude inside its box: no tools, one turn, small budget."""
    return ClaudeAgentOptions(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        tools=[],                      # no built-in tools at all
        allowed_tools=[],              # and none of ours: everything Claude needs is in the prompt
        permission_mode="dontAsk",
        setting_sources=[],
        max_turns=1,
        max_budget_usd=0.10,
    )


async def call_claude(user_prompt, model=MODEL):
    """Send one message to Claude. Returns (reply_text, cost_in_usd)."""
    parts, cost = [], 0.0
    async for message in query(prompt=user_prompt, options=build_options(model)):
        if isinstance(message, AssistantMessage):
            parts += [b.text for b in message.content if isinstance(b, TextBlock)]
        elif isinstance(message, ResultMessage):
            cost = message.total_cost_usd or 0.0
            if message.is_error:
                raise RuntimeError(f"The model call failed: {message.errors or message.result}")
    return "\n".join(parts), cost


async def draft_narrative(package, model_call=call_claude, max_attempts=MAX_ATTEMPTS):
    """Ask for a draft, check it, and allow corrections. `model_call` can be swapped for a fake
    in tests. Returns a dictionary describing what happened."""
    previous, problems = None, None
    attempts, total_cost = [], 0.0
    for number in range(1, max_attempts + 1):
        prompt = build_user_prompt(package, previous, problems)
        reply, cost = await model_call(prompt)
        total_cost += cost or 0.0
        try:
            headline, points = comms_checks.parse_draft(reply)
            problems = comms_checks.check_draft(package, headline, points)
        except ValueError as err:
            headline, points, problems = None, None, [str(err)]
        attempts.append({"attempt": number, "problems": problems})
        if not problems:
            return {"accepted": True, "headline": headline, "discussion_points": points,
                    "attempts": attempts, "cost": total_cost}
        previous = reply
    return {"accepted": False, "headline": None, "discussion_points": None,
            "attempts": attempts, "cost": total_cost}


async def run(model=MODEL):
    package = json.loads(preread.HANDOFF_PATH.read_text(encoding="utf-8"))
    result = await draft_narrative(package, lambda prompt: call_claude(prompt, model))
    text = preread.render_preread(package, result["headline"], result["discussion_points"])
    preread.PREREAD_PATH.write_text(text, encoding="utf-8")

    print("\n----- AI-DRAFTED PARTS -----")
    if result["accepted"]:
        print("Headline:", result["headline"])
        print("Discussion points:")
        for p in result["discussion_points"]:
            print(" -", p)
    else:
        print("The AI draft FAILED the checks, so it was NOT used. The pre-read keeps its placeholders.")
    print("\n----- CHECKS (plain Python, no AI) -----")
    for a in result["attempts"]:
        print(f"Attempt {a['attempt']}: " + ("PASSED" if not a["problems"] else "FAILED"))
        for problem in a["problems"]:
            print("   -", problem)
    print(f"\nAI draft accepted: {'yes' if result['accepted'] else 'NO'}   "
          f"Attempts: {len(result['attempts'])}   Cost (USD): ${result['cost']:.4f}")
    print(f"Pre-read saved to {preread.PREREAD_PATH.relative_to(PROJECT_DIR)}")
    return result


if __name__ == "__main__":
    load_dotenv(PROJECT_DIR / ".env")
    chosen_model = sys.argv[1] if len(sys.argv) > 1 else MODEL
    try:
        asyncio.run(run(chosen_model))
    except (ClaudeSDKError, RuntimeError) as err:
        print("Problem:", err)
