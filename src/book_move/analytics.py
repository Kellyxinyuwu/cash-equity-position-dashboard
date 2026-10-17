"""Pure calculations. No database and no Tiingo.

Callers pass share counts and closes they already have. This file returns
daily P&L, weight against a limit, volatility, and historical VaR. Published
volatility needs 20 simple returns. Published historical VaR needs 60 daily
book P&L observations. A shorter sample stays empty instead of being padded.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

# 252 is the usual count of equity trading sessions in a year.
# Vol is published only after 20 simple returns. VaR only after 60 book P&Ls.
# The 5% lower tail is the same statement as 95% confidence.
TRADING_DAYS_PER_YEAR = 252
PUBLISHED_VOLATILITY_WINDOW = 20
PUBLISHED_VAR_WINDOW = 60
VAR_TAIL = Decimal("0.05")
VAR_CONFIDENCE = Decimal("0.95")


def as_decimal(value: int | str | Decimal) -> Decimal:
    """Accept int, str, or Decimal. Reject float so money is not a binary fraction."""
    # bool is a subclass of int, so it has to be rejected on its own.
    if isinstance(value, bool) or isinstance(value, float):
        raise TypeError("use Decimal, int, or str, not float or bool")
    if isinstance(value, Decimal):
        return value
    return Decimal(value)


def daily_pnl(
    shares: int | str | Decimal,
    previous_close: int | str | Decimal | None,
    close: int | str | Decimal | None,
) -> Decimal | None:
    """shares * (close - previous_close). None if either price is missing.

    Mother problem: 200 shares, 380 to 390, is +2,000. 500 shares, 70 to 68, is -1,000.
    With no trades, this is both the clean P&L and the actual P&L.
    """
    if previous_close is None or close is None:
        return None
    # shares * (today close - previous close). Same shares on both days.
    return as_decimal(shares) * (as_decimal(close) - as_decimal(previous_close))


def trade_pnl(
    shares: int | str | Decimal,
    price: int | str | Decimal,
    close: int | str | Decimal | None,
) -> Decimal | None:
    """Mark one fill from its trade price to the official close. None if the close is missing.

    A buy is a positive share count. Teaching fill: 50 shares at 385, close 390, is +250.
    Those shares do not receive the move from the previous close.
    """
    # Step 1. No official close means this fill cannot be marked. Do not use the trade price.
    if close is None:
        return None
    share_count = as_decimal(shares)
    trade_price = as_decimal(price)
    # Step 2. Zero shares is not a fill. A zero or negative price is not a mark.
    # Leave the leg blank instead of publishing shares * close.
    if share_count == 0 or trade_price <= 0:
        return None
    # Step 3. Bought shares (positive) gain when the close is above the trade price.
    return share_count * (as_decimal(close) - trade_price)


def market_value(
    shares: int | str | Decimal,
    close: int | str | Decimal | None,
) -> Decimal | None:
    """shares * close. None when that session has no official close."""
    if close is None:
        return None
    return as_decimal(shares) * as_decimal(close)


def weight(name_market_value: Decimal, gross_market_value: Decimal) -> Decimal | None:
    """This name's market value divided by the book's gross market value."""
    # A zero gross cannot be a denominator. The caller leaves weight blank.
    if gross_market_value == 0:
        return None
    return name_market_value / gross_market_value


def limit_breached(name_weight: Decimal | None, limit: Decimal) -> bool:
    """True only when weight is known and strictly above the stored limit.

    Equal to the limit is not a breach. A missing weight is not a breach either.
    Mother problem: 78,000 / 112,000 is about 69.6%, which is above a 60% limit.
    """
    if name_weight is None:
        return False
    return name_weight > limit


def simple_returns(
    closes: Sequence[int | str | Decimal | None],
    split_factors: Sequence[int | str | Decimal] | None = None,
    dividends: Sequence[int | str | Decimal] | None = None,
) -> list[Decimal]:
    """One-day simple returns. A missing close breaks the chain and is not filled forward.

    Return is (close * split_factor + dividend) / previous_close - 1.
    With no corporate action that is close_t / close_t-1 - 1.
    Teaching series 100, 110, 99 gives +0.10 then -0.10.
    A 10-for-1 day, 380 then 38 with factor 10, gives 0. The denominator stays
    yesterday's official close. The next day divides by today's official close.
    """
    factors = _aligned(split_factors, len(closes), Decimal(1))
    payouts = _aligned(dividends, len(closes), Decimal(0))
    returns: list[Decimal] = []
    previous: Decimal | None = None
    for close, factor, dividend in zip(closes, factors, payouts, strict=True):
        # Forget the last real close. The next traded day must not jump the gap.
        # A non-positive split or a negative dividend is a broken event, not a crash.
        if close is None or factor <= 0 or dividend < 0:
            previous = None
            continue
        current = as_decimal(close)
        if previous is not None:
            if previous == 0:
                raise ValueError("previous close must not be zero")
            # Step 1. Scale today's close back onto yesterday's share. Add the cash.
            economic = current * factor + dividend
            returns.append(economic / previous - 1)
        # Step 2. Remember the official close, not the scaled one. Tomorrow's
        # factor belongs to tomorrow.
        previous = current
    return returns


def benchmark_return(
    previous: int | str | Decimal | None,
    close: int | str | Decimal | None,
    split_factor: int | str | Decimal = 1,
    dividend: int | str | Decimal = 0,
) -> Decimal | None:
    """One-day simple return of the benchmark. None when either close is missing.

    The move is (close * split_factor + dividend) / previous_close - 1.
    A day from 100 to 101 is 0.01. A missing print is not a 0% day.
    """
    # Step 1. Both prints have to be there. Do not treat a gap as a flat market.
    if previous is None or close is None:
        return None
    factor = as_decimal(split_factor)
    payout = as_decimal(dividend)
    # Step 2. A broken corporate action cannot be scaled into a return.
    if factor <= 0 or payout < 0:
        return None
    prev = as_decimal(previous)
    # A zero previous close cannot be a denominator. Leave the return blank.
    if prev == 0:
        return None
    current = as_decimal(close)
    return (current * factor + payout) / prev - 1


def _aligned(
    values: Sequence[int | str | Decimal] | None,
    count: int,
    default: Decimal,
) -> list[Decimal]:
    """One amount per close. None means every day uses default. A short list is refused."""
    if values is None:
        return [default] * count
    if len(values) != count:
        raise ValueError("corporate-action list must match the closes")
    return [as_decimal(value) for value in values]


def _sample_stdev(values: Sequence[Decimal]) -> Decimal | None:
    """Sample standard deviation. The divisor is n - 1, so two points are the minimum."""
    count = len(values)
    if count < 2:
        return None
    mean = sum(values, start=Decimal(0)) / count
    # Sum of squared deviations from the mean, divided by n - 1, then square root.
    variance = sum((value - mean) ** 2 for value in values) / (count - 1)
    return variance.sqrt()


def annualize(daily_volatility: Decimal) -> Decimal:
    """Scale a one-day volatility to a year of 252 trading sessions: daily * sqrt(252)."""
    return daily_volatility * Decimal(TRADING_DAYS_PER_YEAR).sqrt()


@dataclass(frozen=True)
class Volatility:
    daily: Decimal
    annualized: Decimal
    observations: int

    def to_json(self) -> dict[str, str | int]:
        return {
            "daily": _number(self.daily),
            "annualized": _number(self.annualized),
            "observations": self.observations,
        }


def volatility(returns: Sequence[Decimal]) -> Volatility | None:
    """Sample volatility of the returns given, with no window gate."""
    daily = _sample_stdev(returns)
    if daily is None:
        return None
    return Volatility(daily=daily, annualized=annualize(daily), observations=len(returns))


def published_volatility(
    returns: Sequence[Decimal],
    window: int = PUBLISHED_VOLATILITY_WINDOW,
) -> Volatility | None:
    """Volatility of the last `window` returns. None when the sample is shorter than that."""
    if len(returns) < window:
        return None
    result = volatility(returns[-window:])
    if result is None:
        return None
    return result


def historical_var(
    pnls: Sequence[int | str | Decimal],
    tail: Decimal = VAR_TAIL,
) -> Decimal:
    """Positive loss at the lower tail. A gain in that tail is reported as zero.

    Rank uses n * tail on the ascending P&L list: an integer rank is kept, otherwise
    the rank rounds up. Five P&Ls of +10, +5, -20, +8, -40 therefore select -40 and
    report 40, which is the loss tail rather than the gain tail.
    """
    if not pnls:
        raise ValueError("historical VaR needs at least one P&L")
    if tail <= 0 or tail >= 1:
        raise ValueError("tail must be strictly between 0 and 1")
    # Ascending order puts the worst loss at the front, not the best gain.
    ordered = sorted(as_decimal(pnl) for pnl in pnls)
    # Rank is 1-based. Five P&Ls and a 5% tail select rank 1, which is -40.
    rank = _lower_tail_rank(len(ordered), tail)
    quantile = ordered[rank - 1]
    # Flip the sign so the report is a loss. A positive quantile means the loss is 0.
    return max(-quantile, Decimal(0))


def published_historical_var(
    pnls: Sequence[int | str | Decimal],
    window: int = PUBLISHED_VAR_WINDOW,
    tail: Decimal = VAR_TAIL,
) -> Decimal | None:
    """Historical VaR on the last `window` book P&Ls. None when the sample is shorter."""
    if len(pnls) < window:
        return None
    return historical_var(pnls[-window:], tail=tail)


def _lower_tail_rank(count: int, tail: Decimal) -> int:
    """1-based rank of the lower tail. An exact n * tail stays; anything else rounds up."""
    score = tail * count
    if score == score.to_integral_value():
        rank = int(score)
    else:
        rank = int(score.to_integral_value(rounding="ROUND_CEILING"))
    # A tiny sample can round the score down to 0. Rank 1 is the worst P&L.
    return max(rank, 1)


@dataclass(frozen=True)
class TradeFill:
    """One fill already in hand. Positive shares were bought; negative shares were sold."""

    shares: int | str | Decimal
    price: int | str | Decimal


@dataclass(frozen=True)
class NameMark:
    """Inputs for one name. `shares` is the holding at the close, after today's fills."""

    symbol: str
    shares: int | str | Decimal
    previous_close: int | str | Decimal | None
    close: int | str | Decimal | None
    limit: int | str | Decimal
    trades: tuple[TradeFill, ...] = ()
    # 1 and 0 mean this session had no split and no dividend.
    split_factor: int | str | Decimal = 1
    div_cash: int | str | Decimal = 0
    # None means no intraday print. The official close is not copied here.
    last: int | str | Decimal | None = None


@dataclass(frozen=True)
class BenchmarkMark:
    """The market series for one session. It has closes and no share count."""

    previous_close: int | str | Decimal | None
    close: int | str | Decimal | None
    split_factor: int | str | Decimal = 1
    div_cash: int | str | Decimal = 0


@dataclass(frozen=True)
class NameSnapshot:
    """One name after marking: market value, P&L, weight, and whether the limit broke."""

    symbol: str
    shares: Decimal
    previous_close: Decimal | None
    close: Decimal | None
    market_value: Decimal | None
    daily_pnl: Decimal | None
    clean_pnl: Decimal | None
    weight: Decimal | None
    limit: Decimal
    limit_breached: bool
    # 1 and 0 are a day with no corporate action. dividend_cash is the total added to P&L.
    split_factor: Decimal = Decimal(1)
    div_cash: Decimal = Decimal(0)
    dividend_cash: Decimal | None = Decimal(0)
    # None means the benchmark did not print. It is not a zero market move.
    market_pnl: Decimal | None = None
    residual_pnl: Decimal | None = None
    # None means no last was stored. It is not the official close.
    last: Decimal | None = None
    intraday_pnl: Decimal | None = None
    intraday_clean_pnl: Decimal | None = None
    intraday_market_value: Decimal | None = None
    # A cash share moves one for one. Vega of a stock is zero, not a missing greek.
    dollar_pnl: Decimal | None = None
    vega_pnl: Decimal | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "shares": _number(self.shares),
            "previous_close": _number(self.previous_close),
            "close": _number(self.close),
            "market_value": _number(self.market_value),
            "daily_pnl": _number(self.daily_pnl),
            "clean_pnl": _number(self.clean_pnl),
            "weight": _number(self.weight),
            "limit": _number(self.limit),
            "limit_breached": self.limit_breached,
            "split_factor": _number(self.split_factor),
            "div_cash": _number(self.div_cash),
            "dividend_cash": _number(self.dividend_cash),
            "market_pnl": _number(self.market_pnl),
            "residual_pnl": _number(self.residual_pnl),
            "last": _number(self.last),
            "intraday_pnl": _number(self.intraday_pnl),
            "intraday_clean_pnl": _number(self.intraday_clean_pnl),
            "intraday_market_value": _number(self.intraday_market_value),
            "dollar_pnl": _number(self.dollar_pnl),
            "vega_pnl": _number(self.vega_pnl),
        }


@dataclass(frozen=True)
class OptionMark:
    """One option the book holds. Delta and vega are inputs, not a priced model."""

    symbol: str
    quantity: int | str | Decimal
    delta: int | str | Decimal | None
    vega: int | str | Decimal | None


@dataclass(frozen=True)
class OptionSnapshot:
    """Shocks for one option. Blank when either greek was not supplied."""

    symbol: str
    quantity: Decimal
    delta: Decimal | None
    vega: Decimal | None
    dollar_pnl: Decimal | None
    vega_pnl: Decimal | None

    def to_json(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "quantity": _number(self.quantity),
            "delta": _number(self.delta),
            "vega": _number(self.vega),
            "dollar_pnl": _number(self.dollar_pnl),
            "vega_pnl": _number(self.vega_pnl),
        }


def option_snapshot(mark: OptionMark) -> OptionSnapshot:
    """Value of a 1-point underlying move and a 1-point volatility move.

    100 options with delta 0.4 move 40 when the stock rises 1.
    The same 100 with vega 2 move 200 when volatility rises 1 point.
    A missing greek leaves both shocks blank. A stock's 1 and 0 are not copied in.
    """
    quantity = as_decimal(mark.quantity)
    # Step 1. Both greeks have to be written down. One missing number blanks the row.
    if mark.delta is None or mark.vega is None:
        return OptionSnapshot(mark.symbol, quantity, None, None, None, None)
    delta = as_decimal(mark.delta)
    vega = as_decimal(mark.vega)
    # Step 2. One underlying point, then one volatility point. No contract multiplier.
    return OptionSnapshot(mark.symbol, quantity, delta, vega, quantity * delta, quantity * vega)


@dataclass(frozen=True)
class BookSnapshot:
    """The whole book on one day. historical_var is None until 60 book P&Ls exist."""

    gross_market_value: Decimal | None
    book_pnl: Decimal | None
    book_clean_pnl: Decimal | None
    names: tuple[NameSnapshot, ...]
    historical_var: Decimal | None
    historical_var_window: int
    book_market_pnl: Decimal | None = None
    book_residual_pnl: Decimal | None = None
    benchmark_symbol: str | None = None
    benchmark_previous: Decimal | None = None
    benchmark_close: Decimal | None = None
    benchmark_simple_return: Decimal | None = None
    book_intraday_pnl: Decimal | None = None
    book_intraday_market_value: Decimal | None = None
    book_dollar_pnl: Decimal | None = None
    book_vega_pnl: Decimal | None = None
    options: tuple[OptionSnapshot, ...] = ()

    def to_json(self) -> dict[str, Any]:
        var_loss = _number(self.historical_var)
        return {
            "gross_market_value": _number(self.gross_market_value),
            "book_pnl": _number(self.book_pnl),
            "book_clean_pnl": _number(self.book_clean_pnl),
            "book_market_pnl": _number(self.book_market_pnl),
            "book_residual_pnl": _number(self.book_residual_pnl),
            "book_intraday_pnl": _number(self.book_intraday_pnl),
            "book_intraday_market_value": _number(self.book_intraday_market_value),
            "book_dollar_pnl": _number(self.book_dollar_pnl),
            "book_vega_pnl": _number(self.book_vega_pnl),
            "options": [option.to_json() for option in self.options],
            "names": [name.to_json() for name in self.names],
            "historical_var": None
            if var_loss is None
            else {
                "loss": var_loss,
                "confidence": _number(VAR_CONFIDENCE),
                "window": self.historical_var_window,
            },
        }


def book_snapshot(
    names: Sequence[NameMark],
    book_daily_pnls: Sequence[int | str | Decimal] = (),
    benchmark: BenchmarkMark | None = None,
    benchmark_symbol: str | None = None,
    options: Sequence[OptionMark] = (),
) -> BookSnapshot:
    """Mark the book from closes already in hand. Missing closes stay out of gross and P&L."""
    # Step 1. Turn shares and closes into Decimal. A missing close stays None.
    raw: list[tuple[NameMark, Decimal, Decimal | None, Decimal | None]] = []
    for name in names:
        shares = as_decimal(name.shares)
        close = None if name.close is None else as_decimal(name.close)
        previous = None if name.previous_close is None else as_decimal(name.previous_close)
        raw.append((name, shares, previous, close))

    # Step 2. Gross market value is the sum of names that have a close today.
    # The benchmark is not a name, so its close is not added to this sum.
    values = [market_value(shares, close) for _, shares, _, close in raw]
    known_values = [value for value in values if value is not None]
    gross = sum(known_values, start=Decimal(0)) if known_values else None

    # Step 3. One benchmark return for every name. None leaves market and residual blank.
    # 100 to 101 is 0.01. That is not applied when the caller passes no benchmark.
    bench_return = None if benchmark is None else benchmark_return(
        benchmark.previous_close,
        benchmark.close,
        benchmark.split_factor,
        benchmark.div_cash,
    )
    # Step 4. Split each name. Clean P&L is yesterday's holding. Market P&L is
    # that holding's yesterday market value times the benchmark return.
    # Residual is clean minus market. A fill's own P&L stays in actual only.
    split = [
        _split_pnl(name, shares, previous, close, bench_return)
        for name, shares, previous, close in raw
    ]
    actuals = [item.actual for item in split]
    cleans = [item.clean for item in split]
    known_actuals = [pnl for pnl in actuals if pnl is not None]
    known_cleans = [pnl for pnl in cleans if pnl is not None]
    book_pnl = sum(known_actuals, start=Decimal(0)) if known_actuals else None
    book_clean = sum(known_cleans, start=Decimal(0)) if known_cleans else None
    markets = [item.market_pnl for item in split if item.market_pnl is not None]
    residuals = [item.residual_pnl for item in split if item.residual_pnl is not None]
    book_market = sum(markets, start=Decimal(0)) if markets else None
    book_residual = sum(residuals, start=Decimal(0)) if residuals else None
    intradays = [item.intraday_pnl for item in split if item.intraday_pnl is not None]
    intraday_values = [
        item.intraday_market_value for item in split if item.intraday_market_value is not None
    ]
    book_intraday = sum(intradays, start=Decimal(0)) if intradays else None
    book_intraday_value = sum(intraday_values, start=Decimal(0)) if intraday_values else None

    # Step 5. Weight uses the gross from step 2. Market value uses shares held at the close.
    snapshots: list[NameSnapshot] = []
    for (name, shares, previous, close), value, item in zip(raw, values, split, strict=True):
        name_weight = None if value is None or gross is None else weight(value, gross)
        name_limit = as_decimal(name.limit)
        snapshots.append(
            NameSnapshot(
                symbol=name.symbol,
                shares=shares,
                previous_close=previous,
                close=close,
                market_value=value,
                daily_pnl=item.actual,
                clean_pnl=item.clean,
                weight=name_weight,
                limit=name_limit,
                limit_breached=limit_breached(name_weight, name_limit),
                split_factor=as_decimal(name.split_factor),
                div_cash=as_decimal(name.div_cash),
                dividend_cash=item.dividend_cash,
                market_pnl=item.market_pnl,
                residual_pnl=item.residual_pnl,
                last=item.last,
                intraday_pnl=item.intraday_pnl,
                intraday_clean_pnl=item.intraday_clean_pnl,
                intraday_market_value=item.intraday_market_value,
                dollar_pnl=shares,
                vega_pnl=Decimal(0),
            )
        )

    # Step 6. VaR uses the caller's history of book P&Ls, not the market slice.
    # Fewer than 60 observations publishes None.
    bench_previous = None
    bench_close = None
    if benchmark is not None and bench_return is not None:
        bench_previous = as_decimal(benchmark.previous_close)
        bench_close = as_decimal(benchmark.close)
    return BookSnapshot(
        gross_market_value=gross,
        book_pnl=book_pnl,
        book_clean_pnl=book_clean,
        names=tuple(snapshots),
        historical_var=published_historical_var(book_daily_pnls),
        historical_var_window=PUBLISHED_VAR_WINDOW,
        book_market_pnl=book_market,
        book_residual_pnl=book_residual,
        benchmark_symbol=None if bench_return is None else benchmark_symbol,
        benchmark_previous=bench_previous,
        benchmark_close=bench_close,
        benchmark_simple_return=bench_return,
        book_intraday_pnl=book_intraday,
        book_intraday_market_value=book_intraday_value,
        book_dollar_pnl=sum((shares for _, shares, _, _ in raw), start=Decimal(0)),
        book_vega_pnl=Decimal(0),
        options=tuple(option_snapshot(mark) for mark in options),
    )


@dataclass(frozen=True)
class _NamePnl:
    """Actual, clean, dividend cash, then the market slice and what is left."""

    actual: Decimal | None
    clean: Decimal | None
    dividend_cash: Decimal | None
    market_pnl: Decimal | None
    residual_pnl: Decimal | None
    intraday_pnl: Decimal | None = None
    intraday_clean_pnl: Decimal | None = None
    intraday_market_value: Decimal | None = None
    last: Decimal | None = None


def _split_pnl(
    name: NameMark,
    end_shares: Decimal,
    previous: Decimal | None,
    close: Decimal | None,
    bench_return: Decimal | None = None,
) -> _NamePnl:
    """Actual, clean, dividend cash, market P&L, residual P&L.

    A split changes the share count and the close together. The position row
    is already the post-split holding. Yesterday's close is still the pre-split
    print, so the move is `close * factor - previous` on the pre-split shares.
    Dividend cash is per pre-split share and is added on top of that move.
    Market P&L is yesterday's market value times the benchmark return.
    Residual is clean P&L minus that market piece. A fill is not in either piece.
    """
    blank = _NamePnl(None, None, None, None, None)
    # Step 1. Shares at the previous close, in today's (post-split) units.
    traded = sum((as_decimal(fill.shares) for fill in name.trades), start=Decimal(0))
    start_shares = end_shares - traded
    # Step 2. A negative start means the fills are larger than the position.
    # That is a broken book, so publish neither P&L. Do not invent a short.
    if start_shares < 0:
        return blank
    # Step 3. A non-positive split cannot scale yesterday's close. A negative
    # dividend is not cash received. Leave both P&Ls blank.
    factor = as_decimal(name.split_factor)
    dividend = as_decimal(name.div_cash)
    if factor <= 0 or dividend < 0:
        return blank
    # Step 4. Pre-split shares. Used by the official close and by the last.
    prior_shares = start_shares / factor
    intra_actual, intra_clean, intra_value, shown_last = _intraday(
        name, end_shares, prior_shares, previous, factor, dividend
    )
    # Step 5. Official P&L needs yesterday's close and today's close. A gap stays blank.
    # The last is not copied into the close, so a last alone does not publish +2,000.
    if previous is None or close is None:
        return _NamePnl(
            None,
            None,
            None,
            None,
            None,
            intra_actual,
            intra_clean,
            intra_value,
            shown_last,
        )
    price_pnl = prior_shares * (close * factor - previous)
    # Step 6. Cash dividend on those same pre-split shares. A $2 dividend on
    # 200 shares is +400, which offsets a 380 to 378 drop.
    clean = price_pnl + dividend * prior_shares
    # Step 7. One unusable fill blanks the official name. Do not treat that fill as zero.
    # A fill is in post-split shares and today's close.
    legs: list[Decimal] = []
    for fill in name.trades:
        leg = trade_pnl(fill.shares, fill.price, close)
        if leg is None:
            return blank
        legs.append(leg)
    # Step 8. Market piece uses yesterday's market value. 76,000 * 1% = 760.
    # No benchmark return leaves both slices blank. Clean P&L stays as it was.
    cash = dividend * prior_shares
    actual = clean + sum(legs, start=Decimal(0))
    if bench_return is None:
        return _NamePnl(
            actual,
            clean,
            cash,
            None,
            None,
            intra_actual,
            intra_clean,
            intra_value,
            shown_last,
        )
    market = prior_shares * previous * bench_return
    return _NamePnl(
        actual,
        clean,
        cash,
        market,
        clean - market,
        intra_actual,
        intra_clean,
        intra_value,
        shown_last,
    )


def _intraday(
    name: NameMark,
    end_shares: Decimal,
    prior_shares: Decimal,
    previous: Decimal | None,
    factor: Decimal,
    dividend: Decimal,
) -> tuple[Decimal | None, Decimal | None, Decimal | None, Decimal | None]:
    """Intraday actual, clean, market value, and the last that was stored.

    A last of 395 on 200 shares against yesterday's 380 is +3,000.
    The official close is not used. No last leaves every value blank.
    """
    # Step 1. No stored last. Do not borrow the official close.
    if name.last is None:
        return None, None, None, None
    last = as_decimal(name.last)
    # A non-positive last is not a print.
    if last <= 0:
        return None, None, None, None
    # Step 2. The last still has a market value when yesterday's close is missing.
    # The P&L does not: there is no baseline to subtract.
    value = end_shares * last
    if previous is None:
        return None, None, value, last
    # Step 3. Same share basis as the official clean P&L. Factor 1 leaves 200 * (395 - 380).
    clean = prior_shares * (last * factor - previous) + dividend * prior_shares
    # Step 4. A fill is marked to the last, not to the official close.
    # 50 shares bought at 385 and a last of 395 add +500.
    legs: list[Decimal] = []
    for fill in name.trades:
        leg = trade_pnl(fill.shares, fill.price, last)
        if leg is None:
            return None, None, None, last
        legs.append(leg)
    return clean + sum(legs, start=Decimal(0)), clean, value, last


def _number(value: Decimal | None) -> str | None:
    """JSON number as a plain decimal string. None stays JSON null, not "0"."""
    if value is None:
        return None
    return format(value, "f")
