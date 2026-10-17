# Cash Equity Position Dashboard

After the close, a cash-equity book is scattered across positions, prices, and a concentration limit. The painful part is the exception note: someone copies those numbers into a short note for risk, and a copied number becomes part of the record.

This screen is that book for one session. Official closes and share counts sit in one database. Code computes P&L, weights, volatility, and a historical loss tail. A rule opens an exception ticket when a limit breaks, a name's P&L is far from its own history, or a close is missing. A model may draft one note from that ticket. A person confirms or rejects it. The system does not send orders and does not predict the next price.

The repository is [cash-equity-position-dashboard](https://github.com/Kellyxinyuwu/cash-equity-position-dashboard). The Python import package remains `book_move`. The page title is Cash Equity Position Dashboard.

## Who it is for

| Person | What they do here |
|---|---|
| Trader | Read the day's P&L, weights, and any exception ticket |
| Risk | Read the draft against the ticket fields, then confirm or reject |
| Desk developer | Pull closes, keep the formulas in code, and let a model write only after a rule fires |

## Start

```bash
python3 -m venv .venv
make install
cp .env.example .env
make init
make seed
make pull START=2026-07-01 END=2026-10-16
make serve
```

Set `MARKET_DATA_API_KEY` in `.env` before `pull`. Set `BOOK_PASSWORD` to put a sign-in screen in front of the page. Leave it blank and the page stays open. `MODEL_NAME` is the local model, default `llama3.2:latest`. `ollama serve` has to be running for Write draft.

| Where | Address |
|---|---|
| This laptop | http://127.0.0.1:8000 |
| Docker | http://127.0.0.1:8001 |

`docker compose up --build` serves the same SQLite file from `./data`. The image does not contain `.env`. Inside the container, Write draft calls Ollama on the laptop at `host.docker.internal:11434`.

Refresh checks whether the latest New York official close is already stored. After 16:30 New York time on a weekday, that close is due. If it is already stored, the click does not call Tiingo. If it is missing, the click pulls a short recent window, including SPY. Opening the page does not call Tiingo.

`make mcp` exposes one tool, `book_as_of`, for a date `YYYY-MM-DD`. It returns the same figures as the page and does not draft.

`make test` runs Ruff and pytest.

## Data sources

Share counts are not a market-data product. A vendor such as Tiingo publishes prices, not how many shares this book holds. The live book uses real US listings. The 200-share Tencent example (0700.HK, close 380 to 390, P&L +2,000) lives only in the tests. The free Tiingo tier used here does not serve Hong Kong names.

| Input | Kind | Where it is stored | Link |
|---|---|---|---|
| Official close, split factor, cash dividend | External API. Fields kept: `close`, `splitFactor`, `divCash`. `adjClose` is ignored. | `prices` | [Tiingo end-of-day docs](https://www.tiingo.com/documentation/end-of-day). Request: `https://api.tiingo.com/tiingo/daily/{symbol}/prices?startDate=&endDate=` |
| Benchmark close for SPY | Same Tiingo daily endpoint. SPY is not a position. | `benchmark_prices` | [SPDR S&P 500 ETF](https://www.ssga.com/us/en/intermediary/etfs/spdr-sp-500-etf-trust-spy) via the same [Tiingo daily URL](https://www.tiingo.com/documentation/end-of-day) |
| Sample share counts and 60% weight limits | Written by `seed`. AAPL 100, MSFT 80, NVDA 40, AMZN 30, GOOGL 20. The tickers are real listings. The share counts are ours. | `positions` | No holdings API. Listings: [AAPL](https://www.nasdaq.com/market-activity/stocks/aapl), [MSFT](https://www.nasdaq.com/market-activity/stocks/msft), [NVDA](https://www.nasdaq.com/market-activity/stocks/nvda), [AMZN](https://www.nasdaq.com/market-activity/stocks/amzn), [GOOGL](https://www.nasdaq.com/market-activity/stocks/googl) |
| Fills | Written with `python -m book_move trade`. Not a download. Does not change `positions.shares`. | `trades` | None. An empty table means clean P&L and actual P&L are the same number. |
| Intraday last | Not pulled. A row can be inserted for a test. The official close is never copied into this column. | `last_prices` | No intraday feed is wired. Tiingo's daily bar does not have a separate last. |
| Option delta and vega | Stored inputs. Not from a pricing model. The sample book has no option rows. | `options` | None. |
| Exception draft | One local chat completion, and only after Write draft is clicked on an open ticket. | `ticket_reviews.draft` | [Ollama](https://ollama.com) at `http://127.0.0.1:11434/v1/chat/completions`. Default model `llama3.2`. An empty key is allowed only for `127.0.0.1`, `localhost`, or `host.docker.internal`. |

The database file is `data/book.sqlite`. It is local and not committed.

## Dashboard figures

Shares in these formulas are the holding at the close, already after any split. Yesterday's close is still the previous session's official print. Pre-split shares are `(end shares − today's signed fill shares) / split factor`. A buy is positive. A day with no split stores factor 1. A day with no dividend stores 0. A blank cell means an input is missing. The page does not substitute zero.

| Figure | How it is calculated | Example |
|---|---|---|
| Close | The official Tiingo `close` for that symbol and session. | 390 |
| Shares | The seeded holding, or a later edit of `positions`. Pull does not change it. | 200 |
| Split | Tiingo `splitFactor` for that session. Missing on the vendor row means 1. | 10-for-1 stores 10. No event stores 1. |
| Dividend | Cash on the pre-split shares: `divCash × pre-split shares`. `divCash` is cash per pre-split share. | Dividend 2 on 200 shares is +400. No event is 0.00. |
| Market value | `end shares × official close` | 200 × 390 = 78,000 |
| Gross market value | Sum of the name market values. SPY is not included. | 78,000 + 34,000 = 112,000 in the test book |
| Weight | `name market value / gross market value` | 78,000 / 112,000 ≈ 69.6% |
| Limit | The limit stored on the position. The sample book uses 60% for every name. | 60% |
| Breached | Weight is strictly greater than the limit. Equal to the limit is not a breach. | 69.6% against 60% is yes |
| Actual P&L | Clean P&L plus each fill marked to the official close: `fill shares × (close − fill price)`. No fills means actual equals clean. | No fill: 200 × (390 − 380) = +2,000. Buy 50 at 385 with an end holding of 250: clean +2,000, fill +250, actual +2,250. |
| Clean P&L | `pre-split shares × (close × split factor − previous close) + dividend cash`. The shares bought today are not given the move from yesterday's close. | No split and no dividend: 200 × (390 − 380) = +2,000. A 10-for-1 day, yesterday 200 at 380, today 2,000 shares at 38: 200 × (38 × 10 − 380) = 0. |
| Market P&L | Yesterday's market value times the benchmark simple return. Beta is fixed at 1. It is not estimated. The fill is outside this piece. | Yesterday's value 76,000 and a 1% benchmark move: 760. Clean +2,000 splits into 760 and a residual of 1,240. |
| Residual P&L | `clean P&L − market P&L` | 2,000 − 760 = 1,240 |
| Benchmark line | SPY's own simple return: `(close × split factor + dividend) / previous official close − 1` | Closes 100 to 101 are a 1% return. Those two prints are a teaching pair, not a claim about a live SPY close. |
| Last | The last stored for this session only. Yesterday's last is not reused. | A test last of 395. The live book has no last rows, so the column stays `—`. |
| Intraday P&L | The clean formula with last in place of the official close, plus fills marked to the last. | 200 × (395 − 380) = +3,000. The official close of 390 still produces +2,000. |
| Up 1 | Cash stock: one currency unit times the share count, because cash delta per share is 1. This is not the day's P&L. An option, if one is stored, is `quantity × delta` and is listed under the table, outside this cash total. | 200 shares move 200. The 10-point day is still the +2,000 P&L. |
| Vega | Cash stock: 0. A stored option is `quantity × vega`. If either greek is missing, both option shocks are blank. | The cash book is 0. A test option of 100 contracts, delta 0.4, vega 2, is +40 and +200, and those amounts stay out of gross market value. |
| Daily vol | Sample standard deviation of the last 20 simple returns. The divisor is n − 1. A simple return is `(close × split factor + dividend) / previous official close − 1`. A missing close breaks the chain and is not filled forward. | The column says `fewer than 20 returns` until that window exists. |
| Annualized | `daily vol × √252` | Shown only when daily vol is shown. |
| VaR 95% · 1-day loss | The last 60 book daily P&Ls, sorted worst to best. The rank is `60 × 0.05`. An integer rank is kept. Otherwise it rounds up. The selected P&L is reported as a positive loss. This history does not replay fills, lasts, or the benchmark. | Five P&Ls of +10, +5, −20, +8, −40 select −40 and report a loss of 40. The page hides the number until 60 book P&Ls exist. |

Fetched-at, when it appears, is the clock of the pull. It is not the session date.

## Exception tickets

A ticket opens only when a rule hits. Loading the page does not call a model.

| Rule | Opens when |
|---|---|
| Limit breach | Weight is strictly above that name's limit |
| Abnormal P&L | The absolute daily P&L is greater than 2 times the sample standard deviation of that name's previous 20 daily P&Ls |
| Missing close | That name has no official close for the session |

Fewer than 20 prior P&Ls does not open an abnormal ticket, and it does not invent a standard deviation. Write draft sends one open ticket to the chat endpoint. A draft that quotes a number not on the ticket, or that recommends a trade, is not stored. Confirm and Reject do not call the model.

## Folder structure

```text
src/book_move/     Formulas, SQLite, the Tiingo client, the page, the draft call
ui/                React screen. It displays the API and does not call Tiingo
tests/unit/        No network and no database
tests/integration/ Temporary SQLite and a fake price client
data/              Local book.sqlite. Not committed
```

| File | What it does |
|---|---|
| `src/book_move/analytics.py` | Every figure above. No network and no database. |
| `src/book_move/storage.py` | SQLite tables: positions, prices, trades, lasts, benchmark prices, options, ticket reviews, and the pull check. |
| `src/book_move/marketdata.py` | The only Tiingo HTTP client. |
| `src/book_move/jobs.py` | Pull. Reads symbols from positions, asks Tiingo, writes prices and SPY. |
| `src/book_move/sessions.py` | Which New York session should already have an official close. |
| `src/book_move/reporting.py` | One as-of snapshot. Reads SQLite and calls the formulas. |
| `src/book_move/rules.py` | Limit breach, abnormal P&L, missing close. Fields only. |
| `src/book_move/tickets.py` | Opens those rules for one session. |
| `src/book_move/explain.py` | Checks a finished draft against the ticket. Does not call a model. |
| `src/book_move/agent.py` | One chat completion for one open ticket. |
| `src/book_move/auth.py` | Shared password cookie for the page. |
| `src/book_move/web.py` | `/api/book`, `/api/pull`, ticket posts, and the plain HTML fallback. |
| `src/book_move/mcp_server.py` | Stdio tool `book_as_of`. Same numbers as the page. No vendor call and no draft. |
| `src/book_move/__main__.py` | `init`, `seed`, `pull`, `trade`, `report`, `serve`, `mcp`. |
| `ui/src/App.tsx` | The dashboard. Formats JSON. Does not recompute P&L. |
| `ui/src/Login.tsx` | Sign-in card when `BOOK_PASSWORD` is set. |
| `ui/src/format.ts` | Money, shares, percents, and the sign color. |
| `ui/src/types.ts` | Shape of `/api/book`. |
| `ui/src/styles.css` | Layout of the screen. |
| `ui/src/main.tsx` | React entry. |
| `tests/unit/test_analytics.py` | Formulas, including the 200-share example. |
| `tests/unit/test_mother_problem.py` | The same pinned book: +2,000, −1,000, gross 112,000. |
| `tests/unit/test_rules.py` | The three ticket rules. |
| `tests/unit/test_tiingo.py` | Parser: keep close, split, dividend. Ignore `adjClose`. |
| `tests/unit/test_sessions.py` | Which session is due, including a weekend. |
| `tests/unit/test_auth.py` | The page cookie. |
| `tests/unit/test_agent.py` | A draft is stored only when its numbers match the ticket. |
| `tests/integration/test_book_store.py` | Prices, fills, splits, and the benchmark land in SQLite. |
| `tests/integration/test_web.py` | The page reads the database. A missing close stays blank. |
| `tests/integration/test_refresh.py` | Refresh pulls a missing close and does not call Tiingo when it is already stored. |
| `tests/integration/test_login.py` | The book stays closed until the password matches. |
| `tests/integration/test_mcp.py` | `book_as_of` matches the page. |
| `Makefile` | `install`, `test`, `seed`, `pull`, `serve`, `ui`, `mcp`. |
| `pyproject.toml` | Package metadata. No runtime dependencies. |
| `Dockerfile` | Builds the React page, then serves it with Python. |
| `docker-compose.yml` | Publishes host port 8001 and mounts `./data`. |
| `.env.example` | Tiingo key, Ollama settings, and the desk password. |
| `.github/workflows/ci.yml` | Ruff and pytest on push and pull request. |

Only `marketdata.py` calls Tiingo. Only `agent.py` calls a model, and only after a ticket exists.

## Not a production desk

Share counts are seeded, not rebuilt from a blotter. A split does not multiply the stored shares. The book has to write the post-split holding. There is no intraday vendor, so Last stays blank. Greeks are stored inputs. Historical VaR here is a textbook tail, not an approved risk model. One shared password is the whole sign-in. There is no scheduled pull. Refresh is the manual one.
