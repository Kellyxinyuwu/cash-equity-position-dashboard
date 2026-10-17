import { useState, type FormEvent } from "react";

// The book stays behind this card when BOOK_PASSWORD is set.

export function Login({ onSignedIn }: { onSignedIn: () => void }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!password) {
      setError("Enter the desk password.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const response = await fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password }),
      });
      if (!response.ok) {
        setError("That password does not open this book.");
        return;
      }
      setPassword("");
      onSignedIn();
    } catch {
      setError("The book API is not running. Start it with make serve.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="gate">
      <form className="gate-card" onSubmit={submit}>
        <p className="kicker">Cash Equity Position Dashboard</p>
        <h1>Sign in</h1>
        <p className="deck">One shared password. The numbers stay on this machine.</p>
        <label htmlFor="desk-password">Password</label>
        <input
          id="desk-password"
          type="password"
          name="password"
          autoComplete="current-password"
          autoFocus
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
        {error ? <p className="gate-error">{error}</p> : null}
        <button type="submit" disabled={busy}>
          {busy ? "Checking…" : "Open the book"}
        </button>
      </form>
    </main>
  );
}
