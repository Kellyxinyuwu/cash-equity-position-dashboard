"""End-of-day book move explainer.

What lives in this package:

- analytics.py: P&L, weight, volatility, and historical VaR. No network.
- storage.py: the SQLite file. positions and prices are two different tables.
- marketdata.py: the only module that talks to the Tiingo HTTP API.
- jobs.py: pull job. Reads symbols from positions, asks Tiingo for closes, writes prices.
  The page button calls this only when the due official close is not stored yet.
- reporting.py: as-of snapshot. Reads SQLite only.
- web.py: /api/book and the plain HTML fallback. Reads SQLite only. Does not call Tiingo.
  When BOOK_PASSWORD is set, the page asks for that password first.
  The React screen in ui/ only displays that JSON.
- rules.py: limit breach, abnormal P&L, and missing close. Fields only, no prose.
- tickets.py: opens those rules for one as-of date. Does not call a model.
- explain.py: drafts only when a ticket exists. Checks that a draft's numbers match the ticket.
- agent.py: the only module that calls a model. One open ticket's fields, nothing else.
- mcp_server.py: stdio MCP tools. book_as_of returns the page's numbers. No vendor call, no draft.
- __main__.py: the command line. seed writes sample share counts. pull fetches prices.

Nothing here sends an order. A blank MODEL_API_KEY leaves the draft empty.
"""

__version__ = "0.0.0"
