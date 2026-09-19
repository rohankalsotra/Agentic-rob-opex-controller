"""Tests for formatting.py: numbers shown as $K / $M / $B."""
import pytest
from formatting import format_money, format_pct


@pytest.mark.parametrize("amount, expected", [
    (0, "$0"), (999, "$999"), (1_234, "$1.2K"), (45_000, "$45.0K"),
    (4_540_000, "$4.54M"), (2_100_000_000, "$2.10B"),
    (-12_345, "-$12.3K"),
])
def test_units(amount, expected):
    assert format_money(amount) == expected


@pytest.mark.parametrize("amount, expected", [
    (999_950, "$1.00M"),            # would print as "$1000.0K" if we were careless
    (999_995_000, "$1.00B"),        # would print as "$1000.00M"
])
def test_rounding_up_moves_to_the_next_unit(amount, expected):
    assert format_money(amount) == expected


@pytest.mark.parametrize("amount", [-0.4, -0.5, 0.4, 0])
def test_tiny_amounts_never_show_a_minus_zero(amount):
    assert format_money(amount) == "$0"


def test_signed_shows_plus_and_minus():
    assert format_money(43_600, signed=True) == "+$43.6K"
    assert format_money(-43_600, signed=True) == "-$43.6K"


def test_missing_values_show_n_a():
    assert format_money(None) == "n/a"
    assert format_money(float("nan")) == "n/a"
    assert format_pct(None) == "n/a"


def test_percent():
    assert format_pct(0.0324) == "+3.2%"
    assert format_pct(-0.0891) == "-8.9%"
