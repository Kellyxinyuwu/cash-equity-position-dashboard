import { useEffect, useState } from "react";
import { factor, fetchedAt, money, percent, shares, signClass, signedMoney } from "./format";
import { Login } from "./Login";
import type { BookPage, NameRow, OptionRow, PullState, Ticket } from "./types";

// Desk screen. Every number arrives from /api/book. This file does not mark the book.

export function App() {
  const [asOf, setAsOf] = useState("");
  const [page, setPage] = useState<BookPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const [access, setAccess] = useState<"checking" | "locked" | "open">("checking");
  const [passwordRequired, setPasswordRequired] = useState(false);
  const [pull, setPull] = useState<PullState | null>(null);
  const [phase, setPhase] = useState<"idle" | "pulling" | "done">("idle");
  const [pullNote, setPullNote] = useState<string | null>(null);
  const [pullError, setPullError] = useState<string | null>(null);

  useEffect(() => {
    const control = new AbortController();
    fetch("/api/session", { signal: control.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error("session");
        return response.json() as Promise<{ required: boolean; signed_in: boolean }>;
      })
      .then((session) => {
        setPasswordRequired(session.required);
        setAccess(session.required && !session.signed_in ? "locked" : "open");
      })
      .catch((reason: unknown) => {
        if (reason instanceof DOMException && reason.name === "AbortError") return;
        setAccess("open");
      });
    return () => control.abort();
  }, []);

  useEffect(() => {
    if (access !== "open") return;
    const control = new AbortController();
    const query = asOf ? `?as_of=${asOf}` : "";
    fetch(`/api/book${query}`, { signal: control.signal })
      .then(async (response) => {
        if (response.status === 401) {
          setAccess("locked");
          setPasswordRequired(true);
          return null;
        }
        if (!response.ok) {
          throw new Error("The book did not return that session.");
        }
        return response.json() as Promise<BookPage>;
      })
      .then((next) => {
        if (!next) return;
        setPage(next);
        setError(null);
        document.title = `Cash Equity Position Dashboard · ${next.as_of}`;
      })
      .catch((reason: unknown) => {
        if (reason instanceof DOMException && reason.name === "AbortError") return;
        setError("The book API is not running. Start it with make serve.");
      });
    return () => control.abort();
  }, [asOf, revision, access]);

  useEffect(() => {
    if (access !== "open") return;
    const control = new AbortController();
    fetch("/api/pull", { signal: control.signal })
      .then(async (response) => {
        if (response.status === 401) {
          setAccess("locked");
          setPasswordRequired(true);
          return null;
        }
        if (!response.ok) throw new Error("pull status");
        return response.json() as Promise<PullState>;
      })
      .then((state) => {
        if (state) setPull(state);
      })
      .catch((reason: unknown) => {
        if (reason instanceof DOMException && reason.name === "AbortError") return;
      });
    return () => control.abort();
  }, [access, revision]);

  async function pullLatest() {
    // The request can return at once when the close is already stored.
    // Hold the pulling phase long enough that the click is visible.
    const started = performance.now();
    setPhase("pulling");
    setPullError(null);
    setPullNote(null);
    try {
      const response = await fetch("/api/pull", { method: "POST" });
      await holdPullPhase(started);
      if (response.status === 401) {
        setAccess("locked");
        setPasswordRequired(true);
        setPhase("idle");
        return;
      }
      const body = (await response.json()) as PullState & { error?: string };
      if (!response.ok) {
        setPullError(body.error || "The latest close was not stored.");
        setPhase("idle");
        return;
      }
      setPull(body);
      setPullNote(pullResult(body));
      // The button follows the newest stored close, including a day already pulled.
      if (body.stored) setAsOf(body.stored);
      setRevision((current) => current + 1);
      setPhase("done");
    } catch {
      setPullError("The book API is not running. Start it with make serve.");
      setPhase("idle");
    }
  }

  async function signOut() {
    await fetch("/api/logout", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
    setPage(null);
    setAccess("locked");
  }

  if (access === "checking") {
    return (
      <main className="gate">
        <h1>Cash Equity Position Dashboard</h1>
      </main>
    );
  }

  if (access === "locked") {
    return <Login onSignedIn={() => setAccess("open")} />;
  }

  return (
    <div className="screen">
      <header className="top">
        <div>
          <p className="kicker">After the close</p>
          <h1>Cash Equity Position Dashboard</h1>
          <p className="deck">{heldLine(page)}</p>
        </div>
        <div className="toolbar">
          <form
            className="asof"
            onSubmit={(event) => {
              event.preventDefault();
            }}
          >
            <label htmlFor="as-of">Session date</label>
            <input
              id="as-of"
              type="date"
              value={asOf || page?.as_of || ""}
              onChange={(event) => setAsOf(event.target.value)}
            />
          </form>
          <PullButton
            state={pull}
            phase={phase}
            note={pullNote}
            onPull={() => void pullLatest()}
          />
          {passwordRequired ? (
            <button type="button" className="ghost" onClick={() => void signOut()}>
              Sign out
            </button>
          ) : null}
        </div>
      </header>

      {pullError ? <p className="banner">{pullError}</p> : null}
      {error ? <p className="banner">{error}</p> : null}
      {page ? (
        <Book page={page} onReviewed={() => setRevision((current) => current + 1)} />
      ) : (
        <p className="banner">Reading the book…</p>
      )}
    </div>
  );
}

function PullButton({
  state,
  phase,
  note,
  onPull,
}: {
  state: PullState | null;
  phase: "idle" | "pulling" | "done";
  note: string | null;
  onPull: () => void;
}) {
  const pulling = phase === "pulling";
  return (
    <div className={pulling ? "pull busy" : "pull"}>
      <button
        type="button"
        className="solid"
        disabled={pulling || !state}
        aria-busy={pulling}
        onClick={onPull}
      >
        {pulling ? <span className="spin" aria-hidden="true" /> : null}
        {pulling ? "Pulling…" : "Refresh"}
      </button>
      <p>{pulling ? "Pulling the latest close…" : note || pullStatusLine(state)}</p>
    </div>
  );
}

function pullResult(state: PullState): string {
  if (state.fetched && state.bars > 0 && state.stored) {
    return `Pulled ${state.bars} closes. Latest session ${state.stored}.`;
  }
  if (state.stored && state.stored === state.due) {
    return `Checked. Official close ${state.stored} is already stored.`;
  }
  if (state.stored) return `Checked. No newer close after ${state.stored}.`;
  return "Checked. No official close is stored.";
}

function holdPullPhase(started: number): Promise<void> {
  const remain = 900 - (performance.now() - started);
  if (remain <= 0) return Promise.resolve();
  return new Promise((resolve) => {
    setTimeout(resolve, remain);
  });
}

function pullStatusLine(state: PullState | null): string {
  if (!state) return "Checking the stored close.";
  if (!state.ready) {
    if (state.stored) return `Stored through ${state.stored}. ${state.due} is not in the book.`;
    return `${state.due} is not in the book.`;
  }
  if (state.stored === state.due) return `Official close ${state.due} is stored.`;
  if (state.stored) return `Stored through ${state.stored}. The vendor has nothing for ${state.due}.`;
  return "No official close is stored.";
}

function heldLine(page: BookPage | null): string {
  const base = "Shares held in this book, marked to the official close.";
  if (!page || page.names.length === 0) return base;
  return `${page.names.map((name) => name.symbol).join(" · ")}. ${base}`;
}

function Book({ page, onReviewed }: { page: BookPage; onReviewed: () => void }) {
  const varText = page.historical_var
    ? money(page.historical_var.loss)
    : `fewer than ${page.var_window} book P&Ls`;
  const fetchStamp = sharedFetch(page.names);
  const showFetched = fetchStamp === "mixed";
  return (
    <>
      <p className="pulled">{pullCaption(page.names, fetchStamp)}</p>
      <p className="pulled">{benchmarkCaption(page)}</p>
      <section className="board">
        <div>
          <h2>P&amp;L</h2>
          <div className="totals">
            <Total
              label="Actual P&L"
              value={signedMoney(page.book_pnl)}
              tone={signClass(page.book_pnl)}
            />
            <Total
              label="Clean P&L"
              value={signedMoney(page.book_clean_pnl)}
              tone={signClass(page.book_clean_pnl)}
            />
            <Total
              label="Market P&L"
              value={slice(page.book_market_pnl)}
              tone={page.book_market_pnl === null ? "missing" : signClass(page.book_market_pnl)}
            />
            <Total
              label="Residual P&L"
              value={slice(page.book_residual_pnl)}
              tone={page.book_residual_pnl === null ? "missing" : signClass(page.book_residual_pnl)}
            />
            <Total
              label="Intraday P&L"
              value={slice(page.book_intraday_pnl)}
              tone={page.book_intraday_pnl === null ? "missing" : signClass(page.book_intraday_pnl)}
            />
          </div>
        </div>
        <div>
          <h2>Size and shocks</h2>
          <div className="totals">
            <Total
              label="Up 1"
              value={slice(page.book_dollar_pnl)}
              tone={page.book_dollar_pnl === null ? "missing" : signClass(page.book_dollar_pnl)}
            />
            <Total
              label="Vega"
              value={slice(page.book_vega_pnl)}
              tone={page.book_vega_pnl === null ? "missing" : signClass(page.book_vega_pnl)}
            />
            <Total label="Gross market value" value={money(page.gross_market_value)} />
            <Total
              label="VaR 95% · 1-day loss"
              value={varText}
              tone={page.historical_var ? "var" : "missing"}
              hint={`${page.var_window} sessions`}
            />
          </div>
        </div>
      </section>
      <section className="positions">
        <h2>Positions</h2>
        <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Shares</th>
              <th>Close</th>
              <th>Last</th>
              <th>Split</th>
              <th>Dividend</th>
              {showFetched ? <th>Fetched at</th> : null}
              <th>Actual P&L</th>
              <th>Clean P&L</th>
              <th>Intraday P&L</th>
              <th>Up 1</th>
              <th>Vega</th>
              <th>Market P&L</th>
              <th>Residual</th>
              <th>Market value</th>
              <th>Weight</th>
              <th>Limit</th>
              <th>Breached</th>
              <th>Daily vol</th>
              <th>Annualized</th>
            </tr>
          </thead>
          <tbody>
            {page.names.map((name) => (
              <Name
                key={name.symbol}
                name={name}
                volatilityWindow={page.volatility_window}
                showFetched={showFetched}
              />
            ))}
          </tbody>
        </table>
        </div>
      </section>
      <Options options={page.options} />
      <Tickets page={page} onReviewed={onReviewed} />
    </>
  );
}

function slice(value: string | null): string {
  if (value === null) return "—";
  return signedMoney(value);
}

function benchmarkCaption(page: BookPage): string {
  const bench = page.benchmark;
  if (!bench || bench.simple_return === null || bench.previous_close === null || bench.close === null) {
    return "Market and residual stay blank until the benchmark has yesterday's close and today's close.";
  }
  const symbol = bench.symbol ?? "benchmark";
  return (
    `Benchmark ${symbol} ${money(bench.previous_close)} to ${money(bench.close)}, ` +
    `return ${percent(bench.simple_return)}.`
  );
}

function Total({
  label,
  value,
  tone = "",
  hint,
}: {
  label: string;
  value: string;
  tone?: string;
  hint?: string;
}) {
  return (
    <article className="total">
      <p>{label}</p>
      <strong className={tone}>{value}</strong>
      {hint ? <span className="hint">{hint}</span> : null}
    </article>
  );
}

function Name({
  name,
  volatilityWindow,
  showFetched,
}: {
  name: NameRow;
  volatilityWindow: number;
  showFetched: boolean;
}) {
  const vol = name.volatility
    ? percent(name.volatility.daily)
    : `fewer than ${volatilityWindow} returns`;
  const annualized = name.volatility ? percent(name.volatility.annualized) : "—";
  return (
    <tr>
      <td className="symbol">{name.symbol}</td>
      <td>{shares(name.shares)}</td>
      <td className={name.close === null ? "missing" : ""}>{money(name.close)}</td>
      <td className={name.last === null ? "missing" : ""}>
        {name.last === null ? "—" : money(name.last)}
      </td>
      <td>{factor(name.split_factor)}</td>
      <td className={name.dividend_cash === null ? "missing" : signClass(name.dividend_cash)}>
        {name.dividend_cash === null ? "—" : signedMoney(name.dividend_cash)}
      </td>
      {showFetched ? (
        <td className={name.fetched_at === null ? "missing" : "when"}>{fetchedAt(name.fetched_at)}</td>
      ) : null}
      <td className={signClass(name.daily_pnl)}>{signedMoney(name.daily_pnl)}</td>
      <td className={signClass(name.clean_pnl)}>{signedMoney(name.clean_pnl)}</td>
      <td className={name.intraday_pnl === null ? "missing" : signClass(name.intraday_pnl)}>
        {slice(name.intraday_pnl)}
      </td>
      <td className={name.dollar_pnl === null ? "missing" : signClass(name.dollar_pnl)}>
        {slice(name.dollar_pnl)}
      </td>
      <td className={name.vega_pnl === null ? "missing" : signClass(name.vega_pnl)}>
        {slice(name.vega_pnl)}
      </td>
      <td className={name.market_pnl === null ? "missing" : signClass(name.market_pnl)}>
        {slice(name.market_pnl)}
      </td>
      <td className={name.residual_pnl === null ? "missing" : signClass(name.residual_pnl)}>
        {slice(name.residual_pnl)}
      </td>
      <td className={name.market_value === null ? "missing" : ""}>{money(name.market_value)}</td>
      <td>
        <Weight weight={name.weight} limit={name.limit} breached={name.limit_breached} />
      </td>
      <td>{percent(name.limit)}</td>
      <td>{breach(name.limit_breached)}</td>
      <td className={name.volatility === null ? "missing" : ""}>{vol}</td>
      <td>{annualized}</td>
    </tr>
  );
}

function Weight({
  weight,
  limit,
  breached,
}: {
  weight: string | null;
  limit: string;
  breached: boolean | null;
}) {
  if (weight === null || Number(limit) === 0) {
    return <span className="missing">—</span>;
  }
  const used = Math.min((Number(weight) / Number(limit)) * 100, 100);
  return (
    <span className="weight">
      <span>{percent(weight)}</span>
      <span className="track">
        <span className={breached ? "fill hot" : "fill"} style={{ width: `${used}%` }} />
      </span>
    </span>
  );
}

function sharedFetch(names: NameRow[]): string | null | "mixed" {
  if (names.length === 0) return null;
  const first = names[0]?.fetched_at ?? null;
  if (names.every((name) => name.fetched_at === first)) return first;
  return "mixed";
}

function pullCaption(names: NameRow[], fetchStamp: string | null | "mixed"): string {
  if (names.length === 0) return "No positions in the book.";
  if (fetchStamp === "mixed") return "Fetch times differ by name. They stay on the row.";
  if (fetchStamp === null) return "No close stored for this session.";
  return `Closes fetched ${fetchedAt(fetchStamp)}. That clock is the pull, not the session.`;
}

const RULE_LABEL: Record<string, string> = {
  limit_breach: "Limit breach",
  abnormal_pnl: "Abnormal P&L",
  missing_close: "Missing close",
};

function problemText(problem: string): string {
  if (problem === "missing-close sentence absent") {
    return "The draft never said the close is missing, so it was not saved.";
  }
  if (problem === "trade advice") {
    return "The draft recommended a trade, so it was not saved.";
  }
  if (problem.startsWith("untraced")) {
    return `The draft used a number that is not on the ticket (${problem.slice("untraced ".length)}), so it was not saved.`;
  }
  return problem;
}

function Options({ options }: { options: OptionRow[] }) {
  if (options.length === 0) {
    return <p className="pulled">No options. A cash stock's vega is 0.</p>;
  }
  return (
    <section className="tickets">
      <h2>Options</h2>
      <p className="note">These shocks are not part of official P&L or gross market value.</p>
      <ul>
        {options.map((option) => (
          <li key={option.symbol}>
            {option.symbol} · quantity {shares(option.quantity)} · up 1 {slice(option.dollar_pnl)} ·
            vol +1 {slice(option.vega_pnl)}
          </li>
        ))}
      </ul>
    </section>
  );
}

function Tickets({ page, onReviewed }: { page: BookPage; onReviewed: () => void }) {
  const [notes, setNotes] = useState<Record<string, string>>({});

  async function review(ticket: Ticket, status: string) {
    const response = await fetch("/api/tickets", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        as_of: ticket.as_of,
        symbol: ticket.symbol,
        rule: ticket.rule,
        status,
      }),
    });
    if (response.ok) onReviewed();
  }

  async function writeDraft(ticket: Ticket) {
    const key = `${ticket.symbol}-${ticket.rule}`;
    const response = await fetch("/api/tickets/draft", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        as_of: ticket.as_of,
        symbol: ticket.symbol,
        rule: ticket.rule,
      }),
    });
    const body = (await response.json()) as { problems?: string[]; rejected?: string };
    if (!response.ok || (body.problems && body.problems.length > 0)) {
      const reason = (body.problems ?? []).map(problemText).join(" ") || "The draft was not stored.";
      const wrote = body.rejected ? ` Model wrote: ${body.rejected}` : "";
      setNotes((current) => ({ ...current, [key]: reason + wrote }));
      return;
    }
    setNotes((current) => ({ ...current, [key]: "" }));
    onReviewed();
  }

  return (
    <section className="tickets">
      <h2>Exception tickets</h2>
      {page.tickets.length === 0 ? (
        <p>None. No rule hit on this session, so the model is not called.</p>
      ) : (
        <>
          <p>
            {page.model_configured
              ? "Opening this page does not call the model. Write draft sends this ticket only."
              : "No model key. Tickets stay on the page and the draft stays empty."}
          </p>
          {page.tickets.map((ticket) => (
            <article className="ticket" key={`${ticket.symbol}-${ticket.rule}`}>
              <header>
                <strong>{RULE_LABEL[ticket.rule] ?? ticket.rule}</strong>
                <span>{ticket.symbol}</span>
                <span className="quiet">{ticket.status}</span>
              </header>
              <dl>
                <div>
                  <dt>Shares</dt>
                  <dd>{shares(ticket.shares)}</dd>
                </div>
                <div>
                  <dt>Previous close</dt>
                  <dd>{money(ticket.previous_close)}</dd>
                </div>
                <div>
                  <dt>Close</dt>
                  <dd className={ticket.close === null ? "missing" : ""}>{money(ticket.close)}</dd>
                </div>
                <div>
                  <dt>Actual P&L</dt>
                  <dd className={signClass(ticket.daily_pnl)}>{signedMoney(ticket.daily_pnl)}</dd>
                </div>
                <div>
                  <dt>Clean P&L</dt>
                  <dd className={signClass(ticket.clean_pnl)}>{signedMoney(ticket.clean_pnl)}</dd>
                </div>
                <div>
                  <dt>Weight</dt>
                  <dd>{percent(ticket.weight)}</dd>
                </div>
                <div>
                  <dt>Limit</dt>
                  <dd>{percent(ticket.limit)}</dd>
                </div>
                <div>
                  <dt>Book P&L</dt>
                  <dd>{signedMoney(ticket.book_pnl)}</dd>
                </div>
                {ticket.pnl_stdev ? (
                  <div>
                    <dt>P&L stdev</dt>
                    <dd>
                      {money(ticket.pnl_stdev)} × {ticket.multiple}
                    </dd>
                  </div>
                ) : null}
              </dl>
              <p className="draft">Draft: {ticket.draft || "empty"}</p>
              {notes[`${ticket.symbol}-${ticket.rule}`] ? (
                <p className="missing">{notes[`${ticket.symbol}-${ticket.rule}`]}</p>
              ) : null}
              <div className="actions">
                {page.model_configured ? (
                  <button type="button" onClick={() => writeDraft(ticket)}>
                    Write draft
                  </button>
                ) : null}
                <button type="button" onClick={() => review(ticket, "confirmed")}>
                  Confirm
                </button>
                <button type="button" onClick={() => review(ticket, "rejected")}>
                  Reject
                </button>
              </div>
            </article>
          ))}
        </>
      )}
    </section>
  );
}

function breach(value: boolean | null) {
  if (value === null) return <span className="missing">—</span>;
  if (value) return <span className="pill">Yes</span>;
  return <span className="quiet">No</span>;
}
