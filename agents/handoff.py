"""
handoff.py -- the hand-off package: everything the Comms side is allowed to know.

WHY THIS FILE MATTERS (the trust boundary, made physical):
    The Variance side calculates. The Comms side writes. Between them sits ONE file of
    finished, formatted text: outputs/variance_handoff.json. The Comms side reads that file
    and nothing else. It never opens the workbook and never imports the calculator, so it
    has no way to recalculate, or even see, a raw number.

No AI in this file.

Try it (from the project folder, venv on):    python agents/handoff.py
"""

import json
from pathlib import Path

import variance_tools as vt
from variance_state import PROJECT_DIR, load_state

HANDOFF_PATH = PROJECT_DIR / "outputs" / "variance_handoff.json"


def build_handoff(state):
    """Put the Q3 card, the year-to-date card and the data quality card into one package."""
    dq = vt.build_data_quality_card(state["issues"], state["log"], state["names"])
    quarter = vt.build_report_card(state["report"], "quarter", dq)
    return {
        "package": "variance_handoff",
        "report": quarter["report"],
        "as_of": quarter["as_of"],
        "quarter": quarter,
        "year_to_date": vt.build_report_card(state["report"], "ytd", dq),
        "data_quality": dq,
    }


def save_handoff(package, path=HANDOFF_PATH):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(package, indent=1), encoding="utf-8")
    return path


def load_handoff(path=HANDOFF_PATH):
    return json.loads(Path(path).read_text(encoding="utf-8"))


if __name__ == "__main__":
    written = save_handoff(build_handoff(load_state()))
    print(f"Hand-off package written to {written.relative_to(PROJECT_DIR)}")
    print("This one file is everything the Comms side will be allowed to see.")
