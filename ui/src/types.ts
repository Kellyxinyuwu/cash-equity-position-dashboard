// Shape of GET /api/book. Strings are Decimal text from Python. Null is a missing close.

export interface Volatility {
  daily: string;
  annualized: string;
  observations: number;
}

export interface NameRow {
  symbol: string;
  shares: string;
  close: string | null;
  last: string | null;
  intraday_pnl: string | null;
  dollar_pnl: string | null;
  vega_pnl: string | null;
  split_factor: string;
  div_cash: string;
  dividend_cash: string | null;
  fetched_at: string | null;
  daily_pnl: string | null;
  clean_pnl: string | null;
  market_pnl: string | null;
  residual_pnl: string | null;
  market_value: string | null;
  weight: string | null;
  limit: string;
  limit_breached: boolean | null;
  volatility: Volatility | null;
}

export interface TicketName {
  symbol: string;
  shares: string;
  previous_close: string | null;
  close: string | null;
  market_value: string | null;
  daily_pnl: string | null;
  weight: string | null;
  limit: string;
}

export interface Ticket {
  rule: string;
  symbol: string;
  as_of: string;
  shares: string;
  previous_close: string | null;
  close: string | null;
  market_value: string | null;
  daily_pnl: string | null;
  clean_pnl: string | null;
  weight: string | null;
  limit: string;
  gross_market_value: string | null;
  book_pnl: string | null;
  pnl_stdev: string | null;
  multiple: string | null;
  names: TicketName[];
  status: string;
  draft: string;
}

export interface Benchmark {
  symbol: string | null;
  previous_close: string | null;
  close: string | null;
  simple_return: string | null;
}

export interface OptionRow {
  symbol: string;
  quantity: string;
  delta: string | null;
  vega: string | null;
  dollar_pnl: string | null;
  vega_pnl: string | null;
}

export interface PullState {
  due: string;
  stored: string | null;
  ready: boolean;
  fetched: boolean;
  bars: number;
}

export interface BookPage {
  as_of: string;
  book_pnl: string | null;
  book_clean_pnl: string | null;
  book_market_pnl: string | null;
  book_residual_pnl: string | null;
  book_intraday_pnl: string | null;
  book_dollar_pnl: string | null;
  book_vega_pnl: string | null;
  gross_market_value: string | null;
  historical_var: { loss: string; confidence: string; window: number } | null;
  volatility_window: number;
  var_window: number;
  benchmark: Benchmark | null;
  options: OptionRow[];
  names: NameRow[];
  tickets: Ticket[];
  model_configured: boolean;
}
