"""Hand calculation for the stage 0 mother problem.

The arithmetic stays here so the integers can be checked without the library.
`test_analytics.py` asserts the stage 1 functions return these same numbers.
Prices here exist only in tests.
"""

from fractions import Fraction


def test_tencent_daily_pnl_is_shares_times_close_change():
    shares = 200
    previous_close = 380
    close = 390
    assert shares * (close - previous_close) == 2_000


def test_hsbc_daily_pnl_is_shares_times_close_change():
    shares = 500
    previous_close = 70
    close = 68
    assert shares * (close - previous_close) == -1_000


def test_book_pnl_is_the_sum_of_each_name():
    assert 2_000 + (-1_000) == 1_000


def test_gross_market_value_is_the_sum_of_long_market_values():
    tencent_market_value = 200 * 390
    hsbc_market_value = 500 * 68
    assert tencent_market_value == 78_000
    assert hsbc_market_value == 34_000
    assert tencent_market_value + hsbc_market_value == 112_000


def test_tencent_weight_is_its_market_value_over_gross():
    weight = Fraction(78_000, 112_000)
    assert weight == Fraction(39, 56)
    assert round(float(weight) * 100, 1) == 69.6
