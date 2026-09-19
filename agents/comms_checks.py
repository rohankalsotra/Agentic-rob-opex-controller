"""
comms_checks.py -- plain-Python checks on what the AI wrote for the pre-read.

The Comms Agent may write only two things: a headline and some discussion points. Before
either is allowed into the pre-read, this file checks them. No AI here.

    1. The draft is in the right shape (a headline plus 2 to 6 short points).
    2. Every number and ID in it appears in the hand-off package (the number checker).
    3. It contains none of the judgment words or made-up-cause phrases we banned.
    4. The headline states the total variance, its percentage and its status, as given.
    5. COMPLETENESS: every flagged line, and every budgeted month with no transactions,
       is covered by a discussion point. (Round 5 of the Variance Agent taught us that
       leaving something out is a failure that a number checker cannot see.)

check_draft() returns a list of problems. An empty list means the draft passed.
"""

import json
import re

import verifier

MAX_HEADLINE_WORDS = 50
MAX_POINT_WORDS = 60
MIN_POINTS, MAX_POINTS = 2, 6

# Judgment words: the data gives no basis for them.
BANNED_JUDGMENT = ["significant", "significantly", "major", "minor", "offsetting", "entirely", "mostly",
                   "mainly", "reliable", "accurate", "trustworthy", "concerning", "alarming",
                   "worrying", "healthy", "dramatic", "huge", "massive", "slight", "modest"]
# Made-up causes: the data shows WHAT changed, never WHY.
BANNED_CAUSES = ["because", "due to", "caused by", "as a result", "likely", "probably", "presumably",
                 "perhaps", "seems", "appears to", "suggests", "postponed", "delayed", "unexpected",
                 "one-time", "seasonal"]
MISSING_POSTING_WORDS = ("no transactions", "missing", "posting")


def _words(text):
    return len(text.split())


def _mentions(text, *needles):
    text = text.lower()
    return all(n.lower() in text for n in needles)


def parse_draft(raw_text):
    """Pull {"headline": ..., "discussion_points": [...]} out of the model's reply.
    Raises ValueError with a plain message if the reply is not in that shape."""
    start, end = raw_text.find("{"), raw_text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("The reply was not a JSON object.")
    try:
        data = json.loads(raw_text[start:end + 1])
    except json.JSONDecodeError as err:
        raise ValueError(f"The reply was not valid JSON ({err.msg}).")
    headline, points = data.get("headline"), data.get("discussion_points")
    if not isinstance(headline, str) or not headline.strip():
        raise ValueError('The JSON needs a non-empty "headline" text.')
    if not isinstance(points, list) or not all(isinstance(p, str) and p.strip() for p in points):
        raise ValueError('The JSON needs "discussion_points" as a list of non-empty texts.')
    return headline.strip(), [p.strip() for p in points]


def check_draft(package, headline, points):
    """Run every check. Returns a list of problems (empty list = passed)."""
    problems = []
    quarter = package["quarter"]
    everything = " ".join([headline] + points)

    # 1. shape
    if _words(headline) > MAX_HEADLINE_WORDS:
        problems.append(f"The headline is longer than {MAX_HEADLINE_WORDS} words.")
    if not MIN_POINTS <= len(points) <= MAX_POINTS:
        problems.append(f"Give between {MIN_POINTS} and {MAX_POINTS} discussion points, not {len(points)}.")
    for i, p in enumerate(points, start=1):
        if _words(p) > MAX_POINT_WORDS:
            problems.append(f"Discussion point {i} is longer than {MAX_POINT_WORDS} words.")

    # 2. numbers and IDs must come from the package
    verdict = verifier.verify_numbers(everything, [json.dumps(package)])
    problems += [f"Number check: {f}" for f in verdict["failures"]]

    # 3. banned wording
    for word in BANNED_JUDGMENT:
        if re.search(rf"\b{re.escape(word)}\b", everything, re.IGNORECASE):
            problems.append(f'Banned judgment word: "{word}". State the status and the numbers instead.')
    for phrase in BANNED_CAUSES:
        if re.search(rf"\b{re.escape(phrase)}\b", everything, re.IGNORECASE):
            problems.append(f'Banned cause or speculation wording: "{phrase}". The data never says why.')

    # 4. the headline must state the total, exactly as given
    total = quarter["total"]
    for label, needle in [("total variance", total["variance"]["short"]),
                          ("total variance percentage", total["variance_pct"]),
                          ("total status", total["status"])]:
        if needle.lower() not in headline.lower():
            problems.append(f'The headline must state the {label} exactly as given: "{needle}".')

    # 5. completeness
    for f in quarter["flagged_lines"]:
        if not any(_mentions(p, f["cost_center"], f["category"]) for p in points):
            problems.append(f'No discussion point covers the flagged line {f["cost_center"]} / '
                            f'{f["category"]}. Name both the cost center and the category.')
    for e in quarter["budgeted_months_with_no_transactions"]:
        covered = any(_mentions(p, e["cost_center"], e["category"])
                      and any(w in p.lower() for w in MISSING_POSTING_WORDS) for p in points)
        if not covered:
            problems.append(f'No discussion point raises the missing transactions for {e["cost_center"]} / '
                            f'{e["category"]} in {e["month"]}. Say there are no transactions recorded '
                            'or a posting may be missing.')
    return problems
