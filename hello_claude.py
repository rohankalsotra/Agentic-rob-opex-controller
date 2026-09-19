"""
hello_claude.py -- a tiny "does everything work?" test.

What it proves, in order:
  1. Python can find your API key in the .env file.
  2. The Claude Agent SDK can start up on your Mac.
  3. Claude (the Haiku model) can receive a message and answer it.
  4. You can see what the call cost.

It sends ONE short message, so it should cost a small fraction of a cent.
"""

# --- IMPORTS: borrowing tools that other people already wrote ---------------
import asyncio          # Python's built-in tool for "async" code (explained below)
import os               # Python's built-in tool for talking to your computer's settings

from dotenv import load_dotenv          # reads your .env file
from claude_agent_sdk import (
    query,                  # the function that sends a request to Claude
    ClaudeAgentOptions,     # a settings object: which model, how many turns, etc.
    AssistantMessage,       # the type of message Claude's answer arrives in
    ResultMessage,          # the final "receipt" message (cost, success/failure)
    TextBlock,              # a chunk of plain text inside an answer
    ClaudeSDKError,         # the SDK's own kind of error, so we can catch it politely
)


# --- STEP 1: load the API key from .env into this program's memory ----------
# load_dotenv() opens the .env file and makes ANTHROPIC_API_KEY available to
# this program. The SDK looks for that exact name automatically.
load_dotenv()


async def main():
    # --- STEP 2: check the key is there WITHOUT printing it -----------------
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        print("No ANTHROPIC_API_KEY found. Is the .env file in this folder?")
        return
    print(f"Key found (length {len(key)} characters). Not printing it, on purpose.")

    # --- STEP 3: settings for this one request ------------------------------
    options = ClaudeAgentOptions(
        model="haiku",           # cheapest/fastest model: right for development
        max_turns=1,             # one back-and-forth only, no wandering
        tools=[],                # give the agent NO tools (no file access, nothing)
        max_budget_usd=0.05,     # safety net: stop if this request passes 5 cents
        system_prompt="You are a concise assistant. Answer in one short sentence.",
    )

    prompt = "Say hello to Rohan and confirm that the API connection works."

    # --- STEP 4: send the request and read the replies as they arrive -------
    # `async for` means: "keep listening; each time a message arrives, handle it."
    # The SDK sends several kinds of messages, so we check what each one is.
    try:
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                # Claude's actual answer. It can contain several blocks;
                # we only care about the text ones.
                for block in message.content:
                    if isinstance(block, TextBlock):
                        print(f"\nClaude ({message.model}) says:\n{block.text}")

            elif isinstance(message, ResultMessage):
                # The final receipt, sent last.
                print("\n--- Receipt ---")
                print("Succeeded:", not message.is_error)
                cost = message.total_cost_usd
                print("Cost (USD):", "unknown" if cost is None else f"${cost:.6f}")
                print("Time taken (seconds):", round(message.duration_ms / 1000, 1))
                if message.is_error:
                    print("Details:", message.errors or message.result)

    except ClaudeSDKError as err:
        print("\nThe SDK reported a problem:")
        print(err)


# --- STEP 5: actually run it -------------------------------------------------
# The SDK is "async": it can wait for Claude's reply without freezing the whole
# program. asyncio.run(...) is just the standard way to start an async function.
if __name__ == "__main__":
    asyncio.run(main())
