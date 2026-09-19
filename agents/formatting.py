"""
formatting.py -- turns raw numbers into easy-to-read text (K / M / B).

IMPORTANT: this is for DISPLAY only. All calculations use the full, exact numbers.
We round only at the very end, when a number is about to be shown to a person
(or handed to the AI model as text). That way rounding can never sneak into the math.
"""

import pandas as pd


def format_money(amount, signed=False):
    """1234 -> '$1.2K'   4_500_000 -> '$4.50M'   2_100_000_000 -> '$2.10B'
    Negative numbers get a minus sign. With signed=True, positives get a plus sign."""
    if amount is None or pd.isna(amount):
        return "n/a"

    x = abs(float(amount))
    if round(x) == 0:                  # would display as $0, so show plain "$0" with no sign
        return "$0"
    sign = "-" if amount < 0 else ("+" if signed else "")

    if x >= 1e9:
        text = f"${x / 1e9:,.2f}B"
    elif x >= 1e6:
        # round FIRST; if it rounds up to 1,000M, show it as billions instead
        text = f"${x / 1e6:,.2f}M" if round(x / 1e6, 2) < 1000 else f"${x / 1e9:,.2f}B"
    elif x >= 1e3:
        # if it rounds up to 1,000K, show it as millions instead
        text = f"${x / 1e3:,.1f}K" if round(x / 1e3, 1) < 1000 else f"${x / 1e6:,.2f}M"
    else:
        text = f"${x:,.0f}"
    return sign + text


def format_pct(fraction):
    """0.1234 -> '+12.3%'   -0.05 -> '-5.0%'   (input is a fraction, not already x100)"""
    if fraction is None or pd.isna(fraction):
        return "n/a"
    return f"{fraction * 100:+.1f}%"


def format_money_exact(amount, signed=False):
    """Full dollars with commas, for when someone asks for the exact number.
    43_612.4 -> '$43,612'   -43_612.4 -> '-$43,612'   (rounded to whole dollars, display only)"""
    if amount is None or pd.isna(amount):
        return "n/a"
    whole = round(float(amount))
    if whole == 0:                     # same rule as format_money: never show "-$0"
        return "$0"
    sign = "-" if whole < 0 else ("+" if signed else "")
    return f"{sign}${abs(whole):,}"
