"""
verifier.py -- checks that every number in Claude's answer really came from the tools.

No AI here. Plain Python. The idea:
    1. Pull every "number-like" piece of text out of Claude's answer:
         money   ($61.2K, +$43,600)      percent (+45.7%)
         IDs     (TXN-000121, PO-450002) plain numbers (2026, 1)
    2. Pull the same kinds of pieces out of everything the tools handed to Claude.
    3. Every piece in the answer must appear in the tool output. If one does not,
       Claude made it up, mis-copied it, or calculated it itself. FAIL.

What this catches:      invented numbers, wrong numbers, numbers Claude calculated itself,
                        flipped signs (+ turned into -), invented IDs.
What this CANNOT catch: wrong MEANING. If Claude says "Field Sales" but means "Marketing", both
                        names exist in the tool output, so the check passes. (Round 3 of our
                        testing did exactly that.) A human still reads the answer.
Also not checked:       numbers written as words ("three duplicates").
"""

import re

ID = re.compile(r"\b(?:TXN|PO|CC)-\d+\b")
QUARTER = re.compile(r"\bQ[1-4]\b")                       # "Q3" is a label, not a quantity
LIST_MARKER = re.compile(r"(?m)^\s*\d+[.)]\s")            # "1. first item" numbering in a list
MONEY = re.compile(r"[+-]?\$\d[\d,]*(?:\.\d+)?[KMB]?")
PERCENT = re.compile(r"[+-]?\d+(?:\.\d+)?%")
PLAIN = re.compile(r"(?<![\w$.,+-])\d[\d,]*(?:\.\d+)?(?![\w%])")


def extract(text):
    """Return {'id': [...], 'money': [...], 'percent': [...], 'plain': [...]} found in `text`.
    Each kind is removed from the text once found, so it is not counted twice."""
    found = {}
    text = LIST_MARKER.sub(" ", text)
    for kind, pattern in [("id", ID), ("money", MONEY), ("percent", PERCENT)]:
        found[kind] = pattern.findall(text)
        text = pattern.sub(" ", text)
    text = QUARTER.sub(" ", text)
    found["plain"] = PLAIN.findall(text)
    return found


def _magnitude(token):
    return token.lstrip("+-")


def verify_numbers(answer, tool_texts):
    """Compare Claude's answer with the tool outputs.

    Returns {"passed": bool, "checked": int, "failures": [...], "warnings": [...]}.
      failure = a number/ID in the answer that is NOT in the tool output (or has a flipped sign)
      warning = same number, but the plus/minus sign was dropped or added (still readable)"""
    allowed = {"id": set(), "money": set(), "percent": set(), "plain": set()}
    for text in tool_texts:
        for kind, tokens in extract(text).items():
            allowed[kind].update(tokens)

    failures, warnings, checked = [], [], 0
    for kind, tokens in extract(answer).items():
        for token in tokens:
            checked += 1
            if token in allowed[kind]:
                continue
            if kind in ("money", "percent"):
                same_size = [t for t in allowed[kind] if _magnitude(t) == _magnitude(token)]
                if same_size:
                    flipped = any(t[0] in "+-" and token[0] in "+-" and t[0] != token[0]
                                  for t in same_size)
                    if flipped:
                        failures.append(f"{token}: sign is the opposite of the tool output "
                                        f"({', '.join(sorted(same_size))})")
                    else:
                        warnings.append(f"{token}: sign differs from the tool output "
                                        f"({', '.join(sorted(same_size))})")
                    continue
            failures.append(f"{token}: not found in the tool output")
    return {"passed": not failures, "checked": checked, "failures": failures, "warnings": warnings}


def describe(verdict):
    """The verdict as printable lines."""
    if verdict["passed"]:
        lines = [f"PASSED: {verdict['checked']} numbers and IDs checked; every one appears in the tool output."]
    else:
        lines = [f"FAILED: {len(verdict['failures'])} of {verdict['checked']} numbers/IDs "
                 "cannot be traced to the tool output. Do NOT use this answer:"]
        lines += [f"  - {f}" for f in verdict["failures"]]
    lines += [f"  (warning) {w}" for w in verdict["warnings"]]
    return lines
