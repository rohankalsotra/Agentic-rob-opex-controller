"""
orchestrator.py -- the Orchestrator: turns a plain-English request into a checked, fixed workflow.

    "Give me the Q3 variance report and prep the QBR agenda"
        |
        v
    1. ROUTER (Claude): reads ONLY the request text and picks steps from a fixed menu.
       It never sees any data or numbers.
    2. PLAN CHECK (plain Python): drops anything not on the menu, adds steps that are needed,
       fixes the order, and validates the meeting length. Claude cannot add or reorder a step.
    3. STEPS (plain Python, in a fixed order):
         build_report   -> calculate, write the hand-off package     (no AI)
         draft_preread  -> Comms Agent drafts, checks, assembles      (AI only in two slots, checked)
         draft_agenda   -> timed agenda from fixed rules              (no AI)
    4. AUDIT LOG: outputs/run_log.json records the request, Claude's raw reply, the checked plan,
       what ran, and what it cost.

Every run recalculates from the source data, so a pre-read or agenda is never built from stale numbers.

Try it (from the project folder, venv on):
    python agents/orchestrator.py "Give me the Q3 variance report and prep the QBR agenda"
"""

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from claude_agent_sdk import (
    query, ClaudeAgentOptions, AssistantMessage, ResultMessage, TextBlock, ClaudeSDKError,
)

import agenda
import comms_agent
import handoff
from variance_state import PROJECT_DIR, load_state

MODEL = "haiku"
OUT_DIR = PROJECT_DIR / "outputs"

# --- THE MENU: the only things this system will ever do ----------------------------------------
MENU = {
    "build_report": "Read the source data, check it, apply reviewer decisions, calculate the Q3 and "
                    "year-to-date variance, and write the hand-off package. Needed before any drafting.",
    "draft_preread": "Draft the SLT pre-read: the written variance report for senior leaders.",
    "draft_agenda": "Build the timed QBR meeting agenda.",
}
STEP_ORDER = list(MENU)                    # this is also the fixed order steps always run in

ROUTER_PROMPT = """You are the router for an operating-expense (OPEX) reporting system. You do NOT do any \
reporting yourself, and you never see any figures. You only decide which steps to run.

You may choose ONLY from this menu:
""" + "\n".join(f'  "{name}": {desc}' for name, desc in MENU.items()) + """

RULES:
1. Use only the step names above, exactly as written. Never invent a step.
2. A request for the "variance report", "pre-read" or "briefing" means build_report and draft_preread. \
A request for an "agenda" means build_report and draft_agenda.
3. If the request gives a meeting length (for example "90 minute QBR"), put the whole number of minutes in \
"meeting_minutes". Otherwise use null.
4. If the request asks for something that is not on the menu (sending email, changing a budget, anything else), \
do not pretend it is possible: put it in "unsupported" in a few words. Still list any supported steps the \
request also asks for.

REPLY FORMAT: reply with ONLY a JSON object and nothing else:
{"steps": ["..."], "meeting_minutes": null, "unsupported": null}

EXAMPLES:
Request: Give me the Q3 variance report and prep the QBR agenda
Reply: {"steps": ["build_report", "draft_preread", "draft_agenda"], "meeting_minutes": null, "unsupported": null}
Request: Just the agenda for a 90 minute QBR
Reply: {"steps": ["build_report", "draft_agenda"], "meeting_minutes": 90, "unsupported": null}
Request: Write the pre-read and email it to the SLT
Reply: {"steps": ["build_report", "draft_preread"], "meeting_minutes": null, "unsupported": "emailing the SLT"}"""


# --- STEP 1: the router (Claude sees ONLY the request) -----------------------------------------
def build_router_options(model=MODEL):
    return ClaudeAgentOptions(
        model=model, system_prompt=ROUTER_PROMPT,
        tools=[], allowed_tools=[],                # no tools of any kind
        permission_mode="dontAsk", setting_sources=[],
        max_turns=1, max_budget_usd=0.05,
    )


async def call_router(request, model=MODEL):
    """Send the request (and nothing else) to Claude. Returns (reply_text, cost)."""
    parts, cost = [], 0.0
    async for message in query(prompt=request, options=build_router_options(model)):
        if isinstance(message, AssistantMessage):
            parts += [b.text for b in message.content if isinstance(b, TextBlock)]
        elif isinstance(message, ResultMessage):
            cost = message.total_cost_usd or 0.0
            if message.is_error:
                raise RuntimeError(f"The router call failed: {message.errors or message.result}")
    return "\n".join(parts), cost


# --- STEP 2: the plan check (plain Python; Claude's choice is only a suggestion) ---------------
def parse_plan(raw_reply):
    """Turn the router's reply into a checked plan:
    {"steps": [...in fixed order...], "minutes": int, "unsupported": str or None, "warnings": [...]}
    Raises ValueError if there is nothing on the menu to run."""
    start, end = raw_reply.find("{"), raw_reply.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("The router's reply was not a JSON object.")
    try:
        data = json.loads(raw_reply[start:end + 1])
    except json.JSONDecodeError as err:
        raise ValueError(f"The router's reply was not valid JSON ({err.msg}).")
    steps = data.get("steps")
    if not isinstance(steps, list):
        raise ValueError('The router\'s reply needs "steps" as a list.')

    warnings, requested = [], []
    for step in steps:
        if isinstance(step, str) and step.strip() in MENU:
            if step.strip() not in requested:
                requested.append(step.strip())
        else:
            warnings.append(f"Ignored a step that is not on the menu: {step!r}.")
    unsupported = data.get("unsupported") if isinstance(data.get("unsupported"), str) \
        and data["unsupported"].strip() else None
    if not requested:
        raise ValueError("No step from the menu was requested"
                         + (f" (not supported: {unsupported})." if unsupported else "."))

    if any(s.startswith("draft_") for s in requested) and "build_report" not in requested:
        requested.append("build_report")
        warnings.append("Added build_report: the drafting steps need freshly calculated figures.")
    ordered = [s for s in STEP_ORDER if s in requested]

    minutes = data.get("meeting_minutes")
    if minutes is None:
        minutes = agenda.DEFAULT_MINUTES
    elif (isinstance(minutes, int) and not isinstance(minutes, bool)
          and agenda.MIN_MEETING <= minutes <= agenda.MAX_MEETING):
        pass
    else:
        warnings.append(f"Ignored the meeting length {minutes!r}; using {agenda.DEFAULT_MINUTES} minutes.")
        minutes = agenda.DEFAULT_MINUTES
    return {"steps": ordered, "minutes": minutes, "unsupported": unsupported, "warnings": warnings}


# --- STEP 3: run the steps, always in the fixed order -----------------------------------------
async def orchestrate(request, router_call=call_router, comms_call=comms_agent.call_claude,
                      out_dir=OUT_DIR, load=load_state, progress=None):
    """Run one request end to end. Returns the run log (a dictionary); also saved as run_log.json.
    `progress` is an optional function that is called with a short message as each stage starts,
    so the person is never left staring at a silent screen (an AI step can take about a minute)."""
    say = progress or (lambda message: None)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    log = {"when": datetime.now(timezone.utc).isoformat(timespec="seconds"), "request": request,
           "router_reply": None, "plan": None, "outcome": None, "steps": [], "files": [],
           "cost": {"router": 0.0, "comms": 0.0, "total": 0.0}, "human_review_required": True}

    def finish(outcome):
        log["outcome"] = outcome
        log["cost"]["total"] = log["cost"]["router"] + log["cost"]["comms"]
        (out_dir / "run_log.json").write_text(json.dumps(log, indent=1), encoding="utf-8")
        return log

    say("Asking the router which steps your request needs...")
    try:
        reply, cost = await router_call(request)
    except (ClaudeSDKError, RuntimeError) as err:
        log["steps"].append({"step": "router", "status": "failed", "detail": str(err)})
        return finish("router_failed")
    log["router_reply"], log["cost"]["router"] = reply, cost or 0.0

    try:
        plan = parse_plan(reply)
    except ValueError as err:
        log["plan"] = {"error": str(err)}
        return finish("nothing_to_run")
    log["plan"] = plan
    say("Plan checked: " + " -> ".join(plan["steps"]))

    handoff_path = out_dir / "variance_handoff.json"
    package = None
    waits = {"build_report": "calculating (no AI)",
             "draft_preread": "the AI draft and its checks can take about a minute",
             "draft_agenda": "building from fixed rules (no AI)"}
    for number, step in enumerate(plan["steps"], start=1):
        say(f"Step {number} of {len(plan['steps'])}: {step}: {waits[step]}...")
        record = {"step": step, "status": "done", "detail": ""}
        log["steps"].append(record)
        try:
            if step == "build_report":
                package_built = handoff.build_handoff(load())
                handoff.save_handoff(package_built, handoff_path)
                package = handoff.load_handoff(handoff_path)     # from here on, ONLY the file is used
                log["files"].append(handoff_path.name)
            elif step == "draft_preread":
                path = out_dir / "preread_draft.md"
                result = await comms_agent.make_preread(package, comms_call, path)
                log["cost"]["comms"] += result["cost"]
                record["detail"] = (f"AI draft accepted: {'yes' if result['accepted'] else 'NO (placeholders kept)'}; "
                                    f"attempts: {len(result['attempts'])}")
                record["problems"] = [a["problems"] for a in result["attempts"]]
                log["files"].append(path.name)
            elif step == "draft_agenda":
                path = out_dir / "qbr_agenda.md"
                path.write_text(agenda.render_agenda(package, plan["minutes"]), encoding="utf-8")
                record["detail"] = f"{plan['minutes']} minutes"
                log["files"].append(path.name)
        except Exception as err:                                 # one failed step must not hide the others
            record["status"], record["detail"] = "failed", f"{type(err).__name__}: {err}"
            if step == "build_report":                           # nothing else can run without it
                return finish("build_report_failed")
    failed = any(s["status"] == "failed" for s in log["steps"])
    return finish("finished_with_failures" if failed else "finished")


def print_summary(log):
    print(f"\nRequest: {log['request']}")
    print("\n----- PLAN (Claude suggested; plain Python checked) -----")
    plan = log["plan"] or {}
    if "error" in plan:
        print("Nothing to run:", plan["error"])
        print("I can do these things:")
        for name, desc in MENU.items():
            print(f"  - {name}: {desc}")
    else:
        print("Steps, in order:", " -> ".join(plan["steps"]) if plan.get("steps") else "none")
        if any(s == "draft_agenda" for s in plan.get("steps", [])):
            print("Meeting length:", plan["minutes"], "minutes")
        if plan.get("unsupported"):
            print("Not supported (skipped):", plan["unsupported"])
        for w in plan.get("warnings", []):
            print("Note:", w)
    print("\n----- RESULTS -----")
    for s in log["steps"]:
        print(f"{s['step']}: {s['status']}" + (f" ({s['detail']})" if s["detail"] else ""))
        for attempt_problems in s.get("problems", []):
            for problem in attempt_problems:
                print("     check problem:", problem)
    if log["files"]:
        print("Files written to the outputs folder:", ", ".join(log["files"] + ["run_log.json"]))
    c = log["cost"]
    print(f"\nCost (USD): router ${c['router']:.4f} + Comms Agent ${c['comms']:.4f} = ${c['total']:.4f}")
    print("Outcome:", log["outcome"])
    if log["files"]:
        print("A human must review the pre-read and the agenda before they are sent.")


if __name__ == "__main__":
    load_dotenv(PROJECT_DIR / ".env")
    request = " ".join(sys.argv[1:]) or "Give me the Q3 variance report and prep the QBR agenda"
    print_summary(asyncio.run(orchestrate(request, progress=lambda m: print(m, flush=True))))
