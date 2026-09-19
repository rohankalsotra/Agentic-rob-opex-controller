"""
variance_state.py -- loads the data, applies reviewer decisions and calculates the report.

This is the "calculator side" of the project. NO AI and no Claude SDK in here, so both the
Variance Agent and the hand-off step can use it.
"""

from pathlib import Path

import variance_engine as engine
import variance_math as vm

PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_DIR / "data"
AS_OF = "2026-09-30"                    # the report date: end of Q3 2026


def load_state(data_dir=DATA_DIR, as_of=AS_OF):
    """Read the workbook, apply the reviewer's decisions, calculate. NO AI involved."""
    budget, txn, issues = engine.load_workbook(data_dir / "sample_opex_2026.xlsx")
    decisions = engine.read_decisions(data_dir / "sample_review_decisions.csv")
    txn, issues, log = engine.apply_decisions(txn, issues, decisions)
    report = vm.build_variance_report(budget, txn, as_of)
    names = dict(zip(budget["Cost Center ID"], budget["Cost Center"]))
    # "tool_outputs" keeps a copy of everything our tools hand to Claude, so the number
    # checker can compare Claude's answer against exactly that.
    return {"report": report, "issues": issues, "log": log, "names": names, "tool_outputs": []}
