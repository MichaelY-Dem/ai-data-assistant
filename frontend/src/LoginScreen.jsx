import { useState } from "react";
import { signIn } from "./api.js";

// Sign-in form. On success the backend returns a token, which api.js keeps and sends with
// every request. (With Entra ID this screen becomes a "Sign in with Microsoft" button.)
export default function LoginScreen({ notice, onSignedIn }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  async function submit(event) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const session = await signIn(username.trim(), password);
      setPassword("");
      onSignedIn(session);
    } catch (err) {
      setError(err.message);
      setPassword("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-page">
      <form className="login-card" onSubmit={submit} aria-labelledby="login-title">
        <h1 id="login-title">Portfolio Analyst Assistant</h1>
        <p className="muted">Sign in to ask questions about the infrastructure portfolio.</p>

        {notice && !error && (
          <div className="login-notice" role="status">
            {notice}
          </div>
        )}
        {error && (
          <div className="banner" role="alert">
            {error}
          </div>
        )}

        <label className="field">
          <span>Username</span>
          <input
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            autoFocus
            required
            maxLength={64}
          />
        </label>
        <label className="field">
          <span>Password</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
            maxLength={256}
          />
        </label>
        <button type="submit" className="btn primary login-submit" disabled={busy || !username.trim() || !password}>
          {busy ? "Signing in..." : "Sign in"}
        </button>
      </form>
    </div>
  );
}
