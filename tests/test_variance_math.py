"""Tests for variance_math.py: the variance arithmetic, the flag rule, quarter and year to date."""
import pandas as pd
import pytest

import variance_math as vm
from helpers import txn_row, budget_row, budget_months, with_month


# ---- the basic arithmetic ------------------------------------------------------------
def test_variance_is_actual_minus_budget_and_percent_of_budget():
    df = vm._add_variance(pd.DataFrame([{"Budget": 100_000, "Actual": 120_000}]))
    assert df.loc[0, "Variance $"] == 20_000          # positive = overspent
    assert df.loc[0, "Variance %"] == pytest.approx(0.20)


def test_zero_budget_has_no_percent_and_status_n_a():
    df = vm._add_variance(pd.DataFrame([{"Budget": 0, "Actual": 500}]))
    assert pd.isna(df.loc[0, "Variance %"])
    assert df.loc[0, "Status"] == "n/a"


# ---- the flag rule: BOTH thresholds must be met ------------------------------------------
@pytest.mark.parametrize("budget, actual, expected", [
    (100_000, 120_000, "OVER"),          # +20%  and +$20K  -> both met
    (100_000, 80_000, "UNDER"),          # -20%  and -$20K  -> both met
    (1_000_000, 1_030_000, "On track"),  # +$30K but only +3%  -> percent not met
    (20_000, 23_000, "On track"),        # +15%  but only +$3K -> dollars not met
    (1_000_000, 970_000, "On track"),    # same, on the under side
    (20_000, 17_000, "On track"),
    (100_000, 110_000, "OVER"),          # exactly +10% and +$10K counts as flagged
    (100_000, 90_000, "UNDER"),
    (100_000, 100_000, "On track"),
])
def test_flag_needs_both_thresholds(monkeypatch, budget, actual, expected):
    monkeypatch.setattr(vm, "FLAG_PERCENT", 0.10)     # pin the rule so the test never
    monkeypatch.setattr(vm, "FLAG_DOLLARS", 10_000)   # depends on the current settings
    df = vm._add_variance(pd.DataFrame([{"Budget": budget, "Actual": actual}]))
    assert df.loc[0, "Status"] == expected


# ---- quarters and dates ----------------------------------------------------------------------
def test_q3_bounds_and_label():
    b = vm.period_bounds("2026-09-30")
    assert b["label"] == "Q3 2026"
    assert b["quarter"] == (pd.Timestamp("2026-07-01"), pd.Timestamp("2026-09-30"))
    assert b["ytd"][0] == pd.Timestamp("2026-01-01")


def test_a_quarter_still_in_progress_is_labelled_so():
    assert vm.period_bounds("2026-08-31")["label"] == "Q3 2026 (quarter to date)"


def test_as_of_must_be_a_month_end():
    with pytest.raises(ValueError):
        vm.period_bounds("2026-07-15")


# ---- a small hand-made report: two teams, nine months -----------------------------------------
def small_report():
    months = range(1, 10)
    budget = pd.DataFrame(
        budget_months(months, **{"Cost Center ID": "CC-1", "Cost Center": "Team One",
                                 "Category": "Travel", "Budget": 1000})
        + budget_months(months, **{"Cost Center ID": "CC-2", "Cost Center": "Team Two",
                                   "Category": "Software", "Budget": 2000}))
    txn = with_month(
        [txn_row(**{"Transaction ID": f"A{m}", "Posting Date": pd.Timestamp(2026, m, 5),
                    "Amount": 1200}) for m in months]
        + [txn_row(**{"Transaction ID": f"B{m}", "Posting Date": pd.Timestamp(2026, m, 5),
                      "Cost Center ID": "CC-2", "Cost Center": "Team Two",
                      "Category": "Software", "Amount": 1500}) for m in months])
    return budget, txn, vm.build_variance_report(budget, txn, "2026-09-30")


def test_quarter_covers_jul_to_sep_only():
    _, _, report = small_report()
    lines = report["periods"]["Quarter"]["by_line"].set_index("Category")
    assert lines.loc["Travel", ["Budget", "Actual", "Variance $"]].tolist() == [3_000, 3_600, 600]
    assert lines.loc["Software", ["Budget", "Actual", "Variance $"]].tolist() == [6_000, 4_500, -1_500]


def test_year_to_date_covers_jan_to_sep():
    _, _, report = small_report()
    lines = report["periods"]["Year to date"]["by_line"].set_index("Category")
    assert lines.loc["Travel", ["Budget", "Actual"]].tolist() == [9_000, 10_800]
    assert lines.loc["Software", ["Budget", "Actual"]].tolist() == [18_000, 13_500]


def test_total_row_adds_up_the_teams():
    _, _, report = small_report()
    total = report["periods"]["Quarter"]["total"].iloc[0]
    assert (total["Budget"], total["Actual"], total["Variance $"]) == (9_000, 8_100, -900)
    assert total["Variance %"] == pytest.approx(-0.10)


def test_control_total_passes_when_everything_is_accounted_for():
    _, _, report = small_report()
    for period in report["periods"].values():
        assert period["control"]["passed"] is True


def test_only_months_up_to_the_as_of_date_count():
    budget, txn, _ = small_report()
    report = vm.build_variance_report(budget, txn, "2026-08-31")
    total = report["periods"]["Quarter"]["total"].iloc[0]
    assert total["Budget"] == 6_000                    # Jul + Aug only: (1,000 + 2,000) x 2 months


# ---- edge cases that matter in real data --------------------------------------------------------
def test_budgeted_month_with_no_transactions_counts_as_zero_and_is_listed():
    budget = pd.DataFrame(budget_months([7, 8, 9]))
    txn = with_month([txn_row(**{"Transaction ID": "A", "Posting Date": pd.Timestamp(2026, 7, 5)}),
                      txn_row(**{"Transaction ID": "B", "Posting Date": pd.Timestamp(2026, 8, 5)})])
    period = vm.build_variance_report(budget, txn, "2026-09-30")["periods"]["Quarter"]
    assert period["by_line"].iloc[0]["Actual"] == 2_000            # Sep counts as $0
    empty = period["no_transaction_lines"]
    assert len(empty) == 1 and empty[0]["Month"] == pd.Timestamp(2026, 9, 1)


def test_control_total_fails_if_a_transaction_has_no_budget_line():
    budget = pd.DataFrame(budget_months([7, 8, 9]))
    txn = with_month([txn_row(**{"Transaction ID": "A"}),
                      txn_row(**{"Transaction ID": "B", "Cost Center ID": "CC-9",
                                 "Cost Center": "Ghost Team", "Amount": 5_000})])
    period = vm.build_variance_report(budget, txn, "2026-09-30")["periods"]["Quarter"]
    assert period["control"]["passed"] is False        # the $5,000 would otherwise vanish silently


def test_flagged_team_lists_its_biggest_drivers_first():
    budget = pd.DataFrame(
        [budget_row(Category="Big", Budget=100_000), budget_row(Category="Small", Budget=100_000),
         budget_row(Category="Tiny", Budget=100_000)])
    txn = with_month([txn_row(**{"Transaction ID": "1", "Category": "Big", "Amount": 150_000}),
                      txn_row(**{"Transaction ID": "2", "Category": "Small", "Amount": 98_000}),
                      txn_row(**{"Transaction ID": "3", "Category": "Tiny", "Amount": 101_000})])
    period = vm.build_variance_report(budget, txn, "2026-09-30")["periods"]["Quarter"]
    info = vm._team_drivers(period["by_line"], period["by_cost_center"], top_n=1)["CC-1"]
    assert info["top"]["Category"].tolist() == ["Big"]             # +$50K is the biggest swing
    assert info["other_count"] == 2
    assert info["other_net"] == -1_000                             # -2,000 + 1,000
