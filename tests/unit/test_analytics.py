"""Stage 1 formulas. Prices in this file are the test fixture, not a market feed."""

import json
from decimal import Decimal

import pytest

from book_move.analytics import (
    BenchmarkMark,
    NameMark,
    OptionMark,
    TradeFill,
    annualize,
    book_snapshot,
    historical_var,
    published_historical_var,
    published_volatility,
    simple_returns,
    volatility,
)

MOTHER = (
    NameMark("0700.HK", 200, 380, 390, Decimal("0.60")),
    NameMark("0005.HK", 500, 70, 68, Decimal("0.60")),
)


def test_a_fill_splits_clean_pnl_from_actual_pnl():
    marked = (
        NameMark("0700.HK", 250, 380, 390, Decimal("0.60"), (TradeFill(50, 385),)),
        NameMark("0005.HK", 500, 70, 68, Decimal("0.60")),
    )
    snapshot = book_snapshot(marked)
    tencent, hsbc = snapshot.names
    assert tencent.clean_pnl == Decimal(2_000)
    assert tencent.daily_pnl == Decimal(2_250)
    assert tencent.market_value == Decimal(97_500)
    assert hsbc.clean_pnl == Decimal(-1_000)
    assert hsbc.daily_pnl == Decimal(-1_000)
    assert snapshot.book_clean_pnl == Decimal(1_000)
    assert snapshot.book_pnl == Decimal(1_250)


def test_fills_larger_than_the_position_leave_both_pnls_blank():
    snapshot = book_snapshot(
        (NameMark("0700.HK", 200, 380, 390, Decimal("0.60"), (TradeFill(250, 385),)),)
    )
    name = snapshot.names[0]
    assert name.daily_pnl is None
    assert name.clean_pnl is None
    assert name.market_value == Decimal(78_000)


def test_a_ten_for_one_split_is_flat_when_the_holding_is_post_split():
    marked = (
        NameMark("0700.HK", 2_000, 380, 38, Decimal("0.60"), split_factor=10),
        NameMark("0005.HK", 500, 70, 68, Decimal("0.60")),
    )
    snapshot = book_snapshot(marked)
    tencent, hsbc = snapshot.names
    assert tencent.daily_pnl == Decimal(0)
    assert tencent.clean_pnl == Decimal(0)
    assert tencent.market_value == Decimal(76_000)
    assert tencent.split_factor == Decimal(10)
    assert tencent.dividend_cash == Decimal(0)
    assert hsbc.daily_pnl == Decimal(-1_000)
    assert snapshot.book_pnl == Decimal(-1_000)


def test_an_unrecorded_split_prints_the_fake_loss():
    snapshot = book_snapshot((NameMark("0700.HK", 200, 380, 38, Decimal("0.60")),))
    assert snapshot.names[0].daily_pnl == Decimal(-68_400)
    assert snapshot.names[0].market_value == Decimal(7_600)


def test_a_dividend_offsets_the_ex_date_price_drop():
    snapshot = book_snapshot(
        (NameMark("0700.HK", 200, 380, 378, Decimal("0.60"), div_cash=2),)
    )
    name = snapshot.names[0]
    assert name.daily_pnl == Decimal(0)
    assert name.clean_pnl == Decimal(0)
    assert name.dividend_cash == Decimal(400)
    assert name.div_cash == Decimal(2)
    assert name.market_value == Decimal(75_600)


def test_a_non_positive_split_leaves_pnl_blank():
    snapshot = book_snapshot(
        (NameMark("0700.HK", 200, 380, 390, Decimal("0.60"), split_factor=0),)
    )
    assert snapshot.names[0].daily_pnl is None
    assert snapshot.names[0].dividend_cash is None
    assert snapshot.names[0].market_value == Decimal(78_000)


def test_a_split_return_is_flat_and_the_next_day_uses_the_official_close():
    returns = simple_returns([380, 38, 39], split_factors=[1, 10, 1])
    assert returns == [Decimal(0), Decimal(39) / Decimal(38) - 1]


def test_a_dividend_return_is_flat():
    assert simple_returns([380, 378], dividends=[0, 2]) == [Decimal(0)]


def test_mother_book_pnl_weight_and_limit():
    snapshot = book_snapshot(MOTHER)
    tencent, hsbc = snapshot.names

    assert tencent.daily_pnl == Decimal(2_000)
    assert tencent.clean_pnl == Decimal(2_000)
    assert hsbc.daily_pnl == Decimal(-1_000)
    assert hsbc.clean_pnl == Decimal(-1_000)
    assert snapshot.book_pnl == Decimal(1_000)
    assert snapshot.book_clean_pnl == Decimal(1_000)
    assert tencent.market_value == Decimal(78_000)
    assert hsbc.market_value == Decimal(34_000)
    assert snapshot.gross_market_value == Decimal(112_000)
    assert tencent.weight == Decimal(78_000) / Decimal(112_000)
    assert round(tencent.weight * 100, 1) == Decimal("69.6")
    assert tencent.limit_breached is True
    assert hsbc.limit_breached is False
    assert tencent.split_factor == Decimal(1)
    assert tencent.dividend_cash == Decimal(0)
    assert hsbc.dividend_cash == Decimal(0)
    assert tencent.market_pnl is None
    assert tencent.residual_pnl is None
    assert snapshot.book_market_pnl is None
    assert snapshot.book_residual_pnl is None
    assert tencent.intraday_pnl is None
    assert tencent.last is None
    assert snapshot.book_intraday_pnl is None
    assert tencent.dollar_pnl == Decimal(200)
    assert hsbc.dollar_pnl == Decimal(500)
    assert tencent.vega_pnl == Decimal(0)
    assert hsbc.vega_pnl == Decimal(0)
    assert snapshot.book_dollar_pnl == Decimal(700)
    assert snapshot.book_vega_pnl == Decimal(0)
    assert snapshot.options == ()


def test_a_one_percent_benchmark_splits_clean_pnl():
    snapshot = book_snapshot(MOTHER, benchmark=BenchmarkMark(100, 101), benchmark_symbol="SPY")
    tencent, hsbc = snapshot.names
    assert tencent.daily_pnl == Decimal(2_000)
    assert tencent.market_pnl == Decimal(760)
    assert tencent.residual_pnl == Decimal(1_240)
    assert hsbc.market_pnl == Decimal(350)
    assert hsbc.residual_pnl == Decimal(-1_350)
    assert snapshot.book_market_pnl == Decimal(1_110)
    assert snapshot.book_residual_pnl == Decimal(-110)
    assert snapshot.book_pnl == Decimal(1_000)
    assert snapshot.gross_market_value == Decimal(112_000)
    assert snapshot.benchmark_symbol == "SPY"
    assert snapshot.benchmark_simple_return == Decimal("0.01")


def test_a_flat_benchmark_leaves_the_pnl_on_the_stock():
    snapshot = book_snapshot(MOTHER, benchmark=BenchmarkMark(100, 100))
    assert snapshot.names[0].market_pnl == Decimal(0)
    assert snapshot.names[0].residual_pnl == Decimal(2_000)
    assert snapshot.names[1].residual_pnl == Decimal(-1_000)


def test_a_missing_benchmark_close_blanks_only_the_split():
    snapshot = book_snapshot(MOTHER, benchmark=BenchmarkMark(100, None))
    assert snapshot.names[0].daily_pnl == Decimal(2_000)
    assert snapshot.names[0].market_pnl is None
    assert snapshot.book_market_pnl is None
    assert snapshot.book_pnl == Decimal(1_000)


def test_a_fill_stays_outside_the_market_split():
    marked = (
        NameMark("0700.HK", 250, 380, 390, Decimal("0.60"), (TradeFill(50, 385),)),
        NameMark("0005.HK", 500, 70, 68, Decimal("0.60")),
    )
    snapshot = book_snapshot(marked, benchmark=BenchmarkMark(100, 101))
    tencent = snapshot.names[0]
    assert tencent.clean_pnl == Decimal(2_000)
    assert tencent.daily_pnl == Decimal(2_250)
    assert tencent.market_pnl == Decimal(760)
    assert tencent.residual_pnl == Decimal(1_240)


def test_a_last_does_not_replace_the_official_close():
    marked = (
        NameMark("0700.HK", 200, 380, 390, Decimal("0.60"), last=395),
        NameMark("0005.HK", 500, 70, 68, Decimal("0.60")),
    )
    snapshot = book_snapshot(marked)
    tencent, hsbc = snapshot.names
    assert tencent.daily_pnl == Decimal(2_000)
    assert tencent.market_value == Decimal(78_000)
    assert tencent.last == Decimal(395)
    assert tencent.intraday_pnl == Decimal(3_000)
    assert tencent.intraday_clean_pnl == Decimal(3_000)
    assert tencent.intraday_market_value == Decimal(79_000)
    assert hsbc.intraday_pnl is None
    assert hsbc.last is None
    assert snapshot.book_pnl == Decimal(1_000)
    assert snapshot.book_intraday_pnl == Decimal(3_000)
    assert snapshot.book_intraday_market_value == Decimal(79_000)
    assert snapshot.gross_market_value == Decimal(112_000)
    assert round(tencent.weight * 100, 1) == Decimal("69.6")


def test_a_last_without_an_official_close_stays_off_the_official_pnl():
    snapshot = book_snapshot(
        (NameMark("0700.HK", 200, 380, None, Decimal("0.60"), last=395),)
    )
    name = snapshot.names[0]
    assert name.daily_pnl is None
    assert name.market_value is None
    assert name.weight is None
    assert name.intraday_pnl == Decimal(3_000)
    assert name.intraday_market_value == Decimal(79_000)
    assert snapshot.book_pnl is None
    assert snapshot.book_intraday_pnl == Decimal(3_000)


def test_a_fill_is_marked_to_the_last_and_the_close_separately():
    snapshot = book_snapshot(
        (NameMark("0700.HK", 250, 380, 390, Decimal("0.60"), (TradeFill(50, 385),), last=395),)
    )
    name = snapshot.names[0]
    assert name.clean_pnl == Decimal(2_000)
    assert name.daily_pnl == Decimal(2_250)
    assert name.intraday_clean_pnl == Decimal(3_000)
    assert name.intraday_pnl == Decimal(3_500)
    assert name.intraday_market_value == Decimal(98_750)


def test_an_option_shock_stays_out_of_the_cash_book():
    snapshot = book_snapshot(MOTHER, options=(OptionMark("CALL", 100, Decimal("0.4"), 2),))
    option = snapshot.options[0]
    assert option.dollar_pnl == Decimal(40)
    assert option.vega_pnl == Decimal(200)
    assert snapshot.book_dollar_pnl == Decimal(700)
    assert snapshot.book_vega_pnl == Decimal(0)
    assert snapshot.book_pnl == Decimal(1_000)
    assert snapshot.gross_market_value == Decimal(112_000)
    assert snapshot.names[0].daily_pnl == Decimal(2_000)


def test_a_missing_greek_blanks_the_option_shock():
    marked = (NameMark("0700.HK", 200, 380, 390, Decimal("0.60")),)
    snapshot = book_snapshot(marked, options=(OptionMark("CALL", 100, None, 2),))
    assert snapshot.options[0].dollar_pnl is None
    assert snapshot.options[0].vega_pnl is None
    assert snapshot.names[0].dollar_pnl == Decimal(200)
    assert snapshot.book_pnl == Decimal(2_000)


def test_weight_equal_to_the_limit_is_not_a_breach():
    names = (
        NameMark("0700.HK", 60, 1, 1, Decimal("0.60")),
        NameMark("0005.HK", 40, 1, 1, Decimal("0.60")),
    )
    snapshot = book_snapshot(names)
    assert snapshot.names[0].weight == Decimal("0.60")
    assert snapshot.names[0].limit_breached is False


def test_missing_close_stays_out_of_gross_and_pnl():
    snapshot = book_snapshot(
        (
            NameMark("0700.HK", 200, 380, 390, Decimal("0.60")),
            NameMark("0005.HK", 500, 70, None, Decimal("0.60")),
        )
    )
    tencent, hsbc = snapshot.names
    assert hsbc.daily_pnl is None
    assert hsbc.clean_pnl is None
    assert hsbc.market_value is None
    assert hsbc.weight is None
    assert hsbc.limit_breached is False
    assert snapshot.gross_market_value == Decimal(78_000)
    assert snapshot.book_pnl == Decimal(2_000)
    assert tencent.weight == Decimal(1)


def test_short_series_volatility_matches_the_hand_calculation():
    returns = simple_returns([100, 110, 99])
    assert returns == [Decimal("0.10"), Decimal("-0.10")]
    result = volatility(returns)
    assert result is not None
    assert result.daily == Decimal("0.02").sqrt()
    assert result.annualized == annualize(result.daily)
    assert round(result.daily, 4) == Decimal("0.1414")
    assert round(result.annualized, 3) == Decimal("2.245")


def test_published_volatility_is_empty_until_twenty_returns():
    short = [Decimal("0.01")] * 19
    assert published_volatility(short) is None

    flat = published_volatility([Decimal("0.01")] * 20)
    assert flat is not None
    assert flat.daily == Decimal(0)
    assert flat.annualized == Decimal(0)
    assert flat.observations == 20


def test_missing_close_does_not_create_a_bridged_return():
    assert simple_returns([100, None, 110]) == []


def test_five_pnl_var_is_the_loss_tail():
    pnls = [10, 5, -20, 8, -40]
    assert historical_var(pnls) == Decimal(40)
    assert historical_var(pnls) != Decimal(10)


def test_historical_var_is_zero_when_the_tail_did_not_lose():
    assert historical_var([10, 5, 8, 20, 40]) == Decimal(0)


def test_published_var_is_empty_until_sixty_pnls_and_records_the_window():
    assert published_historical_var([10, 5, -20, 8, -40]) is None
    snapshot = book_snapshot(MOTHER, book_daily_pnls=[10, 5, -20, 8, -40])
    assert snapshot.historical_var is None

    long_sample = [-1] * 60
    assert published_historical_var(long_sample) == Decimal(1)
    published = book_snapshot(MOTHER, book_daily_pnls=long_sample)
    assert published.to_json()["historical_var"] == {
        "loss": "1",
        "confidence": "0.95",
        "window": 60,
    }


def test_snapshot_json_is_identical_across_repeated_calls():
    payloads = [json.dumps(book_snapshot(MOTHER).to_json(), sort_keys=True) for _ in range(10)]
    assert len(set(payloads)) == 1
    payload = json.loads(payloads[0])
    assert payload["book_pnl"] == "1000"
    assert payload["book_clean_pnl"] == "1000"
    assert payload["names"][0]["limit_breached"] is True
    assert payload["historical_var"] is None


def test_float_inputs_are_rejected():
    with pytest.raises(TypeError):
        book_snapshot((NameMark("0700.HK", 200, 380.0, 390, Decimal("0.60")),))
